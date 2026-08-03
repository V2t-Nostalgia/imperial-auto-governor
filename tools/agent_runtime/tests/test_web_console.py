from __future__ import annotations

import base64
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from conversation_store import ConversationStore  # noqa: E402
from web_console import (  # noqa: E402
    ConsoleError,
    ConsoleService,
    RangeNotSatisfiable,
    basic_auth_matches,
    build_save_client_record,
    build_construction_catalog,
    parse_single_byte_range,
    validate_bind_security,
)


class WebConsoleSecurityTests(unittest.TestCase):
    def authorization(self, username: str, password: str) -> str:
        encoded = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {encoded}"

    def test_catalog_places_current_target_first_without_changing_carrier(self) -> None:
        capabilities = {
            "carriers": {
                "build_building": {
                    "command": "building_research_lab_1",
                    "source_label_zh": "研究实验室",
                },
                "build_district": {"command": "district_generator"},
                "build_zone": {"command": "zone_research_engineering"},
                "upgrade_building": {
                    "command": "building_upc_upgrade_command_relay_target"
                },
                "replace_building": {
                    "command": "building_upc_replacement_command_relay_target"
                },
            },
            "buildings": {
                "building_research_lab_1": {
                    "label_zh": "研究实验室",
                    "enabled": True,
                    "role": "research",
                },
                "building_holo_theatres": {
                    "label_zh": "全息影像剧场",
                    "enabled": True,
                    "role": "amenities",
                },
            },
            "districts": {},
            "zones": {},
            "building_upgrades": [
                {
                    "from_building_id": "building_research_lab_1",
                    "to_building_id": "building_research_lab_2",
                    "label_zh": "升级研究实验室 II",
                    "enabled": True,
                    "role": "research",
                }
            ],
        }
        candidates = [
            {
                "action": {
                    "type": "build_building",
                    "building_id": "building_holo_theatres",
                }
            },
            {
                "action": {
                    "type": "build_building",
                    "building_id": "building_research_lab_1",
                }
            },
            {
                "action": {
                    "type": "upgrade_building",
                    "from_building_id": "building_research_lab_1",
                    "to_building_id": "building_research_lab_2",
                }
            },
            {
                "action": {
                    "type": "replace_building",
                    "from_building_id": "building_research_lab_1",
                    "to_building_id": "building_holo_theatres",
                }
            },
        ]
        manifest = {
            "action": {
                "type": "build_building",
                "building_id": "building_holo_theatres",
            }
        }
        catalog = build_construction_catalog(
            capabilities,
            candidates,
            manifest,
            {
                "build_building": {
                    "sequence_ready": True,
                    "required_step_count": 2,
                    "calibrated_step_count": 2,
                },
                "upgrade_building": {
                    "sequence_ready": True,
                    "required_step_count": 2,
                    "calibrated_step_count": 2,
                },
                "replace_building": {
                    "sequence_ready": True,
                    "required_step_count": 3,
                    "calibrated_step_count": 3,
                },
            },
        )
        buildings = catalog["groups"][0]
        self.assertEqual(
            buildings["entries"][0]["object_id"],
            "building_holo_theatres",
        )
        self.assertTrue(buildings["entries"][0]["selected"])
        self.assertEqual(
            buildings["carrier"]["command"],
            "building_research_lab_1",
        )
        self.assertTrue(buildings["carrier"]["sequence_ready"])
        upgrades = next(
            group
            for group in catalog["groups"]
            if group["action_type"] == "upgrade_building"
        )
        self.assertEqual(upgrades["legal_candidate_count"], 1)
        self.assertEqual(
            upgrades["entries"][0]["source_object_id"],
            "building_research_lab_1",
        )
        self.assertTrue(upgrades["carrier"]["sequence_ready"])
        replacements = next(
            group
            for group in catalog["groups"]
            if group["action_type"] == "replace_building"
        )
        self.assertEqual(replacements["legal_candidate_count"], 1)
        self.assertEqual(
            replacements["entries"][0]["object_id"],
            "building_holo_theatres",
        )
        self.assertEqual(
            replacements["carrier"]["calibration"]["required_step_count"],
            3,
        )

    def test_accepts_matching_basic_auth(self) -> None:
        self.assertTrue(
            basic_auth_matches(
                self.authorization("iag", "secret-value"),
                "iag",
                "secret-value",
            )
        )

    def test_rejects_wrong_or_malformed_basic_auth(self) -> None:
        self.assertFalse(
            basic_auth_matches(
                self.authorization("iag", "wrong"),
                "iag",
                "secret-value",
            )
        )
        self.assertFalse(basic_auth_matches("Bearer token", "iag", "secret"))
        self.assertFalse(basic_auth_matches("Basic not-base64", "iag", "secret"))

    def test_loopback_can_run_without_tls_or_auth(self) -> None:
        self.assertTrue(
            validate_bind_security(
                "127.0.0.1",
                allow_lan=False,
                has_tls=False,
                auth_enabled=False,
            )
        )

    def test_lan_requires_explicit_opt_in_auth_and_tls(self) -> None:
        for allow_lan, has_tls, auth_enabled in (
            (False, True, True),
            (True, False, True),
            (True, True, False),
        ):
            with self.subTest(
                allow_lan=allow_lan,
                has_tls=has_tls,
                auth_enabled=auth_enabled,
            ):
                with self.assertRaises(ConsoleError):
                    validate_bind_security(
                        "0.0.0.0",
                        allow_lan=allow_lan,
                        has_tls=has_tls,
                        auth_enabled=auth_enabled,
                    )

        self.assertFalse(
            validate_bind_security(
                "0.0.0.0",
                allow_lan=True,
                has_tls=True,
                auth_enabled=True,
            )
        )


    def test_builds_bounded_save_client_heartbeat_record(self) -> None:
        record = build_save_client_record(
            {
                "client_id": "client-12345678",
                "state": "running",
                "hostname": "HOST-PC",
                "app_version": "gui-test",
                "campaign_label": "test campaign",
                "capabilities": ["save_upload_v1", "host_inbound_rewrite_v1"],
            },
            source_ip="198.51.100.20",
            seen_at="2026-07-29T20:00:00+08:00",
            seen_epoch=123.0,
        )
        self.assertEqual(record["source_ip"], "198.51.100.20")
        self.assertEqual(record["state"], "running")
        self.assertEqual(record["last_seen_epoch"], 123.0)
        self.assertIn("host_inbound_rewrite_v1", record["capabilities"])

    def test_rejects_invalid_save_client_heartbeat(self) -> None:
        with self.assertRaises(ConsoleError):
            build_save_client_record(
                {"client_id": "short", "state": "running"},
                source_ip="198.51.100.20",
            )
        with self.assertRaises(ConsoleError):
            build_save_client_record(
                {"client_id": "client-12345678", "state": "unknown"},
                source_ip="198.51.100.20",
            )

    def test_campaign_binding_never_switches_to_the_save_owner(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / (
            "web_sessions_" + uuid.uuid4().hex
        )
        root.mkdir(parents=True)
        try:
            config = {
                "runtime_root": str(root),
                "save_source_mode": "host_upload",
                "uploaded_save_root": "state/uploaded_saves",
                "save_manifest_path": "state/current_save.json",
            }
            store = ConversationStore(root / "conversation.sqlite3")
            store.bind_campaign(
                "campaign",
                "a" * 32,
                campaign_label="First",
            )
            manifest_path = root / "state" / "current_save.json"
            manifest_path.parent.mkdir(parents=True)

            service = ConsoleService.__new__(ConsoleService)
            service.config = config
            service.conversation_store = store

            manifest_path.write_text(
                json.dumps(
                    {
                        "campaign_id": "a" * 32,
                        "campaign_label": "First",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                service.campaign_binding_status()["state"],
                "ready",
            )

            manifest_path.write_text(
                json.dumps(
                    {
                        "campaign_id": "a" * 32,
                        "campaign_label": "Different Save Slot",
                    }
                ),
                encoding="utf-8",
            )
            label_conflict = service.campaign_binding_status()
            self.assertEqual(label_conflict["state"], "unbound")
            self.assertFalse(label_conflict["execution_allowed"])
            self.assertIsNone(
                label_conflict["current_bound_conversation_id"]
            )
            self.assertEqual(
                label_conflict["campaign_id_holder_conversation_id"],
                "campaign",
            )
            conflict_catalog = service.conversation_catalog_payload()
            self.assertFalse(
                any(
                    item["is_current_save"]
                    for item in conflict_catalog["conversations"]
                )
            )

            second = store.create_conversation(
                "Second",
                campaign_id="b" * 32,
                campaign_label="Second",
            )
            manifest_path.write_text(
                json.dumps(
                    {
                        "campaign_id": "b" * 32,
                        "campaign_label": "Second",
                    }
                ),
                encoding="utf-8",
            )
            status = service.campaign_binding_status()
            self.assertEqual(status["state"], "assigned_elsewhere")
            self.assertEqual(
                status["matching_conversation_id"],
                second["conversation_id"],
            )
            self.assertEqual(status["active_conversation_id"], "campaign")
            self.assertEqual(store.active_conversation_id(), "campaign")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_console_service_creates_and_switches_campaign_sessions(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / (
            "web_service_" + uuid.uuid4().hex
        )
        root.mkdir(parents=True)
        service = None
        try:
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "runtime_root": str(root),
                        "provider": "chat_completions_compatible",
                        "base_url": "https://example.test",
                        "model": "test-model",
                        "tool_calling_enabled": True,
                        "conversation_database": "conversation/campaign.sqlite3",
                        "runs_root": "runs",
                        "save_source_mode": "host_upload",
                        "uploaded_save_root": "state/uploaded_saves",
                        "save_manifest_path": "state/current_save.json",
                        "save_upload_token_file": "secrets/save_upload_token",
                        "save_review_interval_months": 3,
                        "autonomy_mode": "paused",
                        "autonomy_poll_seconds": 300,
                    }
                ),
                encoding="utf-8",
            )
            service = ConsoleService(config_path)
            original_id = service.conversation_store.conversation_id
            catalog = service.create_conversation(
                "Fresh Campaign",
                bind_current=False,
            )
            fresh_id = catalog["active_conversation_id"]
            self.assertNotEqual(fresh_id, original_id)
            self.assertEqual(
                service.conversation_store.get_state("review_interval_months"),
                3,
            )
            service.switch_conversation(original_id)
            self.assertEqual(
                service.conversation_store.conversation_id,
                original_id,
            )

            manifest_path = root / "state" / "current_save.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "campaign_id": "c" * 32,
                        "campaign_label": "Manual Save",
                    }
                ),
                encoding="utf-8",
            )
            service.set_current_campaign_binding(fresh_id)
            self.assertEqual(
                service.conversation_store.conversation_id,
                original_id,
            )
            self.assertEqual(
                service.campaign_binding_status()["state"],
                "assigned_elsewhere",
            )

            service.switch_conversation(fresh_id)
            self.assertEqual(
                service.campaign_binding_status()["state"],
                "ready",
            )
            service.set_current_campaign_binding(None)
            self.assertEqual(
                service.campaign_binding_status()["state"],
                "unbound",
            )

            stopped = service.emergency_stop()
            self.assertTrue(stopped["requested"])
            self.assertTrue(service.stop_path.is_file())
            cleared = service.clear_emergency_stop()
            self.assertTrue(cleared["cleared"])
            self.assertFalse(service.stop_path.exists())

            with service._job_lock:
                service._job = {"state": "running"}
            service.emergency_stop()
            with self.assertRaises(ConsoleError):
                service.clear_emergency_stop()
            self.assertTrue(service.stop_path.is_file())
            with service._job_lock:
                service._job = {"state": "idle"}
            service.clear_emergency_stop()
        finally:
            if service is not None:
                service.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_model_settings_persist_raw_parameters_and_context_policy(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / (
            "web_model_" + uuid.uuid4().hex
        )
        root.mkdir(parents=True)
        service = None
        try:
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "runtime_root": str(root),
                        "provider": "chat_completions_compatible",
                        "base_url": "https://example.test/v1/chat/completions",
                        "model": "test-model",
                        "tool_calling_enabled": True,
                        "conversation_database": "conversation/campaign.sqlite3",
                        "runs_root": "runs",
                        "save_source_mode": "host_upload",
                        "uploaded_save_root": "state/uploaded_saves",
                        "save_manifest_path": "state/current_save.json",
                        "save_upload_token_file": "secrets/save_upload_token",
                    }
                ),
                encoding="utf-8",
            )
            service = ConsoleService(config_path)
            payload = {
                "template_id": "custom",
                "provider": "chat_completions_compatible",
                "base_url": "https://example.test/v1/chat/completions",
                "model": "custom-model",
                "tool_calling_enabled": True,
                "thinking_enabled": False,
                "temperature": 0.65,
                "timeout_seconds": 180,
                "model_context_window_tokens": 64_000,
                "context_output_reserve_tokens": 4_096,
                "context_compression_enabled": True,
                "context_compression_trigger_percent": 72,
                "context_compression_target_percent": 28,
                "request_body_overrides": '{"top_p":0.82,"max_tokens":4096}',
                "web_research_enabled": False,
                "searxng_url": "http://127.0.0.1:8080",
                "crawl4ai_url": "http://127.0.0.1:11235",
                "stellaris_wiki_api_url": "https://stellaris.paradoxwikis.com/api.php",
                "web_search_allowed_domains": ["paradoxwikis.com"],
                "web_fetch_direct_fallback_enabled": False,
            }
            public = service.save_model_config(payload)
            self.assertEqual(public["temperature"], 0.65)
            self.assertEqual(public["model_context_window_tokens"], 64_000)
            self.assertEqual(public["context_compression_trigger_percent"], 72)
            self.assertEqual(public["context_compression_target_percent"], 28)
            self.assertEqual(
                public["request_body_overrides"],
                {"top_p": 0.82, "max_tokens": 4096},
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["request_body_overrides"]["top_p"], 0.82)

            execution = service.save_execution_settings(
                {
                    "fixed_click_guard_enabled": False,
                    "maximum_source_save_lag_versions": 2,
                    "require_fresh_save_seconds": 1200,
                    "autonomy_require_fresh_save_seconds": 900,
                    "maximum_constructions_per_turn": 4,
                    "inconclusive_rewrite_policy": "block_until_save",
                }
            )
            self.assertFalse(execution["fixed_click_guard_enabled"])
            self.assertEqual(execution["maximum_source_save_lag_versions"], 2)
            with self.assertRaises(ConsoleError):
                service.save_execution_settings(
                    {
                        "maximum_source_save_lag_versions": 25,
                        "require_fresh_save_seconds": 1200,
                        "autonomy_require_fresh_save_seconds": 900,
                        "maximum_constructions_per_turn": 4,
                        "inconclusive_rewrite_policy": "block_until_save",
                    }
                )

            invalid = {**payload, "request_body_overrides": '{"messages":[]}'}
            with self.assertRaises(ConsoleError):
                service.save_model_config(invalid)
        finally:
            if service is not None:
                service.close()
            shutil.rmtree(root, ignore_errors=True)


class HttpByteRangeTests(unittest.TestCase):
    def test_parses_open_and_bounded_ranges(self) -> None:
        self.assertEqual(parse_single_byte_range("bytes=100-", 1000), (100, 999))
        self.assertEqual(parse_single_byte_range("bytes=100-199", 1000), (100, 199))

    def test_clamps_range_end_to_resource_size(self) -> None:
        self.assertEqual(parse_single_byte_range("bytes=900-1200", 1000), (900, 999))

    def test_parses_suffix_range(self) -> None:
        self.assertEqual(parse_single_byte_range("bytes=-250", 1000), (750, 999))
        self.assertEqual(parse_single_byte_range("bytes=-1200", 1000), (0, 999))

    def test_rejects_multiple_or_unsatisfiable_ranges(self) -> None:
        for value in ("bytes=100-99", "bytes=1000-", "bytes=0-1,4-5", "items=0-1"):
            with self.subTest(value=value), self.assertRaises(RangeNotSatisfiable):
                parse_single_byte_range(value, 1000)

    def test_missing_range_requests_the_whole_file(self) -> None:
        self.assertIsNone(parse_single_byte_range(None, 1000))


if __name__ == "__main__":
    unittest.main()
