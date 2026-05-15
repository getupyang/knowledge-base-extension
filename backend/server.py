#!/usr/bin/env python3
"""
调研知识库本地服务器
运行：python3 server.py
访问：http://localhost:8765
"""

import http.server
import os
import re
import json
import sqlite3
import secrets
import urllib.parse
from datetime import datetime, timedelta
import markdown

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    SHANGHAI_TZ = None

# 优先用 RESEARCH_DIR 环境变量（从 ~/.kb_config 读取），否则用脚本所在目录
ROOT = os.environ.get("RESEARCH_DIR", os.path.dirname(os.path.abspath(__file__)))
PORT = 8765

# Margin 运营后台：查询 Chrome 插件后端的 SQLite
ADMIN_DB_PATH = os.path.abspath(os.path.expanduser(
    os.environ.get("MARGIN_ADMIN_DB", "~/.knowledge-base-extension/comments.db")
))
# token 未设 → admin 路由全部 404，隐藏存在性
ADMIN_TOKEN = os.environ.get("MARGIN_ADMIN_TOKEN", "")
ADMIN_COOKIE_NAME = "margin_admin"
ADMIN_COOKIE_MAX_AGE = 86400  # 1 天
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK_DIR = os.path.join(REPO_ROOT, "src", "notebook")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; background: #f5f5f5; color: #333; }}
  .layout {{ display: flex; min-height: 100vh; }}

  /* 左导航 */
  .sidebar {{ width: 220px; background: #1a1a2e; color: #ccc; padding: 20px 0; position: fixed; height: 100vh; overflow-y: auto; flex-shrink: 0; z-index: 10; }}
  .sidebar h1 {{ font-size: 12px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 1px; padding: 0 16px 10px; border-bottom: 1px solid #2a2a3e; }}
  .sidebar a {{ display: block; padding: 7px 16px; font-size: 12px; color: #999; text-decoration: none; border-left: 3px solid transparent; transition: all 0.15s; }}
  .sidebar a:hover {{ color: #fff; background: #2a2a3e; border-left-color: #6366f1; }}
  .sidebar .section-title {{ font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 1px; padding: 14px 16px 5px; }}

  /* 正文区 */
  .main-wrap {{ margin-left: 220px; flex: 1; display: flex; min-height: 100vh; }}
  .main {{ flex: 1; padding: 32px 48px; max-width: 820px; }}

  /* 面包屑 */
  .breadcrumb {{ font-size: 12px; color: #aaa; margin-bottom: 16px; }}
  .breadcrumb a {{ color: #6366f1; text-decoration: none; }}

  /* MD渲染 */
  .md-body {{ background: white; border-radius: 10px; padding: 36px 44px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); line-height: 1.75; }}
  .md-body h1 {{ font-size: 24px; font-weight: 700; margin: 0 0 20px; padding-bottom: 12px; border-bottom: 2px solid #f0f0f0; }}
  .md-body h2 {{ font-size: 18px; font-weight: 600; margin: 32px 0 12px; color: #111; }}
  .md-body h3 {{ font-size: 15px; font-weight: 600; margin: 22px 0 8px; color: #333; }}
  .md-body p {{ margin-bottom: 12px; font-size: 14px; }}
  .md-body ul, .md-body ol {{ margin: 0 0 12px 22px; }}
  .md-body li {{ margin-bottom: 5px; font-size: 14px; }}
  .md-body blockquote {{ border-left: 4px solid #6366f1; padding: 10px 14px; background: #f8f7ff; border-radius: 0 8px 8px 0; margin: 14px 0; font-style: italic; color: #555; }}
  .md-body code {{ background: #f4f4f4; padding: 2px 5px; border-radius: 4px; font-size: 12px; font-family: "SF Mono", monospace; }}
  .md-body pre {{ background: #1e1e2e; color: #cdd6f4; padding: 18px; border-radius: 8px; overflow-x: auto; margin: 14px 0; }}
  .md-body pre code {{ background: none; padding: 0; color: inherit; font-size: 13px; }}
  .md-body .table-wrap {{ overflow-x: auto; margin: 14px 0; }}
  .md-body table {{ border-collapse: collapse; font-size: 13px; min-width: 100%; }}
  .md-body th {{ background: #f8f8f8; padding: 9px 12px; text-align: left; border: 1px solid #e8e8e8; font-weight: 600; white-space: nowrap; }}
  .md-body td {{ padding: 8px 12px; border: 1px solid #e8e8e8; white-space: nowrap; }}
  .md-body tr:hover td {{ background: #fafafa; }}
  .md-body a {{ color: #6366f1; text-decoration: none; }}
  .md-body a:hover {{ text-decoration: underline; }}
  .md-body hr {{ border: none; border-top: 2px solid #f0f0f0; margin: 24px 0; }}
  ::selection {{ background: #e8e6ff; }}
</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar">
    <h1>调研知识库</h1>
    {sidebar}
  </nav>
  <div class="main-wrap">
    <main class="main">
      {content}
    </main>
  </div>
</div>
</body>
</html>"""


def build_sidebar():
    html = '<a href="/" style="font-weight:600;color:#fff;">首页</a>'
    html += '<div class="section-title">系统配置</div>'
    html += '<a href="/claude" style="font-size:12px;">CLAUDE.md</a>'
    html += '<a href="/skills" style="font-size:12px;">Skills</a>'
    topics_dir = os.path.join(ROOT, 'topics')
    if not os.path.exists(topics_dir):
        return html

    def render_dir(dir_path, url_prefix, indent=0):
        """递归渲染目录，indent控制缩进层级"""
        result = ''
        pad_px = 16 + indent * 12
        pad_style = f'padding-left:{pad_px}px;'

        # 先显示当前目录下的 _overview.md
        overview = os.path.join(dir_path, '_overview.md')
        if os.path.exists(overview) and indent > 0:
            result += f'<a href="{url_prefix}/_overview.md" style="{pad_style}font-size:12px;">总览</a>'

        entries = sorted(os.listdir(dir_path))

        # MD文件（排除_overview和隐藏文件）
        for f in entries:
            if f.endswith('.md') and f != '_overview.md' and not f.startswith('.'):
                fpath = os.path.join(dir_path, f)
                if os.path.isfile(fpath):
                    name = f.replace('.md', '')
                    result += f'<a href="{url_prefix}/{f}" style="{pad_style}font-size:12px;">{name}</a>'

        # 子目录（递归，跳过隐藏目录）
        for d in entries:
            dpath = os.path.join(dir_path, d)
            if os.path.isdir(dpath) and not d.startswith('.'):
                sub_label_size = max(9, 11 - indent)
                result += f'<div class="section-title" style="{pad_style}font-size:{sub_label_size}px;">{d}</div>'
                result += render_dir(dpath, f'{url_prefix}/{d}', indent + 1)

        return result

    for topic in sorted(os.listdir(topics_dir)):
        topic_path = os.path.join(topics_dir, topic)
        if not os.path.isdir(topic_path):
            continue
        topic_name = topic.replace('-', ' ').replace('_', ' ').title()
        html += f'<div class="section-title">{topic_name}</div>'
        html += render_dir(topic_path, f'/topics/{topic}', indent=0)

    return html


def render_md(filepath, rel_path):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])
    html_content = md.convert(content)
    # 给 table 包一层 div 支持横向滚动
    html_content = html_content.replace('<table>', '<div class="table-wrap"><table>').replace('</table>', '</table></div>')

    filename = os.path.basename(filepath).replace('.md', '')
    parts = rel_path.strip('/').split('/')

    breadcrumb = '<a href="/">首页</a>'
    for part in parts[:-1]:
        breadcrumb += f' › {part}'
    breadcrumb += f' › {filename}'

    content_html = f'''
    <div class="breadcrumb">{breadcrumb}</div>
    <div class="md-body">{html_content}</div>
    '''

    return HTML_TEMPLATE.format(
        title=filename,
        sidebar=build_sidebar(),
        content=content_html
    )


def render_index():
    cards = ''
    topics_dir = os.path.join(ROOT, 'topics')

    if os.path.exists(topics_dir):
        for topic in sorted(os.listdir(topics_dir)):
            topic_path = os.path.join(topics_dir, topic)
            if not os.path.isdir(topic_path):
                continue
            topic_name = topic.replace('-', ' ').replace('_', ' ').title()

            overview = os.path.join(topic_path, '_overview.md')
            desc = ''
            if os.path.exists(overview):
                with open(overview, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and not line.startswith('---') and not line.startswith('>'):
                            desc = line[:80]
                            break

            file_count = len([f for f in os.listdir(topic_path) if f.endswith('.md')])
            cards += f'''
            <a href="/topics/{topic}/_overview.md" class="index-card">
                <h3>{topic_name}</h3>
                <p>{desc or "点击查看调研内容"}</p>
                <p style="margin-top:8px;font-size:12px;color:#bbb;">{file_count} 个文件</p>
            </a>'''

    index_style = """
    <style>
    .index-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }
    .index-card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer; text-decoration: none; color: inherit; display: block; transition: box-shadow 0.15s; }
    .index-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
    .index-card h3 { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
    .index-card p { font-size: 13px; color: #888; line-height: 1.5; }
    </style>
    """

    content_html = f'''
    {index_style}
    <div style="font-size:26px;font-weight:700;margin-bottom:8px;">调研知识库</div>
    <div style="font-size:14px;color:#888;margin-bottom:24px;">AI创业方向探索 · {datetime.now().strftime("%Y-%m-%d")}</div>
    <div class="index-grid">{cards}</div>
    '''

    return HTML_TEMPLATE.format(
        title="调研知识库",
        sidebar=build_sidebar(),
        content=content_html
    )


# ── CLAUDE.md 和 Skill 文档路径映射 ──
CLAUDE_MD_PATHS = {
    "全局 CLAUDE.md": os.path.expanduser("~/.claude/CLAUDE.md"),
    "Margin CLAUDE.md": os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CLAUDE.md"),
    "Research CLAUDE.md": os.path.join(ROOT, "CLAUDE.md"),
}
SKILLS_DIR = os.path.expanduser("~/.claude/skills")


def render_claude_index():
    """列出所有 CLAUDE.md 文件"""
    cards = ''
    for label, path in CLAUDE_MD_PATHS.items():
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            size = os.path.getsize(path)
            safe_key = urllib.parse.quote(label, safe='')
            cards += f'''
            <a href="/claude/{safe_key}" class="index-card">
                <h3>{label}</h3>
                <p style="font-size:12px;color:#888;">{size} bytes · 最后修改 {mtime}</p>
            </a>'''
    content_html = f'''
    <style>
    .index-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }}
    .index-card {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer; text-decoration: none; color: inherit; display: block; transition: box-shadow 0.15s; }}
    .index-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.12); }}
    .index-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 8px; }}
    </style>
    <div style="font-size:22px;font-weight:700;margin-bottom:8px;">CLAUDE.md 文档</div>
    <div style="font-size:13px;color:#888;margin-bottom:24px;">Claude 的行为指令和项目上下文</div>
    <div class="index-grid">{cards}</div>
    '''
    return HTML_TEMPLATE.format(title="CLAUDE.md", sidebar=build_sidebar(), content=content_html)


def render_skills_index():
    """列出所有 skill 目录"""
    cards = ''
    if os.path.exists(SKILLS_DIR):
        for name in sorted(os.listdir(SKILLS_DIR)):
            skill_dir = os.path.join(SKILLS_DIR, name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isdir(skill_dir) and os.path.exists(skill_md):
                # 读第一行非空非标题内容作为描述
                desc = ''
                with open(skill_md, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and not line.startswith('---') and not line.startswith('>'):
                            desc = line[:100]
                            break
                cards += f'''
                <a href="/skills/{name}" class="index-card">
                    <h3>{name}</h3>
                    <p style="font-size:12px;color:#888;">{desc or "点击查看"}</p>
                </a>'''
    content_html = f'''
    <style>
    .index-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }}
    .index-card {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer; text-decoration: none; color: inherit; display: block; transition: box-shadow 0.15s; }}
    .index-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.12); }}
    .index-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 8px; }}
    </style>
    <div style="font-size:22px;font-weight:700;margin-bottom:8px;">Skills 文档</div>
    <div style="font-size:13px;color:#888;margin-bottom:24px;">Claude Code 的 Skill 指令集</div>
    <div class="index-grid">{cards}</div>
    '''
    return HTML_TEMPLATE.format(title="Skills", sidebar=build_sidebar(), content=content_html)


BEHAVIOR_LOG = os.path.join(ROOT, '.behavior_log.jsonl')


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if self.path == '/behavior':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                with open(BEHAVIOR_LOG, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(data, ensure_ascii=False) + '\n')
            except:
                pass
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        # 运营后台：未设 MARGIN_ADMIN_TOKEN 时，所有 /admin/* 一律 404
        if path == '/admin' or path.startswith('/admin/'):
            if not ADMIN_TOKEN:
                self.send_response(404); self.end_headers(); self.wfile.write(b'Not found')
                return
            if not self._check_admin(parsed):
                self.send_response(404); self.end_headers(); self.wfile.write(b'Not found')
                return
            admin_handle(self, path, parsed)
            return

        if path == '/' or path == '/index.html':
            self.serve_html(render_index())
            return

        # ── 记忆笔记本：允许直接用 http://localhost:8765/notebook/ 打开 ──
        if path == '/notebook' or path == '/notebook/':
            self.serve_static_file(os.path.join(NOTEBOOK_DIR, 'index.html'), 'text/html; charset=utf-8')
            return
        if path.startswith('/notebook/'):
            rel = path[len('/notebook/'):].strip('/')
            if not rel or '..' in rel.split('/'):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Not found')
                return
            fpath = os.path.join(NOTEBOOK_DIR, rel)
            content_type = 'text/plain; charset=utf-8'
            if fpath.endswith('.js'):
                content_type = 'application/javascript; charset=utf-8'
            elif fpath.endswith('.css'):
                content_type = 'text/css; charset=utf-8'
            elif fpath.endswith('.html'):
                content_type = 'text/html; charset=utf-8'
            self.serve_static_file(fpath, content_type)
            return

        # ── CLAUDE.md 路由 ──
        if path == '/claude' or path == '/claude/':
            self.serve_html(render_claude_index())
            return
        if path.startswith('/claude/'):
            label = urllib.parse.unquote(path[len('/claude/'):])
            fpath = CLAUDE_MD_PATHS.get(label)
            if fpath and os.path.exists(fpath):
                self.serve_html(render_md(fpath, path))
                return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')
            return

        # ── Skills 路由 ──
        if path == '/skills' or path == '/skills/':
            self.serve_html(render_skills_index())
            return
        if path.startswith('/skills/'):
            parts = path[len('/skills/'):].strip('/').split('/')
            skill_name = parts[0] if parts else ''
            # 默认显示 SKILL.md，也支持 /skills/name/other.md
            if len(parts) <= 1:
                fpath = os.path.join(SKILLS_DIR, skill_name, 'SKILL.md')
            else:
                fpath = os.path.join(SKILLS_DIR, *parts)
            if os.path.exists(fpath):
                self.serve_html(render_md(fpath, path))
                return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')
            return

        filepath = os.path.join(ROOT, path.lstrip('/'))

        if os.path.isdir(filepath):
            overview = os.path.join(filepath, '_overview.md')
            if os.path.exists(overview):
                self.serve_html(render_md(overview, path + '/_overview.md'))
            else:
                self.serve_html(render_index())
            return

        if filepath.endswith('.md') and os.path.exists(filepath):
            self.serve_html(render_md(filepath, path))
            return

        # 支持不带.md后缀的URL，自动补全
        if not filepath.endswith('.md') and not filepath.endswith('.html'):
            md_path = filepath + '.md'
            if os.path.exists(md_path):
                self.serve_html(render_md(md_path, path + '.md'))
                return

        if filepath.endswith('.html') and os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                self.serve_html(f.read())
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'Not found')

    def serve_html(self, html):
        encoded = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def serve_static_file(self, filepath, content_type):
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')
            return
        with open(filepath, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    # ─── Margin 运营后台 helpers ───
    def _check_admin(self, parsed):
        """url ?token=... 或 cookie margin_admin=... 命中 ADMIN_TOKEN 即放行；放行时延续 cookie。"""
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get('token', [''])[0] == ADMIN_TOKEN:
            self._pending_admin_cookie = ADMIN_TOKEN
            return True
        raw_cookie = self.headers.get('Cookie') or ''
        for part in raw_cookie.split(';'):
            k, _, v = part.strip().partition('=')
            if k == ADMIN_COOKIE_NAME and v and secrets.compare_digest(v, ADMIN_TOKEN):
                return True
        return False

    def serve_admin_html(self, html, status=200):
        encoded = html.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(encoded))
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, '_pending_admin_cookie', None):
            self.send_header(
                'Set-Cookie',
                f'{ADMIN_COOKIE_NAME}={self._pending_admin_cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ADMIN_COOKIE_MAX_AGE}'
            )
        self.end_headers()
        self.wfile.write(encoded)

    def serve_admin_json(self, payload, status=200):
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(encoded))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(encoded)


# ────────────────────────────────────────────────────────────
# Margin 运营后台
# ────────────────────────────────────────────────────────────

def _admin_db():
    conn = sqlite3.connect(ADMIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _shanghai_now():
    if SHANGHAI_TZ is not None:
        return datetime.now(SHANGHAI_TZ)
    return datetime.now()


def _range_to_since(rng):
    """24h / 7d / 30d → 起始 ISO 时间戳（Asia/Shanghai 时区前缀）。"""
    rng = (rng or '24h').lower()
    now = _shanghai_now()
    delta = {'24h': timedelta(hours=24), '7d': timedelta(days=7), '30d': timedelta(days=30)}.get(rng, timedelta(hours=24))
    since = now - delta
    return since.isoformat(timespec='seconds')


def _safe_event_names(raw):
    """前端传 events=a,b,c → ['a','b','c']。白名单防注入。"""
    allowed = {
        'page_opened', 'highlight_saved', 'sidebar_comment_created', 'followup_created',
        'ai_reply_requested', 'ai_reply_completed', 'ai_reply_failed',
        'ai_reply_feedback_submitted', 'notebook_opened', 'config_changed',
    }
    if not raw:
        return []
    items = [x.strip() for x in raw.split(',') if x.strip()]
    return [x for x in items if x in allowed]


def _overview_data(rng='24h'):
    since = _range_to_since(rng)
    conn = _admin_db()
    try:
        active_users = conn.execute(
            "SELECT COUNT(DISTINCT anonymous_install_id) AS n FROM telemetry_outbox WHERE created_at >= ?",
            (since,)
        ).fetchone()['n']
        events_by_name = conn.execute(
            """SELECT event_name, COUNT(*) AS n
                 FROM telemetry_outbox
                WHERE created_at >= ?
                GROUP BY event_name ORDER BY n DESC""",
            (since,)
        ).fetchall()
        events_by_name = [dict(r) for r in events_by_name]
        ai_succ = conn.execute(
            "SELECT COUNT(*) AS n FROM telemetry_outbox WHERE event_name='ai_reply_completed' AND created_at >= ?",
            (since,)
        ).fetchone()['n']
        ai_fail = conn.execute(
            "SELECT COUNT(*) AS n FROM telemetry_outbox WHERE event_name='ai_reply_failed' AND created_at >= ?",
            (since,)
        ).fetchone()['n']
        ai_succ_rate = (ai_succ / (ai_succ + ai_fail)) if (ai_succ + ai_fail) > 0 else None
        ledger_summary = conn.execute(
            """SELECT
                 COALESCE(SUM(total_tokens), 0) AS total_tokens,
                 COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                 COUNT(*) AS calls,
                 SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS succ,
                 SUM(CASE WHEN status='timeout' THEN 1 ELSE 0 END) AS timeout,
                 SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error
               FROM llm_call_ledger WHERE created_at >= ?""",
            (since,)
        ).fetchone()
        ledger_summary = dict(ledger_summary) if ledger_summary else {}
        feedback_down = conn.execute(
            """SELECT created_at, properties_json
                 FROM telemetry_outbox
                WHERE event_name='ai_reply_feedback_submitted'
                  AND properties_json LIKE '%"rating": "down"%'
                ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
        feedback_down = [dict(r) for r in feedback_down]
        for f in feedback_down:
            try:
                props = json.loads(f.get('properties_json') or '{}')
                f['feedback_text'] = props.get('feedback_text', '')
            except Exception:
                f['feedback_text'] = ''
        return {
            'range': rng, 'since': since,
            'active_install_ids': active_users,
            'events_by_name': events_by_name,
            'ai_reply_completed': ai_succ,
            'ai_reply_failed': ai_fail,
            'ai_success_rate': ai_succ_rate,
            'llm_ledger': ledger_summary,
            'recent_thumb_down': feedback_down,
        }
    finally:
        conn.close()


def _users_data(rng='7d'):
    since = _range_to_since(rng)
    conn = _admin_db()
    try:
        rows = conn.execute(
            """SELECT
                 anonymous_install_id AS install_id,
                 MAX(created_at) AS last_active,
                 COUNT(*) AS event_count,
                 SUM(CASE WHEN event_name='ai_reply_requested' THEN 1 ELSE 0 END) AS ai_req,
                 SUM(CASE WHEN event_name='ai_reply_completed' THEN 1 ELSE 0 END) AS ai_ok,
                 SUM(CASE WHEN event_name='ai_reply_failed' THEN 1 ELSE 0 END) AS ai_fail
               FROM telemetry_outbox
              WHERE created_at >= ? AND anonymous_install_id IS NOT NULL
              GROUP BY anonymous_install_id
              ORDER BY last_active DESC LIMIT 200""",
            (since,)
        ).fetchall()
        return {'range': rng, 'since': since, 'users': [dict(r) for r in rows]}
    finally:
        conn.close()


def _user_detail_data(install_id, rng='24h', events_filter=None):
    since = _range_to_since(rng)
    events_filter = events_filter or []
    conn = _admin_db()
    try:
        summary = conn.execute(
            """SELECT
                 COUNT(*) AS event_count,
                 SUM(CASE WHEN event_name='ai_reply_requested' THEN 1 ELSE 0 END) AS ai_req,
                 SUM(CASE WHEN event_name='ai_reply_completed' THEN 1 ELSE 0 END) AS ai_ok,
                 SUM(CASE WHEN event_name='ai_reply_failed' THEN 1 ELSE 0 END) AS ai_fail,
                 MIN(created_at) AS first_seen,
                 MAX(created_at) AS last_seen
               FROM telemetry_outbox
              WHERE anonymous_install_id = ? AND created_at >= ?""",
            (install_id, since)
        ).fetchone()
        summary = dict(summary) if summary else {}
        ledger = conn.execute(
            """SELECT
                 COALESCE(SUM(total_tokens), 0) AS total_tokens,
                 COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
                 COUNT(*) AS calls
               FROM llm_call_ledger
              WHERE anonymous_install_id = ? AND created_at >= ?""",
            (install_id, since)
        ).fetchone()
        summary['llm'] = dict(ledger) if ledger else {}
        bucket_expr = "substr(created_at, 1, 13)" if rng == '24h' else "substr(created_at, 1, 10)"
        if events_filter:
            qmarks = ','.join('?' * len(events_filter))
            trend_rows = conn.execute(
                f"""SELECT {bucket_expr} AS bucket, COUNT(*) AS n
                      FROM telemetry_outbox
                     WHERE anonymous_install_id = ? AND created_at >= ?
                       AND event_name IN ({qmarks})
                     GROUP BY bucket ORDER BY bucket""",
                (install_id, since, *events_filter)
            ).fetchall()
        else:
            trend_rows = conn.execute(
                f"""SELECT {bucket_expr} AS bucket, COUNT(*) AS n
                      FROM telemetry_outbox
                     WHERE anonymous_install_id = ? AND created_at >= ?
                     GROUP BY bucket ORDER BY bucket""",
                (install_id, since)
            ).fetchall()
        if events_filter:
            qmarks = ','.join('?' * len(events_filter))
            detail_rows = conn.execute(
                f"""SELECT created_at, event_name, surface, thread_telemetry_id, page_id, properties_json
                      FROM telemetry_outbox
                     WHERE anonymous_install_id = ? AND created_at >= ?
                       AND event_name IN ({qmarks})
                     ORDER BY created_at DESC LIMIT 500""",
                (install_id, since, *events_filter)
            ).fetchall()
        else:
            detail_rows = conn.execute(
                """SELECT created_at, event_name, surface, thread_telemetry_id, page_id, properties_json
                     FROM telemetry_outbox
                    WHERE anonymous_install_id = ? AND created_at >= ?
                    ORDER BY created_at DESC LIMIT 500""",
                (install_id, since)
            ).fetchall()
        # LLM ledger 明细（最近 50 次调用），按时间倒序
        ledger_rows = conn.execute(
            """SELECT call_id, stage, role, intent, status, model, provider,
                      elapsed_ms, input_tokens, output_tokens, total_tokens,
                      cost_usd, usage_source, error_category, error_code, created_at, comment_id
                 FROM llm_call_ledger
                WHERE anonymous_install_id = ? AND created_at >= ?
                ORDER BY created_at DESC LIMIT 50""",
            (install_id, since)
        ).fetchall()
        return {
            'install_id': install_id, 'range': rng, 'since': since,
            'events_filter': events_filter,
            'summary': summary,
            'trend': [dict(r) for r in trend_rows],
            'detail': [dict(r) for r in detail_rows],
            'ledger_detail': [dict(r) for r in ledger_rows],
        }
    finally:
        conn.close()


def _feedback_data(limit=200):
    conn = _admin_db()
    try:
        rows = conn.execute(
            """SELECT created_at, anonymous_install_id, thread_telemetry_id, properties_json
                 FROM telemetry_outbox
                WHERE event_name='ai_reply_feedback_submitted'
                ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                props = json.loads(d.get('properties_json') or '{}')
            except Exception:
                props = {}
            d['rating'] = props.get('rating', '')
            d['feedback_text'] = props.get('feedback_text', '')
            d['reply_chars_bucket'] = props.get('reply_chars_bucket', '')
            d.pop('properties_json', None)
            out.append(d)
        return {'feedback': out}
    finally:
        conn.close()


ADMIN_HTML_BASE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8">
<title>Margin 运营后台 · {title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
         background: #f7f7f9; color: #222; margin: 0; padding: 0; }}
  .topbar {{ background: #1a1a2e; color: #ccc; padding: 12px 24px; display: flex; gap: 24px; align-items: center; }}
  .topbar a {{ color: #9ab; text-decoration: none; font-size: 13px; }}
  .topbar a.active {{ color: #fff; font-weight: 600; }}
  .topbar .title {{ color: #6cf; font-weight: 700; }}
  .container {{ max-width: 1200px; margin: 24px auto; padding: 0 24px; }}
  .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); padding: 20px; margin-bottom: 20px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
  .kpi {{ background: #fafafa; border-radius: 6px; padding: 14px; }}
  .kpi .label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: .04em; }}
  .kpi .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; color: #1a1a2e; }}
  .kpi .sub {{ font-size: 11px; color: #999; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; }}
  th {{ background: #fafafa; font-weight: 600; color: #555; }}
  tr:hover td {{ background: #f8f8ff; }}
  .install-id {{ font-family: ui-monospace, monospace; font-size: 11px; color: #555; }}
  .filter-bar {{ display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }}
  .filter-bar select, .filter-bar button {{ padding: 5px 10px; border: 1px solid #ccc; border-radius: 4px; background: #fff; cursor: pointer; }}
  .chip {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: #eef; color: #336; }}
  .chip.fail {{ background: #fee; color: #b22; }}
  .chip.ok {{ background: #efe; color: #262; }}
  .feedback-block {{ background: #fff8e7; border-left: 3px solid #c80; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px; }}
  .feedback-block .meta {{ font-size: 11px; color: #888; margin-bottom: 4px; }}
  .feedback-block .text {{ font-size: 13px; line-height: 1.5; }}
  .bar {{ display: inline-block; background: #6366f1; height: 14px; vertical-align: middle; }}
  pre {{ font-size: 11px; background: #f5f5f5; padding: 6px; border-radius: 4px; overflow-x: auto; max-height: 200px; }}
</style></head>
<body>
<div class="topbar">
  <span class="title">Margin · 运营后台</span>
  <a href="/admin" class="{nav_overview}">总览</a>
  <a href="/admin/users" class="{nav_users}">用户</a>
  <a href="/admin/feedback" class="{nav_feedback}">反馈 inbox</a>
  <span style="color:#666; font-size:11px; margin-left:auto;">数据时区：Asia/Shanghai · DB：{db_path}</span>
</div>
<div class="container">{body}</div>
</body></html>"""


def _admin_html(body, title='总览', active='overview'):
    return ADMIN_HTML_BASE.format(
        title=title, body=body,
        db_path=ADMIN_DB_PATH,
        nav_overview='active' if active == 'overview' else '',
        nav_users='active' if active == 'users' else '',
        nav_feedback='active' if active == 'feedback' else '',
    )


def _render_overview(rng='24h'):
    data = _overview_data(rng)
    ledger = data.get('llm_ledger') or {}
    succ_rate_txt = f"{data['ai_success_rate']*100:.1f}%" if data.get('ai_success_rate') is not None else '—'
    rng_options = ''.join(
        f'<option value="{r}"{" selected" if r == rng else ""}>{lbl}</option>'
        for r, lbl in [('24h', '近 24 小时'), ('7d', '近 7 天'), ('30d', '近 30 天')]
    )
    events_rows = ''.join(
        f'<tr><td>{html_escape(e["event_name"])}</td><td style="text-align:right">{e["n"]}</td></tr>'
        for e in data['events_by_name']
    ) or '<tr><td colspan="2" style="color:#999">无事件</td></tr>'
    feedback_html = '<div style="color:#999">暂无踩反馈</div>'
    if data.get('recent_thumb_down'):
        feedback_html = ''.join(
            f'<div class="feedback-block"><div class="meta">{html_escape(f["created_at"])}</div>'
            f'<div class="text">{html_escape(f.get("feedback_text", "")) or "<em>用户未填文字</em>"}</div></div>'
            for f in data['recent_thumb_down']
        )
    body = f"""
<form method="get" action="/admin" class="filter-bar">
  <label>时间段：</label>
  <select name="range" onchange="this.form.submit()">{rng_options}</select>
</form>
<div class="card">
  <h2>活跃概况（{html_escape(rng)}）</h2>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">活跃用户</div><div class="value">{data['active_install_ids']}</div><div class="sub">独立 install_id</div></div>
    <div class="kpi"><div class="label">AI 召唤成功</div><div class="value">{data['ai_reply_completed']}</div><div class="sub">ai_reply_completed</div></div>
    <div class="kpi"><div class="label">AI 召唤失败</div><div class="value">{data['ai_reply_failed']}</div><div class="sub">含超时 timeout</div></div>
    <div class="kpi"><div class="label">成功率</div><div class="value">{succ_rate_txt}</div><div class="sub">success / (success+fail)</div></div>
  </div>
</div>
<div class="card">
  <h2>LLM 真实账本（来自 llm_call_ledger）</h2>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">总调用次数</div><div class="value">{ledger.get('calls', 0)}</div><div class="sub">含 router + answer + fallback</div></div>
    <div class="kpi"><div class="label">总 token</div><div class="value">{ledger.get('total_tokens', 0)}</div><div class="sub">真实计费</div></div>
    <div class="kpi"><div class="label">总成本 (USD)</div><div class="value">${ledger.get('total_cost_usd', 0) or 0:.4f}</div><div class="sub">真实计费</div></div>
    <div class="kpi"><div class="label">超时数</div><div class="value">{ledger.get('timeout', 0) or 0}</div><div class="sub">超时仍可能扣 token</div></div>
  </div>
</div>
<div class="card">
  <h2>事件分布</h2>
  <table><thead><tr><th>event_name</th><th style="text-align:right">数量</th></tr></thead>
  <tbody>{events_rows}</tbody></table>
</div>
<div class="card">
  <h2>最近踩反馈（10 条）</h2>
  {feedback_html}
</div>
"""
    return _admin_html(body, title='总览', active='overview')


def _render_users(rng='7d'):
    data = _users_data(rng)
    rng_options = ''.join(
        f'<option value="{r}"{" selected" if r == rng else ""}>{lbl}</option>'
        for r, lbl in [('24h', '近 24 小时'), ('7d', '近 7 天'), ('30d', '近 30 天')]
    )
    rows = ''
    for u in data['users']:
        succ_rate = ''
        if (u['ai_ok'] or 0) + (u['ai_fail'] or 0) > 0:
            r = u['ai_ok'] / max(1, (u['ai_ok'] or 0) + (u['ai_fail'] or 0))
            succ_rate = f"{r*100:.0f}%"
        rows += (
            f'<tr>'
            f'<td><a href="/admin/users/{html_escape(u["install_id"])}" class="install-id">{html_escape(u["install_id"])}</a></td>'
            f'<td>{html_escape(u["last_active"] or "")}</td>'
            f'<td style="text-align:right">{u["event_count"]}</td>'
            f'<td style="text-align:right">{u["ai_req"] or 0}</td>'
            f'<td style="text-align:right">{u["ai_ok"] or 0}</td>'
            f'<td style="text-align:right">{u["ai_fail"] or 0}</td>'
            f'<td style="text-align:right">{succ_rate}</td>'
            f'</tr>'
        )
    body = f"""
<form method="get" action="/admin/users" class="filter-bar">
  <label>时间段：</label>
  <select name="range" onchange="this.form.submit()">{rng_options}</select>
</form>
<div class="card">
  <h2>用户列表（按最近活跃排序）</h2>
  <table>
    <thead><tr><th>install_id</th><th>最近活跃</th><th>事件总数</th><th>AI 召唤</th><th>成功</th><th>失败</th><th>成功率</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="7" style="color:#999">暂无活跃用户</td></tr>'}</tbody>
  </table>
</div>
"""
    return _admin_html(body, title='用户列表', active='users')


def _render_user_detail(install_id, rng='24h', events_filter=None):
    data = _user_detail_data(install_id, rng, events_filter or [])
    s = data['summary']
    ledger = s.get('llm') or {}
    rng_options = ''.join(
        f'<option value="{r}"{" selected" if r == rng else ""}>{lbl}</option>'
        for r, lbl in [('24h', '近 24 小时'), ('7d', '近 7 天'), ('30d', '近 30 天')]
    )
    events_filter = events_filter or []
    event_options = [
        ('highlight_saved', '划线'),
        ('sidebar_comment_created', '评论'),
        ('followup_created', '追问'),
        ('ai_reply_requested', '召唤 AI'),
        ('ai_reply_completed', 'AI 完成'),
        ('ai_reply_failed', 'AI 失败'),
        ('ai_reply_feedback_submitted', '赞踩'),
        ('notebook_opened', '笔记'),
        ('page_opened', '打开页面'),
    ]
    event_checkboxes = ''.join(
        f'<label style="margin-right:12px"><input type="checkbox" name="events" value="{ev}" {"checked" if ev in events_filter else ""}> {lbl}</label>'
        for ev, lbl in event_options
    )
    max_n = max((t['n'] for t in data['trend']), default=1)
    trend_html = ''
    for t in data['trend']:
        w = int(400 * t['n'] / max_n) if max_n else 0
        trend_html += (
            f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0;">'
            f'<span style="display:inline-block;width:130px;font-family:monospace;font-size:11px;color:#666;">{html_escape(t["bucket"])}</span>'
            f'<span class="bar" style="width:{w}px"></span>'
            f'<span style="font-size:12px;color:#333;">{t["n"]}</span></div>'
        )
    trend_html = trend_html or '<div style="color:#999">无数据</div>'
    detail_rows = ''
    for d in data['detail']:
        try:
            props = json.loads(d.get('properties_json') or '{}')
        except Exception:
            props = {}
        chips = []
        for k in ['rating', 'requested_via', 'reply_chars_bucket', 'comment_chars_bucket',
                  'selected_text_chars_bucket', 'followup_chars_bucket', 'notebook_route',
                  'error_category', 'elapsed_s_user', 'app_version', 'browser']:
            if k in props and props[k] not in (None, ''):
                cls = 'chip'
                if k == 'rating' and props[k] == 'down': cls += ' fail'
                if k == 'rating' and props[k] == 'up': cls += ' ok'
                chips.append(f'<span class="{cls}">{html_escape(k)}={html_escape(str(props[k]))}</span>')
        feedback_text = props.get('feedback_text') or ''
        feedback_html = f'<div style="margin-top:4px;color:#a40;">"💬 {html_escape(feedback_text)}"</div>' if feedback_text else ''
        detail_rows += (
            f'<tr>'
            f'<td style="font-family:monospace;font-size:11px;white-space:nowrap;">{html_escape(d["created_at"])}</td>'
            f'<td><b>{html_escape(d["event_name"])}</b>{feedback_html}<div style="margin-top:4px">{" ".join(chips)}</div></td>'
            f'<td style="font-size:11px;color:#888;">{html_escape(d.get("surface") or "")}</td>'
            f'</tr>'
        )
    detail_rows = detail_rows or '<tr><td colspan="3" style="color:#999">无事件</td></tr>'
    # LLM ledger 明细（每一次真实 LLM 调用）
    ledger_rows = ''
    for L in data.get('ledger_detail', []):
        status = L.get('status') or ''
        status_chip = '<span class="chip ok">' + status + '</span>' if status == 'success' else '<span class="chip fail">' + status + '</span>'
        cost_str = f"${L.get('cost_usd') or 0:.4f}" if L.get('cost_usd') is not None else '—'
        usage_chip = ''
        if L.get('usage_source') == 'estimated':
            usage_chip = '<span style="font-size:10px;color:#999;">估算</span>'
        elif L.get('usage_source') == 'api':
            usage_chip = '<span style="font-size:10px;color:#262;">真实</span>'
        err_str = ''
        if L.get('error_category'):
            err_str = f' <span class="chip fail">{html_escape(L["error_category"])}/{html_escape(L.get("error_code") or "")}</span>'
        ledger_rows += (
            f'<tr>'
            f'<td style="font-family:monospace;font-size:11px;white-space:nowrap;">{html_escape(L["created_at"])}</td>'
            f'<td>{html_escape(L.get("stage") or "")}</td>'
            f'<td>{html_escape(L.get("role") or "")}{err_str}</td>'
            f'<td>{status_chip}</td>'
            f'<td style="font-size:11px;color:#666;">{html_escape(L.get("model") or "")}</td>'
            f'<td style="text-align:right;font-family:monospace;">{L.get("elapsed_ms") or 0}ms</td>'
            f'<td style="text-align:right;font-family:monospace;">{L.get("input_tokens") or 0} / {L.get("output_tokens") or 0}</td>'
            f'<td style="text-align:right;font-family:monospace;">{cost_str} {usage_chip}</td>'
            f'<td style="font-size:10px;color:#999;">cmt={L.get("comment_id") or "-"}</td>'
            f'</tr>'
        )
    ledger_rows = ledger_rows or '<tr><td colspan="9" style="color:#999">本时间段无 LLM 调用</td></tr>'
    body = f"""
<div class="card">
  <h2 style="font-family:monospace;">{html_escape(install_id)}</h2>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">事件总数</div><div class="value">{s.get('event_count', 0)}</div><div class="sub">{html_escape(rng)}</div></div>
    <div class="kpi"><div class="label">AI 召唤</div><div class="value">{s.get('ai_req', 0) or 0}</div><div class="sub">requested</div></div>
    <div class="kpi"><div class="label">AI 成功 / 失败</div><div class="value">{s.get('ai_ok', 0) or 0} / {s.get('ai_fail', 0) or 0}</div></div>
    <div class="kpi"><div class="label">LLM 花费</div><div class="value">${ledger.get('total_cost_usd', 0) or 0:.4f}</div><div class="sub">{ledger.get('total_tokens', 0)} tokens · {ledger.get('calls', 0)} 次调用</div></div>
  </div>
</div>
<form method="get" action="/admin/users/{html_escape(install_id)}" class="card">
  <h2>筛选</h2>
  <div class="filter-bar">
    <label>时间段：</label>
    <select name="range">{rng_options}</select>
    <button type="submit">应用</button>
  </div>
  <div style="margin-top:10px;">{event_checkboxes}</div>
</form>
<div class="card">
  <h2>趋势（{'按小时' if rng == '24h' else '按天'}）</h2>
  {trend_html}
</div>
<div class="card">
  <h2>LLM 调用明细（最近 50 次，含 router/answer 分阶段）</h2>
  <p style="font-size:11px;color:#888;margin-bottom:8px;">"估算" = codex_cli / claude_code 直连场景，按 prompt+output 字符近似（中文 1 token/字、英文 4 字符/token，价格按 sonnet 估）。等用户走 OpenAI-兼容 API 时会自动切到真实 usage。</p>
  <table>
    <thead><tr>
      <th>时间</th><th>stage</th><th>role</th><th>状态</th><th>model</th>
      <th style="text-align:right">耗时</th><th style="text-align:right">in/out tokens</th>
      <th style="text-align:right">cost</th><th>关联</th>
    </tr></thead>
    <tbody>{ledger_rows}</tbody>
  </table>
</div>
<div class="card">
  <h2>事件明细（最近 500 条）</h2>
  <table>
    <thead><tr><th>时间</th><th>事件</th><th>surface</th></tr></thead>
    <tbody>{detail_rows}</tbody>
  </table>
</div>
"""
    return _admin_html(body, title=f'用户 {install_id}', active='users')


def _render_feedback():
    data = _feedback_data(200)
    rows = ''
    for f in data['feedback']:
        rating_chip = '<span class="chip fail">踩</span>' if f.get('rating') == 'down' else '<span class="chip ok">赞</span>'
        rows += (
            f'<tr>'
            f'<td style="font-family:monospace;font-size:11px;white-space:nowrap;">{html_escape(f["created_at"])}</td>'
            f'<td><a href="/admin/users/{html_escape(f["anonymous_install_id"] or "")}" class="install-id">{html_escape(f.get("anonymous_install_id") or "")}</a></td>'
            f'<td>{rating_chip}</td>'
            f'<td style="font-size:13px;line-height:1.5;">{html_escape(f.get("feedback_text") or "")}</td>'
            f'<td style="font-size:11px;color:#888;">{html_escape(f.get("reply_chars_bucket") or "")}</td>'
            f'</tr>'
        )
    rows = rows or '<tr><td colspan="5" style="color:#999">暂无反馈</td></tr>'
    body = f"""
<div class="card">
  <h2>赞踩反馈 inbox（最近 200 条）</h2>
  <table>
    <thead><tr><th>时间</th><th>install_id</th><th>评分</th><th>用户写的反馈</th><th>回复长度</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
"""
    return _admin_html(body, title='反馈 inbox', active='feedback')


def html_escape(s):
    return (
        str(s if s is not None else '')
        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        .replace('"', '&quot;').replace("'", '&#39;')
    )


def admin_handle(handler, path, parsed):
    """路由所有 /admin/* 请求，handler 已验过 token。"""
    qs = urllib.parse.parse_qs(parsed.query)
    rng = (qs.get('range', ['24h'])[0]).lower()
    if rng not in ('24h', '7d', '30d'):
        rng = '24h'
    try:
        if path == '/admin' or path == '/admin/':
            handler.serve_admin_html(_render_overview(rng))
            return
        if path == '/admin/users':
            handler.serve_admin_html(_render_users(rng if rng != '24h' else '7d'))
            return
        if path == '/admin/feedback':
            handler.serve_admin_html(_render_feedback())
            return
        if path == '/admin/api/overview.json':
            handler.serve_admin_json(_overview_data(rng))
            return
        if path == '/admin/api/users.json':
            handler.serve_admin_json(_users_data(rng if rng != '24h' else '7d'))
            return
        m = re.match(r'^/admin/users/([A-Za-z0-9_\-]+)/?$', path)
        if m:
            install_id = m.group(1)
            events_filter = _safe_event_names(','.join(qs.get('events', [])))
            handler.serve_admin_html(_render_user_detail(install_id, rng, events_filter))
            return
        m = re.match(r'^/admin/api/user/([A-Za-z0-9_\-]+)\.json$', path)
        if m:
            install_id = m.group(1)
            events_filter = _safe_event_names(','.join(qs.get('events', [])))
            handler.serve_admin_json(_user_detail_data(install_id, rng, events_filter))
            return
        handler.send_response(404); handler.end_headers(); handler.wfile.write(b'Not found')
    except Exception as e:
        handler.serve_admin_json({'error': str(e)[:500]}, status=500)


if __name__ == '__main__':
    os.chdir(ROOT)
    # ThreadingHTTPServer：admin 慢查询不会卡住普通用户的 topics 阅读
    server = http.server.ThreadingHTTPServer(('localhost', PORT), Handler)
    print(f"✓ 知识库服务器启动：http://localhost:{PORT}")
    print(f"  行为数据记录至：{BEHAVIOR_LOG}")
    if ADMIN_TOKEN:
        print(f"  Margin 运营后台：http://localhost:{PORT}/admin?token=<MARGIN_ADMIN_TOKEN>")
    else:
        print(f"  ⚠ 未设 MARGIN_ADMIN_TOKEN，运营后台被禁用（路径全部返回 404）")
    print(f"  Ctrl+C 停止")
    server.serve_forever()
