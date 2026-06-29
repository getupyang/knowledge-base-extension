#!/usr/bin/env python3
"""
Windows-native launcher for Knowledge Base Extension.

This intentionally supports the current terminal environment only:
- PowerShell/CMD users run the backend in native Windows.
- WSL users should keep using the Unix shell scripts inside WSL.

The macOS bash setup/start path is left untouched.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


REPO_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_DIR / "backend"
REQUIREMENTS_PATH = REPO_DIR / "requirements.txt"
CONFIG_PATH = Path.home() / ".kb_config"
DEFAULT_DATA_DIR = Path(os.environ.get("KB_DATA_DIR") or (Path.home() / ".knowledge-base-extension"))
PYTHON = sys.executable


def say(text: str = "") -> None:
    print(text, flush=True)


def is_windows() -> bool:
    return os.name == "nt"


def command_path(command: str) -> Optional[str]:
    found = shutil.which(command)
    if found:
        return found
    if not is_windows():
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    npm_dir = Path(appdata) / "npm"
    for suffix in (".cmd", ".exe", ".bat", ""):
        candidate = npm_dir / f"{command}{suffix}"
        if candidate.exists():
            return str(candidate)
    return None


def load_config() -> dict[str, str]:
    config: dict[str, str] = {}
    if not CONFIG_PATH.exists():
        return config
    for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def apply_config_to_env(config: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in config.items():
        env.setdefault(key, value)
    data_dir = env.get("KB_DATA_DIR") or str(DEFAULT_DATA_DIR)
    env["KB_DATA_DIR"] = data_dir
    env["KB_NOTION_TOKEN"] = env.get("NOTION_TOKEN", "")
    env["KB_NOTION_DATABASE_ID"] = env.get("NOTION_DATABASE_ID", "")
    env["KB_CLAUDE_BIN"] = env.get("MEMAI_CLAUDE_BIN") or env.get("CLAUDE_BIN", "")
    env.setdefault("MEMAI_LLM_PROVIDER", "auto")
    env.setdefault("MEMAI_LOCAL_AGENT", "none")
    env.setdefault("MEMAI_LLM_FALLBACK", "fail")
    env.setdefault("RESEARCH_DIR", str(BACKEND_DIR))
    env["NO_PROXY"] = f"localhost,127.0.0.1,::1,{env.get('NO_PROXY', '')}"
    env["no_proxy"] = f"localhost,127.0.0.1,::1,{env.get('no_proxy', '')}"
    return env


def data_dir_from_config(config: dict[str, str]) -> Path:
    return Path(os.path.expanduser(config.get("KB_DATA_DIR") or str(DEFAULT_DATA_DIR)))


def write_config(values: dict[str, str]) -> None:
    lines = [
        "# 知识库助手配置文件",
        "# 修改后需要重新运行 start.ps1 或 start.cmd",
        "",
    ]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def install_requirements() -> None:
    say("→ 准备运行环境...")
    result = subprocess.run(
        [PYTHON, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS_PATH)],
        cwd=str(REPO_DIR),
    )
    if result.returncode != 0:
        raise SystemExit("  ✗ 运行环境准备失败。请检查网络、代理或 Python/pip 安装。")
    say("  ✓ 运行环境就绪")


def init_local_store(data_dir: Path) -> None:
    say("→ 创建本机资料库...")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".logs").mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "comments.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notion_page_id TEXT,
            page_url TEXT NOT NULL, page_title TEXT, selected_text TEXT,
            comment TEXT NOT NULL, agent_type TEXT, status TEXT DEFAULT "open",
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL, author TEXT NOT NULL,
            agent_type TEXT, content TEXT NOT NULL, created_at TEXT NOT NULL,
            debug_meta TEXT, FOREIGN KEY (comment_id) REFERENCES comments(id))"""
    )
    conn.commit()
    conn.close()
    defaults = {
        data_dir / "project_context.md": "# 项目上下文\n\n（空白，用户还没有填写项目背景。AI 不能假设用户正在做某个项目。）\n",
        data_dir / "user_profile.md": "# 用户画像\n\n（空白，系统会根据这台电脑上的本地批注逐步学习。）\n",
        data_dir / "learned_rules.json": '{\n  "rules": []\n}\n',
    }
    for path, content in defaults.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    say(f"  ✓ 本机资料库就绪：{db_path}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"  {prompt}{suffix}：").strip()
    return value or default


def ask_choice(prompt: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    while True:
        say("")
        say(f"  {prompt}")
        for idx, (label, _) in enumerate(options, start=1):
            say(f"  {idx}) {label}")
        value = input(f"  输入编号后回车 [{default_index + 1}]：").strip()
        if not value:
            return options[default_index][1]
        if value.isdigit():
            idx = int(value) - 1
            if 0 <= idx < len(options):
                return options[idx][1]
        say("  请输入上面列出的编号。")


def run_version(command: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return False, str(exc)
    text = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, text


def run_claude_probe(claude_bin: str, mode: str, anthropic_key: str = "") -> tuple[bool, str]:
    env = os.environ.copy()
    if mode == "account":
        pass
    elif mode == "api_key":
        for key in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            env.pop(key, None)
        env["ANTHROPIC_API_KEY"] = anthropic_key
    cmd = [
        claude_bin,
        "-p",
        "Reply with exactly OK.",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
    except Exception as exc:
        return False, str(exc)
    text = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, text[:1000]
    try:
        payload = json.loads(result.stdout)
        if payload.get("is_error") is True or payload.get("api_error_status"):
            return False, result.stdout[:1000]
    except json.JSONDecodeError:
        pass
    return True, text[:1000]


def run_codex_probe(codex_bin: str) -> tuple[bool, str]:
    output_file = tempfile.NamedTemporaryFile(prefix="memai-codex-", suffix=".txt", delete=False)
    output_path = output_file.name
    output_file.close()
    cmd = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--cd",
        str(BACKEND_DIR),
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        output_path,
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            input="Reply with exactly OK.",
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = ""
        if Path(output_path).exists():
            output = Path(output_path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            Path(output_path).unlink()
        except OSError:
            pass
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or output or "")[:1000]
    return True, (output or result.stdout or "").strip()[:1000]


def configure_api() -> dict[str, str]:
    provider = ask_choice(
        "请选择你准备用哪个 API Key",
        [
            ("千问 / Qwen API", "qwen"),
            ("OpenRouter API", "openrouter"),
        ],
    )
    if provider == "qwen":
        endpoint = ask_choice(
            "先确认你的千问 Key 来自哪里",
            [
                ("阿里云百炼 / 中国内地标准 API", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                ("Qwen Global / 新加坡标准 API", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
                ("Qwen Coding Plan / 中国内地", "https://coding.dashscope.aliyuncs.com/v1"),
                ("Qwen Coding Plan / Global", "https://coding-intl.dashscope.aliyuncs.com/v1"),
                ("手动填写服务地址", "manual"),
            ],
        )
        base_url = ask("服务地址 Base URL") if endpoint == "manual" else endpoint
        model = ask_choice(
            "再选默认模型",
            [
                ("qwen3.5-plus  推荐默认，兼顾质量和稳定性", "qwen3.5-plus"),
                ("qwen3.6-plus  更强；普通百炼 API 更适合", "qwen3.6-plus"),
                ("qwen-plus     保守兼容", "qwen-plus"),
                ("手动填写模型名", "manual"),
            ],
        )
        if model == "manual":
            model = ask("模型名")
    else:
        base_url = "https://openrouter.ai/api/v1"
        model = ask_choice(
            "选择 OpenRouter 默认模型",
            [
                ("openai/gpt-4o-mini  默认，便宜稳妥", "openai/gpt-4o-mini"),
                ("手动填写 OpenRouter 模型 ID", "manual"),
            ],
        )
        if model == "manual":
            model = ask("OpenRouter Model ID")
    api_key = getpass.getpass("  API Key（不会显示，粘贴后按回车）：").strip()
    if not api_key:
        raise SystemExit("  ✗ 没有收到 API Key。")
    validate_api(provider, api_key, base_url, model)
    return {
        "MEMAI_LLM_API_PROVIDER": provider,
        "MEMAI_LLM_API_KEY": api_key,
        "MEMAI_LLM_BASE_URL": base_url.rstrip("/"),
        "MEMAI_LLM_MODEL": model,
    }


def validate_api(provider: str, api_key: str, base_url: str, model: str) -> None:
    say("  正在验证 API 连接（会发送一条极小测试请求）...")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                "temperature": 0,
                "max_tokens": 8,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "knowledge-base-extension-windows/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(4096)
    except urllib.error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        detail = f"HTTP {exc.code}"
        if exc.code == 401:
            detail = "401 认证失败。请确认 API Key、所选服务和地区匹配。"
        elif exc.code == 404:
            detail = "404。通常是服务地址或模型名不正确。"
        elif exc.code == 429:
            detail = "429。服务商返回限流或额度不足。"
        if body:
            detail = f"{detail}\n{body}"
        raise SystemExit(f"  ✗ API 验证失败：{detail}") from exc
    except Exception as exc:
        raise SystemExit(f"  ✗ API 验证失败：{exc}") from exc
    say("  ✓ API 连接验证通过")


def configure_ai(existing_config: dict[str, str]) -> dict[str, str]:
    say("→ 配置 AI 服务...")
    claude_bin = command_path("claude")
    codex_bin = command_path("codex")
    if claude_bin:
        ok, version = run_version(claude_bin)
        say(f"  {'✓' if ok else '○'} Claude Code：{version or claude_bin}")
    else:
        say("  ○ Claude Code 未找到")
    if codex_bin:
        ok, version = run_version(codex_bin)
        say(f"  {'✓' if ok else '○'} Codex CLI：{version or codex_bin}")
    else:
        say("  ○ Codex CLI 未找到")

    options: list[tuple[str, str]] = []
    if claude_bin:
        options.append(("Claude Code 直连（复用当前终端里的 Claude 登录态）", "claude_code"))
    if codex_bin:
        options.append(("Codex 直连（复用当前终端里的 Codex 登录态）", "codex_cli"))
    options.append(("千问 / OpenRouter API（本地 CLI 不可用时再选）", "api"))
    provider = ask_choice("请选择默认使用的 AI 服务", options)

    result = {
        "MEMAI_LLM_PROVIDER": provider,
        "MEMAI_LOCAL_AGENT": "none" if provider == "api" else provider,
        "MEMAI_LLM_FALLBACK": "fail",
        "MEMAI_LLM_API_PROVIDER": "",
        "MEMAI_LLM_API_KEY": "",
        "MEMAI_LLM_BASE_URL": "",
        "MEMAI_LLM_MODEL": "",
        "CLAUDE_BIN": claude_bin or "",
        "MEMAI_CLAUDE_BIN": claude_bin or "",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_BASE_URL": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "MEMAI_CLAUDE_API_KEY": "",
        "MEMAI_CLAUDE_AUTH_TOKEN": "",
        "MEMAI_CLAUDE_BASE_URL": "",
        "MEMAI_CLAUDE_CODE_OAUTH_TOKEN": "",
        "MEMAI_CODEX_BIN": codex_bin or "",
        "MEMAI_CODEX_SANDBOX": "read-only",
    }

    if provider == "claude_code":
        auth_mode = ask_choice(
            "请选择 Claude Code 怎么登录",
            [
                ("使用这台电脑上已经登录的 Claude Code", "account"),
                ("输入 ANTHROPIC_API_KEY", "api_key"),
            ],
        )
        anthropic_key = ""
        if auth_mode == "api_key":
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if anthropic_key:
                say(f"  已检测到当前终端的 ANTHROPIC_API_KEY（长度 {len(anthropic_key)} 位，已隐藏）。")
            else:
                anthropic_key = getpass.getpass("  ANTHROPIC_API_KEY：").strip()
            if not anthropic_key:
                raise SystemExit("  ✗ 没有收到 ANTHROPIC_API_KEY。")
        say("  正在测试 Claude Code...")
        ok, detail = run_claude_probe(claude_bin or "claude", auth_mode, anthropic_key)
        if not ok:
            raise SystemExit(
                "  ✗ Claude Code 自动测试没有通过。\n"
                f"{detail}\n"
                "  请先确认这个命令在同一个终端里能跑通：\n"
                '  claude -p "Reply with exactly OK." --output-format json'
            )
        result["ANTHROPIC_AUTH_TOKEN"] = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        result["ANTHROPIC_BASE_URL"] = os.environ.get("ANTHROPIC_BASE_URL", "")
        result["CLAUDE_CODE_OAUTH_TOKEN"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        result["MEMAI_CLAUDE_AUTH_TOKEN"] = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        result["MEMAI_CLAUDE_BASE_URL"] = os.environ.get("ANTHROPIC_BASE_URL", "")
        result["MEMAI_CLAUDE_CODE_OAUTH_TOKEN"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if auth_mode == "api_key":
            result["ANTHROPIC_API_KEY"] = anthropic_key
            result["MEMAI_CLAUDE_API_KEY"] = anthropic_key
        say("  ✓ Claude Code 可以使用。")
    elif provider == "codex_cli":
        say("  正在测试 Codex CLI...")
        ok, detail = run_codex_probe(codex_bin or "codex")
        if not ok:
            raise SystemExit(
                "  ✗ Codex CLI 自动测试没有通过。\n"
                f"{detail}\n"
                "  请先确认 Codex 已登录，并且这个命令在同一个终端里能跑通：\n"
                '  codex exec --ephemeral --sandbox read-only --skip-git-repo-check -'
            )
        say("  ✓ Codex CLI 可以使用。")
    else:
        result.update(configure_api())

    return result


def setup(_: argparse.Namespace) -> None:
    if not is_windows():
        say("提示：这个入口是给 Windows PowerShell/CMD 用的。macOS 请继续运行 bash setup.sh。")
    say("=== 知识库助手 Windows Setup ===")
    say("")
    if sys.version_info < (3, 9):
        raise SystemExit("  ✗ 需要 Python 3.9 或更新版本。")
    say(f"  ✓ Python：{sys.version.split()[0]}")
    install_requirements()

    existing = load_config()
    if existing and CONFIG_PATH.exists():
        say("")
        say(f"  检测到这台电脑已经配置过：{CONFIG_PATH}")
        reconfigure = ask("是否重新配置？输入 y 重新配置，直接回车沿用", "N")
        if reconfigure.lower() not in {"y", "yes"}:
            init_local_store(data_dir_from_config(existing))
            say("")
            say("安装完成。之后可运行 .\\start.ps1 启动知识库助手。")
            return

    data_dir = Path(os.environ.get("KB_DATA_DIR") or existing.get("KB_DATA_DIR") or str(DEFAULT_DATA_DIR))
    say("")
    say("→ 准备本机资料库...")
    say(f"  数据位置：{data_dir / 'comments.db'}")
    ai_config = configure_ai(existing)
    config_values = {
        "NOTION_TOKEN": existing.get("NOTION_TOKEN", ""),
        "NOTION_DATABASE_ID": existing.get("NOTION_DATABASE_ID", ""),
        "MEMAI_LOCAL_BACKUP_ENABLED": "1",
        "MEMAI_BACKUP_KEEP": "14",
        **ai_config,
        "KB_DATA_DIR": str(data_dir),
        "RESEARCH_DIR": str(BACKEND_DIR),
    }
    write_config(config_values)
    say(f"  ✓ 配置已保存到 {CONFIG_PATH}")
    init_local_store(data_dir)
    say("")
    say("安装完成。现在运行 .\\start.ps1 启动知识库助手。")


def http_get(url: str, timeout: float = 3) -> tuple[Optional[int], str]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return None, ""


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if is_windows():
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout and "No tasks" not in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def start_process(name: str, cmd: list[str], cwd: Path, log_path: Path, env: dict[str, str]) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab")
    kwargs = {
        "cwd": str(cwd),
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if is_windows():
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    say(f"  ✓ 已启动 {name}：PID {proc.pid}")
    return proc.pid


def start(_: argparse.Namespace) -> None:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"✗ 未找到配置文件 {CONFIG_PATH}。请先运行 .\\setup.ps1。")
    config = load_config()
    env = apply_config_to_env(config)
    data_dir = data_dir_from_config(config)
    log_dir = data_dir / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    say("=== 启动知识库助手 ===")
    say(f"→ 本地记忆目录：{data_dir}")
    say(f"→ 当前 AI 服务：{env.get('MEMAI_LLM_PROVIDER', 'auto')}")

    if not port_open(8765):
        pid = start_process("知识库服务器 (8765)", [PYTHON, "server.py"], BACKEND_DIR, log_dir / "server.log", env)
        (log_dir / "server.pid").write_text(str(pid), encoding="utf-8")
    else:
        say("  ○ 端口 8765 已在监听，跳过启动知识库服务器。")

    if not port_open(8766):
        pid = start_process(
            "Agent API (8766)",
            [PYTHON, "-m", "uvicorn", "agent_api:app", "--host", "127.0.0.1", "--port", "8766"],
            BACKEND_DIR,
            log_dir / "agent_api.log",
            env,
        )
        (log_dir / "agent_api.pid").write_text(str(pid), encoding="utf-8")
    else:
        say("  ○ 端口 8766 已在监听，跳过启动 Agent API。")

    for _ in range(30):
        status, _body = http_get("http://127.0.0.1:8766/health", timeout=1)
        if status == 200:
            break
        time.sleep(1)

    worker_pid_file = log_dir / "worker.pid"
    old_worker = read_pid(worker_pid_file)
    if old_worker and pid_running(old_worker):
        say(f"  ○ Worker 已在运行：PID {old_worker}")
    else:
        pid = start_process("后台 worker", [PYTHON, "worker.py"], BACKEND_DIR, log_dir / "worker.log", env)
        worker_pid_file.write_text(str(pid), encoding="utf-8")

    time.sleep(2)
    all_ok = True
    status, _ = http_get("http://127.0.0.1:8765", timeout=3)
    if status:
        say("✓ 知识库服务器：http://localhost:8765")
    else:
        all_ok = False
        say(f"✗ 知识库服务器启动失败，查看日志：{log_dir / 'server.log'}")
    status, _ = http_get("http://127.0.0.1:8766/health", timeout=3)
    if status == 200:
        say("✓ Agent API：http://localhost:8766")
    else:
        all_ok = False
        say(f"✗ Agent API 启动失败，查看日志：{log_dir / 'agent_api.log'}")
    worker_pid = read_pid(worker_pid_file)
    if worker_pid and pid_running(worker_pid):
        say(f"✓ Worker：PID {worker_pid}")
    else:
        all_ok = False
        say(f"✗ Worker 启动失败，查看日志：{log_dir / 'worker.log'}")
    if all_ok:
        say("")
        say("工作台已就绪。")
        say("  知识库：http://localhost:8765")
        say("  记忆笔记本：http://localhost:8765/notebook/")
        say("  停止服务：.\\stop.ps1")


def stop(_: argparse.Namespace) -> None:
    config = load_config()
    data_dir = data_dir_from_config(config)
    log_dir = data_dir / ".logs"
    say("=== 停止知识库助手 ===")
    for filename in ("server.pid", "agent_api.pid", "worker.pid"):
        pid_path = log_dir / filename
        pid = read_pid(pid_path)
        if not pid:
            continue
        if not pid_running(pid):
            say(f"  ○ {filename} 记录的 PID {pid} 已不在运行")
            continue
        if is_windows():
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
        else:
            os.kill(pid, signal.SIGTERM)
        say(f"  ✓ 已停止 PID {pid}")
    say("完成。")


def status(_: argparse.Namespace) -> None:
    config = load_config()
    data_dir = data_dir_from_config(config)
    log_dir = data_dir / ".logs"
    say("=== 知识库助手状态 ===")
    for label, url in (
        ("知识库服务器", "http://127.0.0.1:8765"),
        ("Agent API", "http://127.0.0.1:8766/health"),
    ):
        code, body = http_get(url, timeout=2)
        detail = body.strip()[:160] if body else "无响应"
        say(f"  {'✓' if code else '✗'} {label}：{code or detail}")
    worker_pid = read_pid(log_dir / "worker.pid")
    say(f"  {'✓' if worker_pid and pid_running(worker_pid) else '✗'} Worker：{worker_pid or '未启动'}")
    say(f"  数据目录：{data_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Base Extension Windows launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup").set_defaults(func=setup)
    sub.add_parser("start").set_defaults(func=start)
    sub.add_parser("stop").set_defaults(func=stop)
    sub.add_parser("status").set_defaults(func=status)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
