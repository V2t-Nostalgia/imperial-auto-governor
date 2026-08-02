from __future__ import annotations

import json
import shutil
import sys
import uuid
import unittest
from pathlib import Path
from typing import Any


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from agent_tools import AgentToolError, AgentToolbox  # noqa: E402
from conversation_agent import ConversationAgent  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
from iag_supervisor import StaleSourceSaveError  # noqa: E402


class FakeToolbox:
    def __init__(
        self,
        _config_path: Path,
        _store: ConversationStore,
        *,
        allow_execute: bool,
        trigger: str,
    ):
        self.allow_execute = allow_execute
        self.trigger = trigger
        self.review_recorded = False
        self.prepared_run_id = None
        self.executed = False

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "inspect_empire_state",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def dispatch(
        self,
        name: str,
        _arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if name != "inspect_empire_state":
            raise ValueError(name)
        return (
            {"schema": "test.state", "game_date": "2200.01.01"},
            "已读取测试存档。",
        )

    def record_noop_review(
        self,
        _arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.review_recorded = True
        return {"run_id": "test_noop"}


class ConversationAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data"
        self.test_id = uuid.uuid4().hex
        prompt = root / f"{self.test_id}_prompt.md"
        prompt.write_text("# 灰风\n保持经济稳定。", encoding="utf-8")
        self.prompt_path = prompt
        self.config_path = root / f"{self.test_id}_config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "provider": "chat_completions_compatible",
                    "base_url": "https://example.test",
                    "model": "test-model",
                    "runtime_root": str(root),
                    "operator_prompt_path": str(prompt),
                    "tool_calling_enabled": True,
                    "chat_context_max_chars": 10000,
                    "tool_loop_max_rounds": 4,
                }
            ),
            encoding="utf-8",
        )
        self.database_path = root / f"{self.test_id}_conversation.sqlite3"
        self.store = ConversationStore(self.database_path)

    def tearDown(self) -> None:
        self.prompt_path.unlink(missing_ok=True)
        self.config_path.unlink(missing_ok=True)
        for suffix in ("", "-wal", "-shm"):
            self.database_path.with_name(
                self.database_path.name + suffix
            ).unlink(missing_ok=True)

    def test_web_research_switch_controls_exposed_tool_schemas(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["web_research_enabled"] = False
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        disabled = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=False,
            trigger="chat",
        )
        disabled_names = {
            item["function"]["name"] for item in disabled.schemas()
        }
        self.assertTrue(
            {"search_web", "fetch_page", "search_stellaris_wiki"}.isdisjoint(
                disabled_names
            )
        )

        config["web_research_enabled"] = True
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        enabled = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=False,
            trigger="chat",
        )
        enabled_names = {
            item["function"]["name"] for item in enabled.schemas()
        }
        self.assertTrue(
            {"search_web", "fetch_page", "search_stellaris_wiki"}.issubset(
                enabled_names
            )
        )

    def test_persists_deepseek_tool_reasoning_and_final_reply(self) -> None:
        replies = iter(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "hidden tool reasoning",
                    "tool_calls": [
                        {
                            "id": "call_state",
                            "type": "function",
                            "function": {
                                "name": "inspect_empire_state",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "当前状态稳定，暂不需要改变建设。",
                    "reasoning_content": "hidden final reasoning",
                },
            ]
        )
        requests: list[list[dict[str, Any]]] = []

        def complete(
            _config: dict[str, Any],
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]],
        ) -> dict[str, Any]:
            self.assertTrue(tools)
            requests.append(messages)
            return next(replies)

        agent = ConversationAgent(
            self.config_path,
            self.store,
            completion_fn=complete,
            toolbox_factory=FakeToolbox,
        )
        result = agent.run_turn(
            trigger="chat",
            user_content="现在经济如何？",
            autonomy_mode="paused",
        )

        self.assertIn("状态稳定", result["final_content"])
        self.assertEqual(len(requests), 2)
        replay = requests[1]
        assistant_call = next(
            item
            for item in replay
            if item["role"] == "assistant" and item.get("tool_calls")
        )
        self.assertEqual(
            assistant_call["reasoning_content"],
            "hidden tool reasoning",
        )
        self.assertTrue(any(item["role"] == "tool" for item in replay))

        public_text = "\n".join(
            item["content"] for item in self.store.public_messages()
        )
        self.assertIn("已读取测试存档", public_text)
        self.assertIn("当前状态稳定", public_text)
        self.assertNotIn("hidden", public_text)

    def test_refreshes_same_candidate_once_after_preclick_stale_save(self) -> None:
        calls: list[str] = []

        def execute(run_dir: Path, _config_path: Path) -> dict[str, Any]:
            calls.append(run_dir.name)
            if len(calls) == 1:
                raise StaleSourceSaveError("A newer synchronized save exists.")
            return {
                "success": True,
                "finished_at": "2026-07-29T20:00:00+08:00",
                "telemetry": {"authoritative_confirmation": True},
            }

        toolbox = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=True,
            trigger="autonomous",
            execute_fn=execute,
        )
        old_run_id = f"{self.test_id}_old"
        new_run_id = f"{self.test_id}_refreshed"
        old_dir = toolbox.runs_root / old_run_id
        new_dir = toolbox.runs_root / new_run_id
        old_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, old_dir, True)
        self.addCleanup(shutil.rmtree, new_dir, True)
        toolbox.prepared_run_id = old_run_id
        toolbox.prepared_plan = {
            "source_game_date": "2201.10.01",
            "action": {
                "type": "execute_candidate",
                "candidate_id": "candidate-holo",
            },
            "next_review_months": 3,
        }
        toolbox.review_recorded = True
        toolbox.snapshot = {"game_date": "2201.10.01"}

        def load_latest() -> None:
            toolbox.snapshot = {"game_date": "2201.11.01"}
            toolbox.candidates = [{"candidate_id": "candidate-holo"}]

        def write_refreshed(plan: dict[str, Any]):
            self.assertEqual(plan["source_game_date"], "2201.11.01")
            self.assertEqual(plan["action"]["candidate_id"], "candidate-holo")
            new_dir.mkdir(parents=True)
            return new_dir, {"action": {"type": "build_building"}}

        toolbox._load = load_latest
        toolbox._current_save_path = lambda: Path("test-bound-save.sav")
        toolbox._write_run = write_refreshed
        toolbox._record_next_review = lambda **_kwargs: None

        result = toolbox.execute_prepared_construction({"run_id": old_run_id})

        self.assertEqual(calls, [old_run_id, new_run_id])
        self.assertEqual(result["run_id"], new_run_id)
        self.assertTrue(result["source_save_refreshed"])
        self.assertEqual(result["replanned_from_run_id"], old_run_id)
        superseded = json.loads(
            (old_dir / "superseded.json").read_text(encoding="utf-8")
        )
        self.assertEqual(superseded["replacement_run_id"], new_run_id)
        with self.assertRaises(AgentToolError):
            toolbox.execute_prepared_construction({"run_id": new_run_id})

    def test_prepare_summary_uses_district_label_and_planet_hint(self) -> None:
        toolbox = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=True,
            trigger="autonomous",
        )
        toolbox.prepare_construction = lambda _arguments: {
            "action": {
                "type": "build_district",
                "district_type": "district_mining",
                "planet_id": 2546,
                "planet_name_key": "NEW_COLONY_NAME_1",
                "planet_name_hint": "NAME_Trappist-I",
            }
        }

        _result, summary = toolbox.dispatch("prepare_construction", {})

        self.assertIn("采矿区划", summary)
        self.assertIn("Trappist-I", summary)
        self.assertNotIn("None", summary)
        self.assertNotIn("NEW_COLONY_NAME_1", summary)

    def test_allows_multiple_constructions_only_after_serial_confirmation(self) -> None:
        calls: list[str] = []

        def execute(run_dir: Path, _config_path: Path) -> dict[str, Any]:
            calls.append(run_dir.name)
            return {
                "success": True,
                "finished_at": "2026-08-01T16:30:00+08:00",
                "telemetry": {"authoritative_confirmation": True},
            }

        toolbox = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=True,
            trigger="autonomous",
            execute_fn=execute,
        )
        toolbox.snapshot = {
            "game_date": "2221.07.01",
            "country": {"stockpile": {"minerals": 3000}},
        }
        toolbox.candidates = [
            {
                "candidate_id": "candidate-a",
                "action": {
                    "type": "build_district",
                    "planet_id": 1,
                    "district_type": "district_mining",
                },
                "construction_cost": {"minerals": 300},
            },
            {
                "candidate_id": "candidate-b",
                "action": {
                    "type": "build_district",
                    "planet_id": 2,
                    "district_type": "district_farming",
                },
                "construction_cost": {"minerals": 300},
            },
        ]
        toolbox._current_save_path = lambda: Path("bound-save.sav")
        toolbox._record_next_review = lambda **_kwargs: None
        run_index = 0

        def write_run(plan: dict[str, Any]):
            nonlocal run_index
            run_index += 1
            candidate = toolbox._candidate(plan["action"]["candidate_id"])
            assert candidate is not None
            run_dir = toolbox.runs_root / f"batch-{run_index}"
            return run_dir, {"action": candidate["action"]}

        def reserve(candidate: dict[str, Any]) -> None:
            toolbox.candidates = [
                item
                for item in toolbox.candidates or []
                if item["candidate_id"] != candidate["candidate_id"]
            ]

        toolbox._write_run = write_run
        toolbox._reserve_successful_candidate = reserve
        review = {
            "risk_level": "low",
            "urgent_risks": [],
            "strategic_priority": "按资源余量扩建",
            "reasoning_zh": "资源足以承受有界批次。",
            "confidence": 0.9,
        }

        first = toolbox.prepare_construction(
            {**review, "candidate_id": "candidate-a"}
        )
        with self.assertRaises(AgentToolError):
            toolbox.prepare_construction(
                {**review, "candidate_id": "candidate-b"}
            )
        first_execution = toolbox.execute_prepared_construction(
            {"run_id": first["run_id"]}
        )
        self.assertTrue(first_execution["batch"]["may_prepare_next"])

        second = toolbox.prepare_construction(
            {**review, "candidate_id": "candidate-b"}
        )
        second_execution = toolbox.execute_prepared_construction(
            {"run_id": second["run_id"]}
        )

        self.assertEqual(calls, ["batch-1", "batch-2"])
        self.assertEqual(
            second_execution["batch"]["successful_constructions"],
            2,
        )

    def test_relaxed_policy_continues_with_unconfirmed_actions_in_ledger(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config.update(
            {
                "inconclusive_rewrite_policy": "allow_serial_provisional",
                "maximum_constructions_per_turn": 2,
            }
        )
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        def execute(_run_dir: Path, _config_path: Path) -> dict[str, Any]:
            return {
                "success": False,
                "confirmation_state": "pending_save_confirmation",
                "started_at": "2026-08-02T01:00:00+08:00",
                "finished_at": "2026-08-02T01:00:05+08:00",
                "telemetry": {
                    "carrier_seen": True,
                    "rewritten": True,
                    "authoritative_confirmation": False,
                    "phase": "rewritten_without_authoritative_confirmation",
                },
            }

        toolbox = AgentToolbox(
            self.config_path,
            self.store,
            allow_execute=True,
            trigger="autonomous",
            execute_fn=execute,
        )
        toolbox.snapshot = {
            "game_date": "2224.01.01",
            "source_save": {
                "sha256": "source",
                "modified_at": "2026-08-02T01:00:00+08:00",
            },
            "country": {"stockpile": {"minerals": 3000}},
            "planets": [],
        }
        toolbox.candidates = [
            {
                "candidate_id": "candidate-a",
                "action": {"type": "build_district", "planet_id": 1},
                "construction_cost": {"minerals": 300},
            },
            {
                "candidate_id": "candidate-b",
                "action": {"type": "build_district", "planet_id": 2},
                "construction_cost": {"minerals": 300},
            },
        ]
        toolbox._current_save_path = lambda: Path("bound-save.sav")
        toolbox._record_next_review = lambda **_kwargs: None
        run_index = 0

        def write_run(plan: dict[str, Any]):
            nonlocal run_index
            run_index += 1
            candidate = toolbox._candidate(plan["action"]["candidate_id"])
            assert candidate is not None
            return (
                toolbox.runs_root / f"provisional-{run_index}",
                {"action": candidate["action"]},
            )

        def reserve(candidate: dict[str, Any]) -> None:
            toolbox.candidates = [
                item
                for item in toolbox.candidates or []
                if item["candidate_id"] != candidate["candidate_id"]
            ]

        toolbox._write_run = write_run
        toolbox._reserve_successful_candidate = reserve
        review = {
            "risk_level": "low",
            "urgent_risks": [],
            "strategic_priority": "扩张",
            "reasoning_zh": "按暂定账本串行提交。",
            "confidence": 0.8,
        }

        first = toolbox.prepare_construction(
            {**review, "candidate_id": "candidate-a"}
        )
        first_result = toolbox.execute_prepared_construction(
            {"run_id": first["run_id"]}
        )
        self.assertFalse(first_result["success"])
        self.assertTrue(first_result["provisionally_accepted_for_serial"])
        self.assertTrue(first_result["batch"]["may_prepare_next"])

        second = toolbox.prepare_construction(
            {**review, "candidate_id": "candidate-b"}
        )
        second_result = toolbox.execute_prepared_construction(
            {"run_id": second["run_id"]}
        )
        self.assertEqual(second_result["batch"]["provisional_constructions"], 2)
        self.assertEqual(second_result["batch"]["remaining_capacity"], 0)
        ledger = self.store.get_state("pending_execution_confirmations", [])
        self.assertEqual(len(ledger), 2)
        self.assertTrue(all(item["state"] == "provisional_pending_save" for item in ledger))
        self.assertFalse(self.store.get_state("last_execution", {})["success"])


if __name__ == "__main__":
    unittest.main()
