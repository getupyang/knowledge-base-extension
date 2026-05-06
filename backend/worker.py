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
    MEMAI_LLM_PROVIDER / MEMAI_LOCAL_AGENT / MEMAI_LLM_API_KEY
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
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from llm_client import LLMError, LLMTimeoutError, get_llm_client, get_llm_status

# ── 路径与 agent_api.py 一致 ──
BACKEND_DIR = Path(__file__).resolve().parent

_config_file = Path.home() / ".kb_config"
if _config_file.exists():
    for _line in _config_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

DATA_DIR = Path(os.path.expanduser(os.environ.get("KB_DATA_DIR", "~/.knowledge-base-extension"))).resolve()
LOG_DIR = DATA_DIR / ".logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
DB_PATH = str(DATA_DIR / "comments.db")
USER_PROFILE_PATH = str(DATA_DIR / "user_profile.md")
PROJECT_CONTEXT_PATH = str(DATA_DIR / "project_context.md")
LEARNED_RULES_PATH = str(DATA_DIR / "learned_rules.json")
FAILURE_LOG = LOG_DIR / "failures.jsonl"

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
        if "phase" not in payload:
            payload["phase"] = payload.get("kind") or ("main_loop" if payload.get("main_loop") else "worker")
        with open(FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({**payload, "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_file(path: str, default: str = "") -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return default


_PRIVATE_CONTEXT_LEAK_PATTERNS = [
    "意图-行动缺口",
    "Intention-Action Gap",
    "mem-ai 方向",
    "mem-ai 的产品定位",
    "知识库助手产品迭代",
    "基于浏览器插件的 AI 知识管理产品",
    "正在构建一个基于浏览器插件",
    "记忆赛道",
]


def _looks_like_bundled_private_context(text: str) -> bool:
    if os.environ.get("KB_TRUST_PRIVATE_CONTEXT_FILES") == "1":
        return False
    return any(p in (text or "") for p in _PRIVATE_CONTEXT_LEAK_PATTERNS)


def _load_private_context(path: str, default: str = "") -> str:
    text = _load_file(path, default)
    if _looks_like_bundled_private_context(text):
        log(f"ignoring suspicious private context file: {path}")
        return default
    return text


def _load_learned_rules_data() -> dict:
    raw = _load_file(LEARNED_RULES_PATH, '{"rules":[]}')
    if _looks_like_bundled_private_context(raw):
        log(f"ignoring suspicious learned rules file: {LEARNED_RULES_PATH}")
        return {"rules": []}
    try:
        data = json.loads(raw or '{"rules":[]}')
    except Exception:
        return {"rules": []}
    safe = []
    for rule in data.get("rules", []):
        if not _looks_like_bundled_private_context(rule.get("rule", "")):
            safe.append(rule)
    return {"rules": safe}


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


def wait_for_schema_ready(timeout_sec: int = 30):
    """agent_api owns schema creation; worker waits for it on fresh installs."""
    deadline = time.time() + timeout_sec
    last_error = None
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            conn.close()
            if row:
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"DB schema not ready: jobs table missing ({last_error})")


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

# 历史批注（按时间正序，覆盖用户在这台机器上的长期记忆）

{comments_dump}

# 输出要求

请基于以上长期批注，输出 **一份"用户最近在想的事"思考整理**。
注意：你必须先理解用户从早期到现在的长期关注，再判断最近的变化；不要只被最后几条日报反馈带偏。

绝对不要：
- 平面总结"用户最近在研究 X" — 这是垃圾
- 列关键词或词云
- 输出"目前看不出什么主题" — 永远要给出最深的可见线索

必须做：
- 强制使用"你在逼问 / 你在纠结 / 你反复回到 / 你在试图证明 / 你在悄悄改主意 …"这样的笔法
- 必须引用具体 comment id（写成 `[c#123]` 这种形式），不引用 ≠ 没看
- 必须捕捉演化（从早期 X 转向最近 Y），不只是当前状态
- 篇幅 200 字以内

# 输出格式（严格遵守，不要 markdown 围栏，直接输出 JSON）

输出唯一一个 JSON 对象，字段：
- title: string, ≤ 18 个汉字, 不要包含双引号
- synthesis_md: string, 一段 markdown 整理（≤ 250 字，可以含 [c#xxx] 引用，**不要在内部使用未转义的双引号**，引用原话用「」或『』代替"")
- evidence_comments: string, 用逗号分隔的 comment id 列表（最多 8 个），例如 "12,34,56" — 不是数组，是字符串

不要写解释性文字。不要 ```json 围栏。直接以左大括号开头、右大括号结尾。

示例（仅供格式参考，内容不要照抄）：
{{"title":"你在逼问记忆评测","synthesis_md":"你最近反复回到一个问题：[c#188] [c#176] AI 是否真的学会了，而不是在检索。从「透明度」转向「闭环可证明」。","evidence_comments":"188,176,165"}}
"""


def fetch_recent_comments(conn: sqlite3.Connection, limit: int = 50) -> list:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, page_title, page_url, selected_text, comment, created_at "
        "FROM comments ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_thinking_comments(conn: sqlite3.Connection) -> list:
    """Fetch the durable memory corpus for thinking synthesis.

    LLM providers may change, but the notebook must keep using the user's
    accumulated local memory. By default this reads the full local corpus.
    MEMAI_THINKING_COMMENT_LIMIT is only a user-configured safety cap.
    """
    conn.row_factory = sqlite3.Row
    limit_raw = os.environ.get("MEMAI_THINKING_COMMENT_LIMIT", "").strip()
    limit = 0
    if limit_raw:
        try:
            limit = max(0, int(limit_raw))
        except ValueError:
            log(f"invalid MEMAI_THINKING_COMMENT_LIMIT={limit_raw!r}, using full corpus")
            limit = 0
    total = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    if limit and total > limit:
        log(
            f"thinking synthesis capped by MEMAI_THINKING_COMMENT_LIMIT: "
            f"using latest {limit} of {total} comments"
        )
        rows = conn.execute(
            "SELECT id, page_title, page_url, selected_text, comment, created_at "
            "FROM ("
            "  SELECT id, page_title, page_url, selected_text, comment, created_at "
            "  FROM comments ORDER BY created_at DESC LIMIT ?"
            ") ORDER BY created_at ASC",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT id, page_title, page_url, selected_text, comment, created_at "
        "FROM comments ORDER BY created_at ASC",
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
    """Legacy name kept for call-site stability. Calls configured LLM provider."""
    try:
        return get_llm_client().generate_text(prompt, timeout=timeout_sec)
    except LLMTimeoutError as e:
        raise RuntimeError(f"llm timeout after {timeout_sec}s") from e
    except LLMError as e:
        raise RuntimeError(str(e)) from e


def parse_thinking_output(stdout: str) -> dict:
    """从 claude 输出里提取 JSON。LLM 经常输出未转义的中文引号/换行，要多策略 fallback。"""
    # 策略 1：找 ```json ... ``` 块
    candidates = []
    s = stdout
    if "```json" in s:
        i = s.find("```json") + 7
        j = s.find("```", i)
        if j > i:
            candidates.append(s[i:j].strip())
    # 策略 2：找最外层 { ... }
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        candidates.append(s[i:j+1].strip())

    last_err = None
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data.get("title"), str) and isinstance(data.get("synthesis_md"), str):
                # 兼容两种 evidence 字段：新 prompt 用字符串 evidence_comments，旧用 evidence_comment_ids 数组
                ev = data.get("evidence_comment_ids")
                if not isinstance(ev, list):
                    raw = data.get("evidence_comments") or ""
                    if isinstance(raw, str):
                        ev = []
                        for tok in raw.replace("，", ",").split(","):
                            tok = tok.strip().lstrip("c#").lstrip("#").strip()
                            if tok.isdigit():
                                ev.append(int(tok))
                    else:
                        ev = []
                data["evidence_comment_ids"] = ev
                return data
        except json.JSONDecodeError as e:
            last_err = e
            continue

    # 策略 3：JSON 解析全失败 → 把整段输出当 synthesis_md，标题用第一行
    # 这是兜底产物：宁可让用户看到一段不完美的 markdown，也不让任务失败
    cleaned = stdout.strip()
    # 去掉 ``` 围栏
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValueError(f"claude returned empty output (last json error: {last_err})")
    # 取第一行作为 title（< 60 字）
    first_line = cleaned.split("\n")[0].strip().lstrip("# ").strip("\"'`*")[:60]
    return {
        "title": first_line or "整理结果（解析有损）",
        "synthesis_md": cleaned,
        "evidence_comment_ids": [],
        "_fallback": True,  # 标记兜底，让前端可见
    }


def handle_synthesize_thinking(conn: sqlite3.Connection, job: dict):
    """跑思考整理，写 thinking_summaries"""
    payload = job["payload"]
    trigger_reason = payload.get("trigger_reason", "user_request")

    # 1. 取本机长期 comments。模型服务可以迁移，记忆数据不能退化成只看最近几条。
    comments = fetch_thinking_comments(conn)
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
                comments[0]["created_at"] if comments else now,
                comments[-1]["created_at"] if comments else now,
                "数据还少，先批注几条",
                f"目前只有 {len(comments)} 条批注，还看不出反复在想的事。\n\n继续批注，下次整理就能看到你脑子里在转的东西。",
                json.dumps([c["id"] for c in comments]),
                trigger_reason, len(comments), job["id"], now,
            )
        )
        conn.commit()
        return

    # 2. 拼 prompt
    user_profile = _load_private_context(USER_PROFILE_PATH, "[空白，新用户]")
    project_context = _load_private_context(PROJECT_CONTEXT_PATH, "[未提供]")
    # 截断防止 prompt 过长
    user_profile = user_profile[:3000]
    project_context = project_context[:3000]

    comments_dump_lines = []
    for c in comments:
        excerpt = (c.get("selected_text") or "").strip()[:80]
        excerpt_str = f"\n  > {excerpt}" if excerpt else ""
        comments_dump_lines.append(
            f"[c#{c['id']}] {c['created_at'][:10]} · 《{c.get('page_title','')[:40]}》\n"
            f"  {c['comment'][:180]}{excerpt_str}"
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
    log(f"calling LLM (synthesize_thinking, {len(comments)} comments, prompt {len(prompt)} chars)")
    t0 = time.time()
    stdout = call_claude(prompt, timeout_sec=180)
    elapsed = round(time.time() - t0, 1)
    log(f"LLM returned {len(stdout)} chars in {elapsed}s")

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
            comments[0]["created_at"],
            comments[-1]["created_at"],
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

def _load_active_rules() -> list:
    """读 learned_rules.json 的 active 子集"""
    data = _load_learned_rules_data()
    return [r for r in data.get("rules", []) if r.get("active", True)]


def _load_curated_skills_prompt() -> str:
    """读 agent_prompts/curated_skills.md"""
    p = BACKEND_DIR / "agent_prompts" / "curated_skills.md"
    if not p.exists():
        raise RuntimeError(f"curated_skills.md not found at {p}")
    return p.read_text(encoding='utf-8')


def _safe_parse_curated_json(content: str) -> dict:
    """从 claude 输出里提取 JSON。多策略 fallback。"""
    if not content:
        raise ValueError("empty LLM output")
    s = content.strip()
    if "```json" in s:
        i = s.find("```json") + 7
        j = s.find("```", i)
        if j > i:
            try:
                return json.loads(s[i:j].strip())
            except Exception:
                pass
    i = s.find("{"); j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j+1])
        except Exception:
            pass
    return json.loads(s)


