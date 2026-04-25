#!/usr/bin/env python3
"""
评论区 Agent API
端口：8766
功能：评论存 SQLite + 触发 claude -p agent + 结果写回评论线程
"""

import sqlite3
import subprocess
import json
import os
import re
import threading
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "comments.db")
PROJECT_CONTEXT_PATH = os.path.join(ROOT, "project_context.md")
COMPANY_CULTURE_PATH = os.path.join(ROOT, "company_culture.md")
# v2 新增路径
PROMPTS_DIR = os.path.join(ROOT, "agent_prompts")
AGENT_PRINCIPLES_PATH = os.path.join(ROOT, "agent_principles.md")
USER_PROFILE_PATH = os.path.join(ROOT, "user_profile.md")
LEARNED_RULES_PATH = os.path.join(ROOT, "learned_rules.json")

# 启动时读取 ~/.kb_config，让 uvicorn 子进程也能拿到配置
_config_file = os.path.expanduser("~/.kb_config")
if os.path.exists(_config_file):
    with open(_config_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

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
    # 兼容已有数据库：如果列不存在则添加
    for col, table in [
        ("debug_meta TEXT", "replies"),
        ("surrounding_text TEXT", "comments"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except Exception:
            pass  # 列已存在，忽略
    # page_cache 表可能是新建的，上面 CREATE IF NOT EXISTS 已处理
    conn.commit()
    conn.close()

init_db()

# ──────────────────────────────────────────
# 启动诊断日志（帮助新用户排查问题）
# ──────────────────────────────────────────

def _startup_check():
    claude_bin = os.environ.get("KB_CLAUDE_BIN") or os.path.expanduser("~/.npm-global/bin/claude")
    notion_ok = bool(os.environ.get("KB_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN"))
    print(f"[agent_api] 数据库: {DB_PATH} ({'✓' if os.path.exists(DB_PATH) else '✗ 不存在'})")
    print(f"[agent_api] Claude: {claude_bin} ({'✓' if os.path.exists(claude_bin) else '✗ 未找到'})")
    print(f"[agent_api] Notion Token: {'✓ 已配置' if notion_ok else '✗ 未配置'}")
    print(f"[agent_api] HOME: {os.environ.get('HOME', '未设置')}")
    if not os.path.exists(claude_bin):
        print(f"[agent_api] ⚠ claude 二进制未找到，agent 调用将失败。请检查 ~/.kb_config 中的 CLAUDE_BIN")

_startup_check()

# ──────────────────────────────────────────
# v2: 文件加载工具
# ──────────────────────────────────────────

def _load_file(path: str, default: str = "") -> str:
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return default

def load_project_context() -> str:
    return _load_file(PROJECT_CONTEXT_PATH,
        "项目：意图-行动缺口创业方向调研。目标：验证这个方向是否值得做，并开发Chrome评论区系统作为自用工具和产品原型。")

def load_company_culture() -> str:
    return _load_file(COMPANY_CULTURE_PATH)

def load_agent_principles() -> str:
    return _load_file(AGENT_PRINCIPLES_PATH, load_company_culture())

def load_user_profile() -> str:
    return _load_file(USER_PROFILE_PATH, "[空白，新用户]")

def load_learned_rules() -> str:
    return _load_file(LEARNED_RULES_PATH, '{"rules": []}')

def load_learned_rules_scoped(role: str) -> str:
    """加载适用于指定角色的规则子集"""
    raw = _load_file(LEARNED_RULES_PATH, '{"rules": []}')
    try:
        data = json.loads(raw)
        applicable = [r for r in data.get("rules", [])
                      if r.get("active", True) and r.get("scope") in ("all", f"role:{role}")]
        if not applicable:
            return "（暂无已学到的规则）"
        return "\n".join(f"- {r['rule']}" for r in applicable)
    except Exception:
        return raw

def load_prompt_template(name: str) -> str:
    return _load_file(os.path.join(PROMPTS_DIR, f"{name}.md"))

def fetch_notion_memory(limit: int = 15) -> str:
    """拉取 Notion 最近批注作为记忆上下文，失败时静默返回空字符串"""
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
        return "用户最近的阅读批注（最新 {} 条，用于理解用户当前关注点）：\n".format(len(entries)) + "\n\n".join(entries)
    except Exception:
        return ""

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

def _get_claude_bin():
    return os.environ.get("KB_CLAUDE_BIN") or os.path.expanduser("~/.npm-global/bin/claude")

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
    """调用 claude -p，返回 (content_str, returncode)"""
    result = subprocess.run(
        [_get_claude_bin(), "-p", prompt, "--output-format", "json",
         "--dangerously-skip-permissions", "--system-prompt", system_prompt],
        capture_output=True, text=True, timeout=timeout, env=_get_child_env()
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return data.get("result", ""), 0
        except json.JSONDecodeError:
            return result.stdout, 0
    return (result.stderr or result.stdout or "")[:1000], result.returncode

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

def run_router(page_url: str, page_title: str, selected_text: str,
               surrounding_text: str, comment: str, last_ai_reply: str = "") -> dict:
    """调用路由器 prompt（Step 1），返回解析后的 JSON dict"""
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

    notion_memory = fetch_notion_memory(15)

    prompt = template.replace("{agent_principles}", load_agent_principles())
    prompt = prompt.replace("{user_profile}", load_user_profile())
    prompt = prompt.replace("{project_context}", load_project_context())
    prompt = prompt.replace("{learned_rules_scoped}", load_learned_rules_scoped(role))
    prompt = prompt.replace("{notion_memory}", notion_memory)
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
        raw = _load_file(LEARNED_RULES_PATH, '{"rules": []}')
        data = json.loads(raw)
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
        print(f"[agent_api] learned_rules: 新增 {len(new_rules)} 条，总计 {len([r for r in rules if r.get('active', True)])} 条活跃")
    except Exception as e:
        print(f"[agent_api] save_learned_rules error: {e}")

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
        current_profile = _load_file(USER_PROFILE_PATH, "")
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
        current_context = _load_file(PROJECT_CONTEXT_PATH, "")
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
                 user_comment: str = ""):
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
        system_prompt = "你是知识库助手的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程（不要同步 Notion、不要读 todo、不要确认 session 阶段）。只根据下面的 prompt 内容回复。"
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
        "rules_applied": [r["rule"] for r in json.loads(load_learned_rules()).get("rules", [])
                          if r.get("active") and r.get("scope") in ("all", f"role:{role}")][:5],
        "status": status
    }, ensure_ascii=False)

    # 写回数据库
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, "agent", role, content, now, debug_meta)
    )
    conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
    conn.commit()
    conn.close()

    # 学习层：成功时触发画像/上下文审视
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

    system_prompt = "你是知识库助手的评论区 agent。直接回答用户的问题，不要执行任何 session 初始化流程。"
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
        "rules_applied": [],
        "status": status
    }, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, "agent", agent_type, content, now, debug_meta)
    )
    conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
    conn.commit()
    conn.close()

