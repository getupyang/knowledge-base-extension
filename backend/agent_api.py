#!/usr/bin/env python3
"""
评论区 Agent API
端口：8766
功能：评论存 SQLite + 触发 LLM agent + 结果写回评论线程
"""

import sqlite3
import subprocess
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from llm_client import LLMError, LLMTimeoutError, get_llm_client, get_llm_status

ROOT = os.path.dirname(os.path.abspath(__file__))

# 启动时读取 ~/.kb_config，让 uvicorn 子进程也能拿到配置
_config_file = os.path.expanduser("~/.kb_config")
if os.path.exists(_config_file):
    with open(_config_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

DEFAULT_DATA_DIR = os.path.expanduser("~/.knowledge-base-extension")
DATA_DIR = os.path.abspath(os.path.expanduser(os.environ.get("KB_DATA_DIR", DEFAULT_DATA_DIR)))
LOG_DIR = os.path.join(DATA_DIR, ".logs")
DB_PATH = os.path.join(DATA_DIR, "comments.db")
PROJECT_CONTEXT_PATH = os.path.join(DATA_DIR, "project_context.md")
COMPANY_CULTURE_PATH = os.path.join(ROOT, "company_culture.md")
# v2 新增路径
PROMPTS_DIR = os.path.join(ROOT, "agent_prompts")
AGENT_PRINCIPLES_PATH = os.path.join(ROOT, "agent_principles.md")
USER_PROFILE_PATH = os.path.join(DATA_DIR, "user_profile.md")
LEARNED_RULES_PATH = os.path.join(DATA_DIR, "learned_rules.json")
LEGACY_DB_PATH = os.path.join(ROOT, "comments.db")

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    def _comment_count(path: str) -> int:
        if not os.path.exists(path):
            return 0
        try:
            conn = sqlite3.connect(path)
            n = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            conn.close()
            return int(n)
        except Exception:
            return 0

    should_migrate_db = (
        os.environ.get("KB_DISABLE_LEGACY_DB_MIGRATION") != "1"
        and os.path.exists(LEGACY_DB_PATH)
        and (not os.path.exists(DB_PATH) or (_comment_count(DB_PATH) == 0 and _comment_count(LEGACY_DB_PATH) > 0))
    )
    if should_migrate_db:
        try:
            shutil.copy2(LEGACY_DB_PATH, DB_PATH)
            print(f"[agent_api] migrated legacy DB: {LEGACY_DB_PATH} -> {DB_PATH}")
        except Exception as e:
            print(f"[agent_api] legacy DB migration skipped: {e}")
    defaults = {
        USER_PROFILE_PATH: "# 用户画像\n\n（空白，系统会根据这台电脑上的本地批注逐步学习。）\n",
        PROJECT_CONTEXT_PATH: "# 项目上下文\n\n（空白，用户还没有填写项目背景。AI 不能假设用户正在做某个项目。）\n",
        LEARNED_RULES_PATH: '{\n  "rules": []\n}\n',
    }
    for path, content in defaults.items():
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

_ensure_data_dir()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────
# 数据库初始化
# ──────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notion_page_id TEXT,      -- 对应 Notion page id，用于新机器回填和去重
            page_url TEXT NOT NULL,
            page_title TEXT,
            selected_text TEXT,       -- 用户划线的原文
            surrounding_text TEXT,    -- 划线前后各200字，解决指代不清问题
            comment TEXT NOT NULL,    -- 用户的批注内容
            agent_type TEXT,          -- 从 @xxx 解析出来的 agent 类型
            status TEXT DEFAULT 'open',  -- open / resolved / tracking / archived
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # 全文缓存表：按URL去重，首次评论时存，供长期理解用户信息摄入轨迹
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_url TEXT NOT NULL UNIQUE,
            page_title TEXT,
            full_text TEXT,           -- 页面全文
            summary TEXT,             -- AI生成的摘要（异步填充）
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            author TEXT NOT NULL,     -- 'user' 或 'agent'
            agent_type TEXT,          -- agent 类型
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            debug_meta TEXT,          -- JSON，agent 执行元数据（耗时、token 估算等）
            FOREIGN KEY (comment_id) REFERENCES comments(id)
        )
    """)
    # ── P1.2.1：Memory Events，把“一条划线 thread”和“每次用户表达”拆开 ──
    # comments 仍是一条划线/一张 Notion 档案；memory_input_events 记录首评、追问、补充、
    # 纠正、AI 回复等行为事件。用户事件会独立触发 growth。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_input_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            reply_id INTEGER,
            actor TEXT NOT NULL,                  -- 'user' | 'agent' | 'system'
            event_type TEXT NOT NULL,             -- comment_created | user_followup | agent_reply_added | ...
            source_type TEXT NOT NULL,            -- comment | reply | patch | system
            source_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            growth_status TEXT NOT NULL DEFAULT 'pending',
            growth_job_ids TEXT NOT NULL DEFAULT '[]',
            growth_enqueued_at TEXT,
            growth_processed_at TEXT,
            growth_decision TEXT,
            error_summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (reply_id) REFERENCES replies(id),
            UNIQUE(source_type, source_id, event_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_input_events_comment_created ON memory_input_events(comment_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_input_events_growth_status ON memory_input_events(growth_status, updated_at)")

    # ── M2 新增：jobs 异步任务表（durable + crash recovery）──
    # 设计见 ~/mem-ai/docs/memory-backend-design.md §2.10
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,                    -- 'synthesize_thinking' | (M3+ 更多)
            payload_json TEXT,
            status TEXT NOT NULL DEFAULT 'queued', -- 'queued' | 'running' | 'done' | 'failed'
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            lease_expires_at TEXT,                 -- worker 拿任务时 = now + 5min
            heartbeat_at TEXT,                     -- worker 长任务每 30s 续约
            recovery_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(status, lease_expires_at)")

    # ── M2 新增：thinking_summaries 思考整理（笔记本核心 Aha 产物）──
    # 设计见 ~/mem-ai/docs/memory-backend-design.md §2.6
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thinking_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_start TEXT,
            window_end TEXT,
            title TEXT,
            synthesis_md TEXT,                     -- Opus 4.7 输出的 markdown 整理
            evidence_comment_ids TEXT,             -- JSON array of comment.id
            trigger_reason TEXT,                   -- 'threshold_10_comments' | 'weekly_timeout' | 'user_request' | 'first_open'
            comments_since_last INTEGER,
            status TEXT NOT NULL DEFAULT 'active', -- 'active'（最新） | 'archived'（历史版本）
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thinking_status_created ON thinking_summaries(status, created_at DESC)")

    # ── M3.0 范围 B：curated 持久化 + 版本管理 ──
    # 设计见 ~/mem-ai/docs/memory-three-layers-plan.md
    # 1) skill_generations：每次蒸馏 = 一次 generation（atomic 代次）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,                    -- 'skills' | 'profile'
            trigger_reason TEXT NOT NULL,          -- 'manual' | 'rule_count_threshold' | 'cron'
            trigger_payload TEXT,                  -- JSON: {rule_count: 5, since_generation: 1}
            source_rules_count INTEGER,            -- 当时基于多少条 rules 蒸馏
            llm_model TEXT,
            job_id INTEGER,                        -- 关联到 jobs 表
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gen_kind_created ON skill_generations(kind, created_at DESC)")

    # 2) working_skills：当前 active 的 skills 集合（笔记本读这张）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS working_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence_rule_ids TEXT NOT NULL,       -- JSON array
            triggers TEXT,                         -- JSON array (M3.2 才用)
            status TEXT NOT NULL DEFAULT 'active', -- 'active' | 'superseded'
            generation_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (generation_id) REFERENCES skill_generations(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_status_gen ON working_skills(status, generation_id)")

    # 3) profile_snapshots：当前 active 的 profile（"此刻的你"）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            one_liner TEXT NOT NULL,
            field_who TEXT,
            field_doing TEXT,
            field_focus TEXT,
            field_phase TEXT,
            since_last_check TEXT,
            uncategorized_rule_ids TEXT,           -- JSON array (skills 蒸馏后归属的孤立 rules)
            status TEXT NOT NULL DEFAULT 'active',
            generation_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (generation_id) REFERENCES skill_generations(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_status_gen ON profile_snapshots(status, generation_id)")

    # 4) memory_revisions：版本变化日志（用于 diff 视图）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER NOT NULL,
            kind TEXT NOT NULL,                    -- 'skills' | 'profile'
            diff_summary TEXT NOT NULL,            -- LLM 输出的"这次的变化是什么"一句话
            diff_json TEXT,                        -- JSON: {added, modified, removed}
            created_at TEXT NOT NULL,
            FOREIGN KEY (generation_id) REFERENCES skill_generations(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revisions_kind_created ON memory_revisions(kind, created_at DESC)")

    # ── Memory Intake Ledger V0：每条新增批注的本地/Notion/growth 状态留痕 ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_intake_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL UNIQUE,
            local_status TEXT NOT NULL DEFAULT 'local_saved',
            local_saved_at TEXT,
            notion_status TEXT NOT NULL DEFAULT 'pending',
            notion_synced_at TEXT,
            notion_error TEXT,
            growth_status TEXT NOT NULL DEFAULT 'pending',
            growth_job_ids TEXT NOT NULL DEFAULT '[]',
            growth_enqueued_at TEXT,
            growth_processed_at TEXT,
            growth_decision TEXT,
            error_summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_growth_status ON memory_intake_ledger(growth_status, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_notion_status ON memory_intake_ledger(notion_status, updated_at)")

    # ── Context Loader V0：每次回复/记忆问答实际装载了哪些资产 ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reply_id INTEGER,
            comment_id INTEGER,
            chat_message_id INTEGER,
            source_type TEXT NOT NULL DEFAULT 'reply',
            identity_snapshot_id INTEGER,
            selected_skill_ids TEXT NOT NULL DEFAULT '[]',
            episodic_comment_ids TEXT NOT NULL DEFAULT '[]',
            same_page_comment_ids TEXT NOT NULL DEFAULT '[]',
            current_page_url TEXT,
            selection_reasons TEXT NOT NULL DEFAULT '{}',
            token_budget_used INTEGER,
            query_text TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (reply_id) REFERENCES replies(id),
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (identity_snapshot_id) REFERENCES profile_snapshots(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_context_packs_comment ON context_packs(comment_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_context_packs_reply ON context_packs(reply_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_context_packs_chat ON context_packs(chat_message_id)")

    # ── Memory Growth Pipeline V0：结构化解释与信号缓存，不只抽 rules ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comment_interpretations (
            comment_id INTEGER PRIMARY KEY,
            interpretation_json TEXT NOT NULL,
            decision TEXT,
            produced_by_job_id INTEGER,
            confidence REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_event_interpretations (
            event_id INTEGER PRIMARY KEY,
            comment_id INTEGER NOT NULL,
            interpretation_json TEXT NOT NULL,
            decision TEXT,
            produced_by_job_id INTEGER,
            confidence REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id),
            FOREIGN KEY (comment_id) REFERENCES comments(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            event_id INTEGER,
            rule_text TEXT,
            behavior_type TEXT,
            applies_to TEXT,
            confidence REAL,
            decision TEXT,
            status TEXT NOT NULL DEFAULT 'candidate',
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_candidates_comment ON rule_candidates(comment_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_question_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            event_id INTEGER,
            question TEXT,
            signal_strength REAL,
            scope TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_active_question_signals_status ON active_question_signals(status, created_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER,
            event_id INTEGER,
            theme TEXT NOT NULL,
            intensity REAL,
            window_start TEXT,
            window_end TEXT,
            evidence_count INTEGER,
            representative_comment_ids TEXT,
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_theme_signals_theme_created ON theme_signals(theme, created_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            event_id INTEGER,
            signal_json TEXT NOT NULL,
            confidence REAL,
            decision TEXT,
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            event_id INTEGER,
            signal_json TEXT NOT NULL,
            confidence REAL,
            decision TEXT,
            produced_by_job_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (event_id) REFERENCES memory_input_events(id)
        )
    """)

    # ── Memory Chat V0：notebook 内只读“问记忆”入口的轻量历史 ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            context_pack_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (context_pack_id) REFERENCES context_packs(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chat_created ON memory_chat_messages(created_at DESC)")

    # 兼容已有数据库：如果列不存在则添加
    for col, table in [
        ("debug_meta TEXT", "replies"),
        ("surrounding_text TEXT", "comments"),
        ("notion_page_id TEXT", "comments"),
        ("event_id INTEGER", "rule_candidates"),
        ("event_id INTEGER", "active_question_signals"),
        ("event_id INTEGER", "theme_signals"),
        ("event_id INTEGER", "project_signals"),
        ("event_id INTEGER", "profile_signals"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except Exception:
            pass  # 列已存在，忽略
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_notion_page_id ON comments(notion_page_id)")
    # 非破坏性历史映射：让旧 comments/replies 在 Debug Console 里也能按事件查看。
    # 不自动 enqueue growth，避免突然批量加工用户历史库；未来新增事件会自动入队。
    conn.execute("""
        INSERT OR IGNORE INTO memory_input_events
          (comment_id, reply_id, actor, event_type, source_type, source_id,
           content, growth_status, created_at, updated_at)
        SELECT c.id, NULL, 'user', 'comment_created', 'comment', c.id,
               c.comment,
               CASE WHEN l.growth_status = 'done' THEN 'done' ELSE 'historical' END,
               c.created_at, c.updated_at
        FROM comments c
        LEFT JOIN memory_intake_ledger l ON l.comment_id = c.id
    """)
    conn.execute("""
        INSERT OR IGNORE INTO memory_input_events
          (comment_id, reply_id, actor, event_type, source_type, source_id,
           content, growth_status, created_at, updated_at)
        SELECT r.comment_id, r.id, 'user', 'user_followup', 'reply', r.id,
               r.content, 'historical', r.created_at, r.created_at
        FROM replies r
        WHERE r.author = 'user'
    """)
    conn.execute("""
        INSERT OR IGNORE INTO memory_input_events
          (comment_id, reply_id, actor, event_type, source_type, source_id,
           content, growth_status, created_at, updated_at)
        SELECT r.comment_id, r.id, 'agent', 'agent_reply_added', 'reply', r.id,
               r.content, 'skipped', r.created_at, r.created_at
        FROM replies r
        WHERE r.author = 'agent'
    """)
    conn.commit()
    conn.close()

init_db()

# ──────────────────────────────────────────
# 启动诊断日志（帮助新用户排查问题）
# ──────────────────────────────────────────

def _startup_check():
    llm = get_llm_status()
    notion_ok = bool(os.environ.get("KB_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN"))
    print(f"[agent_api] 数据目录: {DATA_DIR}")
    print(f"[agent_api] 数据库: {DB_PATH} ({'✓' if os.path.exists(DB_PATH) else '✗ 不存在'})")
    print(f"[agent_api] LLM provider: {llm.get('selected_provider') or '✗ 未配置'} ({llm.get('provider_config')})")
    print(f"[agent_api] Claude Code: {llm['claude_code'].get('bin')} ({'✓' if llm['claude_code'].get('available') else 'optional: 未找到'})")
    print(f"[agent_api] Codex CLI: {llm['codex_cli'].get('bin')} ({'✓' if llm['codex_cli'].get('available') else 'optional: 未找到'})")
    print(f"[agent_api] Notion Token: {'✓ 已配置' if notion_ok else '✗ 未配置'}")
    print(f"[agent_api] HOME: {os.environ.get('HOME', '未设置')}")
    if llm.get("error"):
        print(f"[agent_api] ⚠ LLM provider 不可用：{llm['error']}")

_startup_check()

# ──────────────────────────────────────────
# v2: 文件加载工具
# ──────────────────────────────────────────

def _load_file(path: str, default: str = "") -> str:
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
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
    """Detect old maintainer-specific context that may have leaked to installs.

    Newer releases never track user_profile.md/project_context.md/learned_rules.json,
    but old local installs can still have copied files. The safest default for a
    public product is to ignore suspicious static context and learn from local
    user behavior instead.
    """
    if os.environ.get("KB_TRUST_PRIVATE_CONTEXT_FILES") == "1":
        return False
    return any(p in (text or "") for p in _PRIVATE_CONTEXT_LEAK_PATTERNS)

def _load_private_context(path: str, empty_message: str) -> str:
    content = _load_file(path, "")
    if not content.strip():
        return empty_message
    if _looks_like_bundled_private_context(content):
        print(f"[privacy] ignoring suspicious private context file: {path}")
        return empty_message
    return content

def load_project_context() -> str:
    return _load_private_context(
        PROJECT_CONTEXT_PATH,
        "（本机还没有可信项目背景。只能基于当前页面、当前评论和本机最近批注回答；不要假设用户正在做某个项目。）",
    )

def load_company_culture() -> str:
    return _load_file(COMPANY_CULTURE_PATH)

def load_agent_principles() -> str:
    return _load_file(AGENT_PRINCIPLES_PATH, load_company_culture())

def load_user_profile() -> str:
    return _load_private_context(
        USER_PROFILE_PATH,
        "（本机还没有可信用户画像。只根据当前页面、当前评论和本机最近批注推断；不确定就明确说不确定。）",
    )

def _load_learned_rules_data() -> dict:
    raw = _load_file(LEARNED_RULES_PATH, '{"rules": []}')
    if _looks_like_bundled_private_context(raw):
        print(f"[privacy] ignoring suspicious learned rules file: {LEARNED_RULES_PATH}")
        return {"rules": []}
    try:
        data = json.loads(raw or '{"rules": []}')
    except Exception:
        return {"rules": []}
    safe_rules = []
    for rule in data.get("rules", []):
        rule_text = rule.get("rule", "")
        if _looks_like_bundled_private_context(rule_text):
            continue
        safe_rules.append(rule)
    return {"rules": safe_rules}

def load_learned_rules() -> str:
    return json.dumps(_load_learned_rules_data(), ensure_ascii=False)

def load_learned_rules_scoped(role: str) -> str:
    """加载适用于指定角色的规则子集"""
    try:
        data = _load_learned_rules_data()
        applicable = [r for r in data.get("rules", [])
                      if r.get("active", True) and r.get("scope") in ("all", f"role:{role}")]
        if not applicable:
            return "（暂无已学到的规则）"
        return "\n".join(f"- {r['rule']}" for r in applicable)
    except Exception:
        return "（暂无已学到的规则）"

def load_prompt_template(name: str) -> str:
    return _load_file(os.path.join(PROMPTS_DIR, f"{name}.md"))

def fetch_local_memory(limit: int = 15) -> str:
    """Read recent local SQLite comments as the user's current attention layer."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT page_title, selected_text, comment, created_at
               FROM comments
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        entries = []
        for title, excerpt, thought, created in rows:
            if not (thought or excerpt):
                continue
            line = f"[{(created or '')[:10]}]"
            if title:
                line += f" 《{title[:30]}》"
            if excerpt:
                line += f"\n  划线：{excerpt[:100]}"
            if thought:
                line += f"\n  想法：{thought[:150]}"
            entries.append(line)
        if not entries:
            return ""
        return "本机最近的阅读批注（只代表这台电脑/这个用户的关注点）：\n" + "\n\n".join(entries)
    except Exception as e:
        print(f"[memory] fetch_local_memory error: {e}")
        return ""

def fetch_notion_memory(limit: int = 15) -> str:
    """拉取当前用户自己的 Notion 最近批注；仅作迁移/兼容 fallback。"""
    import urllib.request
    import urllib.error
    try:
        body = json.dumps({
            "page_size": limit,
            "sorts": [{"timestamp": "created_time", "direction": "descending"}]
        }).encode()
        notion_token = os.environ.get("KB_NOTION_TOKEN", "")
        notion_db_id = os.environ.get("KB_NOTION_DATABASE_ID", "")
        if not notion_token or not notion_db_id:
            return ""
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{notion_db_id}/query",
            data=body,
            headers={
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        entries = []
        for page in data.get("results", []):
            props = page.get("properties", {})
            title = (props.get("标题", {}).get("title") or [{}])[0].get("text", {}).get("content", "")
            excerpt = "".join(r.get("text", {}).get("content", "") for r in props.get("原文片段", {}).get("rich_text", []))
            thought = "".join(r.get("text", {}).get("content", "") for r in props.get("我的想法", {}).get("rich_text", []))
            created = page.get("created_time", "")[:10]
            if thought or excerpt:
                line = f"[{created}]"
                if title:
                    line += f" 《{title[:30]}》"
                if excerpt:
                    line += f"\n  划线：{excerpt[:100]}"
                if thought:
                    line += f"\n  想法：{thought[:150]}"
                entries.append(line)
        if not entries:
            return ""
        return "当前配置的 Notion 数据库最近批注（仅作本机 SQLite 为空时的兼容上下文）：\n" + "\n\n".join(entries)
    except Exception:
        return ""

def fetch_attention_memory(limit: int = 15) -> str:
    """User attention context for prompts. SQLite is authoritative."""
    local = fetch_local_memory(limit)
    if local:
        return local
    return fetch_notion_memory(limit)


# ──────────────────────────────────────────
# Memory Intake Ledger / Context Loader V0
# ──────────────────────────────────────────

def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _record_intake_local_saved(comment_id: int):
    """Create/refresh the per-comment intake ledger row."""
    now = _now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO memory_intake_ledger
               (comment_id, local_status, local_saved_at, notion_status, growth_status,
                created_at, updated_at)
               VALUES (?, 'local_saved', ?, 'pending', 'pending', ?, ?)
               ON CONFLICT(comment_id) DO UPDATE SET
                 local_status='local_saved',
                 local_saved_at=COALESCE(memory_intake_ledger.local_saved_at, excluded.local_saved_at),
                 updated_at=excluded.updated_at""",
            (comment_id, now, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _record_intake_notion(comment_id: int, status: str, error: str = ""):
    if not comment_id:
        return
    _record_intake_local_saved(comment_id)
    now = _now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """UPDATE memory_intake_ledger
               SET notion_status=?, notion_synced_at=?,
                   notion_error=?, updated_at=?
               WHERE comment_id=?""",
            (status, now if status == "notion_synced" else None, error[:1000] if error else None, now, comment_id),
        )
        conn.commit()
    finally:
        conn.close()


def _record_memory_event(comment_id: int, actor: str, event_type: str, content: str,
                         source_type: str, source_id: int, reply_id: Optional[int] = None,
                         growth_status: Optional[str] = None) -> Optional[int]:
    """Record one behavior event inside an annotation thread.

    comments = thread/container. memory_input_events = every user/agent expression.
    """
    if not comment_id or not source_id:
        return None
    now = _now_iso()
    status = growth_status or ("pending" if actor == "user" else "skipped")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT OR IGNORE INTO memory_input_events
               (comment_id, reply_id, actor, event_type, source_type, source_id,
                content, growth_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                comment_id,
                reply_id,
                actor,
                event_type,
                source_type,
                source_id,
                content or "",
                status,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM memory_input_events WHERE source_type=? AND source_id=? AND event_type=?",
            (source_type, source_id, event_type),
        ).fetchone()
        conn.commit()
        return int(row["id"]) if row else None
    finally:
        conn.close()


def _append_growth_job_id(existing: str, job_id: int) -> str:
    try:
        ids = json.loads(existing or "[]")
        if not isinstance(ids, list):
            ids = []
    except Exception:
        ids = []
    if job_id not in ids:
        ids.append(job_id)
    return _json(ids)


def _enqueue_memory_growth_job(comment_id: int, trigger_reason: str = "agent_reply",
                               event_id: Optional[int] = None) -> Optional[int]:
    """Queue durable growth processing.

    P1.2.1: event_id gives each user expression its own growth lifecycle. Without
    event_id, this keeps the old comment-level dedupe behavior for compatibility.
    """
    if not comment_id:
        return None
    _record_intake_local_saved(comment_id)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        event = None
        if event_id:
            event = conn.execute(
                "SELECT growth_status, growth_job_ids FROM memory_input_events WHERE id=? AND comment_id=?",
                (event_id, comment_id),
            ).fetchone()
            if not event:
                return None
            if event["growth_status"] in ("enqueued", "running", "done"):
                return None

        ledger = conn.execute(
            "SELECT growth_status, growth_job_ids FROM memory_intake_ledger WHERE comment_id=?",
            (comment_id,),
        ).fetchone()
        if not event_id and ledger and ledger["growth_status"] in ("enqueued", "running", "done"):
            return None
        now = _now_iso()
        payload = {
            "comment_id": comment_id,
            "trigger_reason": trigger_reason,
        }
        if event_id:
            payload["event_id"] = event_id
        cur = conn.execute(
            "INSERT INTO jobs (kind, payload_json, status, max_attempts, created_at) "
            "VALUES ('memory_growth_for_comment', ?, 'queued', 3, ?)",
            (_json(payload), now),
        )
        job_id = cur.lastrowid
        if event_id:
            conn.execute(
                """UPDATE memory_input_events
                   SET growth_status='enqueued',
                       growth_job_ids=?,
                       growth_enqueued_at=?,
                       updated_at=?
                   WHERE id=?""",
                (_append_growth_job_id(event["growth_job_ids"] if event else "[]", job_id), now, now, event_id),
            )
        conn.execute(
            """UPDATE memory_intake_ledger
               SET growth_status='enqueued',
                   growth_job_ids=?,
                   growth_enqueued_at=?,
                   updated_at=?
               WHERE comment_id=?""",
            (_append_growth_job_id(ledger["growth_job_ids"] if ledger else "[]", job_id), now, now, comment_id),
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""


_CJK_STOP_TOKENS = {
    "我", "我们", "这个", "那个", "之前", "以前", "现在", "什么", "怎么", "如何",
    "是不是", "有没有", "一个", "一下", "可以", "觉得", "帮我", "记忆", "批注",
}


def _keyword_tokens(text: str, max_tokens: int = 18) -> list:
    text = text or ""
    lowered = text.lower()
    tokens = []
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", lowered):
        tokens.append(word)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if run in _CJK_STOP_TOKENS:
            continue
        if 2 <= len(run) <= 5:
            tokens.append(run)
        else:
            for size in (2, 3):
                for i in range(0, max(0, len(run) - size + 1)):
                    part = run[i:i + size]
                    if part not in _CJK_STOP_TOKENS:
                        tokens.append(part)
    out = []
    seen = set()
    for t in tokens:
        t = t.strip().lower()
        if not t or t in seen or t in _CJK_STOP_TOKENS:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tokens:
            break
    return out


def _latest_agent_reply(conn: sqlite3.Connection, comment_id: int) -> str:
    row = conn.execute(
        "SELECT content FROM replies WHERE comment_id=? AND author='agent' ORDER BY created_at DESC LIMIT 1",
        (comment_id,),
    ).fetchone()
    return row[0] if row else ""


def _comment_ref_line(row: dict, reply: str = "") -> str:
    cid = row.get("id")
    date = (row.get("created_at") or "")[:10]
    title = (row.get("page_title") or "未命名")[:40]
    comment = (row.get("comment") or "").replace("\n", " ")[:180]
    selected = (row.get("selected_text") or "").replace("\n", " ")[:120]
    line = f"- [c#{cid}] {date} 《{title}》：{comment}"
    if selected:
        line += f"\n  划线：{selected}"
    if reply:
        line += f"\n  当时回复：{reply.replace(chr(10), ' ')[:160]}"
    return line


def _select_working_skills(conn: sqlite3.Connection, query_text: str, limit: int = 3) -> tuple:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, description, triggers, created_at FROM working_skills "
        "WHERE status='active' ORDER BY id DESC"
    ).fetchall()]
    if not rows:
        return [], "no_active_skills"
    tokens = _keyword_tokens(query_text)
    scored = []
    for row in rows:
        hay = f"{row.get('name','')} {row.get('description','')} {row.get('triggers') or ''}".lower()
        score = sum(1 for t in tokens if t and t.lower() in hay)
        if score:
            scored.append((score, row["id"], row))
    if scored:
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [r for _, _, r in scored[:limit]], f"keyword_match:{','.join(tokens[:6])}"
    return rows[:limit], "fallback_latest_active_skills"


def _select_comment_memory(conn: sqlite3.Connection, query_text: str, page_url: str = "",
                           exclude_comment_id: int = None, limit: int = 5,
                           same_host_only: bool = False) -> tuple:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, page_url, page_title, selected_text, comment, created_at "
        "FROM comments ORDER BY created_at DESC LIMIT 300"
    ).fetchall()]
    host = _host(page_url)
    tokens = _keyword_tokens(query_text, max_tokens=24)
    scored = []
    for idx, row in enumerate(rows):
        if exclude_comment_id and row["id"] == exclude_comment_id:
            continue
        row_host = _host(row.get("page_url") or "")
        if same_host_only and host and row_host != host and row.get("page_url") != page_url:
            continue
        hay = f"{row.get('page_title','')} {row.get('selected_text','')} {row.get('comment','')}".lower()
        score = 0
        if page_url and row.get("page_url") == page_url:
            score += 8
        if host and row_host == host:
            score += 4
        score += sum(1 for t in tokens if t.lower() in hay)
        if score > 0:
            scored.append((score, -idx, row))
    if not scored and not same_host_only:
        scored = [(0, -idx, r) for idx, r in enumerate(rows) if not exclude_comment_id or r["id"] != exclude_comment_id]
    scored.sort(key=lambda x: (-x[0], -x[1]))
    selected = [r for _, _, r in scored[:limit]]
    reason = "url_host_keyword" if page_url else "query_keyword"
    if selected and scored[0][0] == 0:
        reason = "fallback_recent"
    return selected, reason


