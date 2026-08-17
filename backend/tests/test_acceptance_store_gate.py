# 2B acceptance：上架门禁红名单（场景清单 F/G/D2/E2/H 组）
# 这些测的是【还没实现的目标行为】——现在就该 xfail（预期失败）。
# 全部用 strict=True：一旦对应块（3.x/4.x）落地、行为达标，xfail 变 XPASS 会让套件
# 报错，强制把标记摘掉转成正式绿测——"转绿"因此是机械动作，不靠人记得。
# 上架门禁 = 本文件所有 xfail 标记清零。
# 防清单腐烂：kb-regression 会核对本文件的 通过/xfail/skip 数量清单（见 GATE_MANIFEST），
# 整文件被删或测试静默消失时回归门直接红。
# 2026-08-13 Codex review 修订：F1 加恶意预检+状态断言；F2 覆盖读写两端点 + 反"一刀切
# 拒绝"探测；G2b 断言响应状态与编辑内容保留；D2 升级为端点级出站行为验证。
import json
import sqlite3
import uuid
from pathlib import Path

import pytest


def _client(agent_api):
    from fastapi.testclient import TestClient
    return TestClient(agent_api.app)


# ── F 组 · 安全：谁能跟本地引擎说话（由第 3 块 3.1/3.3 转绿）──

def test_f0_health_stays_open(hermetic, monkeypatch):
    """F0【护栏·常绿】/health 无认证可达（即使强制开关开启）——防止安全修复做成
    '一刀切全拒'。配对流程（1.3 协议）依赖未认证客户端能探活。此测试变红 = 修过头了。"""
    resp = _client(hermetic.agent_api).get("/health")
    assert resp.status_code == 200
    monkeypatch.setenv("MEMAI_REQUIRE_TOKEN", "on")
    resp = _client(hermetic.agent_api).get("/health")
    assert resp.status_code == 200, "强制开关开启后 /health 仍应免鉴权"


def test_f1_malicious_origin_rejected(hermetic):
    """F1【✅ 3.1 已转绿 2026-08-13】恶意网页的简单请求与 CORS 预检都不应获得跨域许可"""
    client = _client(hermetic.agent_api)
    evil = "https://evil.example"
    # 简单请求：服务可用（200）但不给跨域头
    resp = client.get("/health", headers={"Origin": evil})
    assert resp.status_code == 200, "端点本身应可用——拒的是跨域许可，不是服务"
    allowed = resp.headers.get("access-control-allow-origin", "")
    assert allowed not in ("*", evil), f"恶意 Origin 获得了跨域许可：{allowed}"
    # 预检请求：带自定义认证头的 OPTIONS 也不应放行
    pre = client.options("/comments", headers={
        "Origin": evil,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-margin-token",
    })
    pre_allowed = pre.headers.get("access-control-allow-origin", "")
    assert pre_allowed not in ("*", evil), f"恶意预检获得了跨域许可：{pre_allowed}"


def test_f1b_trusted_origins_still_allowed(hermetic):
    """F1b【✅ 3.1 常绿护栏】信任来源不被误伤：扩展来源（TOFU 期）与阅读界面 8765 拿到跨域许可"""
    client = _client(hermetic.agent_api)
    ext_origin = "chrome-extension://" + "a" * 32
    for origin in (ext_origin, "http://localhost:8765"):
        resp = client.get("/health", headers={"Origin": origin})
        assert resp.headers.get("access-control-allow-origin") == origin, \
            f"信任来源 {origin} 未获跨域许可——修过头会打断插件/阅读界面"


def test_f1c_extension_whitelist_closes_tofu(hermetic, monkeypatch):
    """F1c【✅ 3.1 常绿护栏】设置 MEMAI_ALLOWED_EXTENSION_IDS 后 TOFU 关闭：
    名单内扩展放行，名单外扩展拒绝（商店发布后 installer 写入商店 ID 即收口）"""
    client = _client(hermetic.agent_api)
    listed = "b" * 32
    monkeypatch.setenv("MEMAI_ALLOWED_EXTENSION_IDS", listed)
    ok = client.get("/health", headers={"Origin": f"chrome-extension://{listed}"})
    assert ok.headers.get("access-control-allow-origin") == f"chrome-extension://{listed}"
    stranger = client.get("/health", headers={"Origin": "chrome-extension://" + "c" * 32})
    assert not stranger.headers.get("access-control-allow-origin"), \
        "白名单模式下名单外扩展不应获得跨域许可"


def test_f2_missing_token_rejected_read_and_write(hermetic, monkeypatch):
    """F2【✅ 3.3 已转绿 2026-08-13】强制开关开启后，没有配对暗号的请求被 401 拒——读写都拒"""
    monkeypatch.setenv("MEMAI_REQUIRE_TOKEN", "on")
    client = _client(hermetic.agent_api)
    read = client.get("/comments")
    assert read.status_code in (401, 403), f"无 token 读请求应被拒，实际 HTTP {read.status_code}"
    write = client.post("/captures/save", json={
        "title": "未认证写入", "url": f"https://auth.test/{uuid.uuid4().hex}", "platform": "博客",
    })
    assert write.status_code in (401, 403), f"无 token 写请求应被拒，实际 HTTP {write.status_code}"


