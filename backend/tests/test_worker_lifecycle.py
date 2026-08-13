# 2A.3 characterization：worker 生命周期（场景清单 B 组）
# 钉住：入队→执行→完成 / 失败重试 / 崩溃恢复 / 重复执行 的当前真实行为。
# LLM 调用全部 monkeypatch，不产生任何真实调用与费用。
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


GROWTH_JSON = {
    "comment_interpretation": {
        "gist": "用户在质疑黑名单机制",
        "user_attention": "隐私边界",
        "stance_or_objection": "白名单更好",
        "scope": "project",
        "confidence": 0.9,
    },
    "rule_candidate": {
        "should_create": True,
        "rule_text": "隐私出口用白名单不用黑名单",
        "behavior_type": "judgment_standard",
        "applies_to": "product_decision",
        "confidence": 0.8,
        "decision": "candidate",
    },
    "active_question_signal": {
        "question": "遥测字段白名单何时落地",
        "signal_strength": 0.7,
        "scope": "project",
    },
    "theme_signal": {"theme": "隐私边界", "intensity": 0.6},
    "project_signal": {"has_signal": False, "project_name": "", "summary": "",
                       "confidence": 0.0, "decision": "none"},
    "profile_signal": {"has_signal": False, "summary": "", "confidence": 0.0,
                       "decision": "none"},
    "decision": {"type": "structural_update", "summary": "产生规则候选与问题信号"},
}


def _conn(worker):
    return sqlite3.connect(worker.DB_PATH, timeout=10)


