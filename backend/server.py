#!/usr/bin/env python3
"""
调研知识库本地服务器
运行：python3 server.py
访问：http://localhost:8765
"""

import http.server
import os
import json
import urllib.parse
from datetime import datetime
import markdown

# 优先用 RESEARCH_DIR 环境变量（从 ~/.kb_config 读取），否则用脚本所在目录
ROOT = os.environ.get("RESEARCH_DIR", os.path.dirname(os.path.abspath(__file__)))
PORT = 8765
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
    "知识库助手 CLAUDE.md": os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CLAUDE.md"),
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


if __name__ == '__main__':
    os.chdir(ROOT)
    server = http.server.HTTPServer(('localhost', PORT), Handler)
    print(f"✓ 知识库服务器启动：http://localhost:{PORT}")
    print(f"  行为数据记录至：{BEHAVIOR_LOG}")
    print(f"  Ctrl+C 停止")
    server.serve_forever()