# ──────────────────────────────────────────
# Debug 日志
# ──────────────────────────────────────────

DEBUG_LOG_DIR = os.path.join(ROOT, "debug-logs")
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

    # no_agent=True 时仅存储
    if body.no_agent:
        return {"id": comment_id, "agent_type": agent_type_for_db, "status": "open",
                "message": "评论已存储，等待手动召唤 AI"}

    # ── 分发逻辑 ──
    def _dispatch():
        selected = body.selected_text or "（无划线内容）"
        surrounding = body.surrounding_text or ""

        # 路径 1：有 @手动路由 → 映射到 v2 role，跳过路由器
        if v1_agent_type and v1_agent_type in V1_TO_V2_ROLE:
            intent, role = V1_TO_V2_ROLE[v1_agent_type]
            print(f"[agent_api] v1 @{v1_agent_type} → v2 intent={intent} role={role}")
            prompt = build_role_prompt(role, body.page_url, body.page_title,
                                      selected, surrounding, cleaned_comment)
            write_debug_log(comment_id, f"v1→{role}", prompt, {"v1_agent_type": v1_agent_type})
            # 更新 DB 的 agent_type
            _conn = sqlite3.connect(DB_PATH)
            _conn.execute("UPDATE comments SET agent_type = ? WHERE id = ?", (role, comment_id))
            _conn.commit()
            _conn.close()
            run_agent_v2(comment_id, intent, role, prompt, user_comment=cleaned_comment)
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
        write_debug_log(comment_id, f"v2_{role}", prompt, router_result)

        run_agent_v2(comment_id, intent, role, prompt,
                     quick_response=quick_response, learned=learned,
                     user_comment=cleaned_comment)

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
    if body.comment is not None:
        conn.execute(
            "UPDATE comments SET comment = ?, updated_at = ? WHERE id = ?",
            (body.comment, datetime.now().isoformat(), comment_id)
        )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/comments/{comment_id}/reply")
