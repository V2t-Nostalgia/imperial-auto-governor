import unittest

from iag_command_replacement_injector import replace_one_command
from iag_stream_command_injector import (
    EARTH_RESEARCH_COMMAND_TEMPLATE,
    read_uint24_be,
    write_uint24_be,
)


def header(sender: int, ack: int) -> bytes:
    value = bytes.fromhex(
        "010000000000000000000000000000f0000000000000000000"
    )
    value = write_uint24_be(value, 6, sender)
    return write_uint24_be(value, 10, ack)


class CommandReplacementInjectorTests(unittest.TestCase):
    def test_replaces_one_large_command_without_changing_payload_length(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE + bytes(4))
        carrier[:2] = (156).to_bytes(2, "little")
        carrier[58:60] = (210).to_bytes(2, "little")
        payload = header(1000, 500) + bytes(carrier)

        result = replace_one_command(
            payload,
            build_queue_id=6,
            building_id="building_research_lab_1",
            planet_id=3,
            placement=0,
        )

        self.assertIsNotNone(result)
        modified, metadata = result
        self.assertEqual(len(modified), len(payload))
        self.assertEqual(modified[:25], payload[:25])
        self.assertEqual(read_uint24_be(modified, 6), 1000)
        self.assertEqual(metadata["carrier_serial"], 210)
        self.assertEqual(metadata["carrier_length"], 157)
        self.assertEqual(metadata["padding_length"], 4)
        self.assertEqual(
            int.from_bytes(modified[25 + 58 : 25 + 60], "little"),
            210,
        )
        self.assertTrue(modified.endswith(bytes(4)))

    def test_non_building_command_is_not_replaced(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE + bytes(4))
        carrier[:2] = (156).to_bytes(2, "little")
        carrier[6:12] = bytes.fromhex("be2c01000300")
        carrier[58:60] = (210).to_bytes(2, "little")
        payload = header(1000, 500) + bytes(carrier)

        self.assertIsNone(
            replace_one_command(
                payload,
                build_queue_id=6,
                building_id="building_research_lab_1",
                planet_id=3,
                placement=0,
            )
        )

    def test_non_building_command_can_be_explicitly_used(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE + bytes(4))
        carrier[:2] = (156).to_bytes(2, "little")
        carrier[6:12] = bytes.fromhex("be2c01000300")
        carrier[58:60] = (210).to_bytes(2, "little")
        payload = header(1000, 500) + bytes(carrier)

        result = replace_one_command(
            payload,
            build_queue_id=6,
            building_id="building_research_lab_1",
            planet_id=3,
            placement=0,
            allow_any_command=True,
        )

        self.assertIsNotNone(result)
        modified, metadata = result
        self.assertEqual(len(modified), len(payload))
        self.assertEqual(metadata["carrier_serial"], 210)

    def test_short_command_is_not_replaced(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE[:108])
        carrier[:2] = (107).to_bytes(2, "little")
        carrier[58:60] = (210).to_bytes(2, "little")
        payload = header(1000, 500) + bytes(carrier)

        self.assertIsNone(
            replace_one_command(
                payload,
                build_queue_id=6,
                building_id="building_research_lab_1",
                planet_id=3,
                placement=0,
            )
        )

    def test_zero_serial_command_is_not_replaced(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        carrier[58:60] = bytes(2)
        payload = header(1000, 500) + bytes(carrier)
        self.assertIsNone(
            replace_one_command(
                payload,
                build_queue_id=6,
                building_id="building_research_lab_1",
                planet_id=3,
                placement=0,
            )
        )


if __name__ == "__main__":
    unittest.main()