def _build_context_pack_for_comment(comment_id: int, role: str = "") -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        current = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not current:
            return {}
        current = dict(current)
        query_text = " ".join([
            current.get("page_title") or "",
            current.get("selected_text") or "",
            current.get("surrounding_text") or "",
            current.get("comment") or "",
            role or "",
        ])
        profile = conn.execute(
            "SELECT * FROM profile_snapshots WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        profile = dict(profile) if profile else None
        skills, skill_reason = _select_working_skills(conn, query_text, limit=3)
        same_page = [dict(r) for r in conn.execute(
            "SELECT id, page_url, page_title, selected_text, comment, created_at "
            "FROM comments WHERE page_url=? AND id<>? ORDER BY created_at DESC LIMIT 3",
            (current.get("page_url") or "", comment_id),
        ).fetchall()]
        episodic, episodic_reason = _select_comment_memory(
            conn, query_text, page_url=current.get("page_url") or "",
            exclude_comment_id=comment_id, limit=5, same_host_only=True,
        )
        latest_thinking = conn.execute(
            "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
            "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_thinking = dict(latest_thinking) if latest_thinking else None

        sections = ["## 本次回复装载的记忆上下文"]
        if profile:
            sections.append(
                "### B · 此刻的你\n"
                f"{profile.get('one_liner') or ''}\n"
                f"- 身份：{profile.get('field_who') or '—'}\n"
                f"- 正在做：{profile.get('field_doing') or '—'}\n"
                f"- 当前关注：{profile.get('field_focus') or '—'}\n"
                f"- 阶段：{profile.get('field_phase') or '—'}"
            )
        else:
            sections.append("### B · 此刻的你\n暂无 active profile snapshot。")
        if latest_thinking:
            sections.append(
                "### 最近你在想的事\n"
                f"{latest_thinking.get('title') or ''}：{latest_thinking.get('synthesis_md') or ''}"
            )
        if same_page:
            sections.append("### D · 当前页面历史批注\n" + "\n".join(_comment_ref_line(r) for r in same_page))
        else:
            sections.append("### D · 当前页面历史批注\n这页还没有其他历史批注。")
        if episodic:
            lines = []
            for r in episodic:
                lines.append(_comment_ref_line(r, _latest_agent_reply(conn, r["id"])))
            sections.append("### A' · 相关历史批注\n" + "\n".join(lines))
        else:
            sections.append("### A' · 相关历史批注\n没有命中同 URL/同 host 的历史批注。")
        if skills:
            sections.append("### C · 已养成的工作方式\n" + "\n".join(
                f"- [skill#{s['id']}] {s.get('name')}: {s.get('description')}" for s in skills
            ))
        else:
            sections.append("### C · 已养成的工作方式\n暂无 active working skills。")
        context_md = "\n\n".join(sections)
        return {
            "comment_id": comment_id,
            "identity_snapshot_id": profile.get("id") if profile else None,
            "selected_skill_ids": [s["id"] for s in skills],
            "episodic_comment_ids": [r["id"] for r in episodic],
            "same_page_comment_ids": [r["id"] for r in same_page],
            "current_page_url": current.get("page_url") or "",
            "selection_reasons": {
                "identity": "active_profile_snapshot" if profile else "missing",
                "skills": skill_reason,
                "episodic": episodic_reason,
                "same_page": "same_url_latest_3",
                "thinking": "active_thinking_summary" if latest_thinking else "missing",
            },
            "token_budget_used": max(1, len(context_md) // 4),
            "context_md": context_md,
        }
    finally:
        conn.close()


def _build_context_pack_for_query(query: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        profile = conn.execute(
            "SELECT * FROM profile_snapshots WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        profile = dict(profile) if profile else None
        skills, skill_reason = _select_working_skills(conn, query, limit=4)
        episodic, episodic_reason = _select_comment_memory(
            conn, query, page_url="", exclude_comment_id=None, limit=12, same_host_only=False,
        )
        latest_thinking = conn.execute(
            "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
            "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_thinking = dict(latest_thinking) if latest_thinking else None
        sections = ["## 记忆问答装载的上下文"]
        if profile:
            sections.append(
                "### 此刻的你\n"
                f"{profile.get('one_liner') or ''}\n"
                f"- 身份：{profile.get('field_who') or '—'}\n"
                f"- 正在做：{profile.get('field_doing') or '—'}\n"
                f"- 当前关注：{profile.get('field_focus') or '—'}"
            )
        if latest_thinking:
            sections.append(f"### 最近你在想的事\n{latest_thinking.get('title')}: {latest_thinking.get('synthesis_md')}")
        if skills:
            sections.append("### 工作方式\n" + "\n".join(
                f"- [skill#{s['id']}] {s.get('name')}: {s.get('description')}" for s in skills
            ))
        if episodic:
            lines = []
            for r in episodic:
                lines.append(_comment_ref_line(r, _latest_agent_reply(conn, r["id"])))
            sections.append("### 相关历史批注\n" + "\n".join(lines))
        else:
            sections.append("### 相关历史批注\n没有找到明显相关批注。")
        context_md = "\n\n".join(sections)
        return {
            "comment_id": None,
            "identity_snapshot_id": profile.get("id") if profile else None,
            "selected_skill_ids": [s["id"] for s in skills],
            "episodic_comment_ids": [r["id"] for r in episodic],
            "same_page_comment_ids": [],
            "current_page_url": "",
            "selection_reasons": {
                "identity": "active_profile_snapshot" if profile else "missing",
                "skills": skill_reason,
                "episodic": episodic_reason,
                "mode": "memory_chat_v0",
            },
            "token_budget_used": max(1, len(context_md) // 4),
            "context_md": context_md,
        }
    finally:
        conn.close()


def _attach_context_to_prompt(comment_id: int, role: str, prompt: str) -> tuple:
    pack = _build_context_pack_for_comment(comment_id, role)
    if not pack:
        return prompt, None
    return prompt + "\n\n---\n\n" + pack["context_md"], pack


def _insert_context_pack(conn: sqlite3.Connection, pack: dict, reply_id: int = None,
                         source_type: str = "reply", chat_message_id: int = None,
                         query_text: str = "") -> Optional[int]:
    if not pack:
        return None
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO context_packs
           (reply_id, comment_id, chat_message_id, source_type, identity_snapshot_id,
            selected_skill_ids, episodic_comment_ids, same_page_comment_ids,
            current_page_url, selection_reasons, token_budget_used, query_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            reply_id,
            pack.get("comment_id"),
            chat_message_id,
            source_type,
            pack.get("identity_snapshot_id"),
            _json(pack.get("selected_skill_ids") or []),
            _json(pack.get("episodic_comment_ids") or []),
            _json(pack.get("same_page_comment_ids") or []),
            pack.get("current_page_url") or "",
            _json(pack.get("selection_reasons") or {}),
            pack.get("token_budget_used"),
            query_text or "",
            now,
        ),
    )
    return cur.lastrowid

# ──────────────────────────────────────────
# v2: @手动路由兼容 + 映射
# ──────────────────────────────────────────

# v1 @语法 → v2 role 映射
V1_TO_V2_ROLE = {
    "竞品": ("task", "researcher"),
    "调研": ("task", "researcher"),
    "思辨": ("dialogue", "sparring_partner"),
    "解释": ("dialogue", "explainer"),
}
DEFAULT_AGENT = "思辨"

def parse_agent_type(comment: str) -> tuple:
    """从评论里解析 @agent 类型，返回 (agent_type_v1, cleaned_comment)
    agent_type_v1 为 None 时表示没有 @语法，应走 v2 路由器"""
    pattern = r'@(竞品|思辨|调研|解释)'
    match = re.search(pattern, comment)
    if match:
        agent_type = match.group(1)
        cleaned = re.sub(pattern, '', comment).strip()
        return agent_type, cleaned
    return None, comment

# ──────────────────────────────────────────
# v2: 路由器（Step 1）
# ──────────────────────────────────────────

def _get_child_env():
    env = os.environ.copy()
    env.setdefault("HOME", os.path.expanduser("~"))
    # 确保 PATH 包含 node 和 claude 所在目录，防止从缺少完整 PATH 的环境启动时找不到
    path = env.get("PATH", "")
    for extra in ["/usr/local/bin", os.path.expanduser("~/.npm-global/bin"), "/opt/homebrew/bin"]:
        if extra not in path:
            path = extra + ":" + path
    env["PATH"] = path
    return env

def _call_claude(prompt: str, system_prompt: str, timeout: int = 1800) -> tuple:
    """Legacy name kept for call-site stability. Calls configured LLM provider."""
    try:
        content = get_llm_client().generate_text(prompt, system_prompt=system_prompt, timeout=timeout)
        return content, 0
    except LLMTimeoutError as e:
        raise subprocess.TimeoutExpired(cmd="llm_provider", timeout=timeout) from e
    except LLMError as e:
        return str(e)[:1000], 1

def _parse_router_json(text: str) -> dict:
    """从路由器回复中提取 JSON，支持多层降级"""
    text = text.strip()
    # 去掉 markdown 代码块
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)
    text = text.strip()

    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取最大 {...}
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 正则兜底
    intent_m = re.search(r'"intent"\s*:\s*"(task|dialogue)"', text)
    role_m = re.search(r'"role"\s*:\s*"(researcher|sparring_partner|explainer)"', text)
    if intent_m and role_m:
        return {
            "intent": intent_m.group(1), "role": role_m.group(1),
            "confidence": 0, "plan": "", "learned": [], "quick_response": "",
            "_fallback_parse": True
        }

    return None  # 完全无法解析


def _codex_router_should_use_heuristic() -> bool:
    """Codex CLI has high process startup latency; avoid spending it on routing."""
    if os.environ.get("MEMAI_CODEX_USE_LLM_ROUTER") == "1":
        return False
    try:
        return get_llm_status().get("selected_provider") == "codex_cli"
    except Exception:
        return False


def _heuristic_router_result(comment: str, selected_text: str = "", page_title: str = "") -> dict:
    text = f"{comment or ''}\n{selected_text or ''}\n{page_title or ''}".lower()
    explain_words = [
        "解释", "什么意思", "是什么", "怎么理解", "why", "what is", "explain",
        "不懂", "看不懂", "讲一下",
    ]
    research_words = [
        "调研", "研究", "竞品", "搜索", "查一下", "找一下", "帮我找", "整理资料",
        "报告", "对比", "benchmark", "market", "competitor",
    ]
    task_words = [
        "帮我", "做一个", "写一份", "整理", "加入", "记录到", "关注", "以后",
        "todo", "日报", "雷达", "watchlist",
    ]
    if any(w in text for w in research_words):
        role = "researcher"
    elif any(w in text for w in explain_words):
        role = "explainer"
    else:
        role = "sparring_partner"
    intent = "task" if any(w in text for w in task_words + research_words) else "dialogue"
    return {
        "intent": intent,
        "role": role,
        "confidence": 0.35,
        "plan": "",
        "learned": [],
        "quick_response": "",
        "_heuristic_router": True,
    }

def run_router(page_url: str, page_title: str, selected_text: str,
               surrounding_text: str, comment: str, last_ai_reply: str = "") -> dict:
    """调用路由器 prompt（Step 1），返回解析后的 JSON dict"""
    if _codex_router_should_use_heuristic():
        result = _heuristic_router_result(comment, selected_text, page_title)
        print(f"[agent_api] router heuristic: intent={result.get('intent')} role={result.get('role')} provider=codex_cli")
        return result

    template = load_prompt_template("router")
    if not template:
        return None  # 路由器文件缺失，走 v1 fallback

    prompt = template.replace("{user_profile}", load_user_profile())
    prompt = prompt.replace("{project_context}", load_project_context())
    prompt = prompt.replace("{learned_rules}", load_learned_rules())
    prompt = prompt.replace("{last_ai_reply}", last_ai_reply or "")
    prompt = prompt.replace("{page_url}", page_url or "")
    prompt = prompt.replace("{page_title}", page_title or "")
    prompt = prompt.replace("{surrounding_context}", surrounding_text or "")
    prompt = prompt.replace("{selected_text}", selected_text or "")
    prompt = prompt.replace("{comment}", comment or "")

    system_prompt = "你是意图路由器。只输出 JSON，不要任何其他文字、解释或 markdown 代码块。"

    try:
        content, rc = _call_claude(prompt, system_prompt, timeout=30)
        if rc != 0:
            print(f"[agent_api] router error: rc={rc} content={content[:200]}")
            return None
        result = _parse_router_json(content)
        if result:
            print(f"[agent_api] router: intent={result.get('intent')} role={result.get('role')} confidence={result.get('confidence')}")
        return result
    except subprocess.TimeoutExpired:
        print("[agent_api] router timeout (30s)")
        return None
    except Exception as e:
        print(f"[agent_api] router exception: {e}")
        return None

# ──────────────────────────────────────────
# v2: 角色 Prompt 构建（Step 2）
# ──────────────────────────────────────────

def build_role_prompt(role: str, page_url: str, page_title: str,
                      selected_text: str, surrounding_text: str,
                      comment: str, plan: str = "") -> str:
    """根据 role 构建 Step 2 执行 prompt"""
    template = load_prompt_template(role)
    if not template:
        # fallback: 用 sparring_partner 模板
        template = load_prompt_template("sparring_partner") or ""

    attention_memory = fetch_attention_memory(15)

    prompt = template.replace("{agent_principles}", load_agent_principles())
    prompt = prompt.replace("{user_profile}", load_user_profile())
    prompt = prompt.replace("{project_context}", load_project_context())
    prompt = prompt.replace("{learned_rules_scoped}", load_learned_rules_scoped(role))
    prompt = prompt.replace("{notion_memory}", attention_memory)
    prompt = prompt.replace("{page_url}", page_url or "")
    prompt = prompt.replace("{surrounding_context}", surrounding_text or "")
    prompt = prompt.replace("{selected_text}", selected_text or "")
    prompt = prompt.replace("{comment}", comment or "")
    prompt = prompt.replace("{plan}", plan or "")
    return prompt

# ──────────────────────────────────────────
# v2: 学习信号写入
# ──────────────────────────────────────────

def save_learned_rules(new_rules: list, role: str = "all"):
    """把路由器提取的学习信号写入 learned_rules.json"""
    if not new_rules:
        return
    try:
        data = _load_learned_rules_data()
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        rules = data.get("rules", [])

        # 备份当前版本
        bak_path = LEARNED_RULES_PATH + ".bak"
        with open(bak_path, 'w', encoding='utf-8') as f:
            f.write(raw)

        # 添加新规则
        now = datetime.now().strftime("%Y-%m-%d")
        max_id = max((int(r.get("id", "rule_0").split("_")[1]) for r in rules), default=0)
        for i, rule_text in enumerate(new_rules):
            if not rule_text or not rule_text.strip():
                continue
            max_id += 1
            rules.append({
                "id": f"rule_{max_id:03d}",
                "rule": rule_text.strip(),
                "scope": f"role:{role}" if role != "all" else "all",
                "source": f"自动提取 {now}",
                "created_at": now,
                "last_used_at": now,
                "active": True
            })

        # 活跃规则上限 20 条
        active_rules = [r for r in rules if r.get("active", True)]
        if len(active_rules) > 20:
            # 按 last_used_at 排序，归档最旧的
            active_rules.sort(key=lambda r: r.get("last_used_at", ""))
            for r in active_rules[:len(active_rules) - 20]:
                r["active"] = False
            print(f"[agent_api] learned_rules: 归档 {len(active_rules) - 20} 条旧规则")

        data["rules"] = rules
        with open(LEARNED_RULES_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        active_count = len([r for r in rules if r.get('active', True)])
        print(f"[agent_api] learned_rules: 新增 {len(new_rules)} 条，总计 {active_count} 条活跃")

        # M3.0 范围 B：累计 ≥5 条新 rule 自动触发蒸馏
        try:
            _maybe_enqueue_skills_distillation(active_count)
        except Exception as e:
            print(f"[agent_api] enqueue_skills_distillation error: {e}")
    except Exception as e:
        print(f"[agent_api] save_learned_rules error: {e}")


def _now_iso() -> str:
    return datetime.now().isoformat()


_SKILLS_AUTO_TRIGGER_THRESHOLD = 5  # 累计新增 ≥5 条 active rule 触发自动蒸馏


def _last_skills_generation_max_rule_id() -> int:
    """最近一次 skills generation 蒸馏时存的 max_rule_id。无则返回 0。

    比 source_rules_count 更可靠——active rules 受 20 上限影响，count 会卡住，
    但 rule_NNN 的数字 id 是单调递增的，能正确判断"自上次蒸馏后新增了几条"。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT trigger_payload FROM skill_generations WHERE kind='skills' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or not row[0]:
            return 0
        try:
            payload = json.loads(row[0])
            return int(payload.get("max_rule_id_at_distill", 0))
        except Exception:
            return 0
    finally:
        conn.close()


def _current_max_rule_id() -> int:
    """读 learned_rules.json，返回最大 rule id 数值（从 rule_NNN 解析）"""
    data = _load_learned_rules_data()
    max_id = 0
    for r in data.get("rules", []):
        rid = r.get("id", "rule_0")
        try:
            n = int(rid.split("_")[1])
            if n > max_id:
                max_id = n
        except Exception:
            continue
    return max_id


def _has_pending_skills_job() -> bool:
    """是否已有 queued/running 的 synthesize_skills job（避免重复 enqueue）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE kind='synthesize_skills' AND status IN ('queued','running') LIMIT 1"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _maybe_enqueue_skills_distillation(active_rule_count: int):
    """阈值命中时 enqueue 一个 synthesize_skills job。

    判定逻辑：当前最大 rule_id - 上次蒸馏时的 max_rule_id ≥ threshold。
    用 rule_id 而不是 active count 是因为 active_rules cap=20，count 会卡住。
    """
    current_max_id = _current_max_rule_id()
    last_max_id = _last_skills_generation_max_rule_id()
    delta = current_max_id - last_max_id
    if delta < _SKILLS_AUTO_TRIGGER_THRESHOLD:
        return
    if _has_pending_skills_job():
        print(f"[agent_api] skills distillation: 阈值已命中（+{delta}）但已有 pending job，跳过")
        return

    payload = {
        "trigger_reason": "rule_count_threshold",
        "max_rule_id_at_distill": current_max_id,  # 关键字段：下次判定基准
        "last_max_rule_id": last_max_id,
        "active_rule_count": active_rule_count,
        "delta": delta,
        "threshold": _SKILLS_AUTO_TRIGGER_THRESHOLD,
    }
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO jobs (kind, payload_json, status, max_attempts, created_at) "
            "VALUES ('synthesize_skills', ?, 'queued', 3, ?)",
            (json.dumps(payload, ensure_ascii=False), _now_iso()),
        )
        conn.commit()
        print(f"[agent_api] enqueued synthesize_skills job (delta={delta} since last gen)")
    finally:
        conn.close()

# ──────────────────────────────────────────
# v2: 学习层 — 画像 & 项目上下文自动维护
# ──────────────────────────────────────────

_PROFILE_REVIEW_INTERVAL = 20   # 每 20 次交互触发画像审视
_COLD_START_THRESHOLD = 5       # 前 5 条评论后触发冷启动

def _get_interaction_count() -> int:
    """从 DB 查询总交互数（有 agent 回复的评论数）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        count = conn.execute(
            "SELECT COUNT(DISTINCT comment_id) FROM replies WHERE author = 'agent'"
        ).fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0

def _get_recent_interactions(limit: int = 20) -> str:
    """拉最近 N 条交互摘要，用于画像/上下文审视"""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT c.selected_text, c.comment, r.content, c.page_title, c.agent_type
            FROM comments c
            JOIN replies r ON r.comment_id = c.id AND r.author = 'agent'
            ORDER BY r.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        if not rows:
            return ""
        summaries = []
        for selected, comment, reply, title, role in rows:
            summaries.append(
                f"- 页面「{title or '?'}」，角色={role}\n"
                f"  划线：{(selected or '')[:80]}\n"
                f"  评论：{(comment or '')[:100]}\n"
                f"  AI回复：{(reply or '')[:150]}"
            )
        return "\n".join(summaries)
    except Exception as e:
        print(f"[learning] _get_recent_interactions error: {e}")
        return ""

def _review_user_profile(interaction_count: int):
    """异步审视用户画像，有变化则更新 user_profile.md"""
    try:
        current_profile = load_user_profile()
        recent = _get_recent_interactions(20)
        if not recent:
            return

        is_cold_start = interaction_count <= _COLD_START_THRESHOLD
        is_empty = "[空白，待 AI 填充]" in current_profile or not current_profile.strip()

        if is_cold_start and is_empty:
            task = "冷启动"
            instruction = (
                "这是新用户的前几条交互。请根据评论内容推断用户画像，填充以下模板的每个字段。\n"
                "注意：只根据实际交互内容推断，不确定的写「待观察」。\n"
            )
        else:
            task = "定期审视"
            instruction = (
                "请对比现有画像和最近 20 条交互，判断画像是否需要更新。\n"
                "- 如果没有实质变化：直接输出原文，不做任何修改\n"
                "- 如果有变化：更新相关字段，保持格式不变\n"
                "注意：只更新有证据支持的字段，不要猜测。\n"
            )

        prompt = f"""你是知识库助手的学习系统。任务：{task}

{instruction}

## 当前画像
{current_profile or '（空白，首次填充）'}

## 画像模板格式（必须严格保持）
## 角色
[描述用户的职业/身份]

## 知识水平
[描述用户的技术/领域知识程度]

## 思维偏好
[描述用户的思考风格、关注什么]

## 当前关注领域
[描述用户最近在研究什么]

## 最近 {len(recent.split(chr(10)))} 条交互摘要
{recent}

直接输出更新后的 markdown 内容，不要任何额外解释或代码块包裹。"""

        system_prompt = "你是用户画像维护系统。只输出 markdown 格式的画像内容，不要任何其他文字。"

        content, rc = _call_claude(prompt, system_prompt, timeout=60)
        if rc != 0 or not content.strip():
            print(f"[learning] profile review failed: rc={rc}")
            return

        # 清理可能的 markdown 代码块包裹
        content = content.strip()
        content = re.sub(r'^```(?:markdown)?\s*', '', content)
        content = re.sub(r'\s*```\s*$', '', content)
        content = content.strip()

        # 只有内容真的变了才写入
        if content != current_profile.strip():
            with open(USER_PROFILE_PATH, 'w', encoding='utf-8') as f:
                f.write(content + "\n")
            print(f"[learning] user_profile.md 已更新 ({task})")
        else:
            print(f"[learning] user_profile.md 无变化")

    except Exception as e:
        print(f"[learning] _review_user_profile error: {e}")

def _review_project_context(agent_reply: str, user_comment: str):
    """检测 agent 回复中是否有项目状态变化信号，有则更新 project_context.md"""
    try:
        current_context = load_project_context()
        if not current_context.strip():
            return  # 没有现有上下文，不处理

        # 先快速判断：用户评论中是否有项目状态变化的信号词
        change_signals = [
            "上线了", "已部署", "已发布", "方向改了", "不做了", "决定",
            "已经", "改成", "换成", "砍掉", "新增了", "完成了",
            "launched", "shipped", "deployed", "pivoted", "decided"
        ]
        has_signal = any(s in user_comment for s in change_signals)
        if not has_signal:
            return

        prompt = f"""你是知识库助手的项目上下文维护系统。

用户刚才的评论中可能包含项目状态变化的信号。请对比现有上下文和用户评论，判断是否需要更新。

## 现有项目上下文
{current_context}

## 用户评论
{user_comment}

## 规则
1. 只有"项目方向/阶段/假设/技术栈/竞品格局"等核心状态真的变了才更新
2. 日常评论（提问、讨论、调研请求）不算状态变化
3. 如果需要更新：输出完整的更新后 markdown（保持所有原有结构）
4. 如果不需要更新：只输出 NO_CHANGE

直接输出结果，不要任何额外解释。"""

        system_prompt = "你是项目上下文维护系统。判断是否需要更新，输出结果。"

        content, rc = _call_claude(prompt, system_prompt, timeout=60)
        if rc != 0 or not content.strip():
            return

        content = content.strip()
        if content == "NO_CHANGE" or "NO_CHANGE" in content[:50]:
            print(f"[learning] project_context.md 无需更新")
            return

        # 清理 markdown 包裹
        content = re.sub(r'^```(?:markdown)?\s*', '', content)
        content = re.sub(r'\s*```\s*$', '', content)
        content = content.strip()

        if content and content != current_context.strip() and len(content) > 100:
            # 备份
            bak_path = PROJECT_CONTEXT_PATH + ".bak"
            with open(bak_path, 'w', encoding='utf-8') as f:
                f.write(current_context)
            # 写入
            with open(PROJECT_CONTEXT_PATH, 'w', encoding='utf-8') as f:
                f.write(content + "\n")
            print(f"[learning] project_context.md 已更新")
        else:
            print(f"[learning] project_context.md 内容无实质变化")

    except Exception as e:
        print(f"[learning] _review_project_context error: {e}")

def _trigger_learning_layer(comment_id: int, user_comment: str, agent_reply: str):
    """Agent 成功回复后，检查是否需要触发学习层更新"""
    try:
        count = _get_interaction_count()
        print(f"[learning] interaction_count={count}")

        # 冷启动：第 5 条时触发首次画像推断
        if count == _COLD_START_THRESHOLD:
            print(f"[learning] 触发冷启动画像推断 (count={count})")
            thread = threading.Thread(
                target=_review_user_profile, args=(count,), daemon=True)
            thread.start()

        # 定期审视：每 20 条触发
        elif count > 0 and count % _PROFILE_REVIEW_INTERVAL == 0:
            print(f"[learning] 触发定期画像审视 (count={count})")
            thread = threading.Thread(
                target=_review_user_profile, args=(count,), daemon=True)
            thread.start()

        # 项目上下文：每次都检查信号词（快速，不调 API）
        # 只有命中信号词才会真正调 API
        thread = threading.Thread(
            target=_review_project_context, args=(agent_reply, user_comment), daemon=True)
        thread.start()

    except Exception as e:
        print(f"[learning] _trigger_learning_layer error: {e}")

# ──────────────────────────────────────────
# v2: 后台 Agent 调用
# ──────────────────────────────────────────

def run_agent_v2(comment_id: int, intent: str, role: str, prompt: str,
                 plan: str = "", quick_response: str = "", learned: list = None,
                 user_comment: str = "", context_pack: dict = None):
    """后台线程：v2 agent 执行，结果写回 replies 表"""
    import time
    start_time = time.time()
    status = "error"
    content = ""

    # 如果有 quick_response，直接用，不调 Step 2
    if quick_response and quick_response.strip():
        content = quick_response.strip()
        status = "success"
        print(f"[agent_api] v2 quick_response for comment_id={comment_id} role={role}")
    else:
        # 需要调 Step 2
        print(f"[agent_api] v2 Step 2: comment_id={comment_id} role={role} prompt_len={len(prompt)}")
        system_prompt = (
            "你是知识库助手的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程"
            "（不要同步 Notion、不要读 todo、不要确认 session 阶段）。只根据下面的 prompt 内容回复。"
            "默认写成简洁的评论区回复：先给结论，少铺垫；除非用户明确要求深度调研或长文，"
            "否则控制在 300-500 字以内，最多 5 个要点。不要为了显得全面而展开所有分支。"
        )
        try:
            content, rc = _call_claude(prompt, system_prompt, timeout=1800)
            if rc == 0 and content:
                status = "success"
            else:
                content = f"Agent 执行出错（returncode={rc}）：{content[:500]}"
        except subprocess.TimeoutExpired:
            content = "Agent 超时（30分钟），请重试或拆解任务。"
        except Exception as e:
            content = f"Agent 调用失败：{str(e)}"

    elapsed = round(time.time() - start_time, 1)
    print(f"[agent_api] v2 完成 comment_id={comment_id} status={status} elapsed={elapsed}s role={role}")

    # 学习信号写入
    if learned:
        save_learned_rules(learned, role)

    # 判断是否为 plan 回复（researcher 规划阶段的产出）
    is_plan_response = (intent == "task" and not plan and status == "success"
                        and ("确认" in content or "执行」" in content))

    # 构建 debug_meta
    debug_meta = json.dumps({
        "version": "v2",
        "intent": intent,
        "role": role,
        "elapsed_s": elapsed,
        "prompt_tokens_est": len(prompt) // 4,
        "reply_tokens_est": len(content) // 4,
        "is_quick": bool(quick_response and quick_response.strip()),
        "is_plan": is_plan_response,
        "context_refs": {
            "identity_snapshot_id": context_pack.get("identity_snapshot_id") if context_pack else None,
            "selected_skill_ids": context_pack.get("selected_skill_ids") if context_pack else [],
            "episodic_comment_ids": context_pack.get("episodic_comment_ids") if context_pack else [],
            "same_page_comment_ids": context_pack.get("same_page_comment_ids") if context_pack else [],
        },
        "rules_applied": [r["rule"] for r in json.loads(load_learned_rules()).get("rules", [])
                          if r.get("active") and r.get("scope") in ("all", f"role:{role}")][:5],
        "status": status
    }, ensure_ascii=False)

    # 写回数据库
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, "agent", role, content, now, debug_meta)
    )
    reply_id = cur.lastrowid
    try:
        context_pack_id = _insert_context_pack(conn, context_pack, reply_id=reply_id, source_type="reply")
        if context_pack_id:
            meta = json.loads(debug_meta)
            meta["context_pack_id"] = context_pack_id
            conn.execute(
                "UPDATE replies SET debug_meta=? WHERE id=?",
                (_json(meta), reply_id),
            )
    except Exception as e:
        log_failure(comment_id, "context_pack_persist", e)
    conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
    conn.commit()
    conn.close()
    _record_memory_event(
        comment_id,
        actor="agent",
        event_type="agent_reply_added",
        content=content,
        source_type="reply",
        source_id=reply_id,
        reply_id=reply_id,
        growth_status="skipped",
    )

    # 学习层：成功时触发画像/上下文审视。Memory Growth 由用户表达事件触发，
    # agent reply 只作为后续上下文证据，不默认写成用户记忆。
    if status == "success":
        _trigger_learning_layer(comment_id, user_comment, content)

# v1 兼容：保留旧 run_agent 作为 fallback
def run_agent_v1_fallback(comment_id: int, agent_type: str,
                          page_url: str, selected_text: str,
                          surrounding_text: str, comment: str):
    """v1 fallback：用旧的硬编码 prompt 直接调用"""
    import time
    start_time = time.time()
    # 用 v2 的角色 prompt 作为 fallback（比旧硬编码更好）
    v1_role_map = {"竞品": "researcher", "调研": "researcher", "思辨": "sparring_partner", "解释": "explainer"}
    role = v1_role_map.get(agent_type, "sparring_partner")
    prompt = build_role_prompt(role, page_url, "", selected_text, surrounding_text, comment)
    prompt, context_pack = _attach_context_to_prompt(comment_id, role, prompt)

    system_prompt = (
        "你是知识库助手的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程。"
        "默认简洁：先给结论，控制在 300-500 字以内，最多 5 个要点。"
    )
    status = "error"
    content = ""
    try:
        content, rc = _call_claude(prompt, system_prompt, timeout=1800)
        status = "success" if rc == 0 and content else "error"
        if rc != 0:
            content = f"Agent 执行出错：{content[:500]}"
    except subprocess.TimeoutExpired:
        content = "Agent 超时（30分钟），请重试。"
    except Exception as e:
        content = f"Agent 调用失败：{str(e)}"

    elapsed = round(time.time() - start_time, 1)
    debug_meta = json.dumps({
        "version": "v1_fallback",
        "intent": "dialogue",
        "role": role,
        "elapsed_s": elapsed,
        "prompt_tokens_est": len(prompt) // 4,
        "reply_tokens_est": len(content) // 4,
        "is_quick": False,
        "is_plan": False,
        "context_refs": {
            "identity_snapshot_id": context_pack.get("identity_snapshot_id") if context_pack else None,
            "selected_skill_ids": context_pack.get("selected_skill_ids") if context_pack else [],
            "episodic_comment_ids": context_pack.get("episodic_comment_ids") if context_pack else [],
            "same_page_comment_ids": context_pack.get("same_page_comment_ids") if context_pack else [],
        },
        "rules_applied": [],
        "status": status
    }, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, "agent", agent_type, content, now, debug_meta)
    )
    reply_id = cur.lastrowid
    try:
        context_pack_id = _insert_context_pack(conn, context_pack, reply_id=reply_id, source_type="reply")
        if context_pack_id:
            meta = json.loads(debug_meta)
            meta["context_pack_id"] = context_pack_id
            conn.execute("UPDATE replies SET debug_meta=? WHERE id=?", (_json(meta), reply_id))
    except Exception as e:
        log_failure(comment_id, "context_pack_persist", e)
    conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
    conn.commit()
    conn.close()
    _record_memory_event(
        comment_id,
        actor="agent",
        event_type="agent_reply_added",
        content=content,
        source_type="reply",
        source_id=reply_id,
        reply_id=reply_id,
        growth_status="skipped",
    )

# ──────────────────────────────────────────
# Debug 日志
# ──────────────────────────────────────────

DEBUG_LOG_DIR = os.path.join(DATA_DIR, "debug-logs")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)

def write_debug_log(comment_id: int, agent_type: str, prompt: str, extra: dict = None):
    """把完整prompt写到debug-logs目录，方便排查"""
    try:
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{now}_id{comment_id}_{agent_type}.md"
        filepath = os.path.join(DEBUG_LOG_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# Debug Log: Comment #{comment_id}\n")
            f.write(f"**时间：** {now}\n")
            f.write(f"**Agent类型：** {agent_type}\n")
            f.write(f"**Prompt长度：** {len(prompt)} 字符 ≈ {len(prompt)//4} tokens\n")
            if extra:
                f.write(f"**路由结果：** {json.dumps(extra, ensure_ascii=False)}\n")
            f.write("\n---\n\n## 完整 Prompt\n\n")
            f.write(prompt)
        print(f"[agent_api] debug log: {filepath}")
    except Exception as e:
        print(f"[agent_api] debug log写入失败: {e}")

# ──────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────

class CommentCreate(BaseModel):
    page_url: str
    page_title: str = ""
    selected_text: str = ""
    surrounding_text: str = ""   # 划线前后各200字，解决指代不清
    page_content: str = ""       # 页面全文（首次提交时传，后端按URL去重缓存）
    comment: str
    no_agent: bool = False  # True 时仅存储，不触发 agent（用户手动召唤时再触发）

class ReplyCreate(BaseModel):
    content: str

class StatusUpdate(BaseModel):
    status: str  # open / resolved / tracking / archived

@app.post("/comments")
def create_comment(body: CommentCreate):
    """新建评论，v2 路由器自动判断 intent/role，保留 @手动路由兼容"""
    v1_agent_type, cleaned_comment = parse_agent_type(body.comment)
    cleaned_comment = (cleaned_comment or "").strip()
    if not cleaned_comment:
        raise HTTPException(status_code=400, detail="comment is required")
    now = datetime.now().isoformat()

    # 存储时 agent_type 先记录 v1 类型（兼容），后面会被 v2 覆盖
    agent_type_for_db = v1_agent_type or "v2_pending"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO comments (page_url, page_title, selected_text, surrounding_text, comment, agent_type, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (body.page_url, body.page_title, body.selected_text, body.surrounding_text or "",
         cleaned_comment, agent_type_for_db, now, now)
    )
    comment_id = cursor.lastrowid

    # 全文缓存：按URL去重
    if body.page_content and body.page_content.strip():
        try:
            conn.execute(
                """INSERT OR IGNORE INTO page_cache (page_url, page_title, full_text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (body.page_url, body.page_title, body.page_content, now, now)
            )
        except Exception:
            pass

    conn.commit()
    conn.close()
    _record_intake_local_saved(comment_id)
    event_id = _record_memory_event(
        comment_id,
        actor="user",
        event_type="comment_created",
        content=cleaned_comment,
        source_type="comment",
        source_id=comment_id,
    )
    _enqueue_memory_growth_job(comment_id, "comment_created", event_id=event_id)

    # no_agent=True 时仅存储
    if body.no_agent:
        return {"id": comment_id, "agent_type": agent_type_for_db, "status": "open",
                "message": "评论已存储，等待手动召唤 AI"}

    # ── 分发逻辑 ──
    def _dispatch():
        try:
            _dispatch_inner()
        except Exception as e:
            log_failure(comment_id, "dispatch_outer", e)
            write_system_reply(comment_id,
                f"召唤 AI 时出错了，请重试。\n\n"
                f"如果反复出现，把这条消息的时间戳（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）"
                f"和 comment_id={comment_id} 告诉 Claude，让它查 .logs/failures.jsonl 排查。\n\n"
                f"内部错误：{type(e).__name__}: {str(e)[:200]}",
                phase="dispatch_outer")

    def _dispatch_inner():
        selected = body.selected_text or "（无划线内容）"
        surrounding = body.surrounding_text or ""

        # 路径 1：有 @手动路由 → 映射到 v2 role，跳过路由器
        if v1_agent_type and v1_agent_type in V1_TO_V2_ROLE:
            intent, role = V1_TO_V2_ROLE[v1_agent_type]
            print(f"[agent_api] v1 @{v1_agent_type} → v2 intent={intent} role={role}")
            prompt = build_role_prompt(role, body.page_url, body.page_title,
                                      selected, surrounding, cleaned_comment)
            prompt, context_pack = _attach_context_to_prompt(comment_id, role, prompt)
            write_debug_log(comment_id, f"v1→{role}", prompt, {"v1_agent_type": v1_agent_type})
            # 更新 DB 的 agent_type
            _conn = sqlite3.connect(DB_PATH)
            _conn.execute("UPDATE comments SET agent_type = ? WHERE id = ?", (role, comment_id))
            _conn.commit()
            _conn.close()
            run_agent_v2(comment_id, intent, role, prompt, user_comment=cleaned_comment,
                         context_pack=context_pack)
            return

        # 路径 2：v2 路由器
        router_result = run_router(body.page_url, body.page_title,
                                   selected, surrounding, cleaned_comment)

        # 路由器失败 → v1 fallback
        if not router_result:
            print(f"[agent_api] router failed, falling back to v1 ({DEFAULT_AGENT})")
            run_agent_v1_fallback(comment_id, DEFAULT_AGENT, body.page_url,
                                 selected, surrounding, cleaned_comment)
            return

        intent = router_result.get("intent", "dialogue")
        role = router_result.get("role", "sparring_partner")
        quick_response = router_result.get("quick_response", "")
        learned = router_result.get("learned", [])

        # 更新 DB 的 agent_type 为 v2 role
        _conn = sqlite3.connect(DB_PATH)
        _conn.execute("UPDATE comments SET agent_type = ? WHERE id = ?", (role, comment_id))
        _conn.commit()
        _conn.close()

        # 构建 Step 2 prompt（task 时 plan 为空，researcher 会进入规划模式）
        prompt = build_role_prompt(role, body.page_url, body.page_title,
                                   selected, surrounding, cleaned_comment)
        prompt, context_pack = _attach_context_to_prompt(comment_id, role, prompt)
        write_debug_log(comment_id, f"v2_{role}", prompt, router_result)

        run_agent_v2(comment_id, intent, role, prompt,
                     quick_response=quick_response, learned=learned,
                     user_comment=cleaned_comment, context_pack=context_pack)

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()

    return {
        "id": comment_id,
        "agent_type": agent_type_for_db,
        "status": "open",
        "message": "评论已创建，AI 正在思考中..."
    }

@app.get("/comments")
def list_comments(page_url: str = None, status: str = None):
    """拉取评论列表，可按页面 URL 或状态过滤"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM comments WHERE 1=1"
    params = []
    if page_url:
        query += " AND page_url = ?"
        params.append(page_url)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC"

    comments = [dict(row) for row in conn.execute(query, params).fetchall()]

    for c in comments:
        replies = conn.execute(
            "SELECT * FROM replies WHERE comment_id = ? ORDER BY created_at ASC",
            (c["id"],)
        ).fetchall()
        c["replies"] = [dict(r) for r in replies]

    conn.close()
    return comments

@app.get("/comments/{comment_id}")
def get_comment(comment_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    comment = dict(row)
    replies = conn.execute(
        "SELECT * FROM replies WHERE comment_id = ? ORDER BY created_at ASC",
        (comment_id,)
    ).fetchall()
    comment["replies"] = [dict(r) for r in replies]
    conn.close()
    return comment

class CommentPatch(BaseModel):
    comment: str = None

@app.patch("/comments/{comment_id}")
def patch_comment(comment_id: int, body: CommentPatch):
    """更新评论内容（用于追问时把完整对话历史写入）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        exists = conn.execute("SELECT id FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Comment not found")
        if body.comment is not None:
            if not body.comment.strip():
                raise HTTPException(status_code=400, detail="comment is required")
            conn.execute(
                "UPDATE comments SET comment = ?, updated_at = ? WHERE id = ?",
                (body.comment, datetime.now().isoformat(), comment_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}

@app.post("/comments/{comment_id}/reply")
def add_reply(comment_id: int, body: ReplyCreate):
    """用户手动补充回复"""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    conn = sqlite3.connect(DB_PATH)
    try:
        exists = conn.execute("SELECT id FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Comment not found")
        now = datetime.now().isoformat()
        cur = conn.execute(
            "INSERT INTO replies (comment_id, author, content, created_at) VALUES (?, 'user', ?, ?)",
            (comment_id, content, now)
        )
        reply_id = cur.lastrowid
        conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
        conn.commit()
    finally:
        conn.close()
    event_id = _record_memory_event(
        comment_id,
        actor="user",
        event_type="user_followup",
        content=content,
        source_type="reply",
        source_id=reply_id,
        reply_id=reply_id,
    )
    job_id = _enqueue_memory_growth_job(comment_id, "user_followup", event_id=event_id)
    return {"ok": True, "reply_id": reply_id, "event_id": event_id, "growth_job_id": job_id}

@app.patch("/comments/{comment_id}/status")
def update_status(comment_id: int, body: StatusUpdate):
    """更新评论状态"""
    valid = {"open", "resolved", "tracking", "archived"}
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"status 必须是 {valid} 之一")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE comments SET status = ?, updated_at = ? WHERE id = ?",
        (body.status, datetime.now().isoformat(), comment_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/comments/{comment_id}/rerun")
def rerun_agent(comment_id: int):
    """重新触发 agent（v2 路由器重跑，plan 确认后直接执行 Step 2）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()

    # 获取上一轮 AI 回复 + debug_meta
    last_ai_reply = ""
    last_debug_meta = {}
    last_reply = conn.execute(
        "SELECT content, debug_meta FROM replies WHERE comment_id = ? AND author = 'agent' ORDER BY created_at DESC LIMIT 1",
        (comment_id,)
    ).fetchone()
    if last_reply:
        last_ai_reply = last_reply["content"][:500]
        try:
            last_debug_meta = json.loads(last_reply["debug_meta"] or "{}")
        except Exception:
            pass

    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")

    c = dict(row)

    def _rerun():
        try:
            _rerun_inner()
        except Exception as e:
            log_failure(comment_id, "rerun_outer", e)
            write_system_reply(comment_id,
                f"召唤 AI 时出错了，请重试。\n\n"
                f"如果反复出现，把这条消息的时间戳（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）"
                f"和 comment_id={comment_id} 告诉 Claude，让它查 .logs/failures.jsonl 排查。\n\n"
                f"内部错误：{type(e).__name__}: {str(e)[:200]}",
                phase="rerun_outer")

    def _rerun_inner():
        selected = c["selected_text"] or "（无划线内容）"
        surrounding = c.get("surrounding_text", "") or ""
        comment = c["comment"]

        # ── 快捷路径：上一轮是 plan，用户确认后直接执行 Step 2 ──
        if last_debug_meta.get("is_plan"):
            role = last_debug_meta.get("role", "researcher")
            # 上一轮 AI 回复就是 researcher 生成的完整 plan，直接传入
            plan_text = last_ai_reply
            print(f"[agent_api] rerun: plan confirmed, skip router → {role} with plan")
            prompt = build_role_prompt(role, c["page_url"], c.get("page_title", ""),
                                       selected, surrounding, comment,
                                       f"用户已确认以下方案，直接执行：\n\n{plan_text}")
            prompt, context_pack = _attach_context_to_prompt(comment_id, role, prompt)
            write_debug_log(comment_id, f"v2_plan_exec_{role}", prompt,
                           {"plan_confirmed": True, "plan": plan_text[:500]})
            run_agent_v2(comment_id, "task", role, prompt,
                         plan=f"用户已确认",
                         user_comment=comment, context_pack=context_pack)
            return

        # ── 正常路径：走 v2 路由器 ──
        router_result = run_router(c["page_url"], c.get("page_title", ""),
                                   selected, surrounding, comment, last_ai_reply)
        if not router_result:
            run_agent_v1_fallback(comment_id, DEFAULT_AGENT, c["page_url"],
                                 selected, surrounding, comment)
            return

        intent = router_result.get("intent", "dialogue")
        role = router_result.get("role", "sparring_partner")
        quick_response = router_result.get("quick_response", "")
        learned = router_result.get("learned", [])

        prompt = build_role_prompt(role, c["page_url"], c.get("page_title", ""),
                                   selected, surrounding, comment)
        prompt, context_pack = _attach_context_to_prompt(comment_id, role, prompt)
        write_debug_log(comment_id, f"v2_rerun_{role}", prompt, router_result)
        run_agent_v2(comment_id, intent, role, prompt,
                     quick_response=quick_response, learned=learned,
                     user_comment=comment, context_pack=context_pack)

    thread = threading.Thread(target=_rerun, daemon=True)
    thread.start()
    return {"message": f"已重新触发 AI（v2）"}

@app.get("/health")
def health():
    return {"status": "ok", "data_dir": DATA_DIR, "db": DB_PATH}

class ClientErrorReport(BaseModel):
    source: str = "unknown"       # callAIViaAgent / upsertNotionPage / saveToNotion ...
    message: str = ""
    stack: str = ""
    context: dict = {}             # 任意上下文：url / comment_id / agent_type / etc.
    ts: str = ""                   # 客户端时间戳，缺失由后端补

CLIENT_ERROR_LOG = os.environ.get("KB_CLIENT_ERROR_LOG") or os.path.join(LOG_DIR, "client_errors.log")

@app.post("/client-error")
def client_error(body: ClientErrorReport):
    """收集插件侧失败，追加到 .logs/client_errors.log。失败静默，不阻塞客户端。"""
    try:
        os.makedirs(os.path.dirname(CLIENT_ERROR_LOG), exist_ok=True)
        entry = {
            "ts_server": datetime.now().isoformat(),
            "ts_client": body.ts or "",
            "source": body.source,
            "message": body.message[:2000],
            "stack": body.stack[:4000],
            "context": body.context,
        }
        with open(CLIENT_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 诊断端点自身绝不报错
    return {"ok": True}

# ──────────────────────────────────────────
# Reliability：兜底失败可见 + 结构化失败日志
#
# 设计原则（详见 ~/mem-ai/docs/reliability_design.md）：
#   1. 任何用户提交的"召唤"必须在 UI 上呈现一个结果，不能"消失"
#   2. 状态变化必须可观测（写 DB + 写日志，双写）
#   3. 所有失败必须可见：兜底 handler 把错误变成用户可见的 reply
# ──────────────────────────────────────────

FAILURE_LOG = os.environ.get("KB_FAILURE_LOG") or os.path.join(LOG_DIR, "failures.jsonl")


def log_failure(comment_id: int, phase: str, error: Exception, **extra):
    """结构化记录后端失败：grep comment_id 即可还原现场。"""
    import traceback as _tb
    try:
        os.makedirs(os.path.dirname(FAILURE_LOG), exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "comment_id": comment_id,
            "phase": phase,
            "error_type": type(error).__name__,
            "error_msg": str(error)[:1000],
            "traceback": _tb.format_exc()[:4000],
            **extra,
        }
        with open(FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 失败日志自身绝不抛错


def write_system_reply(comment_id: int, message: str, phase: str = "unknown"):
    """兜底回复：把失败做成一条用户可见的 reply，不让请求"消失"。"""
    try:
        now = datetime.now().isoformat()
        debug_meta = json.dumps({
            "version": "system_fallback",
            "phase": phase,
            "is_system_message": True,
        }, ensure_ascii=False)
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
            (comment_id, "agent", "系统", f"⚠️ {message}", now, debug_meta)
        )
        conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
        conn.commit()
        conn.close()
    except Exception:
        pass  # 兜底自身不能再炸


@app.get("/failures")
def get_failures(since: Optional[str] = None, limit: int = 50):
    """读取最近失败日志，方便排查。since 是 ISO 时间戳。"""
    if not os.path.exists(FAILURE_LOG):
        return {"failures": [], "total": 0}
    out = []
    try:
        with open(FAILURE_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if since and entry.get("ts", "") < since:
                continue
            out.append(entry)
            if len(out) >= limit:
                break
    except Exception as e:
        return {"failures": [], "total": 0, "error": str(e)}
    return {"failures": out, "total": len(out)}


def _debug_rows(conn, sql: str, params: tuple = ()) -> list:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _debug_one(conn, sql: str, params: tuple = ()) -> Optional[dict]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _debug_json(value, fallback=None):
    if fallback is None:
        fallback = {}
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _debug_parse_json_fields(row: Optional[dict], fields: list, fallback=None):
    if not row:
        return row
    out = dict(row)
    for field in fields:
        out[field] = _debug_json(out.get(field), fallback)
    return out


def _debug_logs_for_comment(comment_id: int, limit: int = 8) -> list:
    if not os.path.exists(DEBUG_LOG_DIR):
        return []
    marker = f"_id{comment_id}_"
    logs = []
    for filename in os.listdir(DEBUG_LOG_DIR):
        if marker not in filename or not filename.endswith(".md"):
            continue
        path = os.path.join(DEBUG_LOG_DIR, filename)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        logs.append({
            "filename": filename,
            "path": path,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    logs.sort(key=lambda item: item["modified_at"], reverse=True)
    return logs[:max(1, min(int(limit or 8), 50))]


@app.get("/debug/comments")
def debug_comments(limit: int = 30, page_url: Optional[str] = None, q: Optional[str] = None):
    """Read-only SQLite console index: recent comments plus ledger/reply counts."""
    limit = max(1, min(int(limit or 30), 100))
    clauses = ["1=1"]
    params = []
    if page_url:
        clauses.append("c.page_url = ?")
        params.append(page_url)
    if q:
        like = f"%{q}%"
        clauses.append("(c.comment LIKE ? OR c.selected_text LIKE ? OR c.page_title LIKE ? OR c.page_url LIKE ?)")
        params.extend([like, like, like, like])

    where_sql = " AND ".join(clauses)
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = _debug_rows(conn, f"""
            SELECT
                c.id,
                c.page_title,
                c.page_url,
                c.selected_text,
                c.comment,
                c.agent_type,
                c.status,
                c.created_at,
                c.updated_at,
                c.notion_page_id,
                (SELECT COUNT(*) FROM replies r WHERE r.comment_id = c.id) AS reply_count,
                (SELECT COUNT(*) FROM replies r WHERE r.comment_id = c.id AND r.author = 'agent') AS agent_reply_count,
                (SELECT COUNT(*) FROM context_packs cp WHERE cp.comment_id = c.id) AS context_pack_count,
                (SELECT COUNT(*) FROM memory_input_events e WHERE e.comment_id = c.id) AS memory_event_count,
                (SELECT COUNT(*) FROM memory_input_events e
                    WHERE e.comment_id = c.id AND e.actor = 'user') AS user_event_count,
                (SELECT COUNT(*) FROM memory_input_events e
                    WHERE e.comment_id = c.id AND e.actor = 'user' AND e.growth_status = 'done') AS user_event_growth_done_count,
                (SELECT COUNT(*) FROM jobs j
                    WHERE j.payload_json LIKE '%"comment_id": ' || c.id || '%'
                       OR j.payload_json LIKE '%"comment_id":' || c.id || '%') AS job_count,
                (SELECT substr(r.content, 1, 180) FROM replies r
                    WHERE r.comment_id = c.id AND r.author = 'agent'
                    ORDER BY r.created_at DESC LIMIT 1) AS latest_agent_reply_preview,
                l.local_status,
                l.notion_status,
                l.growth_status,
                l.growth_processed_at,
                l.growth_decision
            FROM comments c
            LEFT JOIN memory_intake_ledger l ON l.comment_id = c.id
            WHERE {where_sql}
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, tuple(params + [limit]))
        return {"items": rows, "total": len(rows), "db": DB_PATH}
    finally:
        conn.close()


@app.get("/debug/comments/{comment_id}")
def debug_comment_detail(comment_id: int):
    """Read-only full trace for one comment: replies, context packs, ledger, growth, jobs."""
    conn = sqlite3.connect(DB_PATH)
    try:
        comment = _debug_one(conn, "SELECT * FROM comments WHERE id=?", (comment_id,))
        if not comment:
            raise HTTPException(status_code=404, detail="comment not found")

        replies = _debug_rows(conn, "SELECT * FROM replies WHERE comment_id=? ORDER BY created_at ASC", (comment_id,))
        replies = [_debug_parse_json_fields(row, ["debug_meta"]) for row in replies]

        memory_events = _debug_rows(
            conn,
            "SELECT * FROM memory_input_events WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        memory_events = [_debug_parse_json_fields(row, ["growth_job_ids"], []) for row in memory_events]
        event_interpretations = _debug_rows(
            conn,
            "SELECT * FROM memory_event_interpretations WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        event_interpretations = [
            _debug_parse_json_fields(row, ["interpretation_json", "decision"])
            for row in event_interpretations
        ]

        context_packs = _debug_rows(
            conn,
            "SELECT * FROM context_packs WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        context_packs = [
            _debug_parse_json_fields(
                row,
                ["selected_skill_ids", "episodic_comment_ids", "same_page_comment_ids", "selection_reasons"],
                [],
            )
            for row in context_packs
        ]

        ledger = _debug_parse_json_fields(
            _debug_one(conn, "SELECT * FROM memory_intake_ledger WHERE comment_id=?", (comment_id,)),
            ["growth_job_ids"],
            [],
        )

        jobs = _debug_rows(
            conn,
            """SELECT * FROM jobs
               WHERE payload_json LIKE ? OR payload_json LIKE ?
               ORDER BY created_at ASC""",
            (f'%"comment_id": {comment_id}%', f'%"comment_id":{comment_id}%'),
        )
        jobs = [_debug_parse_json_fields(row, ["payload_json"]) for row in jobs]

        interpretation = _debug_parse_json_fields(
            _debug_one(conn, "SELECT * FROM comment_interpretations WHERE comment_id=?", (comment_id,)),
            ["interpretation_json", "decision"],
        )
        active_questions = _debug_rows(
            conn,
            "SELECT * FROM active_question_signals WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        themes = _debug_rows(
            conn,
            "SELECT * FROM theme_signals WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        themes = [_debug_parse_json_fields(row, ["representative_comment_ids"], []) for row in themes]
        rule_candidates = _debug_rows(
            conn,
            "SELECT * FROM rule_candidates WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        project_signals = _debug_rows(
            conn,
            "SELECT * FROM project_signals WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        project_signals = [_debug_parse_json_fields(row, ["signal_json"]) for row in project_signals]
        profile_signals = _debug_rows(
            conn,
            "SELECT * FROM profile_signals WHERE comment_id=? ORDER BY created_at ASC",
            (comment_id,),
        )
        profile_signals = [_debug_parse_json_fields(row, ["signal_json"]) for row in profile_signals]

        return {
            "comment": comment,
            "replies": replies,
            "memory_events": memory_events,
            "context_packs": context_packs,
            "ledger": ledger,
            "growth": {
                "interpretation": interpretation,
                "event_interpretations": event_interpretations,
                "active_questions": active_questions,
                "themes": themes,
                "rule_candidates": rule_candidates,
                "project_signals": project_signals,
                "profile_signals": profile_signals,
            },
            "jobs": jobs,
            "debug_logs": _debug_logs_for_comment(comment_id),
            "db": DB_PATH,
        }
    finally:
        conn.close()


@app.get("/debug/debug-log/{filename}")
def debug_log_file(filename: str):
    """Read one prompt debug log by basename. This is read-only and constrained to DEBUG_LOG_DIR."""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="invalid debug log filename")
    path = os.path.join(DEBUG_LOG_DIR, safe_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="debug log not found")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"filename": safe_name, "path": path, "content": content}


@app.get("/learning/status")
def learning_status():
    """学习层状态：查看当前画像、交互计数、下次触发时间"""
    count = _get_interaction_count()
    next_profile_review = _PROFILE_REVIEW_INTERVAL - (count % _PROFILE_REVIEW_INTERVAL)
    profile = load_user_profile()
    rules = _load_learned_rules_data().get("rules", [])
    active_rules = [r for r in rules if r.get("active", True)]
    return {
        "interaction_count": count,
        "next_profile_review_in": next_profile_review,
        "profile_is_empty": "[空白，待 AI 填充]" in profile or not profile.strip(),
        "active_learned_rules": len(active_rules),
        "total_learned_rules": len(rules),
        "user_profile_preview": profile[:300] if profile else "(empty)",
    }

@app.post("/learning/review-profile")
def trigger_profile_review():
    """手动触发画像审视"""
    count = _get_interaction_count()
    thread = threading.Thread(target=_review_user_profile, args=(count,), daemon=True)
    thread.start()
    return {"message": f"画像审视已触发 (interaction_count={count})"}

@app.get("/debug-env")
def debug_env():
    """验收用：确认 uvicorn 子进程的关键环境变量正确（不暴露敏感 token）"""
    llm = get_llm_status()
    return {
        "HOME": os.environ.get("HOME", "（未设置）"),
        "llm": llm,
        "claude_bin": llm["claude_code"].get("bin"),
        "claude_bin_exists": llm["claude_code"].get("available"),
        "notion_token_set": bool(os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN")),
        "data_dir": DATA_DIR,
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
    }

@app.get("/config")
def get_config():
    """供插件 background 启动时自动拉取 Notion 配置，写入 chrome.storage"""
    return {
        "notionToken": os.environ.get("NOTION_TOKEN", ""),
        "databaseId": os.environ.get("NOTION_DATABASE_ID", ""),
    }

# ──────────────────────────────────────────
# Notion 代理端点（绕过 Service Worker 休眠问题）
# ──────────────────────────────────────────

def _split_rich_text(s: str, max_len: int = 1990) -> list:
    if not s:
        return [{"text": {"content": ""}}]
    chunks = []
    for i in range(0, len(s), max_len):
        chunks.append({"text": {"content": s[i:i+max_len]}})
    return chunks[:100]

def _truncate(s: str, max_len: int) -> str:
    if not s:
        return ""
    return s[:max_len] + "..." if len(s) > max_len else s

class NotionSaveRequest(BaseModel):
    title: str = ""
    url: str = ""
    platform: str = "网页"
    excerpt: str = ""
    thought: str = ""
    aiConversation: str = ""

class NotionUpsertRequest(BaseModel):
    localCommentId: Optional[int] = None
    notionPageId: Optional[str] = None
    title: str = ""
    url: str = ""
    platform: str = "网页"
    excerpt: str = ""
    thought: str = ""
    aiConversation: str = ""

class NotionImportRequest(BaseModel):
    limit: int = 500

def _notion_credentials():
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("KB_NOTION_DATABASE_ID", "")
    return token, db_id

def _notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

def _notion_request(endpoint: str, method: str = "GET", data: dict = None, timeout: int = 20) -> dict:
    token, _ = _notion_credentials()
    if not token:
        raise HTTPException(status_code=500, detail="Notion 未配置")
    body = json.dumps(data, ensure_ascii=False).encode() if data else None
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{endpoint}",
        data=body,
        headers=_notion_headers(token),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        return {
            "success": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": False,
            "notionError": detail,
        }
    except urllib.error.URLError as e:
        return {
            "success": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": False,
            "notionError": str(e),
        }

def _notion_text(prop: dict) -> str:
    if not prop:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(i.get("plain_text") or i.get("text", {}).get("content", "") for i in items)

def _notion_url(prop: dict) -> str:
    if not prop:
        return ""
    return prop.get("url") or ""

def _notion_select(prop: dict) -> str:
    if not prop:
        return ""
    value = prop.get("select") or {}
    return value.get("name") or ""

def _parse_notion_conversation(ai_conversation: str, fallback_created_at: str) -> list:
    """Parse the conversation text written by the extension into reply rows.

    Old Notion-only installs have no SQLite replies. This keeps enough structure
    for the notebook diary without trying to perfectly recover every timestamp.
    """
    text = (ai_conversation or "").strip()
    if not text:
        return []

    matches = list(re.finditer(r"\[[^\]]+\]\s*(AI|你):\s*", text))
    if not matches:
        if "AI:" in text or "AI：" in text:
            return [{"author": "agent", "content": text, "created_at": fallback_created_at}]
        return []

    replies = []
    for i, match in enumerate(matches):
        author_label = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if not content:
            continue
        replies.append({
            "author": "agent" if author_label == "AI" else "user",
            "content": content,
            "created_at": fallback_created_at,
        })
    return replies

def _insert_local_comment_if_missing(notion_page_id: str = None, page_url: str = "",
                                     page_title: str = "", selected_text: str = "",
                                     comment: str = "", agent_type: str = "notion_import",
                                     created_at: str = None, updated_at: str = None,
                                     replies: list = None) -> tuple:
    """Create a local SQLite event unless the same Notion/local event already exists.

    Returns (comment_id, created_bool).
    """
    created_at = created_at or _now_iso()
    updated_at = updated_at or created_at
    comment = (comment or "").strip() or "（仅高亮，无评论）"
    selected_text = selected_text or ""
    page_url = page_url or "notion://unknown"
    page_title = page_title or ""
    replies = replies or []

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = None
        if notion_page_id:
            existing = conn.execute(
                "SELECT id FROM comments WHERE notion_page_id=? LIMIT 1",
                (notion_page_id,),
            ).fetchone()
        if not existing:
            existing = conn.execute(
                "SELECT id FROM comments WHERE page_url=? AND selected_text=? AND comment=? LIMIT 1",
                (page_url, selected_text, comment),
            ).fetchone()
        if existing:
            comment_id = existing[0]
            if notion_page_id:
                conn.execute(
                    "UPDATE comments SET notion_page_id=COALESCE(notion_page_id, ?), updated_at=? WHERE id=?",
                    (notion_page_id, updated_at, comment_id),
                )
                conn.commit()
            return comment_id, False

        cur = conn.execute(
            """INSERT INTO comments
               (notion_page_id, page_url, page_title, selected_text, surrounding_text, comment,
                agent_type, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, '', ?, ?, 'open', ?, ?)""",
            (notion_page_id, page_url, page_title, selected_text, comment, agent_type,
             created_at, updated_at),
        )
        comment_id = cur.lastrowid
        for reply in replies:
            conn.execute(
                "INSERT INTO replies (comment_id, author, agent_type, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    comment_id,
                    reply.get("author") or "agent",
                    "notion_import",
                    reply.get("content") or "",
                    reply.get("created_at") or created_at,
                ),
            )
        conn.commit()
        _record_intake_local_saved(comment_id)
        return comment_id, True
    finally:
        conn.close()

def _link_comment_to_notion(local_comment_id: int, page_id: str):
    if not local_comment_id or not page_id:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE comments SET notion_page_id=?, updated_at=? WHERE id=?",
            (page_id, _now_iso(), local_comment_id),
        )
        conn.commit()
    finally:
        conn.close()

@app.post("/notion/save")
async def notion_save(req: NotionSaveRequest):
    """高亮保存：先落本地 SQLite，Notion 只是可选外部副本。"""
    local_comment_id, local_created = _insert_local_comment_if_missing(
        page_url=req.url,
        page_title=req.title,
        selected_text=req.excerpt,
        comment=req.thought,
        agent_type="highlight",
    )

    token, db_id = _notion_credentials()
    if not token or not db_id:
        _record_intake_notion(local_comment_id, "notion_skipped", "notion_not_configured")
        return {
            "success": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": False,
            "message": "已保存到本地 SQLite；Notion 未配置",
        }
    body = json.dumps({
        "parent": {"database_id": db_id},
        "properties": {
            "标题": {"title": [{"text": {"content": _truncate(req.title, 100)}}]},
            "来源平台": {"select": {"name": req.platform}},
            "来源URL": {"url": req.url},
            "原文片段": {"rich_text": _split_rich_text(req.excerpt)},
            "我的想法": {"rich_text": _split_rich_text(req.thought)},
            "评论区对话": {"rich_text": _split_rich_text(req.aiConversation)},
        }
    }).encode()
    r = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=body,
        headers=_notion_headers(token),
        method="POST"
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            data = json.loads(resp.read())
        page_id = data.get("id")
        _link_comment_to_notion(local_comment_id, page_id)
        _record_intake_notion(local_comment_id, "notion_synced")
        return {
            "success": True,
            "pageId": page_id,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": True,
        }
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        _record_intake_notion(local_comment_id, "notion_failed", detail)
        return {
            "success": True,
            "pageId": None,
            "localCommentId": local_comment_id,
            "notionSynced": False,
            "notionError": detail,
        }
    except urllib.error.URLError as e:
        _record_intake_notion(local_comment_id, "notion_failed", str(e))
        return {
            "success": True,
            "pageId": None,
            "localCommentId": local_comment_id,
            "notionSynced": False,
            "notionError": str(e),
        }

@app.post("/notion/upsert")
async def notion_upsert(req: NotionUpsertRequest):
    """评论 upsert 到 Notion（代理，不经过 Service Worker）"""
    token, db_id = _notion_credentials()
    if not token or not db_id:
        if req.localCommentId:
            _record_intake_notion(req.localCommentId, "notion_skipped", "notion_not_configured")
        return {"success": True, "pageId": req.notionPageId, "notionSynced": False,
                "message": "本地评论已保存；Notion 未配置"}
    headers = _notion_headers(token)

    has_ai = req.aiConversation and "AI:" in req.aiConversation
    conversation_field = {"评论区对话": {"rich_text": _split_rich_text(req.aiConversation)}} if has_ai else {}

    if req.notionPageId:
        body = json.dumps({
            "properties": {
                "我的想法": {"rich_text": _split_rich_text(req.thought)},
                **conversation_field,
            }
        }).encode()
        r = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{req.notionPageId}",
            data=body, headers=headers, method="PATCH"
        )
    else:
        body = json.dumps({
            "parent": {"database_id": db_id},
            "properties": {
                "标题": {"title": [{"text": {"content": _truncate(req.title, 100)}}]},
                "来源平台": {"select": {"name": req.platform}},
                "来源URL": {"url": req.url},
                "原文片段": {"rich_text": _split_rich_text(req.excerpt)},
                "我的想法": {"rich_text": _split_rich_text(req.thought)},
                **conversation_field,
            }
        }).encode()
        r = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=body, headers=headers, method="POST"
        )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            data = json.loads(resp.read())
        page_id = data.get("id", req.notionPageId)
        if req.localCommentId:
            _link_comment_to_notion(req.localCommentId, page_id)
            local_comment_id = req.localCommentId
        else:
            local_comment_id, _ = _insert_local_comment_if_missing(
                notion_page_id=page_id,
                page_url=req.url,
                page_title=req.title,
                selected_text=req.excerpt,
                comment=req.thought,
                agent_type="notion_upsert",
                replies=_parse_notion_conversation(req.aiConversation, _now_iso()),
            )
        _record_intake_notion(local_comment_id, "notion_synced")
        return {"success": True, "pageId": page_id, "notionSynced": True}
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        if req.localCommentId:
            _record_intake_notion(req.localCommentId, "notion_failed", detail)
        return {
            "success": True,
            "pageId": req.notionPageId,
            "notionSynced": False,
            "notionError": detail,
        }
    except urllib.error.URLError as e:
        if req.localCommentId:
            _record_intake_notion(req.localCommentId, "notion_failed", str(e))
        return {
            "success": True,
            "pageId": req.notionPageId,
            "notionSynced": False,
            "notionError": str(e),
        }

@app.post("/notebook/import-notion")
def notebook_import_notion(req: NotionImportRequest):
    """One-time backfill from the user's own Notion database into local SQLite.

    This is a migration path for old installs that wrote Notion pages but did not
    maintain comments.db. Runtime notebook data remains SQLite-first.
    """
    token, db_id = _notion_credentials()
    if not token or not db_id:
        raise HTTPException(status_code=400, detail="Notion 未配置，无法导入")

    limit = max(1, min(int(req.limit or 500), 2000))
    entries = []
    cursor = None
    while len(entries) < limit:
        payload = {
            "page_size": min(100, limit - len(entries)),
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        }
        if cursor:
            payload["start_cursor"] = cursor
        result = _notion_request(f"databases/{db_id}/query", method="POST", data=payload, timeout=30)
        entries.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    imported = 0
    skipped = 0
    for page in entries:
        props = page.get("properties", {})
        title = _notion_text(props.get("标题", {}))
        url = _notion_url(props.get("来源URL", {}))
        excerpt = _notion_text(props.get("原文片段", {}))
        thought = _notion_text(props.get("我的想法", {}))
        ai_conv = _notion_text(props.get("评论区对话", {}))
        created_at = page.get("created_time") or _now_iso()
        updated_at = page.get("last_edited_time") or created_at
        page_id = page.get("id")
        comment_id, created = _insert_local_comment_if_missing(
            notion_page_id=page_id,
            page_url=url,
            page_title=title,
            selected_text=excerpt,
            comment=thought,
            agent_type="notion_import",
            created_at=created_at,
            updated_at=updated_at,
            replies=_parse_notion_conversation(ai_conv, created_at),
        )
        if created:
            imported += 1
        else:
            skipped += 1
        _record_intake_notion(comment_id, "notion_synced")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "total_seen": len(entries),
        "limit": limit,
        "db": DB_PATH,
    }


# ──────────────────────────────────────────
# M2: 笔记本 endpoints（详见 ~/mem-ai/docs/memory-backend-design.md §4）
# ──────────────────────────────────────────

@app.get("/notebook/overview")
def notebook_overview():
    """顶部计数 + 底部 AGENT 观察 callout"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        c_count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        page_count = conn.execute("SELECT COUNT(DISTINCT page_url) FROM comments").fetchone()[0]
        latest_sync = conn.execute(
            "SELECT MAX(created_at) FROM comments"
        ).fetchone()[0]
        # 最近一条 active thinking_summary（用作底部 callout）
        latest_thinking = conn.execute(
            "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
            "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        # 学到的规则数
        rules_data = _load_learned_rules_data()
        active_rules = sum(1 for r in rules_data.get("rules", []) if r.get("active", True))
        return {
            "comment_count": c_count,
            "page_count": page_count,
            "latest_sync": latest_sync,
            "active_rules": active_rules,
            "latest_thinking": dict(latest_thinking) if latest_thinking else None,
            "storage_source": "sqlite",
            "notion_configured": bool(_notion_credentials()[0] and _notion_credentials()[1]),
        }
    finally:
        conn.close()


@app.get("/notebook/profile")
def notebook_profile():
    """B 类记忆：user_profile.md + project_context.md（markdown 真理）"""
    return {
        "user_profile_md": load_user_profile(),
        "project_context_md": load_project_context(),
    }


@app.get("/notebook/rules")
def notebook_rules():
    """C 类记忆：learned_rules.json（M3 后切到 working_rules 表）"""
    data = _load_learned_rules_data()
    rules = data.get("rules", [])
    # 按 last_used_at desc 排序，未使用的放后面
    rules.sort(key=lambda r: (r.get("last_used_at") or r.get("created_at") or ""), reverse=True)
    active = [r for r in rules if r.get("active", True)]
    archived = [r for r in rules if not r.get("active", True)]
    # 本周新增数（粗算：created_at 在最近 7 天）
    from datetime import timedelta
    one_week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_new = sum(1 for r in active if (r.get("created_at") or "") >= one_week_ago)
    return {
        "active": active,
        "archived": archived,
        "stats": {
            "active_count": len(active),
            "week_new": week_new,
            # 命中率 M3 才有真实数据，M2 占位
            "hit_rate": None,
        }
    }


@app.get("/notebook/thinking")
def notebook_thinking():
    """最近你在想的事 上栏：最新一条 active thinking_summary"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        active = conn.execute(
            "SELECT id, window_start, window_end, title, synthesis_md, "
            "evidence_comment_ids, trigger_reason, comments_since_last, created_at "
            "FROM thinking_summaries WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        archived_count = conn.execute(
            "SELECT COUNT(*) FROM thinking_summaries WHERE status='archived'"
        ).fetchone()[0]
        # 是否有正在跑的 thinking job
        running_job = conn.execute(
            "SELECT id, status, created_at FROM jobs "
            "WHERE kind='synthesize_thinking' AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return {
            "active": dict(active) if active else None,
            "archived_count": archived_count,
            "running_job": dict(running_job) if running_job else None,
        }
    finally:
        conn.close()


@app.get("/notebook/thinking/history")
def notebook_thinking_history(limit: int = 20):
    """历史 thinking_summaries 列表（archived）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, window_start, window_end, title, synthesis_md, created_at "
            "FROM thinking_summaries WHERE status='archived' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/notebook/diary")
def notebook_diary(limit: int = 50, before: str = None):
    """共同日记：comments + replies 时间线（M2 仅"你做了什么"流；M3 后会 join agent_actions）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 取最近 limit 条 comments
        if before:
            rows = conn.execute(
                "SELECT id, page_url, page_title, selected_text, comment, created_at "
                "FROM comments WHERE created_at < ? ORDER BY created_at DESC LIMIT ?",
                (before, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, page_url, page_title, selected_text, comment, created_at "
                "FROM comments ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        items = []
        for r in rows:
            replies = conn.execute(
                "SELECT id, author, content, created_at FROM replies "
                "WHERE comment_id=? ORDER BY created_at ASC",
                (r["id"],)
            ).fetchall()
            items.append({
                **dict(r),
                "replies": [dict(rep) for rep in replies],
            })
        return {"items": items, "has_more": len(items) >= limit}
    finally:
        conn.close()


# ──────────────────────────────────────────
# Memory Chat V0：notebook 全局只读问记忆入口
# ──────────────────────────────────────────

class MemoryChatRequest(BaseModel):
    message: str


@app.get("/notebook/chat")
def notebook_chat_history(limit: int = 40):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, role, content, context_pack_id, created_at "
            "FROM memory_chat_messages ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
        return {"items": [dict(r) for r in reversed(rows)]}
    finally:
        conn.close()


@app.post("/notebook/chat")
def notebook_chat(req: MemoryChatRequest):
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    now = _now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO memory_chat_messages (role, content, created_at) VALUES ('user', ?, ?)",
            (message, now),
        )
        user_message_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    pack = _build_context_pack_for_query(message)
    prompt = f"""{pack.get('context_md', '')}

## 用户的问题
{message}

## 回答要求
- 只基于上面的记忆上下文回答。
- 能引用证据时必须写 [c#123] 这样的引用。
- 如果没有足够证据，直接说没有在现有批注里找到，不要编。
- 这是只读问答入口，不要承诺已经修改记忆。"""
    system_prompt = (
        "你是 mem-ai 记忆笔记本里的只读问答入口。你的任务是帮用户找回、核对、解释自己的历史批注和记忆。"
        "回答要短，先给结论，再列证据。必须用 [c#id] 引用具体批注；没有证据就说没有找到。"
    )

    status = "success"
    try:
        answer, rc = _call_claude(prompt, system_prompt, timeout=300)
        if rc != 0 or not answer.strip():
            status = "error"
            answer = f"记忆问答调用失败：{answer[:500]}"
    except Exception as e:
        status = "error"
        answer = f"记忆问答调用失败：{type(e).__name__}: {str(e)[:300]}"
        log_failure(user_message_id, "memory_chat", e)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO memory_chat_messages (role, content, created_at) VALUES ('assistant', ?, ?)",
            (answer, _now_iso()),
        )
        assistant_message_id = cur.lastrowid
        context_pack_id = _insert_context_pack(
            conn, pack, source_type="chat", chat_message_id=assistant_message_id, query_text=message
        )
        if context_pack_id:
            conn.execute(
                "UPDATE memory_chat_messages SET context_pack_id=? WHERE id=?",
                (context_pack_id, assistant_message_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": status,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "answer": answer,
        "context_pack_id": context_pack_id,
        "context_refs": {
            "identity_snapshot_id": pack.get("identity_snapshot_id"),
            "selected_skill_ids": pack.get("selected_skill_ids") or [],
            "episodic_comment_ids": pack.get("episodic_comment_ids") or [],
        },
    }


# ──────────────────────────────────────────
# M2: jobs endpoints（异步任务队列）
# ──────────────────────────────────────────

class JobCreate(BaseModel):
    kind: str
    payload: dict = {}

@app.post("/jobs")
def create_job(req: JobCreate):
    """queue 一个后台任务（worker.py 会 lease 并执行）"""
    allowed_kinds = {"synthesize_thinking", "memory_growth_for_comment"}  # M3 后会扩展
    if req.kind not in allowed_kinds:
        raise HTTPException(status_code=400, detail=f"unknown kind: {req.kind}")
    if req.kind == "memory_growth_for_comment":
        comment_id = int((req.payload or {}).get("comment_id") or 0)
        event_id = int((req.payload or {}).get("event_id") or 0)
        if not comment_id:
            raise HTTPException(status_code=400, detail="comment_id is required")
        job_id = _enqueue_memory_growth_job(
            comment_id,
            (req.payload or {}).get("trigger_reason") or "manual",
            event_id=event_id or None,
        )
        if job_id:
            return {"id": job_id, "status": "queued", "deduped": False}
        conn = sqlite3.connect(DB_PATH)
        try:
            if event_id:
                row = conn.execute(
                    "SELECT growth_job_ids, growth_status FROM memory_input_events WHERE id=? AND comment_id=?",
                    (event_id, comment_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT growth_job_ids, growth_status FROM memory_intake_ledger WHERE comment_id=?",
                    (comment_id,),
                ).fetchone()
            ids = json.loads(row[0] or "[]") if row else []
            return {"id": ids[-1] if ids else None, "status": row[1] if row else "unknown", "deduped": True}
        finally:
            conn.close()
    conn = sqlite3.connect(DB_PATH)
    try:
        # 防止重复 queue：如果已有同 kind 的 queued/running 任务，直接返回它
        existing = conn.execute(
            "SELECT id, status, created_at FROM jobs "
            "WHERE kind=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1",
            (req.kind,)
        ).fetchone()
        if existing:
            return {"id": existing[0], "status": existing[1], "deduped": True}
        now = datetime.now().isoformat()
        cursor = conn.execute(
            "INSERT INTO jobs (kind, payload_json, status, created_at) "
            "VALUES (?, ?, 'queued', ?)",
            (req.kind, json.dumps(req.payload, ensure_ascii=False), now)
        )
        job_id = cursor.lastrowid
        conn.commit()
        return {"id": job_id, "status": "queued", "deduped": False}
    finally:
        conn.close()


@app.get("/jobs/{job_id}")
def get_job(job_id: int):
    """查 job 状态（前端首次打开笔记本时轮询）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, kind, status, attempts, error, created_at, started_at, finished_at "
            "FROM jobs WHERE id=?",
            (job_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(row)
    finally:
        conn.close()


# ──────────────────────────────────────────
# M2.6: Curated 视图（验证 aha，不持久化、不建表）
#
# 设计思路：先验证「养成的习惯」和「你 & 项目」用 LLM 浓缩后能不能产生 aha。
# 不进 jobs 表、不建 working_rules，直接 sync 调 claude 返回 JSON。
# 前端在 localStorage 缓存结果（避免每次访问都跑 LLM）。
# 验证通过后再决定是否升级成 worker + 持久化（M3）。
# ──────────────────────────────────────────


def _safe_parse_curated_json(content: str) -> dict:
    """从 claude 输出里提取 JSON，多策略 fallback"""
    if not content:
        raise ValueError("empty LLM output")
    s = content.strip()
    # 策略 1：```json ... ```
    if "```json" in s:
        i = s.find("```json") + 7
        j = s.find("```", i)
        if j > i:
            try:
                return json.loads(s[i:j].strip())
            except Exception:
                pass
    # 策略 2：找最外层 { ... }
    i = s.find("{"); j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j+1])
        except Exception:
            pass
    # 策略 3：原始解析
    return json.loads(s)


CURATED_RULES_PROMPT = load_prompt_template("curated_skills") or """你是一个观察 AI 协作者如何成长的产品观察者。你的任务不是挑选最好的规则，
而是说出用户的 AI 正在长出哪几套"工作方式 / skill"。

# 用户身份
{user_profile}

# 当前的 {rule_count} 条工作规则（按时间倒序）
{rules_dump}

# 任务

把这些规则**抽象**成 3-6 个 **"工作方式 / skill"**。这不是挑选，是 distill：
- 每个 skill 是用户的 AI 正在长出的一套能力（"调研方式"、"思辨方式"、"判断方式"等）
- 每个 skill 由多条 rule 共同支撑（≥ 2 条 evidence）
- skill 数量由你判断（但 3-6 个最合适）；不一定要把所有 rule 都覆盖（少数过于零散的可以放 uncategorized）

# 写 description 的语感（最重要）

不要写"用户喜欢 X / 用户偏好 Y"。**用 AI 第一人称："我会..."、"我倾向..."、"在 X 场景里我会 Y"**。

要具体到行为，不要抽象。要场景化举例。长度自然（30–80 字），不要压缩到口号。

# 4 个示范（学这个语感，不要照抄内容）

示范 1 · 调研方式：
> 我会按"原文摘抄 + 中文翻译 + 基于本机上下文的关联思考"输出，并主动补你没问到的问题。

示范 2 · 思辨方式：
> 我会避免二元结论，主动拆 trade-off、机制和信号链路。在 benchmark / memory 这类主题上尤其会展开。

示范 3 · 项目判断方式：
> 我会记住你有明确证据支撑的阶段优先级。在做选型建议时，先引用本机上下文里的证据，再给判断。

示范 4 · 内容质感方式：
> 深度访谈和案例研究我会接近《晚点》风格 — 保留场景、原话、人物和结果，不做信息罗列。

# 输出格式（直接 JSON，不要围栏，不要解释）

{{
  "skills": [
    {{
      "name": "调研方式",
      "description": "我会按...的方式...（第一人称，具体行为 + 场景，30–80 字）",
      "evidence_rule_ids": ["rule_005", "rule_011", "rule_016"]
    }},
    {{
      "name": "...",
      "description": "...",
      "evidence_rule_ids": [...]
    }}
  ],
  "uncategorized_rule_ids": ["rule_xxx", "rule_yyy"]
}}

约束：
- skill 数量在 3–6 之间，由你判断
- 每个 skill 至少 2 条 evidence_rule_ids
- 每个 rule_id 只能出现在一个 skill 里 OR 一次 uncategorized（不能重复）
- 所有 rule_id 必须是输入里真实存在的 id（不能编造）
- description 严格 AI 第一人称，不要在 description 内部使用未转义双引号（用「」/『』代替）
- name 要短（4–7 字，"X 方式" 或 "X 偏好" 形式）
- 全部使用中文
"""


def _llm_distill_skills(rules: list) -> dict:
    """跑 LLM 蒸馏，返回校验后的 {skills, uncategorized_rule_ids}。供同步端点 + worker 复用。

    输入：active rules 列表（每条含 id / rule / scope / source）
    输出：{"skills": [{name, description, evidence_rule_ids}], "uncategorized_rule_ids": [...]}
    异常向上抛（HTTPException / subprocess.TimeoutExpired / RuntimeError）
    """
    if len(rules) < 3:
        raise RuntimeError(f"insufficient_rules: only {len(rules)} active rules")

    user_profile = _load_file(USER_PROFILE_PATH, "[空白]")[:3000]
    rules_dump_lines = []
    for r in rules:
        rid = r.get("id", "?")
        text = r.get("rule", "")
        scope = r.get("scope", "all")
        src = r.get("source", "")
        rules_dump_lines.append(f"[{rid}] scope={scope} · src={src}\n  {text}")
    rules_dump = "\n\n".join(rules_dump_lines)

    prompt = CURATED_RULES_PROMPT.format(
        user_profile=user_profile,
        rules_dump=rules_dump,
        rule_count=len(rules),
        remaining_count=max(0, len(rules) - 3),
    )
    sys_prompt = "You are a careful product observer for a personal memory tool. Output only the requested JSON."

    content, rc = _call_claude(prompt, sys_prompt, timeout=180)
    if rc != 0:
        raise RuntimeError(f"LLM provider error {rc}: {content[:300]}")

    parsed = _safe_parse_curated_json(content)
    valid_ids = {r.get("id") for r in rules}
    seen_ids = set()
    skills = []
    for s in parsed.get("skills", []) or []:
        name = (s.get("name") or "").strip()
        description = (s.get("description") or "").strip()
        if not name or not description:
            continue
        ids = [rid for rid in (s.get("evidence_rule_ids") or []) if rid in valid_ids and rid not in seen_ids]
        if len(ids) < 2:
            continue
        seen_ids.update(ids)
        skills.append({
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
    return {"skills": skills, "uncategorized_rule_ids": uncategorized}


def _persist_skills_generation(distilled: dict, source_rules: list, trigger_reason: str,
                               trigger_payload: dict = None, job_id: int = None,
                               llm_model: str = "claude-opus-4-7") -> int:
    """把 _llm_distill_skills 的输出原子性写入 4 张表，返回新 generation_id。

    - 旧 generation 的 working_skills 全部 status='superseded'
    - 新 generation_id 下写入新一批 working_skills (status='active')
    - memory_revisions 记录一条 diff_summary（M3.0 范围 B 用简单文本，未来用 LLM 出 diff）
    """
    skills = distilled.get("skills") or []
    uncategorized = distilled.get("uncategorized_rule_ids") or []
    if not skills:
        raise RuntimeError("distillation produced no valid skills; keep previous active generation")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # 1) supersede 所有旧 active skill
        cur.execute("UPDATE working_skills SET status='superseded' WHERE status='active'")
        old_active_count = cur.rowcount

        # 2) 写 skill_generations
        cur.execute(
            "INSERT INTO skill_generations (kind, trigger_reason, trigger_payload, source_rules_count, llm_model, job_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('skills', trigger_reason, json.dumps(trigger_payload or {}, ensure_ascii=False),
             len(source_rules), llm_model, job_id, _now_iso()),
        )
        gen_id = cur.lastrowid

        # 3) 写新 working_skills
        for s in skills:
            cur.execute(
                "INSERT INTO working_skills (name, description, evidence_rule_ids, triggers, status, generation_id, created_at) "
                "VALUES (?, ?, ?, NULL, 'active', ?, ?)",
                (s["name"], s["description"], json.dumps(s["evidence_rule_ids"], ensure_ascii=False),
                 gen_id, _now_iso()),
            )

        # 4) 写 memory_revisions（M3.0 范围 B：简单文本 summary）
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
            (gen_id, diff_summary, json.dumps(diff_json, ensure_ascii=False), _now_iso()),
        )

        conn.commit()
        return gen_id
    finally:
        conn.close()


def _read_active_skills() -> dict:
    """读当前 active 的 skills + 最新 generation 的元数据（笔记本用）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, evidence_rule_ids, generation_id, created_at "
            "FROM working_skills WHERE status='active' ORDER BY id ASC"
        )
        skills = []
        gen_ids = set()
        for row in cur.fetchall():
            try:
                evidence = json.loads(row[3] or "[]")
            except Exception:
                evidence = []
            skills.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "evidence_rule_ids": evidence,
                "generation_id": row[4],
                "created_at": row[5],
            })
            gen_ids.add(row[4])

        latest_gen = None
        if gen_ids:
            cur.execute(
                "SELECT id, trigger_reason, source_rules_count, created_at FROM skill_generations "
                "WHERE id = ? LIMIT 1",
                (max(gen_ids),),
            )
            row = cur.fetchone()
            if row:
                latest_gen = {
                    "id": row[0],
                    "trigger_reason": row[1],
                    "source_rules_count": row[2],
                    "created_at": row[3],
                }
        return {"skills": skills, "latest_generation": latest_gen}
    finally:
        conn.close()