def _insert_comment(worker, text="测试批注"):
    now = datetime.now().isoformat()
    conn = _conn(worker)
    cur = conn.execute(
        "INSERT INTO comments (page_url, page_title, selected_text, comment, created_at, updated_at) "
        "VALUES ('https://t.test/p', '测试页', '划线', ?, ?, ?)",
        (text, now, now),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def _enqueue(worker, kind, payload, max_attempts=3):
    conn = _conn(worker)
    cur = conn.execute(
        "INSERT INTO jobs (kind, payload_json, status, max_attempts, created_at) "
        "VALUES (?, ?, 'queued', ?, ?)",
        (kind, json.dumps(payload, ensure_ascii=False), max_attempts,
         datetime.now().isoformat()),
    )
    conn.commit()
    jid = cur.lastrowid
    conn.close()
    return jid


def _job_row(worker, jid):
    conn = _conn(worker)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    return dict(row)


def _qone(worker, sql, params=()):
    conn = _conn(worker)
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def _lease_and_run(worker):
    conn = _conn(worker)
    worker.recover_stale_jobs(conn)
    job = worker.lease_next_job(conn)
    assert job is not None, "应能 lease 到 queued 作业"
    ok = worker.run_one(conn, job)
    conn.close()
    return ok, job


def test_b1_growth_job_end_to_end(hermetic, monkeypatch):
    """B1：新批注 → 记忆作业执行 → done + 记忆产物 + 台账可见"""
    worker = hermetic
    monkeypatch.setattr(worker, "call_llm_with_meta",
                        lambda prompt, timeout_sec=180: (json.dumps(GROWTH_JSON, ensure_ascii=False),
                                                         {"provider": "fake", "model": "fake-1"}))
    cid = _insert_comment(worker)
    jid = _enqueue(worker, "memory_growth_for_comment", {"comment_id": cid})

    ok, job = _lease_and_run(worker)
    assert ok and job["id"] == jid

    row = _job_row(worker, jid)
    assert row["status"] == "done" and row["finished_at"]
    assert _qone(worker, "SELECT * FROM comment_interpretations WHERE comment_id=?", (cid,))
    assert _qone(worker, "SELECT * FROM rule_candidates WHERE comment_id=?", (cid,))
    assert _qone(worker, "SELECT * FROM active_question_signals WHERE comment_id=?", (cid,))
    ledger = _qone(worker, "SELECT * FROM memory_intake_ledger WHERE comment_id=?", (cid,))
    assert ledger and ledger["growth_status"] == "done"
    # 出生证明（场景 C5 的 worker 侧）：作业记录了 LLM provider/model
    payload = json.loads(row["payload_json"])
    assert payload["_runtime"]["llm_call"]["provider"] == "fake"


def test_b2_failure_retries_then_failed(hermetic, monkeypatch):
    """B2：LLM 挂 → 重试至 max_attempts → 显式 failed + 错误可见 + 失败日志落盘"""
    worker = hermetic
    monkeypatch.setattr(worker, "call_llm_with_meta",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down")))
    cid = _insert_comment(worker, "会失败的批注")
    jid = _enqueue(worker, "memory_growth_for_comment", {"comment_id": cid}, max_attempts=3)

    for attempt in range(1, 4):
        ok, job = _lease_and_run(worker)
        assert not ok and job["id"] == jid
        row = _job_row(worker, jid)
        if attempt < 3:
            assert row["status"] == "queued", f"第{attempt}次失败后应回队重试"
            assert "llm down" in (row["error"] or "")
        else:
            assert row["status"] == "failed", "耗尽重试后应显式 failed"
            assert row["finished_at"]

    ledger = _qone(worker, "SELECT * FROM memory_intake_ledger WHERE comment_id=?", (cid,))
    assert ledger["growth_status"] == "failed"
    assert "llm down" in (ledger["error_summary"] or "")
    failures = (worker.LOG_DIR / "failures.jsonl").read_text(encoding="utf-8")
    assert failures.count("llm down") >= 3, "每次失败都应写结构化失败日志"


def test_b3_crash_recovery_requeues_stale_running(hermetic):
    """B3：崩溃恢复——lease 过期的 running 回队（作业不丢），recovery 超限显式 failed"""
    worker = hermetic
    jid = _enqueue(worker, "memory_growth_for_comment", {"comment_id": 999999})
    stale = (datetime.now() - timedelta(minutes=10)).isoformat()
    conn = _conn(worker)
    conn.execute("UPDATE jobs SET status='running', lease_expires_at=? WHERE id=?", (stale, jid))
    conn.commit()

    worker.recover_stale_jobs(conn)
    row = _job_row(worker, jid)
    assert row["status"] == "queued", "引擎崩溃（lease 过期）后作业应回队，不丢"
    assert row["recovery_count"] == 1

    # 反复崩溃：recovery_count 超上限 → 显式 failed，不无限循环
    conn.execute("UPDATE jobs SET recovery_count=? WHERE id=?", (worker.MAX_RECOVERY + 1, jid))
    conn.commit()
    worker.recover_stale_jobs(conn)
    conn.close()
    row = _job_row(worker, jid)
    assert row["status"] == "failed"
    assert "recovery" in (row["error"] or "")


def test_b3b_lease_is_exclusive(hermetic):
    """B3b：lease 原子性——同一作业不会被两个 worker 同时拿走"""
    worker = hermetic
    _enqueue(worker, "memory_growth_for_comment", {"comment_id": 999998})
    c1, c2 = _conn(worker), _conn(worker)
    job1 = worker.lease_next_job(c1)
    job2 = worker.lease_next_job(c2)
    c1.close(); c2.close()
    assert job1 is not None
    assert job2 is None or job2["id"] != job1["id"], "同一作业被 lease 两次 = 重复执行风险"
    # 清场：把 running 残留标记完成，避免影响后续测试
    conn = _conn(worker)
    conn.execute("UPDATE jobs SET status='done' WHERE status IN ('queued','running')")
    conn.commit(); conn.close()


def test_b4_rerun_same_comment_interpretation_not_duplicated(hermetic, monkeypatch):
    """B4：同一批注跑两次 growth → 解读不重复（REPLACE 语义）；
    【钉现状】rule_candidates 会重复累积——当前真实行为，候选去重发生在下游蒸馏，不在此层。"""
    worker = hermetic
    monkeypatch.setattr(worker, "call_llm_with_meta",
                        lambda prompt, timeout_sec=180: (json.dumps(GROWTH_JSON, ensure_ascii=False),
                                                         {"provider": "fake", "model": "fake-1"}))
    cid = _insert_comment(worker, "重复处理的批注")
    for _ in range(2):
        _enqueue(worker, "memory_growth_for_comment", {"comment_id": cid})
        ok, _job = _lease_and_run(worker)
        assert ok

    conn = _conn(worker)
    n_interp = conn.execute(
        "SELECT COUNT(*) FROM comment_interpretations WHERE comment_id=?", (cid,)).fetchone()[0]
    n_rules = conn.execute(
        "SELECT COUNT(*) FROM rule_candidates WHERE comment_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert n_interp == 1, "解读应 REPLACE 不累积"
    assert n_rules == 2, "钉现状：候选规则当前会随重复执行累积（若此断言变红=行为已改，需同步产品决策）"


def test_unknown_kind_fails_without_retry(hermetic):
    """未知作业类型 → 显式 failed，不重试、不静默"""
    worker = hermetic
    jid = _enqueue(worker, "no_such_kind", {})
    ok, job = _lease_and_run(worker)
    assert not ok
    row = _job_row(worker, jid)
    assert row["status"] == "failed"
    assert "unknown kind" in (row["error"] or "")


def test_thinking_placeholder_without_llm(hermetic, monkeypatch):
    """批注太少（<3 条相关语料时）思考整理写占位摘要，不浪费 LLM 调用。
    注意：session 内已有批注则走正常路径——此测试只验证'不足时不调 LLM 也能有产物'的分支
    需要空库语义，故直接调 handler 前清空 comments。"""
    worker = hermetic
    conn = _conn(worker)
    saved = conn.execute("SELECT * FROM comments").fetchall()
    conn.execute("DELETE FROM comments")
    conn.commit()

    def boom(*a, **k):
        raise AssertionError("批注不足时不应调 LLM")
    monkeypatch.setattr(worker, "call_llm_with_meta", boom)

    jid = _enqueue(worker, "synthesize_thinking", {"trigger_reason": "test"})
    job = worker.lease_next_job(conn)
    ok = worker.run_one(conn, job)
    assert ok and job["id"] == jid
    row = conn.execute(
        "SELECT * FROM thinking_summaries WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "数据不足也应有占位思考整理（用户侧不空白）"
    conn.close()
