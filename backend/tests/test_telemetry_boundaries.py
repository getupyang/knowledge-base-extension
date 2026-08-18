# 2A.5 → 4.4 演进：telemetry 隐私边界 = 字段白名单（场景 D 组）
# 2026-08-18 黑名单升级白名单后本文件同步改写：
#   只有显式入册的脱敏字段能出门；正文类字段天然不在册；未知字段一律丢弃并打日志。
#   feedback_text 仍是唯一放行明文（截断 2000）。


def test_d_whitelisted_keys_pass(hermetic):
    """在册字段正常通过（环境/bucket/布尔类）"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    props = {"status": "ok", "browser": "chrome", "has_selected_text": True,
             "reply_chars_bucket": "500-1000"}
    assert scrub(props) == props


def test_d_content_keys_never_in_allowlist(hermetic):
    """正文类字段（划线/URL/批注/回复原文）不在册 → 被丢弃，事件保留在册部分"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    cleaned = scrub({
        "status": "ok",
        "selected_text": "用户划线的秘密内容",
        "url": "https://private.example/page",
        "comment": "用户批注正文",
        "reply_content": "AI 回复正文",
        "page_text": "整页内容",
    })
    assert cleaned == {"status": "ok"}, f"正文类字段应全部丢弃，实际保留：{list(cleaned)}"


def test_d_unknown_keys_rejected(hermetic):
    """【白名单核心】任何名单外字段一律丢弃——没过隐私审查的字段根本传不出去"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    assert scrub({"some_future_field_nobody_reviewed": "leaks?"}) == {}


def test_d_case_insensitive(hermetic):
    """匹配不区分大小写（Status 在册、Selected_Text 不在册）"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    assert scrub({"Status": "ok", "Selected_Text": "x"}) == {"Status": "ok"}


def test_d_feedback_text_allowed_but_truncated(hermetic):
    """feedback_text 唯一放行明文，超长截断到 2000"""
    agent_api = hermetic.agent_api
    cleaned = agent_api._scrub_telemetry_properties({"feedback_text": "长" * 3000})
    assert len(cleaned["feedback_text"]) == agent_api._TELEMETRY_FEEDBACK_TEXT_MAX


def test_d_nondict_rejected(hermetic):
    """properties 不是字典时整体归空"""
    scrub = hermetic.agent_api._scrub_telemetry_properties
    assert scrub("not a dict") == {}
    assert scrub(None) == {}
