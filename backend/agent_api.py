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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "comments.db")
PROJECT_CONTEXT_PATH = os.path.join(ROOT, "project_context.md")
COMPANY_CULTURE_PATH = os.path.join(ROOT, "company_culture.md")

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
            comment TEXT NOT NULL,    -- 用户的批注内容
            agent_type TEXT,          -- 从 @xxx 解析出来的 agent 类型
            status TEXT DEFAULT 'open',  -- open / resolved / tracking / archived
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
    try:
        conn.execute("ALTER TABLE replies ADD COLUMN debug_meta TEXT")
    except Exception:
        pass  # 列已存在，忽略
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
# Agent 路由配置
# ──────────────────────────────────────────

AGENT_PROMPTS = {
    "竞品": """你是竞品调研专家。你的分析服务于一个一人公司创始人。

命题方上下文（必须贯穿全文）：
{project_context}

命题方的具体约束：
- 资源：1个人
- 时间窗口：6个月内可执行
- 目标用户：决策型知识工作者（管理者、研究者、独立创作者）
所有差异化机会必须在这个约束下评估，不要给大公司视角的分析。

当前页面：{page_url}
用户划线内容：{selected_text}
用户问题：{comment}

调研要求：
1. 强制 WebSearch 搜索最新信息，禁止用训练数据填充。搜索覆盖：
   - Product Hunt、Reddit 用户评价
   - 36kr、少数派、量子位等中文媒体
   - GitHub 开源项目
2. 每个数据点必须标注：来源平台 + 数据日期 + 置信度（高/中/低）
   没有来源的数据降级为「推测」
3. 找10条+真实用户原声，格式：中文翻译 / 英文原文 / 来源平台 / 链接

输出结构（必须包含）：
## 产品定位与核心假设
## 真实用户评价（10条+）
## 竞品的失败/限制
## 给命题方的机会缺口
最后必须回答：「这个竞品给命题方留下的最大可执行机会是什么？请给出具体的产品切入点（不超过2个），并说明为什么命题方在6个月内能做到。」

中文回答。引用英文内容时，必须附中文翻译，格式：中文翻译（英文原文）。""",

    "思辨": """你是思辨讨论伙伴，熟悉这个项目的背景。

项目上下文：
{project_context}

当前页面：{page_url}
用户划线内容：{selected_text}
用户观点：{comment}

要求：
1. 认真对待用户的观点，先理解再回应
2. 提供不同角度的思考，包括反驳和支持
3. 结合项目实际情况给出判断
4. 如果用户观点有重要缺漏，直接指出

中文回答，不要说废话。""",

    "调研": """你是创业调研专家。

项目上下文：
{project_context}

当前页面：{page_url}
用户划线内容：{selected_text}
调研问题：{comment}

要求：
1. 强制 WebSearch，搜索 GitHub、ProductHunt、36kr、量子位等平台
2. 找真实数据，不用训练数据填充
3. 覆盖中英文市场
4. 给出调研发现 + 启发性问题

中文回答。引用英文内容时，必须附中文翻译，格式：中文翻译（英文原文）。""",

    "解释": """你是知识讲解助手，熟悉这个项目背景。

项目上下文：
{project_context}

用户划线内容：{selected_text}
用户问题：{comment}

要求：
1. 直接解释，不绕弯
2. 结合项目背景说明和我们方向的关联
3. 如果有延伸价值，顺带提出

中文回答。引用英文内容时，必须附中文翻译，格式：中文翻译（英文原文）。""",
}

DEFAULT_AGENT = "思辨"

def parse_agent_type(comment: str) -> tuple[str, str]:
    """从评论里解析 @agent 类型，返回 (agent_type, cleaned_comment)"""
    pattern = r'@(竞品|思辨|调研|解释)'
    match = re.search(pattern, comment)
    if match:
        agent_type = match.group(1)
        cleaned = re.sub(pattern, '', comment).strip()
        return agent_type, cleaned
    return DEFAULT_AGENT, comment

