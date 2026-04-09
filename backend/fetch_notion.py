#!/usr/bin/env python3
"""
从Notion知识库读取用户批注，输出给Claude阅读。
用法：python3 fetch_notion.py
"""

import urllib.request
import urllib.parse
import json
from datetime import datetime

import os

# 从 ~/.kb_config 或环境变量读取，不硬编码
_config_file = os.path.expanduser("~/.kb_config")
if os.path.exists(_config_file):
    with open(_config_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

def notion_request(endpoint, method="GET", data=None):
    url = f"https://api.notion.com/v1/{endpoint}"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, headers=headers, data=body, method=method)
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())

def get_text(prop):
    """提取Notion rich_text或title字段的纯文本"""
    if not prop:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(i.get("text", {}).get("content", "") for i in items)

def fetch_all_entries():
    """拉取数据库所有条目"""
    entries = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = notion_request(f"databases/{DATABASE_ID}/query", method="POST", data=payload)
        entries.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return entries

def format_entries(entries):
    """格式化为Claude可读的文本"""
    lines = [f"# Notion知识库批注 · 拉取时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    lines.append(f"共 {len(entries)} 条记录\n")
    lines.append("---\n")

    for e in entries:
        props = e.get("properties", {})
        title = get_text(props.get("标题", {}))
        platform = props.get("来源平台", {}).get("select", {})
        platform_name = platform.get("name", "") if platform else ""
        url = props.get("来源URL", {}).get("url", "") or ""
        excerpt = get_text(props.get("原文片段", {}))
        thought = get_text(props.get("我的想法", {}))
        ai_conv = get_text(props.get("评论区对话", {}))
        created = e.get("created_time", "")[:10]

        lines.append(f"## {title or '(无标题)'}")
        lines.append(f"- 来源：{platform_name} | {created}")
        if url:
            lines.append(f"- URL：{url}")
        if excerpt:
            lines.append(f"- 原文：{excerpt[:300]}{'...' if len(excerpt) > 300 else ''}")
        if thought:
            lines.append(f"- **我的想法：{thought}**")
        if ai_conv:
            lines.append(f"- 评论区对话：{ai_conv[:200]}{'...' if len(ai_conv) > 200 else ''}")
        lines.append("")

    return "\n".join(lines)

if __name__ == "__main__":
    print("正在从Notion拉取数据...")
    entries = fetch_all_entries()
    output = format_entries(entries)

    # 写入文件供Claude读取
    root = os.environ.get("RESEARCH_DIR", os.path.dirname(os.path.abspath(__file__)))
    outfile = os.path.join(root, ".notion_cache.md")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(output)

    print(output)
    print(f"\n✓ 已缓存至 {outfile}")