def add_reply(comment_id: int, body: ReplyCreate):
    """用户手动补充回复"""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO replies (comment_id, author, content, created_at) VALUES (?, 'user', ?, ?)",
        (comment_id, body.content, now)
    )
    conn.execute("UPDATE comments SET updated_at = ? WHERE id = ?", (now, comment_id))
    conn.commit()
    conn.close()
    return {"ok": True}

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
            write_debug_log(comment_id, f"v2_plan_exec_{role}", prompt,
                           {"plan_confirmed": True, "plan": plan_text[:500]})
            run_agent_v2(comment_id, "task", role, prompt,
                         plan=f"用户已确认",
                         user_comment=comment)
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
        write_debug_log(comment_id, f"v2_rerun_{role}", prompt, router_result)
        run_agent_v2(comment_id, intent, role, prompt,
                     quick_response=quick_response, learned=learned,
                     user_comment=comment)

    thread = threading.Thread(target=_rerun, daemon=True)
    thread.start()
    return {"message": f"已重新触发 AI（v2）"}

@app.get("/health")
def health():
    return {"status": "ok", "db": DB_PATH}

class ClientErrorReport(BaseModel):
    source: str = "unknown"       # callAIViaAgent / upsertNotionPage / saveToNotion ...
    message: str = ""
    stack: str = ""
    context: dict = {}             # 任意上下文：url / comment_id / agent_type / etc.
    ts: str = ""                   # 客户端时间戳，缺失由后端补

CLIENT_ERROR_LOG = os.environ.get("KB_CLIENT_ERROR_LOG") or os.path.join(ROOT, ".logs", "client_errors.log")

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

@app.get("/learning/status")
def learning_status():
    """学习层状态：查看当前画像、交互计数、下次触发时间"""
    count = _get_interaction_count()
    next_profile_review = _PROFILE_REVIEW_INTERVAL - (count % _PROFILE_REVIEW_INTERVAL)
    profile = _load_file(USER_PROFILE_PATH, "")
    rules_raw = _load_file(LEARNED_RULES_PATH, '{"rules": []}')
    rules = json.loads(rules_raw).get("rules", [])
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
    import shutil
    claude_bin = os.environ.get("KB_CLAUDE_BIN") or os.path.expanduser("~/.npm-global/bin/claude")
    return {
        "HOME": os.environ.get("HOME", "（未设置）"),
        "claude_bin": claude_bin,
        "claude_bin_exists": os.path.exists(claude_bin),
        "notion_token_set": bool(os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN")),
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
    notionPageId: Optional[str] = None
    title: str = ""
    url: str = ""
    platform: str = "网页"
    excerpt: str = ""
    thought: str = ""
    aiConversation: str = ""

@app.post("/notion/save")
async def notion_save(req: NotionSaveRequest):
    """高亮保存到 Notion（代理，不经过 Service Worker）"""
    import urllib.request, urllib.error
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("KB_NOTION_DATABASE_ID", "")
    if not token or not db_id:
        raise HTTPException(status_code=500, detail="Notion 未配置")
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
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            data = json.loads(resp.read())
        return {"success": True, "pageId": data.get("id")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        raise HTTPException(status_code=e.code, detail=detail)

@app.post("/notion/upsert")
async def notion_upsert(req: NotionUpsertRequest):
    """评论 upsert 到 Notion（代理，不经过 Service Worker）"""
    import urllib.request, urllib.error
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("KB_NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("KB_NOTION_DATABASE_ID", "")
    if not token or not db_id:
        raise HTTPException(status_code=500, detail="Notion 未配置")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}

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
        return {"success": True, "pageId": data.get("id", req.notionPageId)}
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        raise HTTPException(status_code=e.code, detail=detail)