def _persist_profile_snapshot(profile: dict, trigger_reason: str,
                              trigger_payload: dict = None,
                              llm_model: str = "claude-opus-4-7") -> int:
    """把当前 profile 状态卡持久化到 profile_snapshots，返回 generation_id。"""
    fields = profile.get("fields") or {}
    one_liner = (profile.get("one_liner") or "").strip()
    if not one_liner:
        raise RuntimeError("profile snapshot missing one_liner")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE profile_snapshots SET status='superseded' WHERE status='active'")
        old_active_count = cur.rowcount
        cur.execute(
            "INSERT INTO skill_generations (kind, trigger_reason, trigger_payload, source_rules_count, llm_model, job_id, created_at) "
            "VALUES (?, ?, ?, NULL, ?, NULL, ?)",
            ('profile', trigger_reason, json.dumps(trigger_payload or {}, ensure_ascii=False),
             llm_model, _now_iso()),
        )
        gen_id = cur.lastrowid
        cur.execute(
            "INSERT INTO profile_snapshots "
            "(one_liner, field_who, field_doing, field_focus, field_phase, since_last_check, "
            " uncategorized_rule_ids, status, generation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, 'active', ?, ?)",
            (
                one_liner,
                (fields.get("identity") or "").strip(),
                (fields.get("current_project") or "").strip(),
                (fields.get("north_star") or "").strip(),
                (fields.get("pending") or "").strip(),
                (profile.get("since_last_check") or "").strip(),
                gen_id,
                _now_iso(),
            ),
        )
        diff_summary = "首次整理 profile 状态卡" if old_active_count == 0 else "重新整理 profile 状态卡"
        cur.execute(
            "INSERT INTO memory_revisions (generation_id, kind, diff_summary, diff_json, created_at) "
            "VALUES (?, 'profile', ?, ?, ?)",
            (gen_id, diff_summary,
             json.dumps({"previous_active": old_active_count}, ensure_ascii=False),
             _now_iso()),
        )
        conn.commit()
        return gen_id
    finally:
        conn.close()


