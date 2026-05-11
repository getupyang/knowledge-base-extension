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
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response
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
LOCAL_BACKUP_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("MEMAI_BACKUP_DIR", os.path.join(DATA_DIR, "backups"))
))
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
    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)

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

@app.middleware("http")
async def local_extension_cors_guard(request: Request, call_next):
    """Keep public pages and extension pages able to reach the localhost backend."""
    origin = request.headers.get("origin") or "*"
    if request.method == "OPTIONS":
        requested_headers = request.headers.get("access-control-request-headers", "content-type")
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT",
            "Access-Control-Allow-Headers": requested_headers,
            "Access-Control-Max-Age": "600",
            "Access-Control-Allow-Private-Network": "true",
        }
        return Response("OK", status_code=200, headers=headers)

    response = await call_next(request)
    response.headers.setdefault("Access-Control-Allow-Origin", origin)
    response.headers.setdefault("Access-Control-Allow-Private-Network", "true")
    return response

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
        CREATE TABLE IF NOT EXISTS page_exposure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_cache_id INTEGER,
            page_url TEXT NOT NULL,
            page_title TEXT,
            source_type TEXT NOT NULL DEFAULT 'seen',      -- seen | highlighted | commented | imported
            evidence_level TEXT NOT NULL DEFAULT 'seen',   -- weak seen evidence, not user endorsement
            capture_reason TEXT,
            full_text_chars INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (page_cache_id) REFERENCES page_cache(id),
            UNIQUE(page_url, source_type, capture_reason)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page_exposure_page_cache ON page_exposure_events(page_cache_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page_exposure_source ON page_exposure_events(source_type, created_at DESC)")
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

    # ── Memory Intake Ledger V0：每条新增批注的本地/外部备份/growth 状态留痕 ──
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
            exposure_page_ids TEXT NOT NULL DEFAULT '[]',
            exposure_refs_json TEXT NOT NULL DEFAULT '[]',
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
        ("exposure_page_ids TEXT NOT NULL DEFAULT '[]'", "context_packs"),
        ("exposure_refs_json TEXT NOT NULL DEFAULT '[]'", "context_packs"),
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


def _local_backup_enabled() -> bool:
    return os.environ.get("MEMAI_LOCAL_BACKUP_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _local_backup_keep_count() -> int:
    try:
        return max(1, int(os.environ.get("MEMAI_BACKUP_KEEP", "14") or "14"))
    except Exception:
        return 14


def _list_local_backups(limit: int = 20) -> list:
    items = []
    if not os.path.isdir(LOCAL_BACKUP_DIR):
        return items
    for name in os.listdir(LOCAL_BACKUP_DIR):
        if not (name.startswith("comments-") and name.endswith(".db")):
            continue
        path = os.path.join(LOCAL_BACKUP_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append({
            "filename": name,
            "path": path,
            "size_bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items[:max(1, limit)]


def _db_integrity_check() -> dict:
    if not os.path.exists(DB_PATH):
        return {"ok": False, "result": "missing_db"}
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.DatabaseError:
            pass
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return {"ok": result == "ok", "result": result}
    finally:
        conn.close()


def _prune_local_backups():
    keep = _local_backup_keep_count()
    for item in _list_local_backups(limit=1000)[keep:]:
        try:
            os.remove(item["path"])
        except OSError:
            pass
        manifest_path = item["path"] + ".json"
        try:
            os.remove(manifest_path)
        except OSError:
            pass


def _ensure_local_backup(reason: str = "manual", min_interval_hours: int = 24, force: bool = False) -> dict:
    if not _local_backup_enabled():
        return {"enabled": False, "status": "disabled", "backup_dir": LOCAL_BACKUP_DIR}
    if not os.path.exists(DB_PATH):
        return {"enabled": True, "status": "missing_db", "backup_dir": LOCAL_BACKUP_DIR}

    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
    latest = (_list_local_backups(limit=1) or [None])[0]
    if latest and not force and min_interval_hours:
        age_hours = (datetime.now() - datetime.fromtimestamp(latest["mtime"])).total_seconds() / 3600
        if age_hours < min_interval_hours:
            return {
                "enabled": True,
                "status": "skipped_recent",
                "backup_dir": LOCAL_BACKUP_DIR,
                "latest_backup": latest,
                "age_hours": round(age_hours, 2),
            }

    integrity = _db_integrity_check()
    if not integrity.get("ok"):
        return {
            "enabled": True,
            "status": "failed_integrity",
            "backup_dir": LOCAL_BACKUP_DIR,
            "integrity": integrity,
        }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = os.path.join(LOCAL_BACKUP_DIR, f"comments-{stamp}.db")
    shutil.copy2(DB_PATH, backup_path)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "source_db": DB_PATH,
        "backup_db": backup_path,
        "integrity": integrity,
    }
    with open(backup_path + ".json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    _prune_local_backups()
    latest = (_list_local_backups(limit=1) or [None])[0]
    return {
        "enabled": True,
        "status": "created",
        "backup_dir": LOCAL_BACKUP_DIR,
        "latest_backup": latest,
        "integrity": integrity,
    }


def _local_backup_status(check_integrity: bool = False) -> dict:
    backups = _list_local_backups(limit=20)
    payload = {
        "enabled": _local_backup_enabled(),
        "backup_dir": LOCAL_BACKUP_DIR,
        "keep_count": _local_backup_keep_count(),
        "backup_count": len(backups),
        "latest_backup": backups[0] if backups else None,
    }
    if check_integrity:
        payload["integrity"] = _db_integrity_check()
    return payload


init_db()
_startup_backup_result = _ensure_local_backup("startup", min_interval_hours=24)

# ──────────────────────────────────────────
# 启动诊断日志（帮助新用户排查问题）
# ──────────────────────────────────────────

def _startup_check():
    llm = get_llm_status()
    notion_enabled_raw = (
        os.environ.get("MEMAI_NOTION_BACKUP_ENABLED")
        or os.environ.get("KB_NOTION_BACKUP_ENABLED")
        or "1"
    )
    notion_enabled = str(notion_enabled_raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}
    notion_ok = notion_enabled and bool(os.environ.get("KB_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN"))
    print(f"[agent_api] 数据目录: {DATA_DIR}")
    print(f"[agent_api] 数据库: {DB_PATH} ({'✓' if os.path.exists(DB_PATH) else '✗ 不存在'})")
    print(f"[agent_api] LLM provider: {llm.get('selected_provider') or '✗ 未配置'} ({llm.get('provider_config')})")
    print(f"[agent_api] Claude Code: {llm['claude_code'].get('bin')} ({'✓' if llm['claude_code'].get('available') else 'optional: 未找到'})")
    print(f"[agent_api] Codex CLI: {llm['codex_cli'].get('bin')} ({'✓' if llm['codex_cli'].get('available') else 'optional: 未找到'})")
    print(f"[agent_api] Notion 备份: {'✓ 已配置' if notion_ok else 'optional: 未开启'}")
    print(f"[agent_api] 本地备份: {_startup_backup_result.get('status')} ({LOCAL_BACKUP_DIR})")
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
    "Margin 产品迭代",
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


def _json_obj(raw) -> dict:
    try:
        obj = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _json_list(raw) -> list:
    try:
        obj = json.loads(raw or "[]") if isinstance(raw, str) else (raw or [])
        return obj if isinstance(obj, list) else []
    except Exception:
        return []


def _float_or(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


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


def _upsert_page_cache(conn: sqlite3.Connection, page_url: str, page_title: str,
                       full_text: str, now: str = None) -> Optional[int]:
    page_url = (page_url or "").strip()
    full_text = (full_text or "").strip()
    if not page_url or not full_text:
        return None
    now = now or _now_iso()
    existing = conn.execute(
        "SELECT id, length(COALESCE(full_text, '')) AS n FROM page_cache WHERE page_url=?",
        (page_url,),
    ).fetchone()
    if existing:
        page_cache_id = existing[0]
        existing_len = int(existing[1] or 0)
        if len(full_text) > existing_len:
            conn.execute(
                """UPDATE page_cache
                   SET page_title=COALESCE(NULLIF(?, ''), page_title),
                       full_text=?,
                       updated_at=?
                   WHERE id=?""",
                (page_title or "", full_text, now, page_cache_id),
            )
        elif page_title:
            conn.execute(
                "UPDATE page_cache SET page_title=COALESCE(NULLIF(page_title, ''), ?), updated_at=? WHERE id=?",
                (page_title, now, page_cache_id),
            )
        return int(page_cache_id)
    cur = conn.execute(
        """INSERT INTO page_cache (page_url, page_title, full_text, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (page_url, page_title or "", full_text, now, now),
    )
    return int(cur.lastrowid)


def _record_page_exposure(conn: sqlite3.Connection, page_url: str, page_title: str,
                          page_cache_id: Optional[int], source_type: str,
                          evidence_level: str, capture_reason: str,
                          full_text_chars: int, now: str = None) -> Optional[int]:
    page_url = (page_url or "").strip()
    if not page_url:
        return None
    now = now or _now_iso()
    source_type = source_type or "seen"
    evidence_level = evidence_level or source_type
    capture_reason = capture_reason or "unspecified"
    conn.execute(
        """INSERT INTO page_exposure_events
           (page_cache_id, page_url, page_title, source_type, evidence_level,
            capture_reason, full_text_chars, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(page_url, source_type, capture_reason)
           DO UPDATE SET
             page_cache_id=excluded.page_cache_id,
             page_title=excluded.page_title,
             full_text_chars=excluded.full_text_chars,
             updated_at=excluded.updated_at""",
        (
            page_cache_id,
            page_url,
            page_title or "",
            source_type,
            evidence_level,
            capture_reason,
            int(full_text_chars or 0),
            now,
            now,
        ),
    )
    row = conn.execute(
        """SELECT id FROM page_exposure_events
           WHERE page_url=? AND source_type=? AND capture_reason=?""",
        (page_url, source_type, capture_reason),
    ).fetchone()
    return int(row[0]) if row else None


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
    "ai", "产品", "项目", "用户", "系统", "研究", "影响", "调研", "问题", "方法",
    "后续", "实际", "真实", "当前", "应用", "评测", "哪些", "最近", "正在",
    "升温", "主题",
}
_CJK_STOP_SUBSTRINGS = {
    "哪些", "最近", "在做", "项目", "问题", "升温", "主题", "当前", "什么",
    "怎么", "如何", "是不是", "有没有",
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
        if "哪些" in run:
            continue
        if 2 <= len(run) <= 5:
            tokens.append(run)
        else:
            for size in (2, 3):
                for i in range(0, max(0, len(run) - size + 1)):
                    part = run[i:i + size]
                    if part not in _CJK_STOP_TOKENS and not any(s in part for s in _CJK_STOP_SUBSTRINGS):
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


def _snippet_for_tokens(text: str, tokens: list, chars: int = 420) -> str:
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return ""
    lowered = text.lower()
    hit = -1
    for token in tokens or []:
        if not token:
            continue
        idx = lowered.find(str(token).lower())
        if idx >= 0:
            hit = idx
            break
    if hit < 0:
        return text[:chars]
    start = max(0, hit - chars // 3)
    end = min(len(text), start + chars)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def _page_ref_line(row: dict) -> str:
    pid = row.get("id")
    date = (row.get("updated_at") or row.get("created_at") or "")[:10]
    title = (row.get("page_title") or "未命名")[:60]
    snippet = (row.get("snippet") or row.get("summary") or "").replace("\n", " ")[:360]
    source = row.get("page_url") or ""
    line = f"- [p#{pid}] {date} 《{title}》：{snippet}"
    if source:
        line += f"\n  来源：{source[:160]}"
    line += "\n  证据等级：exposure/seen，只代表系统有证据用户接触过该页面，不代表用户认同或记住。"
    return line


def _normalize_project_label(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "未命名项目候选"
    text = re.sub(r"\s+", " ", text)
    return text[:36]


def _infer_project_label(signal: dict, text: str, fallback: str = "") -> str:
    for key in ("project", "project_name", "name", "area", "workstream"):
        value = (signal.get(key) or "").strip()
        if value:
            return _normalize_project_label(value)
    return _normalize_project_label(fallback or text)


def _explicit_project_label(signal: dict) -> str:
    for key in ("project", "project_name", "name", "area", "workstream"):
        value = (signal.get(key) or "").strip()
        if value:
            return _normalize_project_label(value)
    return ""


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


_TOPIC_STOP_TERMS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "what", "why", "how",
    "openai", "anthropic", "google", "github", "medium", "substack", "youtube", "wikipedia",
    "show", "showcase", "moment",
    "页面", "这篇", "这个", "这些", "那些", "什么", "为什么", "怎么", "如何", "是否",
    "可以", "有没有", "是不是", "一下", "感觉", "其实", "但是", "因为", "所以",
    "评价一下", "总结一下", "解释一下", "分析一下", "调研一下", "介绍一下",
    "展开讲讲", "详细展开", "展开说说", "帮我看看", "给我看看",
}

_TOPIC_CONTAINER_TITLE_RE = re.compile(
    r"(^|\b)(daily|weekly|digest|newsletter|report|日报|周报|月报|快报|合集|云文档)(\b|$)",
    re.I,
)

_TOPIC_ACTION_ONLY_RE = re.compile(
    r"^(帮我|请|麻烦)?(评价|评估|总结|解释|分析|调研|介绍|展开|对比|讲讲|说说|看看)(一下|下|一下吧|一下吗|看看|吗|吧)?$"
)
_TOPIC_ACTION_PREFIX_RE = re.compile(
    r"^(帮我|请|麻烦|可以|能不能|可不可以|你能不能)?\s*"
    r"(评价|评估|总结|解释|分析|调研|介绍|展开|对比|讲讲|说说|看看|举例|举几个例子)"
    r"(一下|下|一下吧|一下吗|吧|吗|嘛)?(?:[，,：:\s]+|$)"
)
_TOPIC_QUESTION_MARKERS = (
    "为什么", "如何", "怎么", "是否", "是不是", "有没有", "能不能", "可不可以",
    "哪些", "哪里", "谁", "怎样", "什么", "how ", "why ", "what ", "which ",
)
_THOUGHT_SIGNAL_SOURCES = {"active_question", "theme_signal", "project_signal"}
_THOUGHT_USER_INTENT_SOURCES = {"comment", "comment_question", "selected_text"} | _THOUGHT_SIGNAL_SOURCES
_THOUGHT_SOURCE_OBJECT_SOURCES = {"page_title", "surrounding_text"}
_TOPIC_WEAK_FRAGMENT_TERMS = {
    "关注度高", "非常颠覆性", "颠覆性", "用户最嗨的点", "最爽的点", "用户反馈",
    "有哪些偏好", "有什么特点", "哪些特点", "什么特点",
}
_TOPIC_SHOWCASE_FRAGMENT_RE = re.compile(
    r"^(?:哪些)?show\s*case最(?:show)?$|^(?:哪些)?showcase最(?:show)?$",
    re.I,
)


def _topic_hash(label: str) -> str:
    return hashlib.sha1((label or "").encode("utf-8")).hexdigest()[:10]


def _topic_key(label: str) -> str:
    value = (label or "").lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"#[0-9]+", " ", value)
    value = re.sub(r"[\s\-_–—|:：,，.。!！?？;；/\\()\[\]{}<>《》「」“”\"'`]+", "", value)
    return value[:80]


def _clean_topic_label(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"(?i)\bshow\s+case\b", "showcase", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" \t\r\n-–—|:：,，.。!！?？;；/\\()[]{}<>《》「」“”\"'`")
    value = re.sub(r"^(请问|我想知道|我想问|能不能|可不可以|帮我看看|举几个例子吧)[，,：:\s]*", "", value)
    value = _TOPIC_ACTION_PREFIX_RE.sub("", value, count=1).strip()
    value = re.sub(r"^(?:[0-9]+|[一二三四五六七八九十]+)[、.．)\)]\s*", "", value).strip()
    value = re.sub(r"(是什么意思|是什么|有哪些|怎么办|为什么)$", "", value).strip()
    return value[:38]


def _is_topic_like(label: str) -> bool:
    value = _clean_topic_label(label)
    if len(value) < 3:
        return False
    if value.startswith(("的", "了", "和", "与")):
        return False
    key = _topic_key(value)
    if not key or key in _TOPIC_STOP_TERMS:
        return False
    lower = value.lower()
    if re.fullmatch(r"[a-z]{2,14}", lower):
        return False
    if _TOPIC_ACTION_ONLY_RE.fullmatch(value):
        return False
    if len(value) <= 8 and any(marker in value for marker in ("评价", "总结", "解释", "分析", "调研", "展开", "讲讲", "说说")):
        return False
    if value.startswith(("这些", "这个", "那个", "这件事", "背后")):
        return False
    if "这个事情" in value or "什么趋势" in value:
        return False
    if value in _TOPIC_WEAK_FRAGMENT_TERMS or _TOPIC_SHOWCASE_FRAGMENT_RE.fullmatch(value):
        return False
    if re.fullmatch(r"[0-9#.\-_/ ]+", value):
        return False
    return True


def _is_slug_or_date_title(label: str) -> bool:
    value = (label or "").strip().lower()
    if re.fullmatch(r"\d{4}[-_/]\d{2}[-_/]\d{2}(?:[-_][a-z0-9]+)?", value):
        return True
    if re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+){2,}", value):
        return True
    return False


def _split_title(title: str) -> list:
    title = (title or "").strip()
    if not title:
        return []
    if _TOPIC_CONTAINER_TITLE_RE.search(title):
        return []
    chunks = re.split(r"\s(?:[-–—|·•]\s|[|｜])", title)
    out = []
    for chunk in chunks[:3]:
        cleaned = _clean_topic_label(chunk)
        if _is_slug_or_date_title(cleaned):
            continue
        if _is_topic_like(cleaned):
            out.append(cleaned)
    return out


def _extract_named_terms(text: str, limit: int = 8) -> list:
    text = (text or "").replace("\n", " ")
    out = []
    for match in re.findall(r"[《「“\"]([^《》「」“”\"]{3,38})[》」”\"]", text):
        cleaned = _clean_topic_label(match)
        if _is_topic_like(cleaned) and cleaned not in out:
            out.append(cleaned)
    for match in re.findall(r"\b[A-Z][A-Za-z0-9*+.#/-]{1,}(?:\s+[A-Z][A-Za-z0-9*+.#/-]{1,}){0,3}\b", text):
        cleaned = _clean_topic_label(match)
        if _is_topic_like(cleaned) and cleaned.lower() not in _TOPIC_STOP_TERMS and cleaned not in out:
            out.append(cleaned)
    for match in re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9 +#·/-]{3,28}", text):
        cleaned = _clean_topic_label(match)
        if _is_topic_like(cleaned) and cleaned not in out:
            out.append(cleaned)
    return out[:limit]


def _extract_question_phrases(text: str, limit: int = 3) -> list:
    text = (text or "").replace("---追问---", "\n")
    out = []
    for raw in re.split(r"[。！？!?；;\n]+", text):
        raw = raw.strip()
        if not raw:
            continue
        lower = raw.lower()
        if not any(marker in lower for marker in _TOPIC_QUESTION_MARKERS):
            continue
        for chunk in re.split(r"[，,：:]", raw):
            cleaned = _clean_topic_label(chunk)
            if _is_topic_like(cleaned) and cleaned not in out:
                out.append(cleaned)
                break
        if len(out) >= limit:
            break
    return out


def _compact_named_subject(text: str) -> str:
    for term in _extract_named_terms(text, limit=6):
        cleaned = _clean_topic_label(term)
        if _is_topic_like(cleaned):
            return cleaned[:26]
    return ""


def _extract_showcase_subject(text: str) -> str:
    source = (text or "").replace("\n", " ")
    patterns = [
        r"\b([A-Z][A-Za-z0-9*+.#/-]{2,})\s+(?:show\s*case|showcase)\b",
        r"\b(?:show\s*case|showcase)\s*[-–—|:：]\s*([A-Z][A-Za-z0-9*+.#/-]{2,})\b",
        r"\b([A-Za-z][A-Za-z0-9*+.#/-]{2,})\s*的\s*(?:show\s*case|showcase)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, source, re.I)
        if not match:
            continue
        subject = _clean_topic_label(match.group(1))
        if subject and subject.lower() not in _TOPIC_STOP_TERMS:
            return subject[:24]
    return ""


def _refine_thought_label(label: str, row: dict, source: str) -> str:
    cleaned = _clean_topic_label(label)
    comment = row.get("comment") or ""
    selected = row.get("selected_text") or ""
    title = row.get("page_title") or ""
    text = " ".join([cleaned, comment, selected, title])
    lower = text.lower()

    if "showcase" in lower:
        subject = _extract_showcase_subject(" ".join([title, selected, comment, cleaned]))
        if subject and any(marker in text for marker in ("调研", "哪些", "趋势", "偏好", "渠道", "特点", "关注度")):
            return f"{subject} showcase 生态调研"[:38]
    if any(marker in text for marker in ("论文", "方法论", "范式", "读法")) and any(
        marker in text for marker in ("复用", "迁移", "研究", "介绍")
    ):
        return "研究方法论复用"
    if any(marker in text for marker in ("社区", "共同体", "活动", "参与", "组织")) and any(
        marker in text for marker in ("机制", "经验", "入口", "项目", "实践")
    ):
        return "共同体实践与参与路径"
    if any(marker in text for marker in ("视频", "展示", "demo", "录制")) and any(
        marker in text for marker in ("产品", "工具", "表达", "发布")
    ):
        return "产品展示表达任务"
    if any(marker in lower for marker in ("benchmark", "metric")) or any(marker in text for marker in ("评测", "指标", "验证")):
        if any(marker in text for marker in ("产品", "采用", "真实", "决策", "目标", "结果")):
            return "评测证据如何影响真实决策"
    if "后续" in text and ("影响" in text or "衍生" in text or "项目" in text):
        subject = _compact_named_subject(" ".join([selected, title, comment]))
        return f"{subject}的后续影响"[:38] if subject else "研究项目的后续影响"
    similar_match = re.search(r"(?:有哪些类似|类似)\s*([^，,。？?]{3,34})", cleaned)
    if similar_match and ("参加" in text or "活动" in text):
        subject = _clean_topic_label(similar_match.group(1))
        return f"类似 {subject} 的活动与参与路径"[:38]
    if cleaned.startswith(("这些", "这个", "那个", "这件事", "背后")) or "背后是什么" in cleaned:
        if any(marker in lower for marker in ("model", "gpt", "claude", "llm")) or "模型" in text:
            return "模型现实任务表现差异原因"
        subject = _compact_named_subject(" ".join([selected, title]))
        return f"{subject} 的原因追问"[:38] if subject else cleaned
    return cleaned


def _extract_thought_candidates(row: dict) -> list:
    candidates = []

    def add(label: str, source: str, weight: float) -> None:
        cleaned = _refine_thought_label(label, row, source)
        if not _is_topic_like(cleaned):
            return
        key = _topic_key(cleaned)
        if any(item["key"] == key for item in candidates):
            return
        candidates.append({"label": cleaned, "key": key, "source": source, "weight": weight})

    direct_comment = (row.get("comment") or "").split("---追问---", 1)[0]
    for phrase in _extract_question_phrases(direct_comment, limit=2):
        add(phrase, "comment_question", 1.18)
    for term in _extract_named_terms(direct_comment, limit=4):
        add(term, "comment", 1.05)
    for term in _extract_named_terms(row.get("selected_text") or "", limit=4):
        add(term, "selected_text", 0.9)
    for label in _split_title(row.get("page_title") or ""):
        add(label, "page_title", 0.55)
    for term in _extract_named_terms(row.get("surrounding_text") or "", limit=2):
        add(term, "surrounding_text", 0.45)
    return candidates[:4]


_THOUGHT_ACTION_MARKERS = [
    ("我要试试", "明确试用意图", 1.0),
    ("我正需要", "明确需求", 1.0),
    ("正需要", "明确需求", 0.9),
    ("开始执行", "推动执行", 0.9),
    ("开始研究", "推动研究", 0.8),
    ("详细调研", "要求深入调研", 0.8),
    ("调研文档", "要求沉淀文档", 0.7),
    ("以后", "形成工作方式", 0.65),
    ("固定", "形成工作方式", 0.65),
    ("请给我", "明确请求产出", 0.55),
    ("展开", "要求展开", 0.5),
    ("请对比", "要求比较", 0.45),
    ("详细对比", "要求比较", 0.45),
    ("路线", "要求路线判断", 0.45),
]

_THOUGHT_CURIOSITY_MARKERS = [
    ("这是啥", "解释型提问"),
    ("是什么", "解释型提问"),
    ("没听懂", "解释型提问"),
    ("有点没看懂", "解释型提问"),
    ("好像", "不确定探索"),
    ("顺带", "顺手提问"),
]

_THOUGHT_CORRECTION_MARKERS = [
    ("不对", "纠正 AI"),
    ("不是", "纠正 AI"),
    ("没用", "纠正 AI"),
    ("质量", "质量反馈"),
    ("我的预期", "明确预期"),
    ("应该", "修正方向"),
]


def _thought_behavior_signal(row: dict) -> dict:
    comment = row.get("comment") or ""
    score = 0.12
    labels = []
    action_hit = False
    for marker, label, weight in _THOUGHT_ACTION_MARKERS:
        if marker.lower() in comment.lower():
            score += weight
            labels.append(label)
            action_hit = True
    for marker, label in _THOUGHT_CORRECTION_MARKERS:
        if marker in comment:
            score += 0.22
            labels.append(label)
    curiosity_hit = False
    for marker, label in _THOUGHT_CURIOSITY_MARKERS:
        if marker.lower() in comment.lower():
            score += 0.16
            labels.append(label)
            curiosity_hit = True
    followups = comment.count("---追问---")
    if followups:
        score += min(0.75, followups * 0.28)
        labels.append(f"{followups} 次追问")
    question_marks = comment.count("？") + comment.count("?")
    if question_marks and not action_hit:
        score += min(0.22, question_marks * 0.08)
        labels.append("提问")
    if curiosity_hit and not action_hit and followups == 0:
        score = min(score, 0.48)
    labels = list(dict.fromkeys(labels))
    return {
        "score": round(min(1.0, score), 3),
        "labels": labels[:4],
        "action_hit": action_hit,
        "curiosity_hit": curiosity_hit,
        "followups": followups,
    }


def _score_level(score: float) -> str:
    if score >= 0.67:
        return "高"
    if score >= 0.34:
        return "中"
    return "低"


def _thought_card_interpretation(topic_label: str, row: dict, source: str, behavior: dict) -> str:
    source_text = {
        "page_title": "页面标题",
        "comment": "你的评论",
        "selected_text": "划线原文",
        "surrounding_text": "划线上下文",
    }.get(source or "", "本地证据")
    if behavior.get("labels"):
        return f"这条被纳入「{topic_label}」，因为{source_text}里出现了这条线索，并带有「{'、'.join(behavior['labels'])}」这类行为信号。"
    return f"这条被纳入「{topic_label}」，因为{source_text}里出现了这条线索；是否稳定，还需要更多后续证据确认。"


def _thought_possible_misread(evidence_count: int, behavior_scores: list) -> str:
    avg = sum(behavior_scores) / max(1, len(behavior_scores))
    if evidence_count <= 2 and avg < 0.45:
        return "证据还少，可能只是顺手提问，不能过早定性为稳定兴趣。"
    return "这是系统基于本地批注的当前推断；如果用户确认、改名或合并，可信度会更高。"


def _thought_source_object_only(source_counts: dict, action_count: int) -> bool:
    total = sum(int(v or 0) for v in (source_counts or {}).values())
    if total <= 0:
        return False
    source_object = sum(int(source_counts.get(k) or 0) for k in _THOUGHT_SOURCE_OBJECT_SOURCES)
    user_intent = sum(int(source_counts.get(k) or 0) for k in _THOUGHT_USER_INTENT_SOURCES)
    return source_object >= total and user_intent == 0 and action_count == 0


def _looks_entity_only_topic(label: str) -> bool:
    value = (label or "").strip()
    if re.fullmatch(r"[A-Z][A-Za-z0-9*+.#/-]{1,}(?:\s+[A-Z][A-Za-z0-9*+.#/-]{1,}){0,3}", value):
        return True
    if re.fullmatch(r"[A-Z][A-Za-z0-9*+.#/-]{1,}", value):
        return True
    return False


def _thought_read_for_node(label: str, lane: str, trend: str, evidence_count: int, source_counts: dict,
                           action_count: int, distinct_day_count: int) -> str:
    if source_counts.get("active_question"):
        return f"这更像一个你正在推进的未解问题：{label}。之后相关回复会优先带入这条问题线索，而不是只解释当前页面。"
    if source_counts.get("theme_signal"):
        return f"这是一条从多次本地批注里升起的关注面：{label}。它会作为背景主题参与后续上下文选择。"
    if source_counts.get("project_signal"):
        return f"这来自本机明确项目/工作流信号：{label}。系统会把它当作候选工作容器，而不是用户确认的长期档案。"
    if _thought_source_object_only(source_counts, action_count):
        return f"目前「{label}」更像你接触过的资料对象或页面线索；先不提升为项目，只在相关问题出现时作为背景。"
    if lane == "mainline":
        return f"这条线索在本机批注里最集中：{label}。后续回复应优先检查它是否和当前问题有关，避免把问题当成孤立提问。"
    if trend == "rising" or lane in ("sprout", "merging"):
        return f"最近围绕「{label}」的证据在升温。系统会继续观察它是否变成稳定问题线，而不是马上写死。"
    if lane == "cooling":
        return f"「{label}」仍有历史证据，但近期出现变少。它会降为背景，不主动抢占解释方向。"
    if evidence_count <= 2 or distinct_day_count <= 1:
        return f"「{label}」现在还是一条较弱线索，可能只是一次顺手提问；需要更多本地证据确认。"
    return f"本机多次批注都碰到「{label}」。它会作为候选主题参与后续回复的上下文排序。"


def _thought_label_contains(node: dict, *needles: str) -> bool:
    label = (node or {}).get("label") or ""
    lower = label.lower()
    return any((needle.lower() in lower if re.fullmatch(r"[A-Za-z0-9 _/-]+", needle) else needle in label) for needle in needles)


def _thought_aha(main: dict, emerging: list, cooling: list, nodes: list) -> dict:
    if not nodes:
        return {
            "headline": "现在还不是总结你的时候",
            "read": "本机证据太少。系统先保留原始批注，不把一次性兴趣包装成长期主题。",
            "next_question": "先继续正常批注；等同一类问题跨几天反复出现，再生成更强判断。",
            "evidence_comment_ids": [],
        }

    lead = main or (emerging[0] if emerging else nodes[0])
    lead_label = lead.get("label") or "当前问题"
    rising_labels = [n.get("label") for n in emerging if n.get("label")]
    evidence_ids = []
    for node in [lead] + list(emerging or [])[:2]:
        for cid in node.get("evidence_comment_ids") or []:
            if cid and cid not in evidence_ids:
                evidence_ids.append(cid)

    all_focus = [lead] + list(emerging or [])
    has_eval = any(_thought_label_contains(n, "评测", "benchmark", "产品判断") for n in all_focus)
    has_reading = any(_thought_label_contains(n, "读论文", "论文方法论", "方法论复用") for n in all_focus)
    has_video = any(_thought_label_contains(n, "视频", "展示") for n in all_focus)
    has_social = any(_thought_label_contains(n, "社会技术", "活动参与", "附近理论", "双年展") for n in all_focus)
    has_model_reason = any(_thought_label_contains(n, "模型现实任务", "差异原因") for n in all_focus)

    if has_eval and has_reading:
        headline = "你不是在收集论文和评测，而是在找一套判断产品是否真的变好的方法"
        read = "反复出现的不是某篇论文本身，而是“指标、任务、用户价值、真实采用”之间怎样连起来。读论文、看 benchmark、追问后续影响，其实都在服务同一个判断：一个系统怎样证明自己不是看起来很强，而是真的让产品变好。"
        next_question = "下一步最值得问：这些案例里，哪一种证据真正改变了产品决策，哪一种只是论文里的漂亮指标？"
    elif has_eval:
        headline = f"你围着「{lead_label}」转，核心不是资料更多，而是证据能不能改变判断"
        read = "这些批注的共同点是：你不满足于官方叙事或单页解释，会追问真实应用、后续采用和评测是否改变了实践。系统后续应该优先帮你找“证据链”，而不是只补背景知识。"
        next_question = "下一步最值得问：这个评测或指标有没有进入真实产品、团队决策或行业标准？"
    elif has_social:
        headline = "你最近的社会技术兴趣，落点已经从“这是什么”转向“我怎么参与并复用它”"
        read = "Burning Man、黑客营地、附近研究、双年展这些表面上很散，但底层都在问同一件事：理念如何变成可参与的场域、组织经验和真实产物。"
        next_question = "下一步最值得问：这些场域留下了哪些可复用的组织方法，以及你今年能从哪个入口实际参与？"
    elif has_video:
        headline = "这个新线索更像一个马上要执行的产品表达任务"
        read = "围绕视频、展示和工具选择的批注不是长期研究主题，而是当下要把产品讲清楚的执行问题。系统应该帮你压缩选择成本，而不是展开成泛泛的工具榜单。"
        next_question = "下一步最值得问：你的产品视频第一版到底要证明什么，是功能、使用场景，还是用户看到后的行动？"
    elif has_model_reason:
        headline = "你在追问模型表现差异背后的机制，而不只是要例子"
        read = "这些问题表面是分类解释或例子补全，底层是在判断：不同模型、不同任务形态为什么会表现不同，以及这种差异对产品设计意味着什么。"
        next_question = "下一步最值得问：哪些任务差异来自模型能力，哪些其实来自任务定义和反馈循环设计？"
    elif main and rising_labels:
        headline = f"主线是「{lead_label}」，但真正的新变化在「{rising_labels[0]}」"
        read = "稳定主线提供背景，新升温线索说明你最近的注意力正在换挡。系统后续应该把新问题放进旧主线里判断，而不是把它当成孤立兴趣。"
        next_question = f"下一步最值得问：新出现的「{rising_labels[0]}」是在延伸主线，还是在开启一条新的工作线？"
    else:
        headline = f"现在最清楚的线索是「{lead_label}」，但还不能写死成长期画像"
        read = "证据已经足够形成一个候选问题，但跨天持续性或行动信号还不够强。系统会先把它当作下一次回复的上下文候选，而不是替用户下结论。"
        next_question = f"下一步最值得问：你继续追这个问题时，是想要背景解释、行动方案，还是判断框架？"

    if cooling:
        cooling_label = cooling[0].get("label") or ""
        if cooling_label:
            read += f" 同时「{cooling_label}」在降温，说明它更适合作为背景，不该再抢占顶部解释。"

    return {
        "headline": headline,
        "read": read,
        "next_question": next_question,
        "evidence_comment_ids": evidence_ids[:8],
    }


def _dynamic_thought_lane(rank: int, evidence_count: int, recent: int, previous: int, older: int, action_count: int, distinct_day_count: int, span_days: int, intent_strength: str) -> str:
    if recent == 0 and (previous > 0 or older > 0):
        return "cooling"
    persistent = evidence_count >= 4 and (span_days >= 7 or distinct_day_count >= 3)
    if persistent and (rank <= 5 or span_days >= 14 or distinct_day_count >= 5):
        return "mainline"
    if recent > 0 and older == 0 and (span_days < 3 or distinct_day_count <= 2):
        return "sprout"
    if recent > previous and (older > 0 or distinct_day_count >= 2):
        return "merging"
    if evidence_count <= 1 and intent_strength != "高":
        return "occasional"
    if span_days >= 7 or distinct_day_count >= 2:
        return "branch"
    return "occasional"


def _resolve_thought_window(rows: list, requested_days: Optional[int], now: datetime) -> tuple[int, str, str]:
    if requested_days is not None:
        days = max(14, min(int(requested_days or 42), 180))
        return days, "requested", f"过去 {days} 天"
    dates = [_parse_dt(r.get("created_at")) for r in rows]
    dates = [d for d in dates if d]
    if not dates:
        return 14, "auto", "暂无本地批注"
    oldest = min(dates)
    newest = max(dates)
    span_days = max(1, (newest.date() - oldest.date()).days + 1)
    days = max(14, min((now.date() - oldest.date()).days + 1, 180))
    if span_days <= days:
        return days, "auto", f"本机全部批注 · 覆盖 {span_days} 天"
    return days, "auto", f"本机最近 {days} 天批注"


def _build_thought_map(conn: sqlite3.Connection, days: Optional[int] = None) -> dict:
    """Build a local, evidence-backed thought map from this computer's SQLite comments."""
    conn.row_factory = sqlite3.Row
    now = datetime.now()
    rows = [dict(r) for r in conn.execute(
        """SELECT id, created_at, page_url, page_title, selected_text, surrounding_text, comment
           FROM comments
           ORDER BY created_at DESC
           LIMIT 800"""
    ).fetchall()]
    days, window_mode, window_label = _resolve_thought_window(rows, days, now)
    window_start = now - timedelta(days=days)
    rows = [r for r in rows if (_parse_dt(r.get("created_at")) or now) >= window_start]
    bucket_count = 6
    bucket_days = max(1, days // bucket_count)
    clusters = {}
    rejected_candidates = []

    def add_cluster_candidate(row: dict, candidate: dict, behavior: dict, extra_score: float = 0.0,
                              interpretation: str = "") -> None:
        source = candidate.get("source") or "local_evidence"
        cleaned_label = _refine_thought_label(candidate.get("label") or "", row, source)
        if not _is_topic_like(cleaned_label):
            rejected_candidates.append({
                "label": candidate.get("label") or "",
                "source": source,
                "reason": "not_topic_like",
            })
            return
        if source not in _THOUGHT_SIGNAL_SOURCES and _looks_entity_only_topic(cleaned_label) and not behavior.get("action_hit") and not behavior.get("followups"):
            rejected_candidates.append({
                "label": cleaned_label,
                "source": source,
                "reason": "entity_only_without_intent",
            })
            return
        key = candidate.get("key") or _topic_key(cleaned_label)
        key = _topic_key(cleaned_label)
        if not key:
            return
        cluster = clusters.setdefault(key, {
            "label": cleaned_label,
            "label_weight": 0,
            "score": 0.0,
            "evidence": [],
            "buckets": [0] * bucket_count,
            "behavior_scores": [],
            "distinct_days": set(),
            "source_counts": {},
            "seen_evidence_keys": set(),
        })
        label_weight = float(candidate.get("weight") or 0)
        if label_weight > cluster["label_weight"]:
            cluster["label"] = cleaned_label
            cluster["label_weight"] = label_weight
        weighted_score = label_weight * (0.65 + _float_or(behavior.get("score"))) + extra_score
        cluster["score"] += weighted_score

        dt = _parse_dt(row.get("created_at") or row.get("signal_created_at")) or now
        cluster["distinct_days"].add(dt.date().isoformat())
        age_days = max(0, (now - dt).days)
        bucket_idx = bucket_count - 1 - min(bucket_count - 1, age_days // bucket_days)
        cluster["buckets"][bucket_idx] += 1
        cluster["behavior_scores"].append(_float_or(behavior.get("score")))
        cluster["source_counts"][source] = cluster["source_counts"].get(source, 0) + 1

        evidence_key = (row.get("id") or row.get("comment_id") or row.get("signal_id"), source, key)
        if evidence_key in cluster["seen_evidence_keys"]:
            return
        cluster["seen_evidence_keys"].add(evidence_key)
        cluster["evidence"].append({
            "id": row.get("id") or row.get("comment_id"),
            "created_at": row.get("created_at") or row.get("signal_created_at"),
            "page_title": row.get("page_title") or "",
            "page_url": row.get("page_url") or "",
            "selected_text": (row.get("selected_text") or "")[:220],
            "comment": (row.get("comment") or "")[:360],
            "raw_comment": row.get("comment") or "",
            "matched_terms": [cleaned_label],
            "behavior": behavior,
            "interpretation": interpretation or _thought_card_interpretation(cleaned_label, row, source, behavior),
        })

    for row in rows:
        behavior = _thought_behavior_signal(row)
        candidates = _extract_thought_candidates(row)
        for candidate in candidates:
            add_cluster_candidate(row, candidate, behavior)

    cutoff = window_start.isoformat()
    signal_limit = 160
    active_question_rows = [dict(r) for r in conn.execute(
        """SELECT aq.id AS signal_id, aq.comment_id, aq.question, aq.signal_strength,
                  aq.created_at AS signal_created_at,
                  c.id, c.created_at, c.page_url, c.page_title, c.selected_text, c.surrounding_text, c.comment
           FROM active_question_signals aq
           LEFT JOIN comments c ON c.id = aq.comment_id
           WHERE aq.status='active'
             AND COALESCE(c.created_at, aq.created_at) >= ?
           ORDER BY aq.created_at DESC
           LIMIT ?""",
        (cutoff, signal_limit),
    ).fetchall()]
    for row in active_question_rows:
        label = _clean_topic_label(row.get("question") or "")
        behavior = _thought_behavior_signal(row)
        strength = _float_or(row.get("signal_strength"))
        behavior["score"] = max(_float_or(behavior.get("score")), min(1.0, 0.42 + strength * 0.58))
        behavior["labels"] = list(dict.fromkeys((behavior.get("labels") or []) + ["active question"]))
        add_cluster_candidate(
            row,
            {"label": label, "key": _topic_key(label), "source": "active_question", "weight": 1.42 + strength * 0.35},
            behavior,
            extra_score=0.35 + strength * 0.35,
            interpretation=f"这条来自本机 memory growth 抽出的 active question：{label}。",
        )

    theme_rows = [dict(r) for r in conn.execute(
        """SELECT ts.id AS signal_id, ts.comment_id, ts.theme, ts.intensity, ts.evidence_count,
                  ts.created_at AS signal_created_at,
                  c.id, c.created_at, c.page_url, c.page_title, c.selected_text, c.surrounding_text, c.comment
           FROM theme_signals ts
           LEFT JOIN comments c ON c.id = ts.comment_id
           WHERE COALESCE(c.created_at, ts.created_at) >= ?
           ORDER BY ts.created_at DESC
           LIMIT ?""",
        (cutoff, signal_limit),
    ).fetchall()]
    for row in theme_rows:
        label = _clean_topic_label(row.get("theme") or "")
        behavior = _thought_behavior_signal(row)
        intensity = _float_or(row.get("intensity"))
        behavior["score"] = max(_float_or(behavior.get("score")), min(1.0, 0.34 + intensity * 0.58))
        behavior["labels"] = list(dict.fromkeys((behavior.get("labels") or []) + ["theme signal"]))
        add_cluster_candidate(
            row,
            {"label": label, "key": _topic_key(label), "source": "theme_signal", "weight": 1.22 + intensity * 0.28},
            behavior,
            extra_score=0.22 + intensity * 0.28,
            interpretation=f"这条来自本机 memory growth 抽出的主题信号：{label}。",
        )

    project_rows = [dict(r) for r in conn.execute(
        """SELECT ps.id AS signal_id, ps.comment_id, ps.signal_json, ps.confidence,
                  ps.created_at AS signal_created_at,
                  c.id, c.created_at, c.page_url, c.page_title, c.selected_text, c.surrounding_text, c.comment
           FROM project_signals ps
           LEFT JOIN comments c ON c.id = ps.comment_id
           WHERE COALESCE(c.created_at, ps.created_at) >= ?
           ORDER BY ps.created_at DESC
           LIMIT ?""",
        (cutoff, signal_limit),
    ).fetchall()]
    for row in project_rows:
        signal = _json_obj(row.get("signal_json"))
        label = _explicit_project_label(signal)
        if not label:
            continue
        behavior = _thought_behavior_signal(row)
        confidence = _float_or(row.get("confidence"), _float_or(signal.get("confidence")))
        behavior["score"] = max(_float_or(behavior.get("score")), min(1.0, 0.38 + confidence * 0.48))
        behavior["labels"] = list(dict.fromkeys((behavior.get("labels") or []) + ["project signal"]))
        add_cluster_candidate(
            row,
            {"label": label, "key": _topic_key(label), "source": "project_signal", "weight": 1.1 + confidence * 0.22},
            behavior,
            extra_score=0.16 + confidence * 0.22,
            interpretation=f"这条来自本机明确项目/工作流信号：{label}。",
        )

    cluster_items = list(clusters.values())
    cluster_items.sort(key=lambda c: (
        -c["score"],
        -len(c["evidence"]),
        max((e.get("created_at") or "" for e in c["evidence"]), default=""),
    ))
    nodes = []
    for rank, cluster in enumerate(cluster_items[:24]):
        raw_evidence = cluster["evidence"]
        distinct_days = cluster["distinct_days"]
        if not raw_evidence:
            continue
        deduped = {}
        for item in sorted(
            raw_evidence,
            key=lambda e: (e["behavior"]["score"], e.get("created_at") or ""),
            reverse=True,
        ):
            evidence_key = item.get("id") or (
                item.get("page_url") or "",
                item.get("created_at") or "",
                (item.get("raw_comment") or item.get("comment") or "")[:160],
            )
            if evidence_key not in deduped:
                deduped[evidence_key] = item
        evidence = list(deduped.values())
        evidence.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        buckets = [0] * bucket_count
        for item in evidence:
            dt = _parse_dt(item.get("created_at")) or now
            age_days = max(0, (now - dt).days)
            bucket_idx = bucket_count - 1 - min(bucket_count - 1, age_days // bucket_days)
            buckets[bucket_idx] += 1
        behavior_scores = [e["behavior"]["score"] for e in evidence]
        recent = buckets[-1]
        previous = buckets[-2] if len(buckets) >= 2 else 0
        older = sum(buckets[:-2])
        first_seen = min(e.get("created_at") or "" for e in evidence)
        last_seen = max(e.get("created_at") or "" for e in evidence)
        span_days = max(0, ((_parse_dt(last_seen) or now) - (_parse_dt(first_seen) or now)).days)
        distinct_day_count = len(distinct_days)
        recent_behavior = [e["behavior"]["score"] for e in evidence if (_parse_dt(e.get("created_at")) or now) >= now - timedelta(days=7)]
        action_count = sum(1 for e in evidence if e["behavior"].get("action_hit"))
        followup_count = sum(int(e["behavior"].get("followups") or 0) for e in evidence)
        avg_behavior = sum(behavior_scores) / max(1, len(behavior_scores))
        recent_behavior_sum = sum(recent_behavior)
        source_counts = cluster["source_counts"]
        signal_source_count = sum(int(source_counts.get(k) or 0) for k in _THOUGHT_SIGNAL_SOURCES)
        user_intent_source_count = sum(int(source_counts.get(k) or 0) for k in _THOUGHT_USER_INTENT_SOURCES)
        source_object_only = _thought_source_object_only(source_counts, action_count)
        confidence_score = min(1.0, len(evidence) / 10 * 0.42 + distinct_day_count / 5 * 0.34 + (0.24 if span_days >= 14 else 0))
        max_behavior = max(behavior_scores or [0])
        intensity_score = min(1.0, max_behavior * 0.44 + recent_behavior_sum / 4 * 0.25 + action_count / 3 * 0.23 + followup_count / 4 * 0.08)
        if action_count and recent > 0:
            intensity_score = max(intensity_score, 0.72)
        if signal_source_count:
            confidence_score = max(confidence_score, min(0.92, 0.46 + signal_source_count * 0.1))
            intensity_score = max(intensity_score, min(0.9, 0.52 + signal_source_count * 0.08))
        if source_object_only:
            confidence_score = min(confidence_score, 0.42)
            intensity_score = min(intensity_score, 0.32)
        persistence_score = min(1.0, span_days / 28 * 0.42 + distinct_day_count / 6 * 0.38 + len(evidence) / 12 * 0.2)
        role_centrality = min(0.9, 0.32 + len(evidence) / 10 * 0.26 + distinct_day_count / 5 * 0.22 + (0.1 if action_count else 0))
        centrality_score = min(1.0, role_centrality + (0.12 if recent >= 3 else 0) + (0.08 if action_count else 0))
        if source_object_only:
            centrality_score = min(centrality_score, 0.38)
        confidence = _score_level(confidence_score)
        intent_strength = _score_level(intensity_score)
        persistence = _score_level(persistence_score)
        centrality = _score_level(centrality_score)

        lane = _dynamic_thought_lane(rank, len(evidence), recent, previous, older, action_count, distinct_day_count, span_days, intent_strength)
        if source_object_only:
            lane = "occasional"
        elif signal_source_count and lane == "occasional" and (recent > 0 or distinct_day_count >= 2):
            lane = "sprout"
        elif user_intent_source_count == 0 and lane in ("mainline", "merging"):
            lane = "branch"
        if recent > previous:
            trend = "rising"
        elif recent == 0 and (previous > 0 or older > 0):
            trend = "cooling"
        elif recent > 0 and previous == 0 and older == 0:
            trend = "new"
        else:
            trend = "steady"
        if lane == "cooling" and len(evidence) < 3:
            continue
        evidence_for_display = sorted(
            evidence,
            key=lambda e: (e["behavior"]["score"], e.get("created_at") or ""),
            reverse=True,
        )
        evidence_comment_ids = []
        for e in evidence_for_display:
            cid = e.get("id")
            if cid and cid not in evidence_comment_ids:
                evidence_comment_ids.append(cid)
        label = cluster["label"]
        read = _thought_read_for_node(
            label,
            lane,
            trend,
            len(evidence),
            source_counts,
            action_count,
            distinct_day_count,
        )
        nodes.append({
            "id": f"topic-{_topic_hash(label)}",
            "label": label,
            "lane": lane,
            "trend": trend,
            "read": read,
            "why_it_matters": read,
            "possible_misread": _thought_possible_misread(len(evidence), behavior_scores),
            "intent_strength": intent_strength,
            "confidence": confidence,
            "persistence": persistence,
            "centrality": centrality,
            "score_values": {
                "intent": round(intensity_score, 3),
                "confidence": round(confidence_score, 3),
                "persistence": round(persistence_score, 3),
                "centrality": round(centrality_score, 3),
                "avg_behavior": round(avg_behavior, 3),
            },
            "source_counts": dict(source_counts),
            "evidence_count": len(evidence),
            "recent_count": recent,
            "action_count": action_count,
            "distinct_day_count": distinct_day_count,
            "span_days": span_days,
            "first_seen_at": first_seen,
            "last_seen_at": last_seen,
            "sparkline": buckets,
            "evidence_comment_ids": evidence_comment_ids[:8],
            "evidence": evidence_for_display[:5],
        })
        if len(nodes) >= 10:
            break

    lane_order = {"mainline": 0, "merging": 1, "branch": 2, "sprout": 3, "occasional": 4, "cooling": 5}
    nodes.sort(key=lambda n: (
        lane_order.get(n["lane"], 9),
        -_float_or((n.get("score_values") or {}).get("intent")),
        -int(n.get("recent_count") or 0),
        -int(n.get("evidence_count") or 0),
        n.get("last_seen_at") or "",
    ))
    lanes = {
        "mainline": [n for n in nodes if n["lane"] == "mainline"],
        "merging": [n for n in nodes if n["lane"] == "merging"],
        "branch": [n for n in nodes if n["lane"] == "branch"],
        "sprout": [n for n in nodes if n["lane"] == "sprout"],
        "occasional": [n for n in nodes if n["lane"] == "occasional"],
        "cooling": [n for n in nodes if n["lane"] == "cooling"],
    }
    main = (lanes["mainline"] or [{}])[0]
    emerging = (lanes["sprout"] or lanes["merging"] or lanes["branch"] or [])[:2]
    cooling = lanes["cooling"][:2]
    observation = "我只基于这台电脑的本地批注生成思考地图；当前证据还不足以形成稳定主线。"
    if main:
        observation = f"我看到本机批注里最集中的线索是「{main.get('label')}」。"
        if emerging:
            observation += " 最近升温的是 " + "、".join(f"「{n['label']}」" for n in emerging) + "。"
        if cooling:
            observation += " 有些旧线索开始降温：" + "、".join(f"「{n['label']}」" for n in cooling) + "。"
    elif emerging:
        observation = "我还没有看到足够稳定的主线；最近新出现或升温的是 " + "、".join(f"「{n['label']}」" for n in emerging) + "。"
        if cooling:
            observation += " 同时，一些旧线索在降温：" + "、".join(f"「{n['label']}」" for n in cooling) + "。"
    aha = _thought_aha(main, emerging, cooling, nodes)
    return {
        "generated_at": _now_iso(),
        "window_days": days,
        "window_mode": window_mode,
        "window_label": window_label,
        "bucket_days": bucket_days,
        "observation": observation,
        "aha": aha,
        "stats": {
            "node_count": len(nodes),
            "mainline_count": len(lanes["mainline"]),
            "branch_count": len(lanes["branch"]),
            "sprout_count": len(lanes["sprout"]),
            "occasional_count": len(lanes["occasional"]),
            "cooling_count": len(lanes["cooling"]),
            "rejected_candidate_count": len(rejected_candidates),
            "signal_backed_count": sum(1 for n in nodes if any((n.get("source_counts") or {}).get(k) for k in _THOUGHT_SIGNAL_SOURCES)),
        },
        "lanes": lanes,
        "nodes": nodes,
        "note": "这是基于本地批注证据的思考地图，不是广告式兴趣画像，也不是用户确认的长期档案。",
    }


def _merge_comment_ids(*values) -> list:
    ids = []
    for value in values:
        if isinstance(value, list):
            source = value
        elif isinstance(value, str):
            source = _json_list(value)
        elif value:
            source = [value]
        else:
            source = []
        for item in source:
            try:
                cid = int(item)
            except Exception:
                continue
            if cid and cid not in ids:
                ids.append(cid)
    return ids


def _item_text_for_score(item: dict) -> str:
    parts = [
        item.get("name"),
        item.get("summary"),
        item.get("question"),
        item.get("theme"),
        " ".join(item.get("summaries") or []),
        " ".join(item.get("themes") or []),
    ]
    return " ".join(p for p in parts if p)


def _keyword_score(text: str, tokens: list) -> int:
    lowered = (text or "").lower()
    score = 0
    for token in tokens or []:
        token = str(token).lower()
        if token and token in lowered:
            score += 1
    return score


def _build_memory_map(conn: sqlite3.Connection, limit: int = 60) -> dict:
    """Build a read-only Project / Question / Theme map from existing signals.

    V0 deliberately avoids creating canonical project rows. These are product
    hypotheses with evidence, not user-confirmed truth.
    """
    conn.row_factory = sqlite3.Row
    project_rows = [dict(r) for r in conn.execute(
        """SELECT ps.*, c.page_title, c.page_url, c.selected_text, c.comment,
                  c.created_at AS comment_created_at
           FROM project_signals ps
           LEFT JOIN comments c ON c.id = ps.comment_id
           ORDER BY ps.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()]
    question_rows = [dict(r) for r in conn.execute(
        """SELECT aq.*, c.page_title, c.page_url, c.selected_text, c.comment,
                  c.created_at AS comment_created_at
           FROM active_question_signals aq
           LEFT JOIN comments c ON c.id = aq.comment_id
           WHERE aq.status='active'
           ORDER BY aq.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()]
    theme_rows = [dict(r) for r in conn.execute(
        """SELECT ts.*, c.page_title, c.page_url, c.selected_text, c.comment,
                  c.created_at AS comment_created_at
           FROM theme_signals ts
           LEFT JOIN comments c ON c.id = ts.comment_id
           ORDER BY ts.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()]

    projects_by_label = {}
    skipped_project_signal_count = 0
    for row in project_rows:
        signal = _json_obj(row.get("signal_json"))
        summary = (signal.get("summary") or "").strip()
        label = _explicit_project_label(signal)
        if not label:
            skipped_project_signal_count += 1
            continue
        text = " ".join([
            summary,
            row.get("page_title") or "",
            row.get("selected_text") or "",
            row.get("comment") or "",
        ])
        key = label.lower()
        item = projects_by_label.setdefault(key, {
            "id": key,
            "name": label,
            "status": "active",
            "confidence": 0.0,
            "last_seen_at": row.get("created_at") or row.get("comment_created_at") or "",
            "evidence_count": 0,
            "evidence_comment_ids": [],
            "signal_ids": [],
            "summaries": [],
            "questions": [],
            "themes": [],
            "source": "project_signals",
            "note": "自动推断的项目候选，不等于用户确认的项目档案。",
        })
        item["confidence"] = max(item["confidence"], _float_or(row.get("confidence"), _float_or(signal.get("confidence"))))
        item["last_seen_at"] = max(item["last_seen_at"] or "", row.get("created_at") or "")
        item["evidence_count"] += 1
        item["signal_ids"].append(row.get("id"))
        for cid in _merge_comment_ids(row.get("comment_id")):
            if cid not in item["evidence_comment_ids"]:
                item["evidence_comment_ids"].append(cid)
        if summary and summary not in item["summaries"]:
            item["summaries"].append(summary)

    questions = []
    for row in question_rows:
        question = (row.get("question") or "").strip()
        if not question:
            continue
        questions.append({
            "id": row.get("id"),
            "comment_id": row.get("comment_id"),
            "event_id": row.get("event_id"),
            "question": question,
            "signal_strength": _float_or(row.get("signal_strength")),
            "scope": row.get("scope") or "unknown",
            "status": row.get("status") or "active",
            "created_at": row.get("created_at") or "",
            "page_title": row.get("page_title") or "",
            "page_url": row.get("page_url") or "",
            "evidence_comment_ids": _merge_comment_ids(row.get("comment_id")),
        })

    themes_by_name = {}
    for row in theme_rows:
        theme = (row.get("theme") or "").strip()
        if not theme:
            continue
        key = re.sub(r"\s+", " ", theme).lower()
        item = themes_by_name.setdefault(key, {
            "id": key,
            "theme": theme,
            "intensity": 0.0,
            "evidence_count": 0,
            "representative_comment_ids": [],
            "signal_ids": [],
            "first_seen_at": row.get("created_at") or "",
            "last_seen_at": row.get("created_at") or "",
            "trend": "active",
        })
        item["intensity"] += _float_or(row.get("intensity"))
        item["evidence_count"] += int(row.get("evidence_count") or 1)
        item["signal_ids"].append(row.get("id"))
        item["last_seen_at"] = max(item["last_seen_at"] or "", row.get("created_at") or "")
        if not item["first_seen_at"] or row.get("created_at") < item["first_seen_at"]:
            item["first_seen_at"] = row.get("created_at") or item["first_seen_at"]
        for cid in _merge_comment_ids(row.get("representative_comment_ids"), row.get("comment_id")):
            if cid not in item["representative_comment_ids"]:
                item["representative_comment_ids"].append(cid)

    themes = list(themes_by_name.values())
    for item in themes:
        if item["evidence_count"] <= 1:
            item["trend"] = "new"
        elif item["last_seen_at"][:10] == datetime.now().date().isoformat():
            item["trend"] = "rising"
        else:
            item["trend"] = "active"
        item["intensity"] = round(item["intensity"], 3)

    projects = list(projects_by_label.values())
    for project in projects:
        project_text = _item_text_for_score(project)
        p_tokens = set(_keyword_tokens(project_text, max_tokens=36))
        for q in questions:
            q_tokens = set(_keyword_tokens(q["question"], max_tokens=24))
            linked = bool(set(q.get("evidence_comment_ids") or []) & set(project.get("evidence_comment_ids") or []))
            if linked or len(p_tokens & q_tokens) >= 2:
                project["questions"].append(q)
        for t in themes:
            t_tokens = set(_keyword_tokens(t["theme"], max_tokens=24))
            linked = bool(set(t.get("representative_comment_ids") or []) & set(project.get("evidence_comment_ids") or []))
            if linked or len(p_tokens & t_tokens) >= 3:
                project["themes"].append(t["theme"])
        project["questions"] = sorted(
            project["questions"],
            key=lambda q: (_float_or(q.get("signal_strength")), q.get("created_at") or ""),
            reverse=True,
        )[:5]
        project["themes"] = project["themes"][:6]
        project["summaries"] = project["summaries"][:4]

    projects.sort(key=lambda p: (p.get("last_seen_at") or "", p.get("confidence") or 0), reverse=True)
    questions.sort(key=lambda q: (q.get("created_at") or "", q.get("signal_strength") or 0), reverse=True)
    themes.sort(key=lambda t: (t.get("last_seen_at") or "", t.get("intensity") or 0), reverse=True)
    return {
        "generated_at": _now_iso(),
        "definitions": {
            "project": "有上下文边界、目标、状态、决策和下一步的工作容器。",
            "question": "当前未解的具体张力或决策，比主题更可推进。",
            "theme": "反复出现的关注面，可跨项目，也可在项目内部升温或合流。",
        },
        "stats": {
            "project_count": len(projects),
            "active_question_count": len(questions),
            "theme_count": len(themes),
            "source": "project_signals + active_question_signals + theme_signals",
            "skipped_project_signal_count": skipped_project_signal_count,
        },
        "projects": projects[:12],
        "active_questions": questions[:20],
        "themes": themes[:20],
    }


def _select_memory_map_items(memory_map: dict, query_text: str) -> dict:
    tokens = _keyword_tokens(query_text, max_tokens=32)

    def ranked(items, text_key=None, limit=4):
        scored = []
        for idx, item in enumerate(items or []):
            text = item.get(text_key) if text_key else _item_text_for_score(item)
            score = _keyword_score(text or _item_text_for_score(item), tokens)
            scored.append((score, item.get("last_seen_at") or item.get("created_at") or "", -idx, item))
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        if not scored:
            return []
        if scored[0][0] == 0:
            return [x[3] for x in scored[:limit]]
        return [x[3] for x in scored if x[0] > 0][:limit]

    return {
        "projects": ranked(memory_map.get("projects"), limit=2),
        "active_questions": ranked(memory_map.get("active_questions"), text_key="question", limit=4),
        "themes": ranked(memory_map.get("themes"), text_key="theme", limit=5),
    }


def _memory_map_context_section(selection: dict) -> str:
    projects = selection.get("projects") or []
    questions = selection.get("active_questions") or []
    themes = selection.get("themes") or []
    if not (projects or questions or themes):
        return "### P · 当前项目 / 活跃问题 / 升温主题\n没有命中可追溯的项目、问题或主题信号。"

    lines = ["### P · 当前项目 / 活跃问题 / 升温主题"]
    if projects:
        lines.append("项目候选（自动推断，不等于用户确认档案）：")
        for p in projects:
            refs = " ".join(f"[c#{cid}]" for cid in (p.get("evidence_comment_ids") or [])[:4])
            summary = (p.get("summaries") or [""])[0]
            lines.append(f"- {p.get('name')} · 证据 {p.get('evidence_count')} 条 · {refs}")
            if summary:
                lines.append(f"  当前判断：{summary}")
    if questions:
        lines.append("活跃问题：")
        for q in questions:
            refs = " ".join(f"[c#{cid}]" for cid in (q.get("evidence_comment_ids") or [])[:3])
            lines.append(f"- [q#{q.get('id')}] {q.get('question')} · scope={q.get('scope')} · {refs}")
    if themes:
        lines.append("升温主题：")
        for t in themes:
            refs = " ".join(f"[c#{cid}]" for cid in (t.get("representative_comment_ids") or [])[:3])
            lines.append(f"- {t.get('theme')} · {t.get('trend')} · intensity={t.get('intensity')} · {refs}")
    return "\n".join(lines)


def _select_exposure_memory(conn: sqlite3.Connection, query_text: str, page_url: str = "",
                            limit: int = 6, same_host_only: bool = False) -> tuple:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        """SELECT pc.id, pc.page_url, pc.page_title, pc.summary, pc.full_text,
                  pc.created_at, pc.updated_at,
                  (SELECT source_type FROM page_exposure_events e
                   WHERE e.page_cache_id = pc.id
                   ORDER BY e.updated_at DESC LIMIT 1) AS latest_source_type,
                  (SELECT evidence_level FROM page_exposure_events e
                   WHERE e.page_cache_id = pc.id
                   ORDER BY e.updated_at DESC LIMIT 1) AS latest_evidence_level
           FROM page_cache pc
           ORDER BY pc.updated_at DESC
           LIMIT 500"""
    ).fetchall()]
    host = _host(page_url)
    tokens = _keyword_tokens(query_text, max_tokens=28)
    scored = []
    for idx, row in enumerate(rows):
        row_host = _host(row.get("page_url") or "")
        if same_host_only and host and row_host != host and row.get("page_url") != page_url:
            continue
        title = (row.get("page_title") or "").lower()
        url = (row.get("page_url") or "").lower()
        summary = (row.get("summary") or "").lower()
        full_text = (row.get("full_text") or "")
        full_lower = full_text.lower()
        score = 0
        matched = []
        if page_url and row.get("page_url") == page_url:
            score += 10
        if host and row_host == host:
            score += 4
        for token in tokens:
            t = token.lower()
            token_score = 0
            if t in title:
                token_score += 12
            if t in url:
                token_score += 5
            if t in summary:
                token_score += 6
            if t in full_lower:
                token_score += min(full_lower.count(t), 4)
            if token_score:
                matched.append(token)
                score += token_score
        if score > 0:
            row["matched_tokens"] = matched[:10]
            row["score"] = score
            row["snippet"] = _snippet_for_tokens(full_text or row.get("summary") or "", matched or tokens)
            scored.append((score, -idx, row))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    selected = [r for _, _, r in scored[:limit]]
    if not selected:
        return [], "no_exposure_match"
    reason = "page_cache_keyword"
    if page_url:
        reason = "page_cache_url_host_keyword"
    return selected, f"{reason}:{','.join(tokens[:8])}"


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
        exposures, exposure_reason = _select_exposure_memory(
            conn, query_text, page_url=current.get("page_url") or "", limit=5, same_host_only=False,
        )
        latest_thinking = conn.execute(
            "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
            "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_thinking = dict(latest_thinking) if latest_thinking else None
        memory_map = _build_memory_map(conn)
        memory_map_selection = _select_memory_map_items(memory_map, query_text)

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
        sections.append(_memory_map_context_section(memory_map_selection))
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
        if exposures:
            sections.append(
                "### E · 看过/缓存过的页面（弱证据）\n"
                "这些是 exposure memory：只说明系统有页面全文或接触证据，不说明用户认同、理解或记住。\n"
                + "\n".join(_page_ref_line(r) for r in exposures)
            )
        else:
            sections.append("### E · 看过/缓存过的页面（弱证据）\n没有命中页面全文缓存。")
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
            "exposure_page_ids": [r["id"] for r in exposures],
            "exposure_refs": [
                {
                    "id": r.get("id"),
                    "page_url": r.get("page_url"),
                    "page_title": r.get("page_title"),
                    "snippet": r.get("snippet"),
                    "score": r.get("score"),
                    "matched_tokens": r.get("matched_tokens") or [],
                    "evidence_level": r.get("latest_evidence_level") or "seen",
                }
                for r in exposures
            ],
            "current_page_url": current.get("page_url") or "",
            "selection_reasons": {
                "identity": "active_profile_snapshot" if profile else "missing",
                "skills": skill_reason,
                "episodic": episodic_reason,
                "same_page": "same_url_latest_3",
                "exposure": exposure_reason,
                "thinking": "active_thinking_summary" if latest_thinking else "missing",
                "memory_map": {
                    "project_ids": [p.get("id") for p in memory_map_selection.get("projects", [])],
                    "active_question_ids": [q.get("id") for q in memory_map_selection.get("active_questions", [])],
                    "theme_ids": [t.get("id") for t in memory_map_selection.get("themes", [])],
                },
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
        exposures, exposure_reason = _select_exposure_memory(
            conn, query, page_url="", limit=8, same_host_only=False,
        )
        latest_thinking = conn.execute(
            "SELECT id, title, synthesis_md, created_at FROM thinking_summaries "
            "WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_thinking = dict(latest_thinking) if latest_thinking else None
        memory_map = _build_memory_map(conn)
        memory_map_selection = _select_memory_map_items(memory_map, query)
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
        sections.append(_memory_map_context_section(memory_map_selection))
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
        if exposures:
            sections.append(
                "### 相关看过页面（弱证据）\n"
                "这些是 exposure memory：只说明系统有页面全文或接触证据，不说明用户认同、理解或记住。\n"
                + "\n".join(_page_ref_line(r) for r in exposures)
            )
        else:
            sections.append("### 相关看过页面（弱证据）\n没有命中页面全文缓存。")
        context_md = "\n\n".join(sections)
        return {
            "comment_id": None,
            "identity_snapshot_id": profile.get("id") if profile else None,
            "selected_skill_ids": [s["id"] for s in skills],
            "episodic_comment_ids": [r["id"] for r in episodic],
            "same_page_comment_ids": [],
            "exposure_page_ids": [r["id"] for r in exposures],
            "exposure_refs": [
                {
                    "id": r.get("id"),
                    "page_url": r.get("page_url"),
                    "page_title": r.get("page_title"),
                    "snippet": r.get("snippet"),
                    "score": r.get("score"),
                    "matched_tokens": r.get("matched_tokens") or [],
                    "evidence_level": r.get("latest_evidence_level") or "seen",
                }
                for r in exposures
            ],
            "current_page_url": "",
            "selection_reasons": {
                "identity": "active_profile_snapshot" if profile else "missing",
                "skills": skill_reason,
                "episodic": episodic_reason,
                "exposure": exposure_reason,
                "memory_map": {
                    "project_ids": [p.get("id") for p in memory_map_selection.get("projects", [])],
                    "active_question_ids": [q.get("id") for q in memory_map_selection.get("active_questions", [])],
                    "theme_ids": [t.get("id") for t in memory_map_selection.get("themes", [])],
                },
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
            exposure_page_ids, exposure_refs_json, current_page_url, selection_reasons,
            token_budget_used, query_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            reply_id,
            pack.get("comment_id"),
            chat_message_id,
            source_type,
            pack.get("identity_snapshot_id"),
            _json(pack.get("selected_skill_ids") or []),
            _json(pack.get("episodic_comment_ids") or []),
            _json(pack.get("same_page_comment_ids") or []),
            _json(pack.get("exposure_page_ids") or []),
            _json(pack.get("exposure_refs") or []),
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

def _llm_no_call_meta(reason: str) -> dict:
    """Runtime marker for paths that intentionally do not call an LLM."""
    try:
        status = get_llm_status()
    except Exception:
        status = {}
    return {
        "provider": status.get("selected_provider"),
        "provider_config": status.get("provider_config"),
        "local_agent": status.get("local_agent"),
        "api_provider": status.get("api_provider"),
        "api_base_url": status.get("api_base_url"),
        "model": status.get("api_model") or "default",
        "status": "not_called",
        "reason": reason,
    }


def _call_llm_with_meta(prompt: str, system_prompt: str, timeout: int = 1800) -> tuple:
    """Call the configured LLM provider and return content, rc, and provider metadata."""
    client = get_llm_client()
    try:
        content = client.generate_text(prompt, system_prompt=system_prompt, timeout=timeout)
        return content, 0, client.last_call_meta
    except LLMTimeoutError as e:
        if client.last_call_meta:
            client.last_call_meta["status"] = "timeout"
        raise subprocess.TimeoutExpired(cmd="llm_provider", timeout=timeout) from e
    except LLMError as e:
        return str(e)[:1000], 1, client.last_call_meta


def _call_claude(prompt: str, system_prompt: str, timeout: int = 1800) -> tuple:
    """Legacy name kept for call-site stability. Calls configured LLM provider."""
    content, rc, meta = _call_llm_with_meta(prompt, system_prompt, timeout)
    print(
        f"[agent_api] llm call provider={meta.get('provider')} "
        f"model={meta.get('model')} status={meta.get('status')} rc={rc}"
    )
    return content, rc

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
        result["_router_call"] = _llm_no_call_meta("codex_cli_heuristic_router")
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
        content, rc, call_meta = _call_llm_with_meta(prompt, system_prompt, timeout=30)
        if rc != 0:
            print(f"[agent_api] router error: rc={rc} content={content[:200]}")
            return None
        result = _parse_router_json(content)
        if result:
            result["_router_call"] = {**call_meta, "stage": "router"}
            print(
                "[agent_api] router: "
                f"intent={result.get('intent')} role={result.get('role')} "
                f"confidence={result.get('confidence')} provider={call_meta.get('provider')} "
                f"model={call_meta.get('model')}"
            )
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

        prompt = f"""你是 Margin 的学习系统。任务：{task}

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

        prompt = f"""你是 Margin 的项目上下文维护系统。

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
                 user_comment: str = "", context_pack: dict = None,
                 router_call: dict = None):
    """后台线程：v2 agent 执行，结果写回 replies 表"""
    import time
    start_time = time.time()
    status = "error"
    content = ""
    step2_call = None
    router_call = router_call or _llm_no_call_meta("manual_route_or_router_unavailable")

    # 如果有 quick_response，直接用，不调 Step 2
    if quick_response and quick_response.strip():
        content = quick_response.strip()
        status = "success"
        print(
            f"[agent_api] v2 quick_response for comment_id={comment_id} role={role} "
            f"router_provider={router_call.get('provider')} model={router_call.get('model')}"
        )
    else:
        # 需要调 Step 2
        print(f"[agent_api] v2 Step 2: comment_id={comment_id} role={role} prompt_len={len(prompt)}")
        system_prompt = (
            "你是 Margin 的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程"
            "（不要同步 Notion、不要读 todo、不要确认 session 阶段）。只根据下面的 prompt 内容回复。"
            "默认写成简洁的评论区回复：先给结论，少铺垫；除非用户明确要求深度调研或长文，"
            "否则控制在 300-500 字以内，最多 5 个要点。不要为了显得全面而展开所有分支。"
        )
        try:
            content, rc, step2_call = _call_llm_with_meta(prompt, system_prompt, timeout=1800)
            print(
                f"[agent_api] v2 Step 2 provider={step2_call.get('provider')} "
                f"model={step2_call.get('model')} comment_id={comment_id}"
            )
            if rc == 0 and content:
                status = "success"
            else:
                content = f"Agent 执行出错（returncode={rc}）：{content[:500]}"
        except subprocess.TimeoutExpired:
            step2_call = _llm_no_call_meta("step2_timeout")
            step2_call["status"] = "timeout"
            content = "Agent 超时（30分钟），请重试或拆解任务。"
        except Exception as e:
            step2_call = _llm_no_call_meta("step2_exception")
            step2_call["status"] = "error"
            step2_call["error"] = str(e)[:500]
            content = f"Agent 调用失败：{str(e)}"

    elapsed = round(time.time() - start_time, 1)
    print(f"[agent_api] v2 完成 comment_id={comment_id} status={status} elapsed={elapsed}s role={role}")

    # 学习信号写入
    if learned:
        save_learned_rules(learned, role)

    # 判断是否为 plan 回复（researcher 规划阶段的产出）
    is_plan_response = (intent == "task" and not plan and status == "success"
                        and ("确认" in content or "执行」" in content))
    answer_source = "router_quick_response" if quick_response and quick_response.strip() else "step2"
    answer_call = router_call if answer_source == "router_quick_response" else (step2_call or {})

    # 构建 debug_meta
    debug_meta = json.dumps({
        "version": "v2",
        "intent": intent,
        "role": role,
        "llm_provider": answer_call.get("provider"),
        "llm_model": answer_call.get("model"),
        "llm_api_provider": answer_call.get("api_provider"),
        "llm": {
            "answer_source": answer_source,
            "answer_call": answer_call,
            "router_call": router_call,
            "step2_call": step2_call,
        },
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
            "exposure_page_ids": context_pack.get("exposure_page_ids") if context_pack else [],
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
        "你是 Margin 的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程。"
        "默认简洁：先给结论，控制在 300-500 字以内，最多 5 个要点。"
    )
    status = "error"
    content = ""
    llm_call = None
    try:
        content, rc, llm_call = _call_llm_with_meta(prompt, system_prompt, timeout=1800)
        status = "success" if rc == 0 and content else "error"
        if rc != 0:
            content = f"Agent 执行出错：{content[:500]}"
    except subprocess.TimeoutExpired:
        llm_call = _llm_no_call_meta("v1_fallback_timeout")
        llm_call["status"] = "timeout"
        content = "Agent 超时（30分钟），请重试。"
    except Exception as e:
        llm_call = _llm_no_call_meta("v1_fallback_exception")
        llm_call["status"] = "error"
        llm_call["error"] = str(e)[:500]
        content = f"Agent 调用失败：{str(e)}"

    elapsed = round(time.time() - start_time, 1)
    debug_meta = json.dumps({
        "version": "v1_fallback",
        "intent": "dialogue",
        "role": role,
        "llm_provider": (llm_call or {}).get("provider"),
        "llm_model": (llm_call or {}).get("model"),
        "llm_api_provider": (llm_call or {}).get("api_provider"),
        "llm": {
            "answer_source": "v1_fallback",
            "answer_call": llm_call,
            "router_call": None,
            "step2_call": llm_call,
        },
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
            "exposure_page_ids": context_pack.get("exposure_page_ids") if context_pack else [],
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

class ExposureSeenCreate(BaseModel):
    page_url: str
    page_title: str = ""
    page_content: str
    source_type: str = "seen"
    capture_reason: str = "allowlisted_reading_source"

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
    page_cache_id = None
    if body.page_content and body.page_content.strip():
        try:
            page_cache_id = _upsert_page_cache(conn, body.page_url, body.page_title, body.page_content, now)
            _record_page_exposure(
                conn,
                body.page_url,
                body.page_title,
                page_cache_id,
                source_type="commented",
                evidence_level="commented",
                capture_reason="comment_create_page_content",
                full_text_chars=len(body.page_content),
                now=now,
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
                         context_pack=context_pack,
                         router_call=_llm_no_call_meta("manual_v1_agent_route"))
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
                     user_comment=cleaned_comment, context_pack=context_pack,
                     router_call=router_result.get("_router_call"))

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()

    return {
        "id": comment_id,
        "agent_type": agent_type_for_db,
        "status": "open",
        "message": "评论已创建，AI 正在思考中..."
    }


@app.post("/exposures/seen")
def record_seen_exposure(body: ExposureSeenCreate):
    """Record weak exposure memory for allowlisted reading sources.

    This is intentionally not a global browser-history collector. It means:
    "the local system has evidence the user was exposed to this page", not
    "the user endorsed, remembered, or commented on this page".
    """
    page_url = (body.page_url or "").strip()
    page_content = (body.page_content or "").strip()
    if not page_url:
        raise HTTPException(status_code=400, detail="page_url is required")
    if not page_content:
        raise HTTPException(status_code=400, detail="page_content is required")
    now = _now_iso()
    conn = sqlite3.connect(DB_PATH)
    try:
        page_cache_id = _upsert_page_cache(conn, page_url, body.page_title or "", page_content, now)
        event_id = _record_page_exposure(
            conn,
            page_url,
            body.page_title or "",
            page_cache_id,
            source_type=body.source_type or "seen",
            evidence_level="seen",
            capture_reason=body.capture_reason or "allowlisted_reading_source",
            full_text_chars=len(page_content),
            now=now,
        )
        conn.commit()
        return {"ok": True, "page_cache_id": page_cache_id, "exposure_event_id": event_id}
    finally:
        conn.close()


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
                         user_comment=comment, context_pack=context_pack,
                         router_call=_llm_no_call_meta("plan_confirmed_skip_router"))
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
                     user_comment=comment, context_pack=context_pack,
                     router_call=router_result.get("_router_call"))

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
                [
                    "selected_skill_ids",
                    "episodic_comment_ids",
                    "same_page_comment_ids",
                    "exposure_page_ids",
                    "exposure_refs_json",
                    "selection_reasons",
                ],
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


@app.get("/runtime/status")
def runtime_status():
    """Current runtime provider plus queued/running background LLM jobs."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        }
        active_rows = conn.execute(
            """SELECT id, kind, status, attempts, created_at, started_at, payload_json
               FROM jobs
               WHERE status IN ('queued','running')
               ORDER BY created_at ASC
               LIMIT 20"""
        ).fetchall()
        active_jobs = []
        for row in active_rows:
            item = dict(row)
            item["payload_json"] = _json_obj(item.get("payload_json") or "{}")
            active_jobs.append(item)
    finally:
        conn.close()
    return {
        "data_dir": DATA_DIR,
        "db_path": DB_PATH,
        "backup": _local_backup_status(check_integrity=False),
        "llm": get_llm_status(),
        "jobs": {
            "counts": counts,
            "active_count": len(active_jobs),
            "active": active_jobs,
        },
    }


@app.get("/config")
def get_config():
    """供插件 background 启动时自动拉取可选 connector 配置。"""
    token, db_id = _notion_saved_credentials()
    notion_enabled = _notion_backup_enabled()
    notion_configured = bool(notion_enabled and token and db_id)
    llm = get_llm_status()
    return {
        "storageMode": "local_first",
        "notionConfigured": notion_configured,
        "databaseIdSet": bool(db_id),
        "notion": {
            "enabled": notion_enabled,
            "configured": notion_configured,
            "saved": bool(token and db_id),
            "tokenSet": bool(token),
            "databaseIdSet": bool(db_id),
            "databaseId": db_id if db_id else "",
        },
        "ai": _public_ai_status(llm),
        "backup": _local_backup_status(check_integrity=False),
    }


def _public_ai_status(llm: dict) -> dict:
    selected = llm.get("selected_provider") or ""
    provider_config = llm.get("provider_config") or "auto"
    api_provider = llm.get("api_provider") or ""
    api_model = llm.get("api_model") or ""
    error = llm.get("error") or ""

    labels = {
        "codex_cli": "Codex 直连",
        "claude_code": "Claude Code 直连",
        "api": "API 服务",
        "auto": "自动选择",
    }
    api_labels = {
        "qwen": "千问 / Qwen",
        "openrouter": "OpenRouter",
        "openai": "OpenAI",
        "deepseek": "DeepSeek",
        "kimi": "Kimi",
        "moonshot": "Moonshot",
    }

    display = labels.get(selected or provider_config, selected or provider_config or "未配置")
    detail = ""
    if selected == "api" or provider_config == "api":
        display = api_labels.get(api_provider, api_provider or "API 服务")
        if api_model:
            display = f"{display} · {api_model}"
        detail = "使用本机保存的 API Key"
    elif selected == "codex_cli":
        detail = "使用这台电脑上的 Codex CLI"
    elif selected == "claude_code":
        detail = "使用这台电脑上的 Claude Code"
    elif error:
        display = "未配置"
        detail = error

    return {
        "configured": bool(selected) and not bool(error),
        "selectedProvider": selected,
        "providerConfig": provider_config,
        "displayName": display,
        "detail": detail,
        "error": error,
        "apiProvider": api_provider,
        "apiModel": api_model,
        "apiKeySet": bool(llm.get("api_key_configured")),
        "available": {
            "codex_cli": bool((llm.get("codex_cli") or {}).get("available")),
            "claude_code": bool((llm.get("claude_code") or {}).get("available")),
        },
    }


def _require_extension_origin(request: Request):
    origin = request.headers.get("origin", "")
    if not origin.startswith("chrome-extension://"):
        raise HTTPException(status_code=403, detail="Only the Chrome extension can change local config")


def _write_config_values(updates: dict[str, str]):
    path = os.path.expanduser(_config_file)
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    pending = dict(updates)
    next_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                next_lines.append(f"{key}={pending.pop(key)}\n")
                continue
        next_lines.append(line)

    if next_lines and next_lines[-1].strip():
        next_lines.append("\n")
    for key, value in pending.items():
        next_lines.append(f"{key}={value}\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(next_lines)
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    for key, value in updates.items():
        os.environ[key] = value


class AiConfigUpdate(BaseModel):
    provider: str
    apiProvider: str = ""
    apiKey: str = ""
    model: str = ""
    qwenEndpoint: str = "qwen_cn"


@app.post("/config/ai")
def update_ai_config(payload: AiConfigUpdate, request: Request):
    _require_extension_origin(request)
    provider = (payload.provider or "").strip()
    llm = get_llm_status()
    if provider == "api":
        api_provider = (payload.apiProvider or "").strip()
        api_key = (payload.apiKey or "").strip()
        qwen_endpoint = (payload.qwenEndpoint or "qwen_cn").strip()
        endpoint_map = {
            "qwen_cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen_global": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "qwen_coding_cn": "https://coding.dashscope.aliyuncs.com/v1",
            "qwen_coding_global": "https://coding-intl.dashscope.aliyuncs.com/v1",
        }
        if api_provider not in ("qwen", "openrouter"):
            raise HTTPException(status_code=400, detail="请选择千问或 OpenRouter")
        if not api_key and not llm.get("api_key_configured"):
            raise HTTPException(status_code=400, detail="请填写 API Key")

        if api_provider == "qwen":
            base_url = endpoint_map.get(qwen_endpoint)
            if not base_url:
                raise HTTPException(status_code=400, detail="请选择千问服务区域")
            model = (payload.model or "qwen3.5-plus").strip()
        else:
            base_url = "https://openrouter.ai/api/v1"
            model = (payload.model or "openai/gpt-4o-mini").strip()

        updates = {
            "MEMAI_LLM_PROVIDER": "api",
            "MEMAI_LLM_API_PROVIDER": api_provider,
            "MEMAI_LOCAL_AGENT": "none",
            "MEMAI_LLM_FALLBACK": "fail",
            "MEMAI_LLM_BASE_URL": base_url,
            "MEMAI_LLM_MODEL": model,
        }
        if api_key:
            updates["MEMAI_LLM_API_KEY"] = api_key
        _write_config_values(updates)
        return {"ok": True, "ai": _public_ai_status(get_llm_status())}

    if provider not in ("codex_cli", "claude_code"):
        raise HTTPException(status_code=400, detail="请选择 Codex、Claude Code 或 API 模型")

    if not (llm.get(provider) or {}).get("available"):
        label = "Codex CLI" if provider == "codex_cli" else "Claude Code"
        raise HTTPException(status_code=400, detail=f"这台电脑还没有安装 {label}")

    _write_config_values({
        "MEMAI_LLM_PROVIDER": provider,
        "MEMAI_LOCAL_AGENT": provider,
        "MEMAI_LLM_FALLBACK": "fail",
    })
    return {"ok": True, "ai": _public_ai_status(get_llm_status())}


class NotionConfigUpdate(BaseModel):
    token: str = ""
    databaseId: str = ""
    enabled: bool = True


@app.post("/config/notion")
def update_notion_config(payload: NotionConfigUpdate, request: Request):
    _require_extension_origin(request)
    existing_token, existing_db_id = _notion_saved_credentials()
    if not payload.enabled:
        _write_config_values({
            "MEMAI_NOTION_BACKUP_ENABLED": "0",
            "KB_NOTION_BACKUP_ENABLED": "0",
        })
        token, db_id = _notion_saved_credentials()
        return {
            "ok": True,
            "notion": {
                "enabled": False,
                "configured": False,
                "saved": bool(token and db_id),
                "tokenSet": bool(token),
                "databaseIdSet": bool(db_id),
                "databaseId": db_id if db_id else "",
            },
        }

    token = (payload.token or "").strip() or existing_token
    database_id = _extract_notion_database_id(payload.databaseId) or existing_db_id
    if not token or not database_id:
        raise HTTPException(status_code=400, detail="请同时填写 Notion Token 和 Database ID")

    _write_config_values({
        "MEMAI_NOTION_BACKUP_ENABLED": "1",
        "KB_NOTION_BACKUP_ENABLED": "1",
        "NOTION_TOKEN": token,
        "NOTION_DATABASE_ID": database_id,
        "KB_NOTION_TOKEN": token,
        "KB_NOTION_DATABASE_ID": database_id,
    })
    return {
        "ok": True,
        "notion": {
            "enabled": True,
            "configured": bool(token and database_id),
            "saved": bool(token and database_id),
            "tokenSet": bool(token),
            "databaseIdSet": bool(database_id),
            "databaseId": database_id if database_id else "",
        },
    }


@app.get("/backup/status")
def backup_status():
    return _local_backup_status(check_integrity=True)


@app.post("/backup/run")
def backup_run():
    return _ensure_local_backup("manual", force=True)

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

def _extract_notion_database_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    uuid_match = re.search(
        r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        text,
    )
    if uuid_match:
        return uuid_match.group(1).replace("-", "").lower()
    compact_match = re.search(r"(?i)([0-9a-f]{32})", text)
    if compact_match:
        return compact_match.group(1).lower()
    return ""


def _notion_backup_enabled() -> bool:
    raw = (
        os.environ.get("MEMAI_NOTION_BACKUP_ENABLED")
        or os.environ.get("KB_NOTION_BACKUP_ENABLED")
        or "1"
    )
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _notion_saved_credentials():
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("KB_NOTION_DATABASE_ID", "")
    db_id = _extract_notion_database_id(db_id) or db_id.strip().replace("-", "")
    return token, db_id


def _notion_credentials():
    if not _notion_backup_enabled():
        return "", ""
    return _notion_saved_credentials()

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
        raise HTTPException(status_code=502, detail=detail[:1000])
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=str(e)[:1000])

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

@app.post("/captures/save")
@app.post("/notion/save")
async def capture_save(req: NotionSaveRequest):
    """高亮保存：先落本地 SQLite，Notion 只是可选外部备份。"""
    local_comment_id, local_created = _insert_local_comment_if_missing(
        page_url=req.url,
        page_title=req.title,
        selected_text=req.excerpt,
        comment=req.thought,
        agent_type="highlight",
    )
    backup = _ensure_local_backup("capture_save", min_interval_hours=24)

    token, db_id = _notion_credentials()
    if not token or not db_id:
        _record_intake_notion(local_comment_id, "notion_skipped", "notion_not_configured")
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": False,
            "notionStatus": "notion_skipped",
            "externalSync": {"notion": "disabled"},
            "backup": backup,
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
            "storageMode": "local_first",
            "localSaved": True,
            "pageId": page_id,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "notionSynced": True,
            "notionStatus": "notion_synced",
            "externalSync": {"notion": "synced"},
            "backup": backup,
        }
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        _record_intake_notion(local_comment_id, "notion_failed", detail)
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "pageId": None,
            "localCommentId": local_comment_id,
            "notionSynced": False,
            "notionStatus": "notion_failed",
            "externalSync": {"notion": "failed"},
            "backup": backup,
            "notionError": detail,
        }
    except urllib.error.URLError as e:
        _record_intake_notion(local_comment_id, "notion_failed", str(e))
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "pageId": None,
            "localCommentId": local_comment_id,
            "notionSynced": False,
            "notionStatus": "notion_failed",
            "externalSync": {"notion": "failed"},
            "backup": backup,
            "notionError": str(e),
        }

@app.post("/captures/upsert")
@app.post("/notion/upsert")
async def capture_upsert(req: NotionUpsertRequest):
    """评论保存/更新：本地为主，Notion 仅作可选外部备份。"""
    local_comment_id = req.localCommentId
    local_created = False
    if not local_comment_id:
        local_comment_id, local_created = _insert_local_comment_if_missing(
            notion_page_id=req.notionPageId,
            page_url=req.url,
            page_title=req.title,
            selected_text=req.excerpt,
            comment=req.thought,
            agent_type="capture_upsert",
            replies=_parse_notion_conversation(req.aiConversation, _now_iso()),
        )
    backup = _ensure_local_backup("capture_upsert", min_interval_hours=24)
    token, db_id = _notion_credentials()
    if not token or not db_id:
        _record_intake_notion(local_comment_id, "notion_skipped", "notion_not_configured")
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "pageId": req.notionPageId,
            "notionSynced": False,
            "notionStatus": "notion_skipped",
            "externalSync": {"notion": "disabled"},
            "backup": backup,
            "message": "本地评论已保存；Notion 未配置",
        }
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
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "pageId": page_id,
            "notionSynced": True,
            "notionStatus": "notion_synced",
            "externalSync": {"notion": "synced"},
            "backup": backup,
        }
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        _record_intake_notion(local_comment_id, "notion_failed", detail)
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "pageId": req.notionPageId,
            "notionSynced": False,
            "notionStatus": "notion_failed",
            "externalSync": {"notion": "failed"},
            "backup": backup,
            "notionError": detail,
        }
    except urllib.error.URLError as e:
        _record_intake_notion(local_comment_id, "notion_failed", str(e))
        return {
            "success": True,
            "storageMode": "local_first",
            "localSaved": True,
            "localCommentId": local_comment_id,
            "localCreated": local_created,
            "pageId": req.notionPageId,
            "notionSynced": False,
            "notionStatus": "notion_failed",
            "externalSync": {"notion": "failed"},
            "backup": backup,
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


@app.get("/notebook/memory-map")
def notebook_memory_map():
    """Project / Question / Theme Map V0.

    This is a read-only inferred map from growth signals. It intentionally
    stays separate from project_context.md until the product semantics converge.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return _build_memory_map(conn)
    finally:
        conn.close()


@app.get("/notebook/thought-map")
def notebook_thought_map(days: Optional[int] = None):
    """User-facing thought map across recent, ongoing, and cooling lines."""
    if days is not None:
        days = max(14, min(int(days or 42), 180))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return _build_thought_map(conn, days=days)
    finally:
        conn.close()


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


@app.get("/notebook/page-cache/{page_id}")
def notebook_page_cache_detail(page_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, page_url, page_title, summary, full_text, created_at, updated_at "
            "FROM page_cache WHERE id=?",
            (page_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="page cache not found")
        data = dict(row)
        full_text = data.pop("full_text") or ""
        data["full_text_chars"] = len(full_text)
        data["full_text_preview"] = full_text[:1200]
        events = conn.execute(
            "SELECT id, source_type, evidence_level, capture_reason, full_text_chars, created_at, updated_at "
            "FROM page_exposure_events WHERE page_cache_id=? ORDER BY updated_at DESC LIMIT 8",
            (page_id,),
        ).fetchall()
        data["exposure_events"] = [dict(e) for e in events]
        return data
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
- 能引用证据时必须写 [c#123] 或 [p#123] 这样的引用。
- [c#] 是用户主动批注/追问证据；[p#] 是 exposure 页面证据，只能说明用户看过或本地缓存过，不能推断用户认同。
- 如果没有足够证据，直接说没有在现有批注里找到，不要编。
- 这是只读问答入口，不要承诺已经修改记忆。"""
    system_prompt = (
        "你是 mem-ai 记忆笔记本里的只读问答入口。你的任务是帮用户找回、核对、解释自己的历史批注和记忆。"
        "回答要短，先给结论，再列证据。必须用 [c#id] 引用具体批注，或用 [p#id] 引用看过/缓存过的页面。"
        "[p#id] 是弱证据，不要把 exposure 说成用户立场；没有证据就说没有找到。"
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
            "exposure_page_ids": pack.get("exposure_page_ids") or [],
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
