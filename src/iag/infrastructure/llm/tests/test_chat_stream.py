#!/usr/bin/env python3
"""Tests for visible-content streaming and protocol reconstruction."""

from __future__ import annotations

import unittest

from iag.infrastructure.llm.chat_stream import ChatCompletionAccumulator


class ChatCompletionAccumulatorTests(unittest.TestCase):
    def test_reasoning_is_retained_but_never_emitted(self) -> None:
        visible: list[str] = []
        accumulator = ChatCompletionAccumulator(visible.append)
        accumulator.ingest(
            {"choices": [{"delta": {"reasoning_content": "private chain"}}]}
        )
        accumulator.ingest({"choices": [{"delta": {"content": "visible answer"}}]})

        self.assertEqual(visible, ["visible answer"])
        self.assertEqual(accumulator.message()["content"], "visible answer")
        self.assertEqual(
            accumulator.message()["reasoning_content"],
            "private chain",
        )

    def test_fragmented_tool_call_is_reconstructed(self) -> None:
        accumulator = ChatCompletionAccumulator()
        accumulator.ingest(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_",
                                        "arguments": '{"plan',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        )
        accumulator.ingest(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "1",
                                    "function": {
                                        "name": "empire_state",
                                        "arguments": 'et":"Earth"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

        call = accumulator.message()["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["function"]["name"], "inspect_empire_state")
        self.assertEqual(
            call["function"]["arguments"],
            '{"planet":"Earth"}',
        )


if __name__ == "__main__":
    unittest.main()
