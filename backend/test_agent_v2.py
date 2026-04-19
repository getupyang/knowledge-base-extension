#!/usr/bin/env python3
"""
Agent v2 路由测试脚本
用法：python3 test_agent_v2.py [--cases 1,2,3] [--verbose]

跑 20 条用例过路由器 prompt，对比 intent + role。
门槛：intent >= 90%, role >= 85%
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(ROOT, "agent_prompts")
TEST_DIR = os.path.join(ROOT, ".test_cases")
RESULTS_DIR = os.path.join(TEST_DIR, "results")

os.makedirs(RESULTS_DIR, exist_ok=True)

# ── 加载资源 ──

def load_file(path, default=""):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return default

def load_router_prompt():
    return load_file(os.path.join(PROMPTS_DIR, "router.md"))

def load_test_cases():
    with open(os.path.join(TEST_DIR, "test_intents.json"), "r", encoding="utf-8") as f:
        return json.load(f)

# ── 构建单条测试的完整 prompt ──

def build_router_input(template: str, case: dict) -> str:
    """填充路由器模板的变量"""
    user_profile = load_file(os.path.join(ROOT, "user_profile.md"), "[空白，新用户]")
    project_context = load_file(os.path.join(ROOT, "project_context.md"),
        "项目：意图-行动缺口创业方向。产品：Chrome 知识库插件，评论区 AI agent。目标用户：决策型知识工作者。")
    learned_rules = load_file(os.path.join(ROOT, "learned_rules.json"), '{"rules": []}')

    prompt = template.replace("{user_profile}", user_profile)
    prompt = prompt.replace("{project_context}", project_context)
    prompt = prompt.replace("{learned_rules}", learned_rules)
    prompt = prompt.replace("{last_ai_reply}", "")
    prompt = prompt.replace("{page_url}", case.get("page_url", ""))
    prompt = prompt.replace("{page_title}", case.get("page_title", ""))
    prompt = prompt.replace("{surrounding_context}", case.get("surrounding_context", ""))
    prompt = prompt.replace("{selected_text}", case.get("selected_text", ""))
    prompt = prompt.replace("{comment}", case.get("comment", ""))
    return prompt

# ── 调用 claude -p ──

def call_claude(prompt: str, timeout: int = 120) -> dict:
    """调用 claude -p，返回解析后的 JSON"""
    claude_bin = os.environ.get("KB_CLAUDE_BIN") or os.path.expanduser("~/.npm-global/bin/claude")
    if not os.path.exists(claude_bin):
        # fallback: 直接用 claude（PATH 里找）
        claude_bin = "claude"

    env = os.environ.copy()
    env.setdefault("HOME", os.path.expanduser("~"))

    system_prompt = "你是意图路由器。只输出 JSON，不要任何其他文字、解释或 markdown 代码块。"

    result = subprocess.run(
        [claude_bin, "-p", prompt, "--output-format", "json",
         "--dangerously-skip-permissions", "--system-prompt", system_prompt],
        capture_output=True, text=True, timeout=timeout, env=env
    )

    if result.returncode != 0:
        return {"error": f"claude exit {result.returncode}: {result.stderr[:300]}"}

    try:
        outer = json.loads(result.stdout)
        content = outer.get("result", "")
    except json.JSONDecodeError:
        content = result.stdout

    # 从内容中提取 JSON
    return parse_router_json(content)

def parse_router_json(text: str) -> dict:
    """从 claude 回复中提取路由器 JSON，支持降级"""
    text = text.strip()

    # 去掉 markdown 代码块包裹
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)
    text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取最大的 {...}（支持嵌套）
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # JSON 可能被截断（learned 字段太长），尝试修复
            fixed = try_fix_truncated_json(candidate)
            if fixed:
                return fixed

    # 正则兜底：提取 intent 和 role
    intent_m = re.search(r'"intent"\s*:\s*"(task|dialogue)"', text)
    role_m = re.search(r'"role"\s*:\s*"(researcher|sparring_partner|explainer)"', text)
    if intent_m and role_m:
        return {
            "intent": intent_m.group(1),
            "role": role_m.group(1),
            "confidence": 0,
            "learned": [],
            "quick_response": "",
            "_fallback_parse": True
        }

    return {"error": f"无法解析 JSON: {text[:200]}"}

def try_fix_truncated_json(text: str) -> dict:
    """尝试修复被截断的 JSON（常见于 learned 字段过长）"""
    # 策略：逐步截断尾部，尝试补全 ]} 来闭合
    for suffix in [']}', '"]]}', '"]}', '""]}', ']', '}']:
        try:
            # 从尾部开始找最后一个完整的字段分隔点
            for i in range(len(text) - 1, max(len(text) - 200, 0), -1):
                candidate = text[:i] + suffix
                result = json.loads(candidate)
                if "intent" in result and "role" in result:
                    result["_truncation_fixed"] = True
                    return result
        except (json.JSONDecodeError, ValueError):
            continue
    return None

# ── 评分 ──

def evaluate(case: dict, result: dict) -> dict:
    """对比一条用例的期望和实际结果"""
    intent_match = result.get("intent") == case["expected_intent"]
    role_match = result.get("role") == case["expected_role"]

    has_quick = bool(result.get("quick_response", "").strip())
    has_learned = bool(result.get("learned", []))

    return {
        "case_id": case["id"],
        "intent_match": intent_match,
        "role_match": role_match,
        "actual_intent": result.get("intent", "?"),
        "actual_role": result.get("role", "?"),
        "expected_intent": case["expected_intent"],
        "expected_role": case["expected_role"],
        "has_quick": has_quick,
        "expected_quick": case.get("expected_quick", False),
        "has_learned": has_learned,
        "expected_learned": case.get("expected_learned", False),
        "confidence": result.get("confidence", 0),
        "quick_response_preview": (result.get("quick_response", "") or "")[:80],
        "learned_preview": str(result.get("learned", []))[:80],
        "error": result.get("error"),
    }

# ── 主流程 ──

def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    # 解析 --cases 参数
    case_filter = None
    for arg in sys.argv[1:]:
        if arg.startswith("--cases="):
            case_filter = [int(x) for x in arg.split("=")[1].split(",")]
        elif arg.startswith("--cases"):
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                case_filter = [int(x) for x in sys.argv[idx + 1].split(",")]

    data = load_test_cases()
    template = load_router_prompt()
    cases = data["cases"]
    thresholds = data["thresholds"]

    if case_filter:
        cases = [c for c in cases if c["id"] in case_filter]

    print(f"\n{'='*60}")
    print(f"  Agent v2 路由测试 — {len(cases)} 条用例")
    print(f"  门槛：intent >= {thresholds['intent_accuracy']*100:.0f}%  role >= {thresholds['role_accuracy']*100:.0f}%")
    print(f"{'='*60}\n")

    results = []
    errors = 0

    for i, case in enumerate(cases):
        print(f"[{i+1}/{len(cases)}] Case #{case['id']}: {case['comment'][:40]}...", end=" ", flush=True)
        start = time.time()

        prompt = build_router_input(template, case)
        result = call_claude(prompt)
        elapsed = time.time() - start

        if "error" in result:
            print(f"ERROR ({elapsed:.1f}s): {result['error'][:100]}")
            errors += 1
            ev = {
                "case_id": case["id"],
                "intent_match": False,
                "role_match": False,
                "error": result["error"],
                "actual_intent": "?",
                "actual_role": "?",
                "expected_intent": case["expected_intent"],
                "expected_role": case["expected_role"],
            }
        else:
            ev = evaluate(case, result)
            status = "✓" if (ev["intent_match"] and ev["role_match"]) else "✗"
            detail = f"intent={ev['actual_intent']}({'✓' if ev['intent_match'] else '✗'}) role={ev['actual_role']}({'✓' if ev['role_match'] else '✗'})"
            print(f"{status} {detail} ({elapsed:.1f}s)")

        if verbose and "error" not in result:
            if ev.get("quick_response_preview"):
                print(f"       quick: {ev['quick_response_preview']}")
            if ev.get("plan_preview"):
                print(f"       plan: {ev['plan_preview']}")
            if ev.get("learned_preview") and ev.get("learned_preview") != "[]":
                print(f"       learned: {ev['learned_preview']}")

        ev["elapsed_s"] = round(elapsed, 1)
        results.append(ev)

    # ── 汇总 ──
    total = len(results)
    valid = [r for r in results if not r.get("error")]
    intent_correct = sum(1 for r in valid if r["intent_match"])
    role_correct = sum(1 for r in valid if r["role_match"])
    both_correct = sum(1 for r in valid if r["intent_match"] and r["role_match"])

    intent_acc = intent_correct / total if total else 0
    role_acc = role_correct / total if total else 0

    print(f"\n{'='*60}")
    print(f"  结果汇总")
    print(f"{'='*60}")
    print(f"  总用例: {total}  成功: {len(valid)}  失败: {errors}")
    print(f"  Intent 准确率: {intent_correct}/{total} = {intent_acc*100:.0f}%  {'✓ PASS' if intent_acc >= thresholds['intent_accuracy'] else '✗ FAIL'}")
    print(f"  Role   准确率: {role_correct}/{total} = {role_acc*100:.0f}%  {'✓ PASS' if role_acc >= thresholds['role_accuracy'] else '✗ FAIL'}")
    print(f"  Both   准确率: {both_correct}/{total} = {both_correct/total*100:.0f}%")

    # 列出错误用例
    wrong = [r for r in valid if not (r["intent_match"] and r["role_match"])]
    if wrong:
        print(f"\n  错误用例:")
        for r in wrong:
            parts = []
            if not r["intent_match"]:
                parts.append(f"intent: 期望{r['expected_intent']} 实际{r['actual_intent']}")
            if not r["role_match"]:
                parts.append(f"role: 期望{r['expected_role']} 实际{r['actual_role']}")
            print(f"    #{r['case_id']}: {', '.join(parts)}")

    print()

    # 保存结果
    output = {
        "timestamp": datetime.now().isoformat(),
        "total": total,
        "intent_accuracy": round(intent_acc, 3),
        "role_accuracy": round(role_acc, 3),
        "intent_pass": intent_acc >= thresholds["intent_accuracy"],
        "role_pass": role_acc >= thresholds["role_accuracy"],
        "results": results,
    }
    out_path = os.path.join(RESULTS_DIR, f"round1_routing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {out_path}\n")

    # 退出码
    if intent_acc >= thresholds["intent_accuracy"] and role_acc >= thresholds["role_accuracy"]:
        print("  ✓ 路由测试通过，可以进入 Step 2")
        sys.exit(0)
    else:
        print("  ✗ 路由测试未通过，需要调 prompt")
        sys.exit(1)

if __name__ == "__main__":
    main()
