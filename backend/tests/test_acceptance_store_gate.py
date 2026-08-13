# 2B acceptance：上架门禁红名单（场景清单 F/G/D2/E2/H 组）
# 这些测的是【还没实现的目标行为】——现在就该 xfail（预期失败）。
# 全部用 strict=True：一旦对应块（3.x/4.x）落地、行为达标，xfail 变 XPASS 会让套件
# 报错，强制把标记摘掉转成正式绿测——"转绿"因此是机械动作，不靠人记得。
# 上架门禁 = 本文件所有 xfail 标记清零。
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest


def _client(agent_api):
    from fastapi.testclient import TestClient
    return TestClient(agent_api.app)


# ── F 组 · 安全：谁能跟本地引擎说话（由第 3 块 3.1/3.3 转绿）──

@pytest.mark.xfail(strict=True, reason="F1 由 3.1 CORS 收紧转绿：当前 allow_origins=['*']，恶意网页放行")
def test_f1_malicious_origin_rejected(hermetic):
    """F1：随便一个恶意网页向引擎发请求 → 不应获得跨域许可"""
    resp = _client(hermetic.agent_api).get("/health", headers={"Origin": "https://evil.example"})
    allowed = resp.headers.get("access-control-allow-origin", "")
    assert allowed not in ("*", "https://evil.example"), \
        f"恶意 Origin 获得了跨域许可：{allowed}"


@pytest.mark.xfail(strict=True, reason="F2 由 3.3 token 落地转绿：当前后端无任何认证")
def test_f2_missing_token_rejected(hermetic):
    """F2：没有配对暗号（X-Margin-Token）的请求 → 应被 401/403 拒绝"""
    resp = _client(hermetic.agent_api).get("/comments")
    assert resp.status_code in (401, 403), \
        f"无 token 请求应被拒，实际 HTTP {resp.status_code}"


def test_f3_request_with_token_header_passes(hermetic):
    """F3：带 token 头的请求正常通过（现在无认证也通过；3.3 后此测试用真配对 token 仍应绿）"""
    resp = _client(hermetic.agent_api).get("/comments", headers={"X-Margin-Token": "placeholder"})
    assert resp.status_code == 200


# ── G 组 · 离线同步（由第 4 块 4.3 转绿）──

def test_g2a_identical_resubmit_already_idempotent(hermetic):
    """G2a【钉现状·已成立】一字不差的重复提交幂等——按内容三元组(URL+划线+批注)判重。
    写测试时发现该能力已存在（_insert_local_comment_if_missing），转为绿测保护它。"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    url = f"https://offline.test/{uuid.uuid4().hex}"
    payload = {"title": "离线补传", "url": url, "platform": "博客", "excerpt": "e", "thought": "t"}
    for _ in range(2):
        resp = client.post("/captures/save", json=payload)
        assert resp.status_code == 200, f"保存失败 HTTP {resp.status_code}"
    conn = sqlite3.connect(agent_api.DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM comments WHERE page_url=?", (url,)).fetchone()[0]
    conn.close()
    assert n == 1, f"完全相同的内容提交两次产生 {n} 条（应幂等=1）"


@pytest.mark.xfail(strict=True, reason="G2b 由 4.3 稳定 UUID 转绿：内容判重扛不住离线编辑——改过文字的同一批注补传会存成两条")
def test_g2b_edited_capture_resubmit_needs_stable_uuid(hermetic):
    """G2b【真缺口】用户离线改了批注文字再补传（同一 client 侧 UUID、内容不同）→ 仍应只有一条。
    当前按内容三元组判重，内容一变即判为新记录。4.3 需引入稳定 client UUID 判重
    （字段名以实现为准，实现时同步更新本测试）。"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    url = f"https://offline.test/{uuid.uuid4().hex}"
    base = {"title": "离线补传", "url": url, "platform": "博客", "excerpt": "e",
            "client_capture_id": "cap_fixed_uuid_002"}
    client.post("/captures/save", json={**base, "thought": "第一版想法"})
    client.post("/captures/save", json={**base, "thought": "改过的想法"})
    conn = sqlite3.connect(agent_api.DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM comments WHERE page_url=?", (url,)).fetchone()[0]
    conn.close()
    assert n == 1, f"同一 UUID 编辑后补传产生 {n} 条记录（应按 UUID 幂等=1）"


# ── D2 · telemetry 白名单（由第 4 块 4.4 转绿）──

@pytest.mark.xfail(strict=True, reason="D2 由 4.4 白名单化转绿：当前黑名单语义，未知字段放行")
def test_d2_unknown_telemetry_keys_rejected(hermetic):
    """D2：名单之外的任何字段一律拒收——没批准过的字段根本传不出去"""
    cleaned = hermetic.agent_api._scrub_telemetry_properties(
        {"some_future_field_nobody_reviewed": "leaks?"}
    )
    assert cleaned == {}, f"未知字段应被拒收，实际放行：{list(cleaned)}"


# ── E2 · support report 默认不带正文（由第 4 块 4.4 转绿）──

@pytest.mark.xfail(strict=True, reason="E2 由 4.4 转绿：当前前端四类附件默认全勾（?? true）")
def test_e2_frontend_report_defaults_unchecked(hermetic):
    """E2：用户不做任何勾选动作 → 前端默认不带任何正文附件"""
    content = (Path(__file__).resolve().parents[2] / "src" / "content" / "index.js").read_text(encoding="utf-8")
    unchecked_defaults = [
        "current.include_conversation ?? false",
        "current.include_selection ?? false",
        "current.include_page_info ?? false",
        "current.include_model_io ?? false",
    ]
    missing = [m for m in unchecked_defaults if m not in content]
    assert not missing, f"前端默认仍是勾选态，未翻转：{missing}"
    # 翻转完成后，请同步删除 test_support_report.py 里钉 ?? true 现状的 test_e2_pin


# ── H 组 · 商店包 E2E（依赖 5.1 先产出 zip，暂无法执行）──

@pytest.mark.skip(reason="H1 依赖 5.1 精确 allowlist 打包脚本产出 zip 后才能执行（真解包装 Chrome 跑核心流程）")
def test_h1_store_zip_end_to_end(hermetic):
    """H1：用真正提交商店的 zip 解包装进 Chrome → 划线→批注→AI 回复完整可用"""
    raise NotImplementedError
