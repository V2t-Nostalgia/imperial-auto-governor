from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.fleet_operations.agent_tools import (
    FLEET_PERMISSIONS_KEY,
    FleetToolbox,
    FleetToolError,
)
from iag.core.conversation_store import ConversationStore


def fleet_profile() -> dict[str, object]:
    return {
        "schema": "iag.stellaris_fleet_state.v1",
        "schema_version": 1,
        "game_date": "2204.09.15",
        "owner_country_id": 0,
        "player_country_ids": [0],
        "fleets": [
            {
                "fleet_id": 7,
                "name_key": "HUMAN1_FLEET_1",
                "display_name_hint": "HUMAN1_FLEET 1",
                "player_controllable": True,
                "d32c_move_verified_family": True,
                "ai_callable_now": True,
                "availability": "AVAILABLE",
                "movement": {"current_system_id": 10},
            }
        ],
        "systems": [
            {
                "system_id": 20,
                "name_key": "NAME_Alpha_Centauri",
                "move_destination": {
                    "destination_tag_hex": "0c3a01001400",
                    "destination_object": 118,
                },
            }
        ],
    }


class FleetToolboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.save = self.root / "test.sav"
        self.save.write_bytes(b"test")
        self.store = ConversationStore(self.root / "conversation.sqlite3")
        self.config = {
            "runtime_root": str(self.root),
            "execution_mode": "session_proxy",
            "experimental_fleet_tools_enabled": True,
            "experimental_fleet_attack_enabled": True,
        }

    def toolbox(self) -> FleetToolbox:
        value = FleetToolbox(self.config, self.store, allow_execute=True)
        value._profile = lambda: (self.save, fleet_profile())  # type: ignore[method-assign]
        return value

    def test_new_fleet_is_denied_until_player_grants_permission(self) -> None:
        toolbox = self.toolbox()
        with self.assertRaisesRegex(FleetToolError, "没有授权"):
            toolbox.prepare_move(
                {
                    "fleet_id": 7,
                    "destination_system_id": 20,
                    "reason": "Move to the frontier.",
                }
            )

    def test_prepare_and_execute_revalidate_permission(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_move(
            {
                "fleet_id": 7,
                "destination_system_id": 20,
                "reason": "Move to the frontier.",
            }
        )
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": False, "allow_attack": False}},
        )
        with self.assertRaisesRegex(FleetToolError, "撤销"):
            toolbox.execute({"run_id": prepared["run_id"]})

    def test_confirmed_move_records_machine_fact(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_move(
            {
                "fleet_id": 7,
                "destination_system_id": 20,
                "reason": "Move to the frontier.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "request_id": prepared["run_id"],
                "outcome": "confirmed",
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertTrue(result["success"])
        self.assertEqual(
            self.store.get_state("last_fleet_execution")["fleet_id"],
            7,
        )

    def test_attack_permission_does_not_bypass_missing_protocol_pair(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": True}},
        )
        with self.assertRaisesRegex(FleetToolError, "成对样本"):
            self.toolbox().prepare_attack(
                {"fleet_id": 7, "target_fleet_id": 99, "reason": "Intercept."}
            )


if __name__ == "__main__":
    unittest.main()