def _persist_skills_generation_in_worker(conn: sqlite3.Connection, distilled: dict,
                                         source_rules: list, trigger_reason: str,
                                         trigger_payload: dict, job_id: int,
                                         llm_model: str = "claude-opus-4-7") -> int:
    """worker 版本：原子性写 4 张表，返回新 generation_id。"""
    skills = distilled.get("skills") or []
    uncategorized = distilled.get("uncategorized_rule_ids") or []
    if not skills:
        raise RuntimeError("distillation produced no valid skills; keep previous active generation")
    now = datetime.now().isoformat()
    cur = conn.cursor()

    cur.execute("UPDATE working_skills SET status='superseded' WHERE status='active'")
    old_active_count = cur.rowcount

    cur.execute(
        "INSERT INTO skill_generations (kind, trigger_reason, trigger_payload, source_rules_count, llm_model, job_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ('skills', trigger_reason, json.dumps(trigger_payload or {}, ensure_ascii=False),
         len(source_rules), llm_model, job_id, now),
    )
    gen_id = cur.lastrowid

    for s in skills:
        cur.execute(
            "INSERT INTO working_skills (name, description, evidence_rule_ids, triggers, status, generation_id, created_at) "
            "VALUES (?, ?, ?, NULL, 'active', ?, ?)",
            (s["name"], s["description"], json.dumps(s["evidence_rule_ids"], ensure_ascii=False),
             gen_id, now),
        )

    if old_active_count == 0:
        diff_summary = f"首次蒸馏：{len(skills)} 个工作方式，基于 {len(source_rules)} 条 rules"
    else:
        diff_summary = f"重蒸馏：{len(skills)} 个工作方式（之前 {old_active_count} 条），基于 {len(source_rules)} 条 rules · 触发：{trigger_reason}"
    diff_json = {
        "skill_count": len(skills),
        "uncategorized_count": len(uncategorized),
        "rules_used": len(source_rules),
        "previous_active": old_active_count,
    }
    cur.execute(
        "INSERT INTO memory_revisions (generation_id, kind, diff_summary, diff_json, created_at) "
        "VALUES (?, 'skills', ?, ?, ?)",
        (gen_id, diff_summary, json.dumps(diff_json, ensure_ascii=False), now),
    )
    conn.commit()
    return gen_id


