# 2A 测试基线共享 fixture：隔离环境（临时 KB_DATA_DIR，绝不碰真实数据）
# 环境变量口径与 scripts/kb-regression hermetic 段一致。
import contextlib
import io
import os
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
}


@pytest.fixture(scope="session")
def hermetic():
    """临时 KB_DATA_DIR + 真实 schema（由 agent_api 建）+ worker 模块。

    session 级：agent_api / worker 模块的 DATA_DIR 在 import 时定死，
    所以整个测试进程共用一个临时目录；测试之间用不同的 comment/job 行隔离。
    """
    temp_dir = tempfile.mkdtemp(prefix="kb-worker-test-")
    os.environ["KB_DATA_DIR"] = temp_dir
    os.environ.update(_ENV)
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
    assert worker.DB_PATH.startswith(resolved_temp), f"DB 不在临时目录：{worker.DB_PATH}"
    import types
    yield types.SimpleNamespace(worker=worker, agent_api=agent_api)
