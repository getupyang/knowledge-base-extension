# 2A.6 characterization：问题反馈报告链路（场景清单 E 组）
# 实读代码后的精确现状（2026-08-13）：
#   后端默认 include_* 全 False（默认不带正文）——安全的一侧在后端。
#   "四类附件默认全勾"是前端 UI 层的坑（content/index.js `?? true`），E2/4.4 只需改前端。
#   提交强制 confirm_consent，否则 400。
import sqlite3
from datetime import datetime
from pathlib import Path


def _seed_comment(agent_api):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(agent_api.DB_PATH)
    cur = conn.execute(
        "INSERT INTO comments (page_url, page_title, selected_text, surrounding_text, comment, created_at, updated_at) "
        "VALUES ('https://report.test/p', '报告测试页', '划线原文-隐私', '前后文-隐私', '批注正文-隐私', ?, ?)",
        (now, now),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def _client(agent_api):
    from fastapi.testclient import TestClient
    return TestClient(agent_api.app)


def test_e1_preview_exists_and_defaults_carry_no_content(hermetic):
    """E1：预览机制存在；后端默认（不勾任何附件）时预览不含任何正文"""
    agent_api = hermetic.agent_api
    cid = _seed_comment(agent_api)
    resp = _client(agent_api).post("/debug/problem-reports/preview", json={"comment_id": cid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["preview"]["default_sent"], "预览应列出默认发送的诊断项"
    assert data["preview"]["optional_sent"] == [], "后端默认不带任何正文附件"
    assert data["attachments"] == {}, "默认预览的附件应为空"
    # 正文关键词绝不出现在默认 diagnostic 里
    diag_text = str(data["diagnostic"])
    for secret in ("划线原文-隐私", "批注正文-隐私", "前后文-隐私"):
        assert secret not in diag_text, f"默认诊断包泄漏正文：{secret}"


def test_e1b_checked_attachments_shown_in_preview(hermetic):
    """E1b：勾选附件后，预览如实展示将要发送的正文（用户能看到发什么）"""
    agent_api = hermetic.agent_api
    cid = _seed_comment(agent_api)
    resp = _client(agent_api).post(
        "/debug/problem-reports/preview",
        json={"comment_id": cid, "include_selection": True},
    )
    data = resp.json()
    assert "划线与前后文" in data["preview"]["optional_sent"]
    assert data["attachments"]["selection"]["selected_text"] == "划线原文-隐私"


def test_e1c_submit_requires_explicit_consent(hermetic):
    """E1c：不带 confirm_consent 的提交被 400 拒绝——确认机制是后端强制，不是 UI 装饰"""
    agent_api = hermetic.agent_api
    cid = _seed_comment(agent_api)
    resp = _client(agent_api).post("/debug/problem-reports", json={"comment_id": cid})
    assert resp.status_code == 400


def test_e1d_submit_writes_local_report(hermetic):
    """E1d：确认后提交 → 本地 support_reports 落库（云同步在测试环境已禁用）"""
    agent_api = hermetic.agent_api
    cid = _seed_comment(agent_api)
    resp = _client(agent_api).post(
        "/debug/problem-reports",
        json={"comment_id": cid, "confirm_consent": True, "rating": "bad", "user_note": "测试反馈"},
    )
    assert resp.status_code == 200
    conn = sqlite3.connect(agent_api.DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM support_reports WHERE comment_id=?", (cid,)
    ).fetchone()
    conn.close()
    assert row[0] == 1, "提交后应有且仅有一条本地报告"
