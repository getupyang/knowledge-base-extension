"""
mem-ai 异步 worker — 跑 jobs 表里的后台任务。

设计：~/mem-ai/docs/memory-backend-design.md §3
- durable：jobs 表 + lease/recovery，crash 不丢任务
- 失败显式：不静默丢失（对应 Hermes Issue #2771 反面教材）
- 失败重试 ≤ max_attempts（默认 3），超过写 error 字段
- recovery_count > 3 直接 failed（避免 crash 循环）

启动：
    cd backend && python3 worker.py

环境变量（与 agent_api.py 共享）：
    KB_CLAUDE_BIN（默认 ~/.npm-global/bin/claude）
    KB_NOTION_TOKEN / KB_NOTION_DATABASE_ID（用于读 Notion 上下文）

M2 范围：
- synthesize_thinking（用 Opus 4.7 跑思考整理）

M3 扩展：
- extract_rule_candidate / extract_theme_signals / interpret_comment /
  synthesize_recurring_questions / update_theme_clusters /
  update_profile_project_context / log_agent_action
"""

import os
import sys
import json
import time
import sqlite3
import subprocess
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# ── 路径与 agent_api.py 一致 ──
BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = str(BACKEND_DIR / "comments.db")
USER_PROFILE_PATH = str(BACKEND_DIR / "user_profile.md")
PROJECT_CONTEXT_PATH = str(BACKEND_DIR / "project_context.md")
LEARNED_RULES_PATH = str(BACKEND_DIR / "learned_rules.json")
LOG_DIR = BACKEND_DIR / ".logs"
LOG_DIR.mkdir(exist_ok=True)
FAILURE_LOG = LOG_DIR / "failures.jsonl"

CLAUDE_BIN = os.environ.get("KB_CLAUDE_BIN") or os.path.expanduser("~/.npm-global/bin/claude")

# ── worker 配置 ──
POLL_INTERVAL_SEC = 5         # queued 任务扫描间隔
LEASE_DURATION_MIN = 5        # 一次 lease 时长
HEARTBEAT_INTERVAL_SEC = 30   # 长任务心跳间隔（M3 才用）
MAX_RECOVERY = 3              # 同一任务被 recovery 次数上限