def handle_synthesize_skills(conn: sqlite3.Connection, job: dict):
    """蒸馏 active rules → working_skills（M3.0 范围 B 自动触发路径）。"""
    payload = job.get("payload") or job.get("payload_json") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    rules = _load_active_rules()
    if len(rules) < 3:
        log(f"job#{job['id']} synthesize_skills: insufficient rules ({len(rules)} active)")
        return

    user_profile = _load_private_context(USER_PROFILE_PATH, "")[:3000]

    rules_dump_lines = []
    for r in rules:
        rid = r.get("id", "?")
        text = r.get("rule", "")
        scope = r.get("scope", "all")
        src = r.get("source", "")
        rules_dump_lines.append(f"[{rid}] scope={scope} · src={src}\n  {text}")
    rules_dump = "\n\n".join(rules_dump_lines)

    template = _load_curated_skills_prompt()
    prompt = template.format(
        user_profile=user_profile,
        rules_dump=rules_dump,
        rule_count=len(rules),
        remaining_count=max(0, len(rules) - 3),
    )

    log(f"job#{job['id']} synthesize_skills: calling LLM with {len(rules)} rules")
    content = call_claude(prompt, timeout_sec=180)
    parsed = _safe_parse_curated_json(content)

    valid_ids = {r.get("id") for r in rules}
    seen_ids = set()
    skills_clean = []
    for s in parsed.get("skills", []) or []:
        name = (s.get("name") or "").strip()
        description = (s.get("description") or "").strip()
        if not name or not description:
            continue
        ids = [rid for rid in (s.get("evidence_rule_ids") or []) if rid in valid_ids and rid not in seen_ids]
        if len(ids) < 2:
            continue
        seen_ids.update(ids)
        skills_clean.append({
            "name": name,
            "description": description,
            "evidence_rule_ids": ids,
        })
    uncategorized = [rid for rid in (parsed.get("uncategorized_rule_ids") or [])
                     if rid in valid_ids and rid not in seen_ids]
    covered = set(seen_ids) | set(uncategorized)
    for r in rules:
        rid = r.get("id")
        if rid and rid not in covered:
            uncategorized.append(rid)

    distilled = {"skills": skills_clean, "uncategorized_rule_ids": uncategorized}
    gen_id = _persist_skills_generation_in_worker(
        conn=conn,
        distilled=distilled,
        source_rules=rules,
        trigger_reason=payload.get("trigger_reason", "unknown"),
        trigger_payload=payload,
        job_id=job["id"],
    )
    log(f"job#{job['id']} synthesize_skills: persisted generation_id={gen_id} with {len(skills_clean)} skills")


