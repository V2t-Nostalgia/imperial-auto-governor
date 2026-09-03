from __future__ import annotations

import unittest

from iag.stellaris.execution.packet.fleet_reinforcement_commands import (
    ADD_TEMPLATE_SHIP_RECORD,
    CREATE_FLEET_TEMPLATE_RECORD,
    REINFORCE_STAGE_ONE_RECORD,
    REINFORCE_STAGE_TWO_RECORD,
    REMOVE_TEMPLATE_SHIP_RECORD,
    FleetReinforcementTarget,
    FleetTemplateAddTarget,
    FleetTemplateCreationTarget,
    FleetTemplateRemoveTarget,
    build_reinforcement_stage_record,
    build_template_creation_record,
    build_template_edit_record,
    command_identity,
    parse_reinforcement_stage,
    parse_template_creation,
    parse_template_edit,
)


class FleetReinforcementCommandTests(unittest.TestCase):
    def test_parses_paired_capture_template_edits(self) -> None:
        self.assertEqual(
            parse_template_edit(ADD_TEMPLATE_SHIP_RECORD),
            (
                "add",
                FleetTemplateAddTarget(
                    context_822c=0,
                    fleet_template_id=0,
                    design_id=3188,
                ),
            ),
        )
        self.assertEqual(
            parse_template_edit(REMOVE_TEMPLATE_SHIP_RECORD),
            (
                "remove",
                FleetTemplateRemoveTarget(
                    fleet_template_id=0,
                    design_id=3188,
                ),
            ),
        )

    def test_builds_second_fleet_samples_byte_for_byte(self) -> None:
        add = build_template_edit_record(
            action="add",
            command_serial=54,
            actor=2,
            origin=0,
            target=FleetTemplateAddTarget(
                context_822c=0,
                fleet_template_id=175,
                design_id=3188,
            ),
        )
        expected = bytes.fromhex(
            "9800040000005f3b01000300f30101000300400201000c0002000000"
            "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
            "14003600000004004100010003000e3b01001400af000000692c0100"
            "0300652c01001400740c0000143501001400ffffffffc84401000c00"
            "000000000400822c0100140000000000132b01000e00010c00000000"
            "0001000c000100000004000400"
        )
        self.assertEqual(add, expected)
        self.assertEqual(command_identity(add), (2, 0, 54))

    def test_parses_and_builds_two_stage_reinforcement(self) -> None:
        target = FleetReinforcementTarget(context_822c=0, fleet_template_id=0)
        self.assertEqual(
            parse_reinforcement_stage(REINFORCE_STAGE_ONE_RECORD),
            (1, target),
        )
        self.assertEqual(
            parse_reinforcement_stage(REINFORCE_STAGE_TWO_RECORD),
            (2, target),
        )
        self.assertEqual(
            build_reinforcement_stage_record(
                stage=1,
                command_serial=51,
                actor=2,
                origin=0,
                target=target,
            ),
            REINFORCE_STAGE_ONE_RECORD,
        )
        self.assertEqual(
            build_reinforcement_stage_record(
                stage=2,
                command_serial=52,
                actor=2,
                origin=0,
                target=target,
            ),
            REINFORCE_STAGE_TWO_RECORD,
        )

    def test_builds_each_captured_create_template_serial(self) -> None:
        target = FleetTemplateCreationTarget(context_822c=0)
        self.assertEqual(parse_template_creation(CREATE_FLEET_TEMPLATE_RECORD), target)
        for serial in (53, 62, 64, 66, 68):
            record = build_template_creation_record(
                command_serial=serial,
                actor=2,
                origin=0,
                target=target,
            )
            self.assertEqual(parse_template_creation(record), target)
            self.assertEqual(command_identity(record), (2, 0, serial))


if __name__ == "__main__":
    unittest.main()