def log(msg: str):
    print(f"[worker {datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def log_failure(payload: dict):
    """结构化失败日志，对应 mem-ai 失败不静默规范"""
    try:
        with open(FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({**payload, "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_file(path: str, default: str = "") -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return default


# ──────────────────────────────────────────
# Lease / Recovery
# ──────────────────────────────────────────

def recover_stale_jobs(conn: sqlite3.Connection):
    """worker 启动时 + 每次扫描前调用：把 lease 过期的 running 重置为 queued"""
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "UPDATE jobs SET status='queued', recovery_count=recovery_count+1, lease_expires_at=NULL "
        "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (now,)
    )
    if cursor.rowcount > 0:
        log(f"recovered {cursor.rowcount} stale running jobs")
    # recovery_count 超限的直接 failed
    cursor = conn.execute(
        "UPDATE jobs SET status='failed', error='exceeded max recovery_count', "
        "finished_at=? WHERE status='queued' AND recovery_count > ?",
        (now, MAX_RECOVERY)
    )
    if cursor.rowcount > 0:
        log(f"marked {cursor.rowcount} jobs failed (exceeded max recovery)")
    conn.commit()


def lease_next_job(conn: sqlite3.Connection):
    """原子拿一个 queued 任务（防止多 worker 抢同一个）"""
    now = datetime.now().isoformat()
    lease_until = (datetime.now() + timedelta(minutes=LEASE_DURATION_MIN)).isoformat()
    # 用条件 UPDATE 防并发
    row = conn.execute(
        "SELECT id, kind, payload_json, attempts, max_attempts FROM jobs "
        "WHERE status='queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not row:
        return None
    job_id = row[0]
    cursor = conn.execute(
        "UPDATE jobs SET status='running', started_at=?, lease_expires_at=?, "
        "attempts=attempts+1 WHERE id=? AND status='queued'",
        (now, lease_until, job_id)
    )
    if cursor.rowcount == 0:
        return None  # 被别的 worker 抢走了
    conn.commit()
    return {
        "id": job_id,
        "kind": row[1],
        "payload": json.loads(row[2] or "{}"),
        "attempts": row[3] + 1,
        "max_attempts": row[4],
    }


def mark_job_done(conn: sqlite3.Connection, job_id: int):
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=?, lease_expires_at=NULL WHERE id=?",
        (now, job_id)
    )
    conn.commit()


def mark_job_failed(conn: sqlite3.Connection, job_id: int, error: str, retry: bool):
    now = datetime.now().isoformat()
    if retry:
        # 还有重试机会 → 重新 queued（attempts 已经在 lease 时 +1）
        conn.execute(
            "UPDATE jobs SET status='queued', error=?, lease_expires_at=NULL WHERE id=?",
            (error[:1000], job_id)
        )
    else:
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=?, lease_expires_at=NULL WHERE id=?",
            (error[:2000], now, job_id)
        )
    conn.commit()


# ──────────────────────────────────────────
# Job Handler: synthesize_thinking
# ──────────────────────────────────────────

THINKING_PROMPT_TEMPLATE = """你是一个深度阅读用户最近批注的产品观察者。你的任务不是总结用户在看什么，
而是 **说出用户隐约在思考但还没整理清楚的核心问题**。

# 用户身份与项目背景

## 用户画像
{user_profile}

## 当前项目
{project_context}

# 最近 {comment_count} 条批注（按时间倒序）

{comments_dump}

# 输出要求

请基于以上批注，输出 **一份"用户最近在想的事"思考整理**。

绝对不要：
- 平面总结"用户最近在研究 X" — 这是垃圾
- 列关键词或词云
- 输出"目前看不出什么主题" — 永远要给出最深的可见线索

必须做：
- 强制使用"你在逼问 / 你在纠结 / 你反复回到 / 你在试图证明 / 你在悄悄改主意 …"这样的笔法
- 必须引用具体 comment id（写成 `[c#123]` 这种形式），不引用 ≠ 没看
- 必须捕捉演化（从 X 转向 Y），不只是当前状态
- 篇幅 200 字以内

# 输出 JSON（严格遵守，不要别的文字）

```json
{{
  "title": "你最近在 ...（10 字以内的短标题）",
  "synthesis_md": "一段 markdown 整理（≤200 字，必须引用 [c#xxx]）",
  "evidence_comment_ids": [按相关性排序的 comment id 列表，最多 8 个]
}}
```
"""


def fetch_recent_comments(conn: sqlite3.Connection, limit: int = 50) -> list:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, page_title, page_url, selected_text, comment, created_at "
        "FROM comments ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_last_thinking_summary(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
        "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def call_claude(prompt: str, timeout_sec: int = 120) -> str:
    """调用 claude CLI，返回 stdout（一段含 JSON 的文本）"""
    if not os.path.exists(CLAUDE_BIN):
        raise RuntimeError(f"claude bin not found: {CLAUDE_BIN}")
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env={**os.environ, "HOME": os.environ.get("HOME", "")},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:500]}")
    return proc.stdout


def parse_thinking_output(stdout: str) -> dict:
    """从 claude 输出里提取 JSON"""
    # 找第一个 ```json ... ``` 块
    s = stdout
    start = s.find("```json")
    if start >= 0:
        s = s[start + 7:]
        end = s.find("```")
        if end >= 0:
            s = s[:end]
    s = s.strip()
    # 兜底：直接找第一个 { ... }
    if not s.startswith("{"):
        i = s.find("{")
        j = s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j+1]
    data = json.loads(s)
    # 字段校验
    if not isinstance(data.get("title"), str) or not isinstance(data.get("synthesis_md"), str):
        raise ValueError("missing title or synthesis_md")
    if not isinstance(data.get("evidence_comment_ids"), list):
        data["evidence_comment_ids"] = []
    return data


