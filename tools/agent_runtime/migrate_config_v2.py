#!/usr/bin/env python3
"""Add persistent-conversation defaults without replacing user credentials."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "model_template_id": "custom",
    "tool_calling_enabled": True,
    "conversation_database": "conversation/campaign.sqlite3",
    "chat_context_max_chars": 120_000,
    "model_context_window_tokens": 128_000,
    "context_output_reserve_tokens": 8_192,
    "context_compression_enabled": True,
    "context_compression_trigger_percent": 80,
    "context_compression_target_percent": 35,
    "context_compression_min_recent_segments": 8,
    "request_body_overrides": {},
    "decade_plan_renewal_lead_months": 12,
    "chat_message_max_chars": 12_000,
    "tool_loop_max_rounds": 12,
    "maximum_constructions_per_turn": 3,
    "maximum_source_save_lag_versions": 2,
    "inconclusive_rewrite_policy": "block_until_save",
    "autonomy_mode": "paused",
    "autonomy_poll_seconds": 15,
    "autonomy_require_fresh_save_seconds": 900,
    "save_source_mode": "host_upload",
    "uploaded_save_root": "state/uploaded_saves",
    "save_manifest_path": "state/current_save.json",
    "save_upload_token_file": "secrets/save_upload_token",
    "save_upload_max_bytes": 268_435_456,
    "save_client_status_path": "state/save_client_status.json",
    "save_client_heartbeat_timeout_seconds": 15,
    "save_review_interval_months": 1,
    "web_research_enabled": False,
    "searxng_url": "http://127.0.0.1:8080",
    "crawl4ai_url": "http://127.0.0.1:11235",
    "stellaris_wiki_api_url": "https://stellaris.paradoxwikis.com/api.php",
    "web_fetch_maximum_chars": 16_000,
    "web_fetch_direct_fallback_enabled": False,
    "transport_process_names": ["steam"],
    "network_interface": "",
    "passive_telemetry_path": "state/passive_flow_status.json",
    "passive_observer_window_seconds": 12,
    "network_owner_refresh_seconds": 10,
    "host_discovery_timeout_seconds": 20,
    "host_discovery_poll_seconds": 0.5,
    "interceptor_privilege_mode": "capability",
    "execution_transport": "windows_host_bridge",
    "host_executor_ready_timeout_seconds": 30,
    "carrier_click_profiles": {
        "build_building": "calibration/carrier_click.json",
        "build_district": "calibration/carrier_district_click.json",
        "build_zone": "calibration/carrier_zone_click.json",
        "upgrade_building": "calibration/carrier_upgrade_click.json",
    },
    "carrier_navigation_enabled": True,
    "carrier_navigation_profiles": {
        "build_building": "calibration/carrier_building_open.json",
        "build_zone": "calibration/carrier_zone_open.json",
        "upgrade_building": "calibration/carrier_upgrade_open.json",
    },
    "carrier_click_step_delay_seconds": 0.45,
    "carrier_pointer_settle_seconds": 0.20,
    "carrier_click_hold_seconds": 0.08,
    "carrier_post_click_settle_seconds": 0.25,
    "fixed_click_guard_enabled": True,
}


def migrate_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Agent config must contain a JSON object.")
    added: list[str] = []
    for key, default in DEFAULTS.items():
        if key not in value:
            value[key] = default
            added.append(key)
        elif isinstance(default, dict) and isinstance(value[key], dict):
            for nested_key, nested_default in default.items():
                if nested_key not in value[key]:
                    value[key][nested_key] = nested_default
                    added.append(f"{key}.{nested_key}")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return {
        "path": str(path),
        "added": added,
        "autonomy_mode": value["autonomy_mode"],
        "model_template_id": value["model_template_id"],
        "web_research_enabled": value["web_research_enabled"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate_config(args.config), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