def load_project_context() -> str:
    if os.path.exists(PROJECT_CONTEXT_PATH):
        with open(PROJECT_CONTEXT_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    return "项目：意图-行动缺口创业方向调研。目标：验证这个方向是否值得做，并开发Chrome评论区系统作为自用工具和产品原型。"

def load_company_culture() -> str:
    if os.path.exists(COMPANY_CULTURE_PATH):
        with open(COMPANY_CULTURE_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    return ""

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
# 后台 Agent 调用
# ──────────────────────────────────────────

def run_agent(comment_id: int, agent_type: str, prompt: str):
    """后台线程：调用 claude -p，把结果写回 replies 表"""
    import time
    start_time = time.time()
    prompt_tokens_est = len(prompt) // 4
    status = "error"
    content = ""
    print(f"[agent_api] 开始处理 comment_id={comment_id} agent_type=@{agent_type} prompt_len={len(prompt)}")

    # 路径从环境变量读（由 start.sh 注入），fallback 到常见位置
    CLAUDE_BIN = (
        os.environ.get("KB_CLAUDE_BIN") or
        os.path.expanduser("~/.npm-global/bin/claude")
    )
    # 确保子进程继承当前进程的完整环境（PATH、HOME、认证配置等）
    child_env = os.environ.copy()
    child_env.setdefault("HOME", os.path.expanduser("~"))
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=1800,  # 30分钟超时
            env=child_env
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            content = data.get("result", "（Agent 未返回内容）")
            status = "success"
        else:
            # 同时捕获 stdout（可能有部分输出）和 stderr
            err_detail = (result.stderr or result.stdout or "（无错误信息）")[:1000]
            content = f"Agent 执行出错（returncode={result.returncode}）：{err_detail}"
            print(f"[agent_api] run_agent error: rc={result.returncode} stderr={result.stderr[:200]} stdout={result.stdout[:200]}")
    except subprocess.TimeoutExpired:
        content = "Agent 超时（30分钟），深度调研仍未完成，请重试。"
    except Exception as e:
        content = f"Agent 调用失败：{str(e)}"
        print(f"[agent_api] run_agent exception: {e}")

    elapsed = round(time.time() - start_time, 1)
    reply_tokens_est = len(content) // 4
    print(f"[agent_api] 完成 comment_id={comment_id} status={status} elapsed={elapsed}s reply_len={len(content)}")

    # 从 comments 表查询原始数据，用于 debug_meta 上下文推断
    try:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _row = _conn.execute("SELECT selected_text FROM comments WHERE id = ?", (comment_id,)).fetchone()
        _conn.close()
        has_selected_text = bool(_row and _row["selected_text"] and _row["selected_text"].strip() and _row["selected_text"] != "（无划线内容）")
    except Exception:
        has_selected_text = False

    debug_meta = json.dumps({
        "agent_type": agent_type,
        "elapsed_s": elapsed,
        "prompt_tokens_est": prompt_tokens_est,
        "reply_tokens_est": reply_tokens_est,
        "context_layers": {
            "project_context": True,
            "notion_memory": True,  # fetch_notion_memory 总是被调用（失败时返回空字符串）
            "selected_text": has_selected_text,
            "article_context": False  # 暂未实现
        },
        "status": status
    }, ensure_ascii=False)

    # 写回数据库
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO replies (comment_id, author, agent_type, content, created_at, debug_meta) VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, "agent", agent_type, content, now, debug_meta)
    )
    conn.execute(
        "UPDATE comments SET updated_at = ? WHERE id = ?",
        (now, comment_id)
    )
    conn.commit()
    conn.close()

# ──────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────

class CommentCreate(BaseModel):
    page_url: str
    page_title: str = ""
    selected_text: str = ""
    comment: str
    no_agent: bool = False  # True 时仅存储，不触发 agent（用户手动召唤时再触发）

class ReplyCreate(BaseModel):
    content: str

class StatusUpdate(BaseModel):
    status: str  # open / resolved / tracking / archived

@app.post("/comments")
def create_comment(body: CommentCreate):
    """新建评论，自动解析 @agent 并后台触发"""
    agent_type, cleaned_comment = parse_agent_type(body.comment)
    now = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """INSERT INTO comments (page_url, page_title, selected_text, comment, agent_type, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
        (body.page_url, body.page_title, body.selected_text, cleaned_comment, agent_type, now, now)
    )
    comment_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # 构建 agent prompt（注入 L1 文化层 + Notion 记忆 + 项目上下文）
    company_culture = load_company_culture()
    project_context = load_project_context()
    notion_memory = fetch_notion_memory(15)
    prompt_template = AGENT_PROMPTS.get(agent_type, AGENT_PROMPTS[DEFAULT_AGENT])
    prompt = prompt_template.format(
        project_context=project_context,
        page_url=body.page_url,
        selected_text=body.selected_text or "（无划线内容）",
        comment=cleaned_comment,
    )
    # L1 文化层放最前，作为全局行为约束
    if company_culture:
        prompt = company_culture + "\n\n---\n\n" + prompt
    if notion_memory:
        prompt = notion_memory + "\n\n---\n\n" + prompt

    # no_agent=True 时仅存储，不触发后台 agent（用户手动召唤时再触发）
    if not body.no_agent:
        thread = threading.Thread(target=run_agent, args=(comment_id, agent_type, prompt), daemon=True)
        thread.start()
        message = f"评论已创建，@{agent_type} agent 正在处理中..."
    else:
        message = "评论已存储，等待手动召唤 AI"

    return {
        "id": comment_id,
        "agent_type": agent_type,
        "status": "open",
        "message": message
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
    """重新触发 agent（用于不满意结果时）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")

    c = dict(row)
    company_culture = load_company_culture()
    project_context = load_project_context()
    notion_memory = fetch_notion_memory(15)
    agent_type = c["agent_type"] or DEFAULT_AGENT
    prompt_template = AGENT_PROMPTS.get(agent_type, AGENT_PROMPTS[DEFAULT_AGENT])
    prompt = prompt_template.format(
        project_context=project_context,
        page_url=c["page_url"],
        selected_text=c["selected_text"] or "（无划线内容）",
        comment=c["comment"],
    )
    if company_culture:
        prompt = company_culture + "\n\n---\n\n" + prompt
    if notion_memory:
        prompt = notion_memory + "\n\n---\n\n" + prompt
    thread = threading.Thread(target=run_agent, args=(comment_id, agent_type, prompt), daemon=True)
    thread.start()
    return {"message": f"已重新触发 @{agent_type} agent"}

@app.get("/health")
def health():
    return {"status": "ok", "db": DB_PATH}

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
