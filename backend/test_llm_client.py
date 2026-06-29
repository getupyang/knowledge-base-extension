#!/usr/bin/env python3

import json
import os
import types
import unittest

import llm_client


class ClaudeCodeEnvTest(unittest.TestCase):
    ENV_KEYS = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "MEMAI_CLAUDE_API_KEY",
        "MEMAI_CLAUDE_AUTH_TOKEN",
        "MEMAI_CLAUDE_BASE_URL",
        "MEMAI_CLAUDE_CODE_OAUTH_TOKEN",
    )

    def setUp(self):
        self._old_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_namespaced_claude_base_url_reaches_cli_env(self):
        os.environ["MEMAI_CLAUDE_BASE_URL"] = "https://proxy.example.com/v1"
        captured = {}
        original_run = llm_client.subprocess.run

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"type": "result", "is_error": False, "result": "OK"}),
                stderr="",
            )

        llm_client.subprocess.run = fake_run
        try:
            result = llm_client.ClaudeCodeProvider(bin_path="/usr/bin/claude").generate_text("hello")
        finally:
            llm_client.subprocess.run = original_run

        self.assertEqual(result, "OK")
        self.assertEqual(captured["env"]["ANTHROPIC_BASE_URL"], "https://proxy.example.com/v1")

    def test_existing_anthropic_base_url_is_preserved(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://existing.example.com/v1"

        env = llm_client._claude_child_env()

        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://existing.example.com/v1")


if __name__ == "__main__":
    unittest.main()
