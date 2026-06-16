#!/usr/bin/env python3
"""
Search-capability wiring tests for local CLI and OpenAI-compatible providers.

These tests mock subprocess/API boundaries. They verify command/request shape
without spending model tokens or depending on external network availability.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_client import ClaudeCodeProvider, CodexCliProvider, OpenAICompatibleProvider


SEARCH_ENV_KEYS = [
    "MEMAI_WEB_SEARCH",
    "MEMAI_CODEX_WEB_SEARCH",
    "MEMAI_CLAUDE_WEB_SEARCH",
    "MEMAI_CLAUDE_TOOLS",
    "MEMAI_CLAUDE_ALLOWED_TOOLS",
    "MEMAI_API_WEB_SEARCH",
    "MEMAI_OPENROUTER_WEB_SEARCH_MAX_RESULTS",
]


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class SearchProviderWiringTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = {key: os.environ.get(key) for key in SEARCH_ENV_KEYS}
        for key in SEARCH_ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in SEARCH_ENV_KEYS:
            if self.saved_env[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = self.saved_env[key] or ""

    def test_codex_live_mode_enables_top_level_search_flag_and_json_events(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            output_path = cmd[cmd.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("codex answer")
            stdout = "\n".join([
                json.dumps({"type": "session.started"}),
                json.dumps({"type": "tool_call", "tool": "web_search"}),
            ])
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        provider = CodexCliProvider(bin_path="/tmp/codex")
        with mock.patch("subprocess.run", side_effect=fake_run):
            answer = provider.generate_text("today news", search_mode="live")

        cmd = calls[0][0]
        self.assertEqual(answer, "codex answer")
        self.assertIn("--search", cmd)
        self.assertLess(cmd.index("--search"), cmd.index("exec"))
        self.assertIn("--json", cmd)
        self.assertNotIn("web_search=\"cached\"", cmd)
        self.assertEqual(provider.last_provider_meta["search_mode"], "live")
        self.assertTrue(provider.last_provider_meta["actual_search_called"])
        self.assertEqual(provider.last_provider_meta["codex_web_search_event_count"], 1)

    def test_codex_cached_mode_uses_cached_web_search_config(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            output_path = cmd[cmd.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("cached answer")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        provider = CodexCliProvider(bin_path="/tmp/codex")
        with mock.patch("subprocess.run", side_effect=fake_run):
            answer = provider.generate_text("stable question")

        cmd = calls[0][0]
        self.assertEqual(answer, "cached answer")
        self.assertNotIn("--search", cmd)
        self.assertIn("-c", cmd)
        self.assertIn("web_search=\"cached\"", cmd)
        self.assertEqual(provider.last_provider_meta["search_mode"], "cached")
        self.assertFalse(provider.last_provider_meta["actual_search_called"])

    def test_claude_code_appends_system_prompt_and_allows_web_tools(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"result": "claude answer"}),
                stderr="",
            )

        provider = ClaudeCodeProvider(bin_path="/tmp/claude")
        with mock.patch("subprocess.run", side_effect=fake_run):
            answer = provider.generate_text(
                "查一下",
                system_prompt="system",
                search_mode="auto",
            )

        cmd = calls[0][0]
        self.assertEqual(answer, "claude answer")
        self.assertIn("--append-system-prompt", cmd)
        self.assertNotIn("--system-prompt", cmd)
        self.assertIn("--tools", cmd)
        self.assertIn("WebSearch,WebFetch", cmd)
        self.assertIn("--allowedTools", cmd)
        self.assertEqual(provider.last_provider_meta["search_capability"], "claude_code_tools")
        self.assertEqual(provider.last_provider_meta["claude_system_prompt_mode"], "append_system_prompt")

    def test_openrouter_search_mode_adds_server_search_tool(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeHTTPResponse({
                "choices": [{
                    "message": {
                        "content": "api answer",
                        "annotations": [{"type": "url_citation"}],
                    }
                }],
                "usage": {"server_tool_use": {"web_search_requests": 1}},
            })

        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            answer = provider.generate_text("latest", search_mode="auto")

        request = requests[0][0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(answer, "api answer")
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(body["tools"], [{"type": "openrouter:web_search"}])
        self.assertEqual(provider.last_provider_meta["search_capability"], "openrouter_server_tool")
        self.assertTrue(provider.last_provider_meta["actual_search_called"])
        self.assertEqual(provider.last_provider_meta["api_citation_count"], 1)

    def test_openai_search_mode_uses_responses_web_search_tool(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeHTTPResponse({
                "output": [
                    {"type": "web_search_call", "status": "completed"},
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "responses answer"}],
                    },
                ]
            })

        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            answer = provider.generate_text(
                "latest",
                system_prompt="system",
                search_mode="auto",
            )

        request = requests[0][0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(answer, "responses answer")
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["instructions"], "system")
        self.assertEqual(provider.last_provider_meta["search_capability"], "openai_responses_web_search")
        self.assertTrue(provider.last_provider_meta["actual_search_called"])


if __name__ == "__main__":
    unittest.main()