def _read_active_profile_snapshot() -> dict:
    """读当前 active profile snapshot，返回前端 renderProfileCurated 使用的 schema。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, one_liner, field_who, field_doing, field_focus, field_phase, "
            "since_last_check, generation_id, created_at "
            "FROM profile_snapshots WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"_status": "no_generation_yet"}
        gen = conn.execute(
            "SELECT id, trigger_reason, created_at FROM skill_generations WHERE id=? LIMIT 1",
            (row["generation_id"],),
        ).fetchone()
        return {
            "one_liner": row["one_liner"],
            "fields": {
                "identity": row["field_who"] or "",
                "current_project": row["field_doing"] or "",
                "north_star": row["field_focus"] or "",
                "pending": row["field_phase"] or "",
            },
            "since_last_check": row["since_last_check"] or "",
            "latest_generation": dict(gen) if gen else None,
            "_status": "ok",
        }
    finally:
        conn.close()


def _load_active_rules() -> list:
    """读 learned_rules.json 的 active 子集"""
    data = _load_learned_rules_data()
    return [r for r in data.get("rules", []) if r.get("active", True)]


@app.post("/notebook/rules/curated")
def notebook_rules_curated():
    """蒸馏 active rules 成 N 个工作方式。
    手动触发：跑 LLM → 写 4 张表 → 返回新 generation 的 active skills
    """
    rules = _load_active_rules()
    if len(rules) < 3:
        active = _read_active_skills()
        if active.get("skills"):
            generation = active.get("latest_generation") or {}
            return {
                "skills": [{"name": s["name"], "description": s["description"],
                            "evidence_rule_ids": s["evidence_rule_ids"]} for s in active["skills"]],
                "uncategorized_rule_ids": [],
                "_status": "ok",
                "_message": "当前原始规则不足，先保留上一次已提炼的工作方式。",
                "_total_rules": generation.get("source_rules_count") or len(rules),
                "_generation_id": generation.get("id"),
                "_persisted": True,
                "_stale_rules_source": True,
            }
        return {"skills": [], "uncategorized_rule_ids": [], "_status": "insufficient_data",
                "_message": f"当前只有 {len(rules)} 条 active 规则，先批注几次让 AI 学到东西",
                "_total_rules": len(rules)}

    try:
        distilled = _llm_distill_skills(rules)
        gen_id = _persist_skills_generation(
            distilled=distilled,
            source_rules=rules,
            trigger_reason='manual',
            trigger_payload={
                "endpoint": "/notebook/rules/curated",
                "max_rule_id_at_distill": _current_max_rule_id(),
            },
        )
        active = _read_active_skills()
        return {
            "skills": [{"name": s["name"], "description": s["description"],
                        "evidence_rule_ids": s["evidence_rule_ids"]} for s in active["skills"]],
            "uncategorized_rule_ids": distilled.get("uncategorized_rule_ids") or [],
            "_status": "ok",
            "_total_rules": len(rules),
            "_generation_id": gen_id,
            "_persisted": True,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="LLM timeout")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        print(f"[curated_rules] error: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)[:300]}")


@app.get("/notebook/skills")
def notebook_skills_get():
    """读当前 active 的 skills + latest generation 元数据。
    无 LLM 调用，<200ms 返回。笔记本默认入口。
    """
    rules = _load_active_rules()
    active = _read_active_skills()
    return {
        "skills": [{"name": s["name"], "description": s["description"],
                    "evidence_rule_ids": s["evidence_rule_ids"]} for s in active["skills"]],
        "uncategorized_rule_ids": [],  # M3.0：未蒸馏的 rules 暂不分类，未来 worker 加
        "latest_generation": active["latest_generation"],
        "_total_rules": len(rules),
        "_status": "ok" if active["skills"] else "no_generation_yet",
    }


CURATED_PROFILE_PROMPT = """你是一个深度阅读用户当前状态的同事观察者。

