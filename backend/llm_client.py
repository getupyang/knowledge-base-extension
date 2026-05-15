#!/usr/bin/env python3
"""
Provider-agnostic LLM client for mem-ai.

The product can run in two cost/capability modes:
- local subscription-backed CLIs (Claude Code / Codex) when available
- OpenAI-compatible APIs when local CLIs are absent or explicitly disabled
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class LLMError(Exception):
    """Base class for provider errors."""


class LLMConfigError(LLMError):
    """No usable provider is configured."""


class LLMTimeoutError(LLMError):
    """Provider did not finish before timeout."""


class LLMCallError(LLMError):
    """Provider returned an execution/API error."""


def _is_ssl_certificate_error(error: urllib.error.URLError) -> bool:
    reason = getattr(error, "reason", None)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(error)


def _ssl_certificate_hint(error: urllib.error.URLError) -> str:
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        "api ssl certificate error: Python 无法验证模型 API 的 HTTPS 证书。"
        f"如果你用的是 macOS python.org 安装的 Python {py_version}，请在这台电脑运行："
        f" /Applications/Python\\ {py_version}/Install\\ Certificates.command"
        "；然后重新执行 bash start.sh。"
        "如果你开启了 HTTPS 代理，请关闭代理测试，或把代理根证书安装到这台 Mac/Python 信任链。"
        f" 原始错误：{error}"
    )


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _find_executable(env_name: str, command: str, extra_paths: list[str]) -> Optional[str]:
    configured = os.environ.get(env_name)
    candidates = [configured, *extra_paths, shutil.which(command)]
    for candidate in candidates:
        if not candidate:
            continue
        path = shutil.which(candidate) if os.path.basename(candidate) == candidate else None
        path = path or os.path.expanduser(candidate)
        if os.path.exists(path):
            if os.access(path, os.X_OK) or os.name == "nt":
                return path
    return None


def _windows_npm_bin(command: str) -> list[str]:
    if os.name != "nt":
        return []
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    npm_dir = Path(appdata) / "npm"
    return [
        str(npm_dir / f"{command}.cmd"),
        str(npm_dir / f"{command}.exe"),
        str(npm_dir / f"{command}.bat"),
    ]


def find_claude_bin() -> Optional[str]:
    return _find_executable(
        "MEMAI_CLAUDE_BIN",
        "claude",
        [
            os.environ.get("KB_CLAUDE_BIN") or "",
            os.environ.get("CLAUDE_BIN") or "",
            *_windows_npm_bin("claude"),
            "~/.npm-global/bin/claude",
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
        ],
    )


def find_codex_bin() -> Optional[str]:
    return _find_executable(
        "MEMAI_CODEX_BIN",
        "codex",
        [
            *_windows_npm_bin("codex"),
            "~/.npm-global/bin/codex",
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
        ],
    )


def _provider_enabled(name: str, default: bool = True) -> bool:
    env_name = {
        "claude_code": "MEMAI_CLAUDE_CODE_ENABLED",
        "codex_cli": "MEMAI_CODEX_ENABLED",
    }.get(name)
    if not env_name:
        return default
    return _env_truthy(env_name, default=default)


def _api_key() -> Optional[str]:
    for key in (
        "MEMAI_LLM_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "BAILIAN_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
    ):
        value = os.environ.get(key)
        if value:
            return value
    return None


def _api_base_url() -> str:
    explicit = (
        os.environ.get("MEMAI_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or os.environ.get("QWEN_BASE_URL")
        or os.environ.get("BAILIAN_BASE_URL")
    )
    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("OPENROUTER_API_KEY"):
        return "https://openrouter.ai/api/v1"
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("BAILIAN_API_KEY"):
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "https://api.deepseek.com"
    if os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY"):
        return "https://api.moonshot.ai/v1"
    return "https://api.openai.com/v1"


def _default_model(base_url: str) -> str:
    configured = os.environ.get("MEMAI_LLM_MODEL") or os.environ.get("OPENAI_MODEL")
    if configured:
        return configured
    if "deepseek" in base_url:
        return "deepseek-chat"
    if "dashscope" in base_url or "aliyuncs.com/compatible-mode" in base_url:
        return "qwen3.5-plus"
    if "moonshot" in base_url:
        return "kimi-latest"
    if "openrouter" in base_url:
        return "openai/gpt-4o-mini"
    return "gpt-4o-mini"


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    path = env.get("PATH", "")
    extras = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".npm-global/bin")]
    if os.name == "nt" and os.environ.get("APPDATA"):
        extras.insert(0, str(Path(os.environ["APPDATA"]) / "npm"))
    existing_parts = [part for part in path.split(os.pathsep) if part]
    for extra in extras:
        if extra and extra not in existing_parts:
            existing_parts.insert(0, extra)
    path = os.pathsep.join(existing_parts)
    env["PATH"] = path
    return env


def _combine_prompt(prompt: str, system_prompt: str = "") -> str:
    if not system_prompt:
        return prompt
    return f"{system_prompt.strip()}\n\n---\n\n{prompt}"


class BaseProvider:
    name = "base"

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        raise NotImplementedError


class MockProvider(BaseProvider):
    name = "mock"

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        if os.environ.get("MEMAI_MOCK_RESPONSE"):
            return os.environ["MEMAI_MOCK_RESPONSE"]
        if "intent" in prompt and "role" in prompt:
            return json.dumps(
                {
                    "intent": "dialogue",
                    "role": "sparring_partner",
                    "confidence": 0.5,
                    "plan": "",
                    "learned": [],
                    "quick_response": "",
                },
                ensure_ascii=False,
            )
        return "MOCK_LLM_RESPONSE"


class ClaudeCodeProvider(BaseProvider):
    name = "claude_code"

    def __init__(self, bin_path: Optional[str] = None):
        self.bin_path = bin_path or find_claude_bin()
        if not self.bin_path:
            raise LLMConfigError("Claude Code CLI not found")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        cmd = [self.bin_path, "-p", prompt, "--output-format", "json"]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if model:
            cmd.extend(["--model", model])
        # The old agent_api path used this flag for non-interactive background
        # calls. Keep it as the compatibility default; users can set
        # MEMAI_CLAUDE_SKIP_PERMISSIONS=0 to disable it.
        if _env_truthy("MEMAI_CLAUDE_SKIP_PERMISSIONS", default=True):
            cmd.append("--dangerously-skip-permissions")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise LLMTimeoutError(f"claude_code timeout after {timeout}s") from e
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[:1000]
            raise LLMCallError(f"claude_code exit {result.returncode}: {detail}")
        try:
            data = json.loads(result.stdout)
            api_error_status = data.get("api_error_status")
            if api_error_status:
                detail = data.get("result") or data.get("message") or result.stdout
                raise LLMCallError(f"claude_code api_error_status {api_error_status}: {str(detail)[:1000]}")
            if data.get("is_error") is True:
                detail = data.get("result") or data.get("message") or result.stdout
                raise LLMCallError(f"claude_code error: {str(detail)[:1000]}")
            return data.get("result", result.stdout)
        except json.JSONDecodeError:
            return result.stdout


class CodexCliProvider(BaseProvider):
    name = "codex_cli"

    def __init__(self, bin_path: Optional[str] = None):
        self.bin_path = bin_path or find_codex_bin()
        if not self.bin_path:
            raise LLMConfigError("Codex CLI not found")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        workspace = os.environ.get("MEMAI_CODEX_WORKSPACE") or os.getcwd()
        sandbox = os.environ.get("MEMAI_CODEX_SANDBOX", "read-only")
        full_prompt = _combine_prompt(prompt, system_prompt)
        with tempfile.NamedTemporaryFile(prefix="memai-codex-", suffix=".txt", delete=False) as tmp:
            output_path = tmp.name
        cmd = [
            self.bin_path,
            "exec",
            "--ephemeral",
            "--cd",
            workspace,
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            output_path,
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")
        try:
            proc = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_child_env(),
            )
            output = ""
            if os.path.exists(output_path):
                with open(output_path, "r", encoding="utf-8") as f:
                    output = f.read()
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or output or "")[:1000]
                raise LLMCallError(f"codex_cli exit {proc.returncode}: {detail}")
            return output or proc.stdout
        except subprocess.TimeoutExpired as e:
            raise LLMTimeoutError(f"codex_cli timeout after {timeout}s") from e
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass


class OpenAICompatibleProvider(BaseProvider):
    name = "api"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or _api_key()
        if not self.api_key:
            raise LLMConfigError("LLM API key not configured")
        self.base_url = (base_url or _api_base_url()).rstrip("/")
        self.model = _default_model(self.base_url)

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": float(os.environ.get("MEMAI_LLM_TEMPERATURE", "0.2")),
        }
        max_tokens = os.environ.get("MEMAI_LLM_MAX_TOKENS")
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter" in self.base_url:
            headers["HTTP-Referer"] = os.environ.get("MEMAI_HTTP_REFERER", "http://localhost:8765")
            headers["X-Title"] = os.environ.get("MEMAI_APP_TITLE", "mem-ai")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except TimeoutError as e:
            raise LLMTimeoutError(f"api timeout after {timeout}s") from e
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:1000]
            raise LLMCallError(f"api http {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            if _is_ssl_certificate_error(e):
                raise LLMCallError(_ssl_certificate_hint(e)) from e
            raise LLMCallError(f"api url error: {e}") from e
        try:
            payload = json.loads(raw)
            return payload["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMCallError(f"api response parse error: {str(e)} raw={raw[:500]}") from e


def _provider_call_meta(provider: BaseProvider, model: Optional[str] = None,
                        fallback_from: str = "") -> dict[str, Any]:
    provider_model = getattr(provider, "model", None)
    base_url = getattr(provider, "base_url", None)
    effective_model = model or provider_model or os.environ.get("MEMAI_LLM_MODEL", "")
    if provider.name == "codex_cli":
        effective_model = model or os.environ.get("MEMAI_CODEX_MODEL", "") or "default"
    elif provider.name == "claude_code":
        effective_model = model or os.environ.get("MEMAI_CLAUDE_MODEL", "") or "default"
    return {
        "provider": provider.name,
        "provider_config": os.environ.get("MEMAI_LLM_PROVIDER", "auto"),
        "local_agent": os.environ.get("MEMAI_LOCAL_AGENT", "none"),
        "api_provider": os.environ.get("MEMAI_LLM_API_PROVIDER", ""),
        "api_base_url": base_url,
        "model": effective_model,
        "fallback_used": bool(fallback_from),
        "fallback_from": fallback_from,
    }


def _provider_from_name(name: str) -> BaseProvider:
    if name == "mock":
        return MockProvider()
    if name in {"api", "openai", "openai_compatible", "openrouter", "qwen", "dashscope", "bailian"}:
        return OpenAICompatibleProvider()
    if name == "claude_code":
        return ClaudeCodeProvider()
    if name == "codex_cli":
        return CodexCliProvider()
    raise LLMConfigError(f"unknown LLM provider: {name}")


def _available_local_provider_names() -> list[str]:
    names = []
    if _provider_enabled("claude_code", default=True) and find_claude_bin():
        names.append("claude_code")
    if _provider_enabled("codex_cli", default=True) and find_codex_bin():
        names.append("codex_cli")
    return names


def _select_provider() -> BaseProvider:
    provider = os.environ.get("MEMAI_LLM_PROVIDER", "auto").strip().lower()
    local_agent = os.environ.get("MEMAI_LOCAL_AGENT", "none").strip().lower()

    if provider != "auto":
        return _provider_from_name(provider)

    if local_agent in {"claude_code", "codex_cli"}:
        return _provider_from_name(local_agent)

    if local_agent == "auto":
        local_providers = _available_local_provider_names()
        if len(local_providers) == 1:
            return _provider_from_name(local_providers[0])
        if len(local_providers) > 1:
            raise LLMConfigError(
                "Multiple local LLM providers available "
                f"({', '.join(local_providers)}). Set MEMAI_LLM_PROVIDER to one fixed value."
            )

    if _api_key():
        return OpenAICompatibleProvider()

    local_hint = _available_local_provider_names()
    if len(local_hint) == 1:
        return _provider_from_name(local_hint[0])
    if len(local_hint) > 1:
        raise LLMConfigError(
            "Multiple local LLM providers available "
            f"({', '.join(local_hint)}). Set MEMAI_LLM_PROVIDER to one fixed value."
        )
    raise LLMConfigError("No LLM provider available: configure API key or install Claude Code/Codex")


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("no JSON object found in LLM output")


# ──────────────────────────────────────────
# Usage 估算（本地 CLI 不返回真实 token，用字符近似）
# ──────────────────────────────────────────

def _count_tokens_rough(text: str) -> int:
    """粗略 token 估算：中文 ~1 char/token，英文 ~4 chars/token。
    实际是统计中文字符 + 英文 word 数。够好用于 cost 排序，不是计费精度。"""
    if not text:
        return 0
    s = str(text)
    chinese = sum(1 for ch in s if "一" <= ch <= "鿿")
    rest_chars = max(0, len(s) - chinese)
    return chinese + (rest_chars // 4)


# 大致价格（USD/百万 token）。粗略估算，仅用于成本排序。
_MODEL_PRICING = {
    "default": (3.0, 15.0),  # 兜底用 sonnet 价
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def _estimate_usage(provider_name: str, prompt: str, system_prompt: str, content: str) -> dict:
    """返回 usage dict 合并进 last_call_meta。本地 CLI 没真实 usage 时用 estimated。"""
    in_tok = _count_tokens_rough((system_prompt or "") + "\n" + (prompt or ""))
    out_tok = _count_tokens_rough(content or "")
    total = in_tok + out_tok
    # 价格匹配：用 model 名前缀粗匹配
    model_key = "default"
    p_lower = (provider_name or "").lower()
    for k in _MODEL_PRICING:
        if k != "default" and k in p_lower:
            model_key = k
            break
    price_in, price_out = _MODEL_PRICING[model_key]
    cost = round((in_tok * price_in + out_tok * price_out) / 1_000_000, 6)
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total,
        "prompt_tokens_est": in_tok,
        "cost_usd": cost,
        "usage_source": "estimated",  # 区分于真实 API usage
    }


@dataclass
class LLMClient:
    provider: Optional[BaseProvider] = None
    last_call_meta: dict[str, Any] = field(default_factory=dict)

    def _current_provider(self) -> BaseProvider:
        if self.provider is None:
            self.provider = _select_provider()
        return self.provider

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> str:
        primary = self._current_provider()
        started = time.time()
        meta = _provider_call_meta(primary, model=model)
        self.last_call_meta = {**meta, "status": "running"}
        try:
            content = primary.generate_text(prompt, system_prompt=system_prompt, timeout=timeout, model=model)
            usage = _estimate_usage(primary.name, prompt, system_prompt, content)
            self.last_call_meta = {
                **meta,
                "status": "success",
                "elapsed_s": round(time.time() - started, 3),
                **usage,
            }
            return content
        except LLMError as primary_error:
            fallback = os.environ.get("MEMAI_LLM_FALLBACK", "api").strip().lower()
            if primary.name != "api" and fallback == "api" and _api_key():
                api_provider = OpenAICompatibleProvider()
                fallback_started = time.time()
                fallback_meta = _provider_call_meta(api_provider, model=model, fallback_from=primary.name)
                self.last_call_meta = {**fallback_meta, "status": "running"}
                try:
                    content = api_provider.generate_text(prompt, system_prompt=system_prompt, timeout=timeout, model=model)
                    usage = _estimate_usage(api_provider.name, prompt, system_prompt, content)
                    self.last_call_meta = {
                        **fallback_meta,
                        "status": "success",
                        "elapsed_s": round(time.time() - fallback_started, 3),
                        **usage,
                    }
                    return content
                except LLMError as fallback_error:
                    self.last_call_meta = {
                        **fallback_meta,
                        "status": "error",
                        "elapsed_s": round(time.time() - fallback_started, 3),
                        "error": str(fallback_error)[:500],
                    }
                    raise
            self.last_call_meta = {
                **meta,
                "status": "error",
                "elapsed_s": round(time.time() - started, 3),
                "error": str(primary_error)[:500],
            }
            raise

    def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        model: Optional[str] = None,
    ) -> Any:
        return _extract_json(self.generate_text(prompt, system_prompt=system_prompt, timeout=timeout, model=model))

    def provider_name(self) -> str:
        return self._current_provider().name


def get_llm_client() -> LLMClient:
    return LLMClient()


def get_llm_status() -> dict[str, Any]:
    claude_bin = find_claude_bin()
    codex_bin = find_codex_bin()
    status: dict[str, Any] = {
        "provider_config": os.environ.get("MEMAI_LLM_PROVIDER", "auto"),
        "local_agent": os.environ.get("MEMAI_LOCAL_AGENT", "none"),
        "api_provider": os.environ.get("MEMAI_LLM_API_PROVIDER", ""),
        "api_model": os.environ.get("MEMAI_LLM_MODEL", ""),
        "api_key_configured": bool(_api_key()),
        "api_base_url": _api_base_url() if _api_key() else None,
        "claude_code": {"available": bool(claude_bin), "bin": claude_bin},
        "codex_cli": {
            "available": bool(codex_bin),
            "bin": codex_bin,
            "sandbox": os.environ.get("MEMAI_CODEX_SANDBOX", "read-only"),
        },
        "available_local_providers": _available_local_provider_names(),
        "selected_provider": None,
        "error": None,
    }
    try:
        status["selected_provider"] = _select_provider().name
    except Exception as e:
        status["error"] = str(e)
    return status