MEMORY_GROWTH_PROMPT_TEMPLATE = """你是 mem-ai 的 Memory Growth Pipeline。你的任务不是回复用户，而是把一条新批注解释成可追溯的记忆生长信号。

重要原则：
- 不要只抽 rule。批注也可能表达当前问题、主题热度、项目变化、用户画像变化，或没有结构化更新。
- 不确定时写 no_structural_update，但必须解释为什么。
- 所有字段都只基于输入证据，不要编。

## 当前批注
comment_id: {comment_id}
created_at: {created_at}
page_title: {page_title}
page_url: {page_url}

划线：
{selected_text}

周边文本：
{surrounding_text}

用户批注：
{comment}

AGENT 回复：
{agent_reply}

## 输出格式
只输出一个 JSON 对象，不要 markdown 围栏。schema:
{{
  "comment_interpretation": {{
    "gist": "这条批注核心在说什么",
    "user_attention": "用户注意力落在哪里",
    "stance_or_objection": "用户的判断/反对/疑问；没有就写空字符串",
    "scope": "global_user|project|topic|life|temporary|unknown",
    "confidence": 0.0
  }},
  "rule_candidate": {{
    "should_create": false,
    "rule_text": "",
    "behavior_type": "preference|workflow|anti_pattern|judgment_standard|none",
    "applies_to": "reply|research|prototype|product_decision|writing|other",
    "confidence": 0.0,
    "decision": "candidate|no_rule|duplicate_or_weak"
  }},
  "active_question_signal": {{
    "question": "",
    "signal_strength": 0.0,
    "scope": "project|topic|global_user|temporary|unknown"
  }},
  "theme_signal": {{
    "theme": "",
    "intensity": 0.0
  }},
  "project_signal": {{
    "has_signal": false,
    "summary": "",
    "confidence": 0.0,
    "decision": "none|candidate_project_update"
  }},
  "profile_signal": {{
    "has_signal": false,
    "summary": "",
    "confidence": 0.0,
    "decision": "none|candidate_profile_update"
  }},
  "decision": {{
    "type": "structural_update|no_structural_update",
    "summary": "一句话说明本次是否产生结构化信号"
  }}
}}
"""


