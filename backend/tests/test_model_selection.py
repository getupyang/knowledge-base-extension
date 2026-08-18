# 模型档位（PRD 2026-08-18）：解析矩阵 / 配置端点往返 / 双层防腐兜底
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _llm(hermetic):
    sys.path.insert(0, str(BACKEND_DIR))
    import llm_client
    return llm_client


def _client(agent_api):
    from fastapi.testclient import TestClient
    return TestClient(agent_api.app)


_EXT_HEADERS = {"Origin": "chrome-extension://" + "a" * 32}


def test_model_resolution_matrix(hermetic, monkeypatch):
    """档位解析：出厂默认=sonnet 别名 / env 覆盖 / follow_cli=不传 / 显式参数最优先"""
    llm = _llm(hermetic)
    monkeypatch.delenv("MEMAI_CLAUDE_MODEL", raising=False)
    assert llm.resolve_claude_model() == "sonnet", "出厂默认必须是稳定别名（防腐第一层），不是具体型号"
    monkeypatch.setenv("MEMAI_CLAUDE_MODEL", "opus")
    assert llm.resolve_claude_model() == "opus"
    monkeypatch.setenv("MEMAI_CLAUDE_MODEL", "follow_cli")
    assert llm.resolve_claude_model() is None, "follow_cli 应解析为 None（不传 --model，跟随终端）"
    assert llm.resolve_claude_model("haiku") == "haiku", "显式参数应最优先"


def test_default_never_hardcodes_dated_model(hermetic):
    """防腐守卫：出厂默认常量必须是别名（不含数字/日期）——写死型号=未来必炸"""
    llm = _llm(hermetic)
    assert not any(ch.isdigit() for ch in llm.DEFAULT_CLAUDE_MODEL_ALIAS), \
        f"出厂默认 '{llm.DEFAULT_CLAUDE_MODEL_ALIAS}' 含数字，疑似写死具体型号——违反 PRD 防腐决策"


def test_config_endpoint_roundtrip_and_validation(hermetic, monkeypatch):
    """popup 档位选择 → POST /config/ai → env+状态生效；非法档位 400"""
    agent_api = hermetic.agent_api
    client = _client(agent_api)
    # CI 环境可能没有 claude CLI：把可用性检查 mock 掉（测的是配置链路，不是 CLI 探测）
    monkeypatch.setattr(agent_api, "get_llm_status", lambda: {
        "selected_provider": "claude_code", "provider_config": "claude_code",
        "claude_code": {"available": True}, "codex_cli": {"available": False},
    })
    r = client.post("/config/ai", json={"provider": "claude_code", "claudeModel": "opus"},
                    headers=_EXT_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["ai"]["claudeModelChoice"] == "opus"
    import os
    assert os.environ.get("MEMAI_CLAUDE_MODEL") == "opus", "选择应立即写入进程 env（不重启生效）"
    bad = client.post("/config/ai", json={"provider": "claude_code", "claudeModel": "gpt-9"},
                      headers=_EXT_HEADERS)
    assert bad.status_code == 400, "名单外档位值应被拒（这里也是白名单思想）"


def test_model_fallback_strips_flag_and_retries(hermetic, monkeypatch):
    """防腐第二层：--model 调用失败 → 剥掉档位退回终端默认重试，产品不挂死"""
    llm = _llm(hermetic)
    calls = []

    class _R:
        pass

    def fake_run(cmd, **kw):
        r = _R()
        calls.append(list(cmd))
        if "--model" in cmd:
            r.returncode, r.stdout, r.stderr = 1, "", "unknown model alias"
        else:
            r.returncode, r.stdout, r.stderr = 0, json.dumps({"result": "ok"}), ""
        return r

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.delenv("MEMAI_CLAUDE_MODEL", raising=False)
    provider = llm.ClaudeCodeProvider(bin_path="/bin/echo")
    out = provider.generate_text("hi", timeout=5)
    assert out == "ok", "兜底重试成功后应正常返回"
    assert len(calls) == 2, "应恰好两次调用：带 --model 失败一次 + 剥掉后成功一次"
    assert "--model" in calls[0] and "--model" not in calls[1]
    assert provider.last_provider_meta.get("model_fallback_from") == "sonnet", \
        "兜底应留痕（出生证明可见档位回退）"
