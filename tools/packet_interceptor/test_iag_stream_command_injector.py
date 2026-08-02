import unittest

from iag_stream_command_injector import (
    EARTH_RESEARCH_COMMAND_TEMPLATE,
    CommandSerialTranslator,
    StreamTranslator,
    build_construction_command,
    inject_into_ack,
    inject_before_live_command,
    read_uint24_be,
    write_uint24_be,
)


def header(sender: int, ack: int) -> bytes:
    value = bytes.fromhex(
        "010000000000000000000000000000f0000000000000000000"
    )
    value = write_uint24_be(value, 6, sender)
    return write_uint24_be(value, 10, ack)


class StreamCommandInjectorTests(unittest.TestCase):
    def test_verified_template_length(self):
        self.assertEqual(len(EARTH_RESEARCH_COMMAND_TEMPLATE), 153)
        self.assertEqual(
            int.from_bytes(EARTH_RESEARCH_COMMAND_TEMPLATE[:2], "little") + 1,
            153,
        )

    def test_build_current_earth_research_command(self):
        command = build_construction_command(
            command_serial=0x019E,
            build_queue_id=6,
            building_id="building_research_lab_1",
            planet_id=3,
            placement=0,
        )
        self.assertEqual(len(command), 153)
        self.assertIn(b"building_research_lab_1", command)
        self.assertIn(bytes.fromhex("ea3f0100140006000000"), command)
        self.assertIn(bytes.fromhex("fc290100140003000000"), command)
        self.assertTrue(command.endswith(bytes.fromhex("932b0100140000000000040004000400")))

    def test_shorter_building_id_is_zero_padded(self):
        command = build_construction_command(
            command_serial=1,
            build_queue_id=6,
            building_id="building_foundry_1",
            planet_id=3,
            placement=0,
        )
        self.assertEqual(len(command), 153)
        self.assertIn(b"\x12\x00building_foundry_1\xfc\x29", command)
        self.assertTrue(command.endswith(bytes(5)))

    def test_ack_carrier_injection_preserves_header(self):
        carrier = header(0x123456, 0x654321)
        command = EARTH_RESEARCH_COMMAND_TEMPLATE
        result, offset = inject_into_ack(carrier, command)
        self.assertEqual(offset, 0x123456)
        self.assertEqual(result[:25], carrier)
        self.assertEqual(result[25:], command)

    def test_outbound_sender_offset_is_shifted(self):
        translator = StreamTranslator(1000, 153)
        translated = translator.translate_outbound(header(1000, 500))
        self.assertEqual(read_uint24_be(translated, 6), 1153)
        self.assertEqual(read_uint24_be(translated, 10), 500)

    def test_inbound_ack_is_hidden_from_local_game(self):
        translator = StreamTranslator(1000, 153)
        partial = translator.translate_inbound(header(500, 1100))
        complete = translator.translate_inbound(header(500, 1153))
        later = translator.translate_inbound(header(500, 1300))
        self.assertEqual(read_uint24_be(partial, 10), 1000)
        self.assertEqual(read_uint24_be(complete, 10), 1000)
        self.assertEqual(read_uint24_be(later, 10), 1147)

    def test_non_reliable_packet_passes_unchanged(self):
        translator = StreamTranslator(1000, 153)
        payload = b"\x00" * 56
        self.assertEqual(translator.translate_outbound(payload), payload)
        self.assertEqual(translator.translate_inbound(payload), payload)

    def test_offsets_translate_across_uint24_wrap(self):
        translator = StreamTranslator(0xFFFFF0, 153)
        translated = translator.translate_outbound(header(0x000020, 0))
        self.assertEqual(read_uint24_be(translated, 6), 0x0000B9)

        inbound = translator.translate_inbound(header(0, 0x000089))
        self.assertEqual(read_uint24_be(inbound, 10), 0xFFFFF0)

    def test_live_command_supplies_serial_and_is_shifted(self):
        carrier_command = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        carrier_command[58:60] = (50).to_bytes(2, "little")
        carrier = header(1000, 500) + bytes(carrier_command)

        result = inject_before_live_command(
            carrier,
            lambda serial: build_construction_command(
                command_serial=serial,
                build_queue_id=6,
                building_id="building_research_lab_1",
                planet_id=3,
                placement=0,
            ),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.command_serial, 50)
        self.assertEqual(result.injection_offset, 1000)
        self.assertEqual(result.translated_serials, [(50, 51)])
        self.assertEqual(
            int.from_bytes(result.payload[25 + 58 : 25 + 60], "little"),
            50,
        )
        self.assertEqual(
            int.from_bytes(result.payload[25 + 153 + 58 : 25 + 153 + 60], "little"),
            51,
        )

    def test_serial_translator_handles_record_split_before_marker(self):
        command = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        command[58:60] = (51).to_bytes(2, "little")
        first = bytes(command[:20])
        second = bytes(command[20:])
        translator = CommandSerialTranslator()

        unchanged, changes = translator.rewrite(first, 2000)
        translated, second_changes = translator.rewrite(second, 2020)

        self.assertEqual(unchanged, first)
        self.assertEqual(changes, [])
        self.assertEqual(second_changes, [(51, 52)])
        combined = unchanged + translated
        self.assertEqual(int.from_bytes(combined[58:60], "little"), 52)


if __name__ == "__main__":
    unittest.main()
