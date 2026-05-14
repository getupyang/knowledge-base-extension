#!/usr/bin/env python3
"""
Offline evaluator for page-memory digest experiments.

This script is deliberately local and fixture-driven. Real page/comment eval data
should live under .local/ and should not be committed to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures/page-memory/synthetic_eval.v0.json"
SEVERE_LEVELS = {"seen", "highlighted"}
STRONG_LEVELS = {"commented", "asked", "corrected"}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    severe: bool = False


def hash16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]{1,}", (text or "").lower())


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def page_index(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {page["source_id"]: page for page in fixture.get("pages", [])}


def digest_index(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {digest["source_id"]: digest for digest in fixture.get("source_digests", [])}


def exposure_index(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {digest["exposure_id"]: digest for digest in fixture.get("exposure_digests", [])}


def ok(name: str, detail: str = "") -> Check:
    return Check(name=name, passed=True, detail=detail)


def fail(name: str, detail: str, severe: bool = False) -> Check:
    return Check(name=name, passed=False, detail=detail, severe=severe)


def validate_source_digests(fixture: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    pages = page_index(fixture)
    forbidden = {"user_interest", "user_stance", "user_project"}
    for digest in fixture.get("source_digests", []):
        sid = digest.get("source_id")
        name = f"source:{sid}"
        page = pages.get(sid)
        if not page:
            checks.append(fail(name, "missing source page", severe=True))
            continue
        page_text = normalize(page.get("normalized_text", ""))
        if digest.get("normalized_text_hash") != hash16(page_text):
            checks.append(fail(name, "normalized_text_hash mismatch", severe=True))
        else:
            checks.append(ok(name + ":normalized_hash"))
        if forbidden & set(digest):
            checks.append(fail(name, f"forbidden user inference fields present: {sorted(forbidden & set(digest))}", severe=True))

        section_ids = {section.get("section_id") for section in digest.get("sections", [])}
        for section in digest.get("sections", []):
            label = f"{name}:section:{section.get('section_id')}"
            checks.extend(validate_span_hash(label, page_text, section.get("source_span"), section.get("quote_hash")))
        for claim in digest.get("claims", []):
            label = f"{name}:claim:{claim.get('claim', '')[:32]}"
            if claim.get("source_section_id") not in section_ids:
                checks.append(fail(label, "claim source_section_id is missing", severe=True))
            checks.extend(validate_span_hash(label, page_text, claim.get("source_span"), claim.get("quote_hash")))
        for hint in digest.get("retrieval_hints", []):
            label = f"{name}:hint:{hint.get('hint', '')[:32]}"
            refs = set(hint.get("source_section_ids", []))
            if not refs or not refs <= section_ids:
                checks.append(fail(label, "retrieval hint has invalid section refs", severe=True))
            elif not hint.get("must_match_terms"):
                checks.append(fail(label, "retrieval hint missing must_match_terms"))
            else:
                checks.append(ok(label))
    return checks


def validate_span_hash(label: str, text: str, span: dict[str, Any] | None, quote_hash: str | None) -> list[Check]:
    checks: list[Check] = []
    if not span:
        return [fail(label, "missing source_span", severe=True)]
    start = int(span.get("start", -1))
    end = int(span.get("end", -1))
    if span.get("span_base") != "normalized_text":
        checks.append(fail(label, "span_base must be normalized_text", severe=True))
    if start < 0 or end <= start or end > len(text):
        checks.append(fail(label, f"invalid span {start}:{end} for text length {len(text)}", severe=True))
        return checks
    actual = hash16(text[start:end])
    if actual != quote_hash:
        checks.append(fail(label, f"quote_hash mismatch expected={quote_hash} actual={actual}", severe=True))
    else:
        checks.append(ok(label + ":span_hash"))
    return checks


def score_text(query: str, text: str) -> int:
    q = tokens(query)
    haystack = (text or "").lower()
    return sum(haystack.count(token) for token in q)


def retrieval_eval(fixture: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    pages = page_index(fixture)
    digests = digest_index(fixture)
    cases = fixture.get("retrieval_cases", [])
    baseline_ranks = []
    digest_ranks = []
    for case in cases:
        query = case.get("query", "")
        gold = set(case.get("gold_source_ids", []))
        baseline_ranked = rank_sources(query, {
            sid: page.get("normalized_text", "")
            for sid, page in pages.items()
        })
        digest_ranked = rank_sources(query, {
            sid: digest_search_text(digest)
            for sid, digest in digests.items()
        })
        baseline_rank = first_gold_rank(baseline_ranked, gold)
        digest_rank = first_gold_rank(digest_ranked, gold)
        baseline_ranks.append(baseline_rank)
        digest_ranks.append(digest_rank)
        if digest_rank > 3:
            checks.append(fail(f"retrieval:{case.get('case_id')}", f"digest Recall@3 miss rank={digest_rank}", severe=True))
        else:
            checks.append(ok(f"retrieval:{case.get('case_id')}", f"baseline_rank={baseline_rank} digest_rank={digest_rank}"))
    if cases:
        baseline_mrr = mrr(baseline_ranks)
        digest_mrr = mrr(digest_ranks)
        if digest_mrr + 1e-9 < baseline_mrr:
            checks.append(fail("retrieval:mrr", f"digest_mrr={digest_mrr:.3f} below baseline_mrr={baseline_mrr:.3f}", severe=True))
        else:
            checks.append(ok("retrieval:mrr", f"baseline_mrr={baseline_mrr:.3f} digest_mrr={digest_mrr:.3f}"))
    return checks


def digest_search_text(digest: dict[str, Any]) -> str:
    parts: list[str] = [digest.get("page_title", "")]
    for section in digest.get("sections", []):
        parts.extend([section.get("heading", ""), section.get("summary", "")])
    for claim in digest.get("claims", []):
        parts.append(claim.get("claim", ""))
    for entity in digest.get("entities", []):
        parts.append(entity.get("name", ""))
    for hint in digest.get("retrieval_hints", []):
        parts.append(hint.get("hint", ""))
        parts.extend(hint.get("must_match_terms", []))
    return "\n".join(parts)


def rank_sources(query: str, source_text: dict[str, str]) -> list[str]:
    scored = []
    for sid, text in source_text.items():
        scored.append((score_text(query, text), sid))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [sid for _, sid in scored]


def first_gold_rank(ranked: list[str], gold: set[str]) -> int:
    for idx, sid in enumerate(ranked, start=1):
        if sid in gold:
            return idx
    return 999


def mrr(ranks: list[int]) -> float:
    if not ranks:
        return 0.0
    return sum(0 if rank >= 999 else 1 / rank for rank in ranks) / len(ranks)


def validate_exposure_boundaries(fixture: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    for digest in fixture.get("exposure_digests", []):
        eid = digest.get("exposure_id")
        level = digest.get("evidence_level")
        if "temporal_boundary" not in digest:
            checks.append(fail(f"exposure:{eid}:temporal", "missing temporal_boundary", severe=True))
        if level == "seen":
            if digest.get("inferred_attention") or digest.get("possible_interest") or digest.get("active_question_candidates"):
                checks.append(fail(f"exposure:{eid}:seen_boundary", "seen-only exposure has user inference", severe=True))
            elif digest.get("stance_if_any", {}).get("stance") != "unknown":
                checks.append(fail(f"exposure:{eid}:seen_stance", "seen-only exposure has stance", severe=True))
            else:
                checks.append(ok(f"exposure:{eid}:seen_boundary"))
        if level == "highlighted" and digest.get("stance_if_any", {}).get("stance") != "unknown":
            checks.append(fail(f"exposure:{eid}:highlight_stance", "highlight-only evidence inferred stance", severe=True))
        for field in ("inferred_attention", "possible_interest", "active_question_candidates"):
            for item in digest.get(field, []):
                refs = item.get("evidence_refs", [])
                if not refs:
                    checks.append(fail(f"exposure:{eid}:{field}", "inference missing evidence_refs", severe=True))
        stance = digest.get("stance_if_any", {})
        if stance.get("stance") != "unknown":
            refs = stance.get("evidence_refs", [])
            levels = {ref.get("evidence_level") for ref in refs}
            if not refs or not levels <= STRONG_LEVELS:
                checks.append(fail(f"exposure:{eid}:stance_refs", "stance must be grounded in commented/asked/corrected evidence", severe=True))
            else:
                checks.append(ok(f"exposure:{eid}:stance_refs"))
        if level == "corrected" and "correction_priority" not in digest.get("promotion_rules", []):
            checks.append(fail(f"exposure:{eid}:correction", "corrected digest missing correction_priority", severe=True))
    return checks


def validate_memory_growth_signals(fixture: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    signals = fixture.get("memory_growth_signals") or {}
    exposures = exposure_index(fixture)
    if not signals:
        return [fail("memory_growth_signals", "missing memory_growth_signals", severe=True)]
    if signals.get("display_rule") != "evidence_path_first":
        checks.append(fail("memory_growth:display_rule", "display_rule should be evidence_path_first for surfaced signals", severe=True))
    if "temporal_boundary" not in signals:
        checks.append(fail("memory_growth:temporal", "missing temporal_boundary", severe=True))
    for item_type in ("active_questions", "project_clues", "theme_heat"):
        for item in signals.get(item_type, []):
            label = f"memory_growth:{item_type}:{item.get('question') or item.get('label') or item.get('theme')}"
            refs = item.get("evidence_refs", [])
            if not refs:
                checks.append(fail(label, "missing evidence_refs", severe=True))
                continue
            if item_type == "project_clues" and item.get("state") == "confirmed_project":
                levels = evidence_levels(refs, exposures)
                if not levels or levels <= SEVERE_LEVELS:
                    checks.append(fail(label, "confirmed_project unsupported by strong evidence", severe=True))
            if item_type == "theme_heat":
                levels = evidence_levels(refs, exposures)
                if not levels - {"seen"}:
                    checks.append(fail(label, "theme supported only by seen evidence", severe=True))
            checks.append(ok(label))
    return checks


def evidence_levels(refs: list[dict[str, Any]], exposures: dict[str, dict[str, Any]]) -> set[str]:
    levels = set()
    for ref in refs:
        if ref.get("evidence_level"):
            levels.add(ref["evidence_level"])
        if ref.get("ref_type") == "exposure_digest" and ref.get("ref_id") in exposures:
            levels.add(exposures[ref["ref_id"]].get("evidence_level"))
    return {level for level in levels if level}


def run(path: Path) -> dict[str, Any]:
    fixture = load_fixture(path)
    checks: list[Check] = []
    checks.extend(validate_source_digests(fixture))
    checks.extend(retrieval_eval(fixture))
    checks.extend(validate_exposure_boundaries(fixture))
    checks.extend(validate_memory_growth_signals(fixture))
    failed = [check for check in checks if not check.passed]
    severe = [check for check in failed if check.severe]
    return {
        "fixture": str(path),
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "severe": len(severe),
        },
        "checks": [check.__dict__ for check in checks],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--json", action="store_true", help="print full JSON report")
    args = parser.parse_args(argv)
    report = run(Path(args.fixture))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "page-memory eval: "
            f"{summary['passed']}/{summary['checks']} passed, "
            f"failed={summary['failed']}, severe={summary['severe']}"
        )
        for check in report["checks"]:
            if not check["passed"]:
                marker = "SEVERE" if check["severe"] else "FAIL"
                print(f"- {marker} {check['name']}: {check['detail']}")
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
