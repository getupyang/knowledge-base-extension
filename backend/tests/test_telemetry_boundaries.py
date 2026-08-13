# 2A.5 characterization：telemetry 隐私边界现状 = 禁词黑名单（场景清单 D1）
# 真实语义（比场景表述更细，2026-08-13 实读代码钉住）：
#   事件不整条拒收——违禁【字段】被剔除，事件其余部分保留。
#   feedback_text 是唯一放行的明文字段（截断 2000）。
#   未知字段直接放行 ← 这正是黑名单与白名单(D2/2B.4)的差别，D2 落地后本组同步升级。


def test_d1_forbidden_content_keys_dropped(hermetic):
    """D1 核心：正文类禁词字段（划线原文/URL/批注正文/AI回复）被剔除，事件保留"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    cleaned = scrub({
        "action": "click",
        "selected_text": "用户划线的秘密内容",
        "url": "https://private.example/page",
        "comment": "用户批注正文",
        "reply_content": "AI 回复正文",
        "page_text": "整页内容",
    })
    assert cleaned == {"action": "click"}, f"禁词字段应全部剔除，实际保留：{list(cleaned)}"


def test_d1_forbidden_keys_case_insensitive(hermetic):
    """禁词匹配不区分大小写（Selected_Text 也拦）"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    assert scrub({"Selected_Text": "x", "URL": "y", "ok_field": 1}) == {"ok_field": 1}


def test_d1_feedback_text_allowed_but_truncated(hermetic):
    """feedback_text 是唯一放行明文，超长截断到 2000"""
    agent_api = hermetic.agent_api
    cleaned = agent_api._scrub_telemetry_properties({"feedback_text": "长" * 3000})
    assert len(cleaned["feedback_text"]) == agent_api._TELEMETRY_FEEDBACK_TEXT_MAX


def test_d1_underscore_and_nondict_rejected(hermetic):
    """下划线开头字段剔除；properties 不是字典时整体归空"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    assert scrub({"_internal": "x", "ok": 1}) == {"ok": 1}
    assert scrub("not a dict") == {}
    assert scrub(None) == {}


def test_d1_pin_blacklist_lets_unknown_keys_through(hermetic):
    """【钉现状·黑名单语义】未知字段直接放行——这就是 D2 白名单要关掉的口子。
    D2（4.4 白名单化）落地后本断言应改写为：未知字段被拒。它变红=白名单已生效。"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    cleaned = scrub({"some_future_field_nobody_reviewed": "leaks?"})
    assert cleaned == {"some_future_field_nobody_reviewed": "leaks?"}
