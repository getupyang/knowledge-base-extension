#!/usr/bin/env python3
"""_build_dialog_comment 单元测试 — 多轮追问压平 bug 的修复验证。

覆盖：
  - 首轮（无 replies）→ 原样返回首评
  - 正常多轮 → 用户/AI 按时间交错，终于用户消息
  - 错误兜底回复（Agent 超时/执行出错/⚠️）不进对话
  - 超长 AI 轮截断，用户轮不截断
  - 末尾 AI 轮丢弃（重新生成场景 = 重答最后一条用户消息）
  - 只有 AI 回复没有追问（首轮重答）→ 返回首评
"""
import os
import sqlite3
import tempfile

import pytest

# 隔离 DB：必须在 import agent_api 前设好（模块 import 时确定 DB_PATH 并 init_db）
_TMP = tempfile.mkdtemp(prefix="kb-dialog-test-")
os.environ["KB_DATA_DIR"] = _TMP
os.environ["MARGIN_CLOUD_ENDPOINT"] = "disabled"
os.environ["KB_DISABLE_LEGACY_DB_MIGRATION"] = "1"

import agent_api  # noqa: E402


def _mk_thread(first_comment="首轮问题", replies=()):
    """建一条 comment + 按序插入 replies，返回 comment_id。"""
    conn = sqlite3.connect(agent_api.DB_PATH)
    cur = conn.execute(
        "INSERT INTO comments (page_url, comment, status, created_at, updated_at) "
        "VALUES ('http://t', ?, 'open', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (first_comment,),
    )
    cid = cur.lastrowid
    for i, (author, content) in enumerate(replies):
        conn.execute(
            "INSERT INTO replies (comment_id, author, content, created_at) VALUES (?, ?, ?, ?)",
            (cid, author, content, f"2026-01-01T00:00:{i:02d}"),
        )
    conn.commit()
    conn.close()
    return cid


def test_first_round_returns_original():
    cid = _mk_thread("只有首评")
    assert agent_api._build_dialog_comment(cid, "只有首评") == "只有首评"


def test_multiturn_interleaved_order():
    cid = _mk_thread("u1", [("agent", "a1"), ("user", "u2")])
    out = agent_api._build_dialog_comment(cid, "u1")
    # 结构：说明头 + [用户]u1 + [AI]a1 + [用户]u2
    assert out.index("[用户]\nu1") < out.index("[AI]\na1") < out.index("[用户]\nu2")
    # 压平 bug 的旧痕迹不能出现
    assert "---追问---" not in out


def test_error_replies_excluded():
    cid = _mk_thread("u1", [
        ("agent", "Agent 超时（30分钟），请重试。"),
        ("agent", "Agent 执行出错（returncode=1）：xxx"),
        ("agent", "⚠️ 召唤 AI 时出错了"),
        ("user", "u2"),
    ])
    out = agent_api._build_dialog_comment(cid, "u1")
    assert "Agent 超时" not in out
    assert "Agent 执行出错" not in out
    assert "⚠️" not in out
    assert "[用户]\nu2" in out


def test_long_ai_turn_truncated_user_turn_not():
    long_ai = "字" * 5000
    long_user = "问" * 5000
    cid = _mk_thread("u1", [("agent", long_ai), ("user", long_user)])
    out = agent_api._build_dialog_comment(cid, "u1")
    assert "已截断" in out
    assert "字" * 1201 not in out          # AI 轮被截
    assert "问" * 5000 in out              # 用户轮完整保留


def test_trailing_ai_turns_dropped_for_regenerate():
    cid = _mk_thread("u1", [("agent", "a1"), ("user", "u2"), ("agent", "a2-要被重答的旧回复")])
    out = agent_api._build_dialog_comment(cid, "u1")
    assert "a2-要被重答的旧回复" not in out
    assert out.rstrip().endswith("u2")


def test_only_ai_replies_returns_original():
    """首轮重新生成：只有 AI 回复、无追问 → 等价首轮"""
    cid = _mk_thread("u1", [("agent", "a1")])
    assert agent_api._build_dialog_comment(cid, "u1") == "u1"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
