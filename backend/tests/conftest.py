# 2A 测试基线共享 fixture：隔离环境（临时 KB_DATA_DIR，绝不碰真实数据）
# 环境变量口径与 scripts/kb-regression hermetic 段一致。
# 2026-08-13 Codex review 加固：备份目录钉死临时、网络出口 fail-closed、LLM fail-closed。
import contextlib
import io
import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

_ENV = {
    "KB_DISABLE_LEGACY_DB_MIGRATION": "1",
    "NOTION_TOKEN": "",
    "NOTION_DATABASE_ID": "",
    "KB_NOTION_TOKEN": "",
    "KB_NOTION_DATABASE_ID": "",
    # ⚠ 必须禁用（2026-08-13 发现）：agent_api import 即启动云同步线程，且 endpoint/token
    # 有内置默认值——不禁用的话，隔离测试的假数据会被同步到真实生产云端（假 install_id 教训）。
    "MARGIN_CLOUD_ENDPOINT": "disabled",
    "MARGIN_INGEST_TOKEN": "disabled",
}

# 这些 key 若存在（来自真实 ~/.kb_config 或 shell），会把测试写入导向真实路径 → 必须移除
_ENV_REMOVE = ["MEMAI_BACKUP_DIR", "KB_CLIENT_ERROR_LOG"]


@pytest.fixture(scope="session")
def hermetic():
    """临时 KB_DATA_DIR + 真实 schema（由 agent_api 建）+ worker 模块。

    session 级：agent_api / worker 模块的 DATA_DIR 在 import 时定死，
    所以整个测试进程共用一个临时目录；测试之间用不同的 comment/job 行隔离。
    """
    temp_dir = tempfile.mkdtemp(prefix="kb-worker-test-")
    os.environ["KB_DATA_DIR"] = temp_dir
    os.environ.update(_ENV)
    for key in _ENV_REMOVE:
        os.environ.pop(key, None)
    sys.path.insert(0, str(BACKEND_DIR))
    with contextlib.redirect_stdout(io.StringIO()):
        import agent_api  # noqa: F401 — import 即建全量真实 schema
        # ⚠ 已实锤的现状（2026-08-13）：worker import 时读 ~/.kb_config 并无条件覆盖
        # 环境变量（含 KB_DATA_DIR），优先级=配置文件>env，与 agent_api（env 优先）相反。
        # 测试隔离手段：import 期间把 HOME 指向临时目录，让它找不到 .kb_config。
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = temp_dir
        try:
            import worker
        finally:
            if real_home is not None:
                os.environ["HOME"] = real_home
    resolved_temp = str(Path(temp_dir).resolve())

    def _in_temp(p):
        # macOS /var 是 /private/var 的符号链接；worker resolve 而 agent_api 不 resolve
        return str(p).startswith(temp_dir) or str(p).startswith(resolved_temp)

    # 隔离断言三连：任何一个失败 = 测试可能触碰真实数据，宁可全套不跑
    assert _in_temp(worker.DB_PATH), f"worker DB 不在临时目录：{worker.DB_PATH}"
    assert _in_temp(agent_api.DB_PATH), f"agent_api DB 不在临时目录：{agent_api.DB_PATH}"
    assert _in_temp(agent_api.LOCAL_BACKUP_DIR), \
        f"备份目录不在临时目录（import 时会写入并修剪它！）：{agent_api.LOCAL_BACKUP_DIR}"
    import types
    yield types.SimpleNamespace(worker=worker, agent_api=agent_api)


@pytest.fixture(scope="session", autouse=True)
def _no_outbound_network():
    """fail-closed 网络护栏：测试进程内任何真实 socket 连接直接抛错。
    TestClient 走进程内 ASGI 不经 socket，不受影响；漏网的云同步/Notion/LLM HTTP 调用会在这里炸。"""
    real_connect = socket.socket.connect

    def _blocked(self, address, *args, **kwargs):
        raise RuntimeError(f"测试环境禁止真实网络连接：{address}（fail-closed，见 conftest.py）")

    socket.socket.connect = _blocked
    try:
        yield
    finally:
        socket.socket.connect = real_connect


@pytest.fixture(autouse=True)
def _no_real_llm(request, monkeypatch):
    """fail-closed LLM 护栏：真实 provider 配置会从 ~/.kb_config 流入测试进程，
    默认把 worker 的 LLM 入口炸掉；需要 LLM 的测试用 monkeypatch 覆盖（后设优先，自动生效）。"""
    if "hermetic" not in request.fixturenames:
        yield
        return
    hermetic_ns = request.getfixturevalue("hermetic")

    def _boom(*a, **k):
        raise AssertionError("测试触发了未 mock 的 LLM 调用（fail-closed，见 conftest.py）")

    monkeypatch.setattr(hermetic_ns.worker, "call_llm_with_meta", _boom)
    yield