# user_profile.md
{user_profile}

# project_context.md
{project_context}

# 最近 30 条批注（按时间倒序，了解用户当下在做什么）
{recent_comments}

# 任务

写出一份 **"AI 此刻怎么理解我"** 的浓缩状态卡片。这不是简历，是同事眼里的我。

# 输出格式（直接 JSON，不要围栏，不要解释）

{{
  "one_liner": "你是 X，在做 Y，现在卡在 Z 上 — 一句话 ≤ 50 字，要有穿透力，不是简历自我介绍",
  "fields": {{
    "identity": "身份 — 你是谁，做什么阶段，≤ 25 字",
    "current_project": "当前手上的项目 — 是什么，到了哪一步，≤ 30 字",
    "north_star": "北极星 — 你在追什么验证 / 终局长什么样，≤ 30 字",
    "pending": "悬而未决 — 当前最纠结 / 没拍板的具体问题，≤ 30 字"
  }},
  "since_last_check": "如果可见，最近 7 天有什么变化 ≤ 25 字（无明显变化就写"无明显变化"）"
}}

约束：
- one_liner 必须有穿透力，不是泛泛"独立创业者，做 AI 产品"，要具体到当下
- pending 优先用 user 自己最近批注里反复提的问题，不是项目文档里固定列的
- 不要在字段值内部使用未转义双引号（用「」代替）
- 全部使用中文
"""


@app.get("/notebook/profile/snapshot")
def notebook_profile_snapshot_get():
    """读当前 active profile snapshot。无 LLM 调用，笔记本默认入口。"""
    return _read_active_profile_snapshot()


@app.post("/notebook/profile/curated")
def notebook_profile_curated():
    """生成 你 & 项目 的浓缩状态卡：跑 LLM → 写 DB → 返回 active snapshot。"""
    user_profile = load_user_profile()
    project_context = load_project_context()
    if not user_profile.strip() and not project_context.strip():
        return {"_status": "insufficient_data",
                "_message": "user_profile.md 和 project_context.md 都是空的，先填一下底"}

    # 取最近 30 条 comments 作为"当下"信号
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, page_title, selected_text, comment, created_at "
        "FROM comments ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    recent_lines = []
    for r in rows:
        excerpt = (r["selected_text"] or "").strip()[:80]
        recent_lines.append(
            f"[c#{r['id']}] {r['created_at'][:10]} 《{(r['page_title'] or '')[:30]}》\n"
            f"  {(r['comment'] or '')[:150]}"
            + (f"\n  > {excerpt}" if excerpt else "")
        )
    recent_dump = "\n\n".join(recent_lines) if recent_lines else "[暂无批注]"

    prompt = CURATED_PROFILE_PROMPT.format(
        user_profile=user_profile[:5000],
        project_context=project_context[:5000],
        recent_comments=recent_dump,
    )
    sys_prompt = "You are a careful colleague-observer for a personal memory tool. Output only the requested JSON."

    try:
        content, rc = _call_claude(prompt, sys_prompt, timeout=120)
        if rc != 0:
            raise HTTPException(status_code=502, detail=f"LLM provider error {rc}: {content[:300]}")
        parsed = _safe_parse_curated_json(content)
        # 校验字段
        if not isinstance(parsed.get("one_liner"), str):
            raise ValueError("missing one_liner")
        fields = parsed.get("fields") or {}
        profile = {
            "one_liner": parsed.get("one_liner", "").strip(),
            "fields": {
                "identity": (fields.get("identity") or "").strip(),
                "current_project": (fields.get("current_project") or "").strip(),
                "north_star": (fields.get("north_star") or "").strip(),
                "pending": (fields.get("pending") or "").strip(),
            },
            "since_last_check": (parsed.get("since_last_check") or "").strip(),
            "_status": "ok",
        }
        gen_id = _persist_profile_snapshot(
            profile=profile,
            trigger_reason='manual',
            trigger_payload={"endpoint": "/notebook/profile/curated"},
        )
        profile["latest_generation"] = {"id": gen_id, "trigger_reason": "manual", "created_at": _now_iso()}
        profile["_generation_id"] = gen_id
        profile["_persisted"] = True
        return profile
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="LLM timeout")
    except Exception as e:
        print(f"[curated_profile] error: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)[:300]}")
