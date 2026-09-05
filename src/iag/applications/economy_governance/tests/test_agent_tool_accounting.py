from __future__ import annotations

import unittest
from unittest.mock import patch

from iag.applications.economy_governance.agent_tools import (
    AgentToolError,
    AgentToolbox,
)
from iag.stellaris.execution.session_proxy_controller import SessionProxyError


class AgentToolAccountingTests(unittest.TestCase):
    def test_economy_ledger_contains_only_economy_executions(self) -> None:
        toolbox = AgentToolbox.__new__(AgentToolbox)
        toolbox.successful_executions = [{"run_id": "construction-1"}]
        toolbox.provisional_executions = [{"run_id": "construction-2"}]
        toolbox.review_recorded = False

        self.assertTrue(toolbox.turn_action_recorded)
        self.assertEqual(
            toolbox.confirmed_turn_actions(),
            [{"run_id": "construction-1"}],
        )
        self.assertEqual(
            toolbox.provisional_turn_actions(),
            [{"run_id": "construction-2"}],
        )

    def test_economy_toolbox_no_longer_dispatches_research_tools(self) -> None:
        toolbox = AgentToolbox.__new__(AgentToolbox)

        with self.assertRaisesRegex(AgentToolError, "Unknown tool"):
            toolbox.dispatch("execute_prepared_research", {})

    def test_execution_channel_status_is_sanitized(self) -> None:
        toolbox = AgentToolbox.__new__(AgentToolbox)
        toolbox.config = {"execution_mode": "session_proxy"}
        raw_status = {
            "running": True,
            "ready": True,
            "state": "FLOW_LOCKED",
            "flow": {"route": "tailscale", "remote_ip": "192.0.2.44"},
            "source_actor": 2,
            "candidate_source_actors": [2],
            "armed": False,
            "insertion_count": 4,
            "synthetic_serial_count": 4,
            "pid": 1234,
        }
        with patch(
            "iag.applications.economy_governance.agent_tools."
            "SessionProxyController"
        ) as controller:
            controller.return_value.status.return_value = raw_status
            result = toolbox._execution_channel_status()

        self.assertTrue(result["flow_locked"])
        self.assertEqual(result["route"], "tailscale")
        self.assertNotIn("pid", result)
        self.assertNotIn("remote_ip", result)

    def test_execution_channel_status_failure_does_not_break_state_read(self) -> None:
        toolbox = AgentToolbox.__new__(AgentToolbox)
        toolbox.config = {"execution_mode": "session_proxy"}
        with patch(
            "iag.applications.economy_governance.agent_tools."
            "SessionProxyController"
        ) as controller:
            controller.return_value.status.side_effect = SessionProxyError(
                "broken runtime state"
            )
            result = toolbox._execution_channel_status()

        self.assertEqual(result["state"], "status_unavailable")
        self.assertFalse(result["ready"])
        self.assertFalse(result["flow_locked"])
        self.assertNotIn("broken runtime state", str(result))


if __name__ == "__main__":
    unittest.main()