def _safe_parse_growth_json(content: str) -> dict:
    if not content:
        raise ValueError("empty LLM output")
    s = content.strip()
    if "```json" in s:
        i = s.find("```json") + 7
        j = s.find("```", i)
        if j > i:
            try:
                return json.loads(s[i:j].strip())
            except Exception:
                pass
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    return {
        "comment_interpretation": {
            "gist": content.strip()[:600],
            "user_attention": "",
            "stance_or_objection": "",
            "scope": "unknown",
            "confidence": 0.2,
        },
        "rule_candidate": {"should_create": False, "decision": "parse_fallback"},
        "active_question_signal": {"question": "", "signal_strength": 0.0, "scope": "unknown"},
        "theme_signal": {"theme": "", "intensity": 0.0},
        "project_signal": {"has_signal": False, "decision": "parse_fallback"},
        "profile_signal": {"has_signal": False, "decision": "parse_fallback"},
        "decision": {"type": "no_structural_update", "summary": "LLM 输出不是合法 JSON，已保留原文摘要。"},
    }


def _growth_now() -> str:
    return datetime.now().isoformat()


def _set_growth_ledger(conn: sqlite3.Connection, comment_id: int, status: str,
                       decision: str = "", error: str = ""):
    now = _growth_now()
    conn.execute(
        """INSERT INTO memory_intake_ledger
           (comment_id, local_status, local_saved_at, growth_status, created_at, updated_at)
           VALUES (?, 'local_saved', ?, ?, ?, ?)
           ON CONFLICT(comment_id) DO UPDATE SET
             growth_status=excluded.growth_status,
             growth_processed_at=CASE WHEN excluded.growth_status IN ('done','failed') THEN excluded.updated_at ELSE memory_intake_ledger.growth_processed_at END,
             growth_decision=COALESCE(NULLIF(?, ''), memory_intake_ledger.growth_decision),
             error_summary=COALESCE(NULLIF(?, ''), memory_intake_ledger.error_summary),
             updated_at=excluded.updated_at""",
        (comment_id, now, status, now, now, decision[:1000] if decision else "", error[:1000] if error else ""),
    )