def handle_synthesize_thinking(conn: sqlite3.Connection, job: dict):
    """跑思考整理，写 thinking_summaries"""
    payload = job["payload"]
    trigger_reason = payload.get("trigger_reason", "user_request")

    # 1. 取最近 50 条 comments
    comments = fetch_recent_comments(conn, limit=50)
    if len(comments) < 3:
        # 数据太少，直接写一个占位 summary（不调 LLM 浪费）
        log(f"only {len(comments)} comments, writing placeholder summary")
        now = datetime.now().isoformat()
        # 把当前 active 设为 archived
        conn.execute("UPDATE thinking_summaries SET status='archived' WHERE status='active'")
        conn.execute(
            "INSERT INTO thinking_summaries "
            "(window_start, window_end, title, synthesis_md, evidence_comment_ids, "
            " trigger_reason, comments_since_last, status, produced_by_job_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                comments[-1]["created_at"] if comments else now,
                comments[0]["created_at"] if comments else now,
                "数据还少，先批注几条",
                f"目前只有 {len(comments)} 条批注，还看不出反复在想的事。\n\n继续批注，下次整理就能看到你脑子里在转的东西。",
                json.dumps([c["id"] for c in comments]),
                trigger_reason, len(comments), job["id"], now,
            )
        )
        conn.commit()
        return

    # 2. 拼 prompt
    user_profile = _load_file(USER_PROFILE_PATH, "[空白，新用户]")
    project_context = _load_file(PROJECT_CONTEXT_PATH, "[未提供]")
    # 截断防止 prompt 过长
    user_profile = user_profile[:3000]
    project_context = project_context[:3000]

    comments_dump_lines = []
    for c in comments:
        excerpt = (c.get("selected_text") or "").strip()[:200]
        excerpt_str = f"\n  > {excerpt}" if excerpt else ""
        comments_dump_lines.append(
            f"[c#{c['id']}] {c['created_at'][:10]} · 《{c.get('page_title','')[:40]}》\n"
            f"  {c['comment'][:300]}{excerpt_str}"
        )
    comments_dump = "\n\n".join(comments_dump_lines)

    last_summary = fetch_last_thinking_summary(conn)
    last_summary_block = ""
    if last_summary:
        last_summary_block = (
            f"\n# 上一版思考整理（用于检测演化，不要重复）\n\n"
            f"标题：{last_summary['title']}\n"
            f"内容：{last_summary['synthesis_md']}\n"
        )

    prompt = THINKING_PROMPT_TEMPLATE.format(
        user_profile=user_profile,
        project_context=project_context,
        comment_count=len(comments),
        comments_dump=comments_dump,
    ) + last_summary_block

    # 3. 调 LLM
    log(f"calling claude (synthesize_thinking, {len(comments)} comments, prompt {len(prompt)} chars)")
    t0 = time.time()
    stdout = call_claude(prompt, timeout_sec=180)
    elapsed = round(time.time() - t0, 1)
    log(f"claude returned {len(stdout)} chars in {elapsed}s")

    # 4. 解析输出
    data = parse_thinking_output(stdout)

    # 5. 写库
    now = datetime.now().isoformat()
    # 把当前 active 设为 archived（保留历史版本）
    conn.execute("UPDATE thinking_summaries SET status='archived' WHERE status='active'")
    conn.execute(
        "INSERT INTO thinking_summaries "
        "(window_start, window_end, title, synthesis_md, evidence_comment_ids, "
        " trigger_reason, comments_since_last, status, produced_by_job_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (
            comments[-1]["created_at"],
            comments[0]["created_at"],
            data["title"][:120],
            data["synthesis_md"],
            json.dumps(data.get("evidence_comment_ids", []), ensure_ascii=False),
            trigger_reason,
            len(comments),
            job["id"],
            now,
        )
    )
    conn.commit()
    log(f"thinking_summary written: {data['title']}")


# ──────────────────────────────────────────
# 主循环
# ──────────────────────────────────────────

JOB_HANDLERS = {
    "synthesize_thinking": handle_synthesize_thinking,
    # M3 扩展点
}


def run_one(conn: sqlite3.Connection, job: dict) -> bool:
    """跑单个 job，返回是否成功"""
    handler = JOB_HANDLERS.get(job["kind"])
    if not handler:
        mark_job_failed(conn, job["id"], f"unknown kind: {job['kind']}", retry=False)
        return False
    try:
        handler(conn, job)
        mark_job_done(conn, job["id"])
        log(f"job#{job['id']} ({job['kind']}) done")
        return True
    except Exception as e:
        tb = traceback.format_exc()
        log(f"job#{job['id']} ({job['kind']}) FAILED attempt {job['attempts']}/{job['max_attempts']}: {e}")
        log_failure({
            "job_id": job["id"], "kind": job["kind"], "attempt": job["attempts"],
            "error": str(e), "traceback": tb[:2000],
        })
        retry = job["attempts"] < job["max_attempts"]
        mark_job_failed(conn, job["id"], f"{type(e).__name__}: {e}\n\n{tb}", retry=retry)
        return False


def main():
    log(f"worker starting (DB={DB_PATH})")
    log(f"claude bin: {CLAUDE_BIN} ({'OK' if os.path.exists(CLAUDE_BIN) else 'MISSING'})")
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            recover_stale_jobs(conn)
            job = lease_next_job(conn)
            conn.close()
            if job is None:
                time.sleep(POLL_INTERVAL_SEC)
                continue
            log(f"leased job#{job['id']} kind={job['kind']} attempt={job['attempts']}")
            conn = sqlite3.connect(DB_PATH)
            run_one(conn, job)
            conn.close()
        except KeyboardInterrupt:
            log("worker stopped (Ctrl-C)")
            sys.exit(0)
        except Exception as e:
            log(f"main loop error: {e}")
            log_failure({"main_loop": True, "error": str(e), "traceback": traceback.format_exc()[:2000]})
            time.sleep(POLL_INTERVAL_SEC * 2)


if __name__ == "__main__":
    main()