def test_f3_token_matrix(hermetic, monkeypatch):
    """F3【✅ 3.3 升级为真矩阵】正确 token 通过 / 错误 token 401 / 开关关闭时不校验"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    real_token = agent_api._pair_token()
    assert real_token, "引擎启动时应已自动生成配对 token"
    monkeypatch.setenv("MEMAI_REQUIRE_TOKEN", "on")
    ok = client.get("/comments", headers={"X-Margin-Token": real_token})
    assert ok.status_code == 200, "正牌 token 应通过"
    bad = client.get("/comments", headers={"X-Margin-Token": "wrong-token"})
    assert bad.status_code == 401, "错误 token 应 401"
    monkeypatch.setenv("MEMAI_REQUIRE_TOKEN", "off")
    legacy = client.get("/comments")
    assert legacy.status_code == 200, "开关关闭时保持存量兼容"


def test_f4_pair_endpoint_protocol(hermetic, monkeypatch):
    """F4【✅ 3.3】/pair 配对协议：扩展来源领取 / 网页来源被拒 / 白名单收口 / 幂等同 token"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    ext = "chrome-extension://" + "d" * 32
    # 扩展来源领取成功，且与引擎侧 token 一致
    r1 = client.post("/pair", headers={"Origin": ext})
    assert r1.status_code == 200 and r1.json()["token"] == agent_api._pair_token()
    # 幂等：再领是同一个
    r2 = client.post("/pair", headers={"Origin": ext})
    assert r2.json()["token"] == r1.json()["token"]
    # 网页来源被拒
    web = client.post("/pair", headers={"Origin": "https://evil.example"})
    assert web.status_code == 403
    # 白名单收口：名单外扩展被拒
    monkeypatch.setenv("MEMAI_ALLOWED_EXTENSION_IDS", "e" * 32)
    stranger = client.post("/pair", headers={"Origin": ext})
    assert stranger.status_code == 403, "白名单设置后名单外扩展不应能配对"


def test_f5_allowlist_closure_invalidates_earlier_tokens(hermetic, monkeypatch):
    """F5【✅ 第 4 块已转绿 2026-08-17】TOFU 期发出的 token 在白名单收口后失效——
    白名单变更即轮换 token（懒轮换：下一次校验/配对时触发），合法扩展经 401→/pair 自愈。"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    token_issued_during_tofu = agent_api._pair_token()
    monkeypatch.setenv("MEMAI_ALLOWED_EXTENSION_IDS", "f" * 32)  # 收口
    monkeypatch.setenv("MEMAI_REQUIRE_TOKEN", "on")
    resp = client.get("/comments", headers={"X-Margin-Token": token_issued_during_tofu})
    assert resp.status_code == 401, "收口前发出的 token 仍然有效——需要轮换机制"


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
        assert resp.json().get("success") is True
    conn = sqlite3.connect(agent_api.DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM comments WHERE page_url=?", (url,)).fetchone()[0]
    conn.close()
    assert n == 1, f"完全相同的内容提交两次产生 {n} 条（应幂等=1）"


@pytest.mark.xfail(strict=True, reason="G2b 由 4.3 稳定 UUID 转绿：内容判重扛不住离线编辑——改过文字的同一批注补传会存成两条")
def test_g2b_edited_capture_resubmit_needs_stable_uuid(hermetic):
    """G2b【真缺口】用户离线改了批注文字再补传（同一 client 侧 UUID、内容不同）→
    仍应只有一条，且保留的是【编辑后】的文字（去重不能变成丢编辑）。
    当前按内容三元组判重，内容一变即判为新记录。4.3 需引入稳定 client UUID
    （字段名以实现为准，实现时同步更新本测试）。"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    url = f"https://offline.test/{uuid.uuid4().hex}"
    base = {"title": "离线补传", "url": url, "platform": "博客", "excerpt": "e",
            "client_capture_id": "cap_fixed_uuid_002"}
    for thought in ("第一版想法", "改过的想法"):
        resp = client.post("/captures/save", json={**base, "thought": thought})
        assert resp.status_code == 200, f"保存失败 HTTP {resp.status_code}（失败不能冒充幂等）"
        assert resp.json().get("success") is True
    conn = sqlite3.connect(agent_api.DB_PATH)
    rows = conn.execute(
        "SELECT comment FROM comments WHERE page_url=?", (url,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"同一 UUID 编辑后补传产生 {len(rows)} 条记录（应按 UUID 幂等=1）"
    assert "改过的想法" in rows[0][0], "去重后保留的应是编辑后的内容，不能丢编辑"


# ── D2 · telemetry 白名单（由第 4 块 4.4 转绿）──

@pytest.mark.xfail(strict=True, reason="D2 由 4.4 白名单化转绿：当前黑名单语义，未知字段放行并落库")
def test_d2_unknown_telemetry_keys_never_stored(hermetic):
    """D2：端点级出站行为——未知字段经 POST /telemetry/events 后不应出现在任何落库数据里
    （Codex review #8：测出站行为，不测内部函数）"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    marker_key = "some_future_field_nobody_reviewed"
    resp = client.post("/telemetry/events", json={
        "event_name": "d2_acceptance_probe",
        "anonymous_install_id": "test-install-d2",
        "properties": {marker_key: "should-not-leave", "surface_kind": "test"},
    })
    assert resp.status_code == 200
    conn = sqlite3.connect(agent_api.DB_PATH)
    row = conn.execute("SELECT * FROM telemetry_outbox ORDER BY rowid DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "事件应有落库（拒的是字段，不是事件）"
    assert marker_key not in json.dumps([str(v) for v in row]), \
        f"未知字段 {marker_key} 被存进了出站队列（白名单应拒收）"


# ── E2 · support report 默认不带正文（由第 4 块 4.4 转绿）──

@pytest.mark.xfail(strict=True, reason="E2 由 4.4 转绿：当前前端四类附件默认全勾（?? true）。⚠ 静态文本检查是弱验证，4.4 落地时必须配 Playwright 真点验收")
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