def _dict_value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else default


def handle_memory_growth_for_comment(conn: sqlite3.Connection, job: dict):
    payload = job.get("payload") or {}
    comment_id = int(payload.get("comment_id") or 0)
    if not comment_id:
        raise RuntimeError("memory_growth_for_comment missing comment_id")

    _set_growth_ledger(conn, comment_id, "running")
    conn.commit()

    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, page_url, page_title, selected_text, surrounding_text, comment, created_at "
        "FROM comments WHERE id=?",
        (comment_id,),
    ).fetchone()
    if not row:
        _set_growth_ledger(conn, comment_id, "failed", error="comment not found")
        conn.commit()
        raise RuntimeError(f"comment#{comment_id} not found")
    comment = dict(row)
    reply_row = conn.execute(
        "SELECT content FROM replies WHERE comment_id=? AND author='agent' ORDER BY created_at DESC LIMIT 1",
        (comment_id,),
    ).fetchone()
    agent_reply = reply_row["content"] if reply_row else ""

    prompt = MEMORY_GROWTH_PROMPT_TEMPLATE.format(
        comment_id=comment_id,
        created_at=comment.get("created_at") or "",
        page_title=(comment.get("page_title") or "")[:200],
        page_url=comment.get("page_url") or "",
        selected_text=(comment.get("selected_text") or "")[:1200],
        surrounding_text=(comment.get("surrounding_text") or "")[:1600],
        comment=(comment.get("comment") or "")[:1600],
        agent_reply=(agent_reply or "")[:1600],
    )
    log(f"job#{job['id']} memory_growth: calling LLM for comment#{comment_id}")
    content = call_claude(prompt, timeout_sec=180)
    parsed = _safe_parse_growth_json(content)
    now = _growth_now()

    interpretation = _dict_value(parsed, "comment_interpretation", {}) or {}
    decision = _dict_value(parsed, "decision", {}) or {}
    decision_summary = _dict_value(decision, "summary", "") or _dict_value(decision, "type", "no_structural_update")
    confidence = _dict_value(interpretation, "confidence", None)
    try:
        confidence = float(confidence) if confidence is not None else None
    except Exception:
        confidence = None

    conn.execute(
        """INSERT OR REPLACE INTO comment_interpretations
           (comment_id, interpretation_json, decision, produced_by_job_id, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (comment_id, json.dumps(interpretation, ensure_ascii=False),
         json.dumps(decision, ensure_ascii=False), job["id"], confidence, now),
    )

    rule = _dict_value(parsed, "rule_candidate", {}) or {}
    if rule.get("should_create") or (rule.get("rule_text") or "").strip():
        conn.execute(
            """INSERT INTO rule_candidates
               (comment_id, rule_text, behavior_type, applies_to, confidence, decision,
                status, produced_by_job_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
            (
                comment_id,
                (rule.get("rule_text") or "").strip(),
                rule.get("behavior_type") or "none",
                rule.get("applies_to") or "other",
                float(rule.get("confidence") or 0),
                rule.get("decision") or "candidate",
                job["id"],
                now,
            ),
        )

    question = _dict_value(parsed, "active_question_signal", {}) or {}
    if (question.get("question") or "").strip():
        conn.execute(
            """INSERT INTO active_question_signals
               (comment_id, question, signal_strength, scope, status, produced_by_job_id, created_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (
                comment_id,
                question.get("question") or "",
                float(question.get("signal_strength") or 0),
                question.get("scope") or "unknown",
                job["id"],
                now,
            ),
        )

    theme = _dict_value(parsed, "theme_signal", {}) or {}
    if (theme.get("theme") or "").strip():
        conn.execute(
            """INSERT INTO theme_signals
               (comment_id, theme, intensity, window_start, window_end, evidence_count,
                representative_comment_ids, produced_by_job_id, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                comment_id,
                theme.get("theme") or "",
                float(theme.get("intensity") or 0),
                comment.get("created_at") or now,
                comment.get("created_at") or now,
                json.dumps([comment_id]),
                job["id"],
                now,
            ),
        )

    project_signal = _dict_value(parsed, "project_signal", {}) or {}
    if project_signal.get("has_signal") or (project_signal.get("summary") or "").strip():
        conn.execute(
            """INSERT INTO project_signals
               (comment_id, signal_json, confidence, decision, produced_by_job_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                comment_id,
                json.dumps(project_signal, ensure_ascii=False),
                float(project_signal.get("confidence") or 0),
                project_signal.get("decision") or "candidate_project_update",
                job["id"],
                now,
            ),
        )

    profile_signal = _dict_value(parsed, "profile_signal", {}) or {}
    if profile_signal.get("has_signal") or (profile_signal.get("summary") or "").strip():
        conn.execute(
            """INSERT INTO profile_signals
               (comment_id, signal_json, confidence, decision, produced_by_job_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                comment_id,
                json.dumps(profile_signal, ensure_ascii=False),
                float(profile_signal.get("confidence") or 0),
                profile_signal.get("decision") or "candidate_profile_update",
                job["id"],
                now,
            ),
        )

    _set_growth_ledger(conn, comment_id, "done", decision=decision_summary)
    conn.commit()
    log(f"job#{job['id']} memory_growth: comment#{comment_id} done ({decision_summary[:80]})")


JOB_HANDLERS = {
    "synthesize_thinking": handle_synthesize_thinking,
    "synthesize_skills": handle_synthesize_skills,
    "memory_growth_for_comment": handle_memory_growth_for_comment,
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
        if job["kind"] == "memory_growth_for_comment":
            try:
                comment_id = int((job.get("payload") or {}).get("comment_id") or 0)
                if comment_id:
                    _set_growth_ledger(conn, comment_id, "failed", error=str(e))
                    conn.commit()
            except Exception:
                pass
        log_failure({
            "job_id": job["id"], "kind": job["kind"], "attempt": job["attempts"],
            "error": str(e), "traceback": tb[:2000],
        })
        retry = job["attempts"] < job["max_attempts"]
        mark_job_failed(conn, job["id"], f"{type(e).__name__}: {e}\n\n{tb}", retry=retry)
        return False


def main():
    llm = get_llm_status()
    log(f"worker starting (DB={DB_PATH})")
    log(f"data dir: {DATA_DIR}")
    log(f"llm provider: {llm.get('selected_provider') or 'MISSING'} ({llm.get('provider_config')})")
    log(f"claude code: {llm['claude_code'].get('bin') or 'MISSING'}")
    log(f"codex cli: {llm['codex_cli'].get('bin') or 'MISSING'}")
    wait_for_schema_ready()
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
