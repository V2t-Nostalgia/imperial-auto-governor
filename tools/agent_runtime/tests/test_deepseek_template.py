from __future__ import annotations

import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from model_client import chat_completion_body, request_body_overrides  # noqa: E402
from model_templates import apply_model_template  # noqa: E402


class DeepSeekTemplateTests(unittest.TestCase):
    def test_template_enables_thinking_and_tool_protocol(self) -> None:
        value = apply_model_template(
            {
                "provider": "responses_compatible",
                "base_url": "https://example.test/v1",
                "model": "old-model",
                "temperature": 0.15,
            },
            "deepseek_thinking_tools",
        )

        self.assertEqual(value["provider"], "chat_completions_compatible")
        self.assertEqual(value["base_url"], "https://api.deepseek.com")
        self.assertEqual(value["model"], "deepseek-v4-pro")
        self.assertEqual(value["thinking"], {"type": "enabled"})
        self.assertEqual(value["reasoning_effort"], "max")
        self.assertTrue(value["tool_calling_enabled"])

    def test_thinking_request_omits_temperature(self) -> None:
        body = chat_completion_body(
            {
                "model": "deepseek-v4-pro",
                "temperature": 0.15,
                "thinking": {"type": "enabled"},
                "reasoning_effort": "max",
            },
            [{"role": "user", "content": "test"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "inspect",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            ],
        )

        self.assertNotIn("temperature", body)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "max")
        self.assertEqual(body["tool_choice"], "auto")

    def test_standard_request_keeps_temperature(self) -> None:
        body = chat_completion_body(
            {"model": "custom", "temperature": 0.2},
            [{"role": "user", "content": "test"}],
        )
        self.assertEqual(body["temperature"], 0.2)

    def test_raw_parameters_merge_but_cannot_replace_protocol_fields(self) -> None:
        body = chat_completion_body(
            {
                "model": "custom",
                "temperature": 0.2,
                "request_body_overrides": {
                    "top_p": 0.8,
                    "max_tokens": 4096,
                },
            },
            [{"role": "user", "content": "test"}],
        )
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["max_tokens"], 4096)

        with self.assertRaises(ValueError):
            request_body_overrides(
                {"request_body_overrides": {"messages": []}}
            )


if __name__ == "__main__":
    unittest.main()
