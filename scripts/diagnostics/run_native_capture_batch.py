#!/usr/bin/env python3
"""Dry-run or execute an explicitly approved native capture-action batch."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from iag.stellaris.execution._native_capture_adapter import (
    adapt_capture_target,
)
from iag.stellaris.execution.native_runtime import (
    NativeRuntimeClient,
    NativeRuntimeOutcome,
)

SCHEMA = "iag.native_capture_batch.v1"
EXECUTION_CONFIRMATION = "EXECUTE NATIVE CAPTURE BATCH"


def read_plan(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError(f"Plan must use schema {SCHEMA!r}.")
    if not isinstance(document.get("country_id"), int):
        raise TypeError("Plan country_id must be an integer.")
    actions = document.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Plan must contain a non-empty actions list.")
    for index, item in enumerate(actions):
        if not isinstance(item, dict):
            raise TypeError(f"Action {index} must be an object.")
        if item.get("approved") is not True:
            raise ValueError(f"Action {index} is not explicitly approved.")
        if not isinstance(item.get("action"), str) or not isinstance(
            item.get("target"), dict
        ):
            raise TypeError(f"Action {index} has an invalid action or target.")
    return document


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("--endpoint")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()

    plan = read_plan(args.plan)
    if args.execute and args.confirmation != EXECUTION_CONFIRMATION:
        parser.error(
            f"--execute requires --confirmation {EXECUTION_CONFIRMATION!r}"
        )

    country_id = int(plan["country_id"])
    client = NativeRuntimeClient(args.endpoint)
    manifest = client.describe_tools()
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": "execute" if args.execute else "dry_run",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build_id": manifest.build_id,
        "country_id": country_id,
        "results": [],
    }
    exit_code = 0
    for index, item in enumerate(plan["actions"]):
        action_type = str(item["action"])
        request_id = str(item.get("request_id") or f"capture-batch-{index + 1}")
        tool = manifest.get(action_type, 1)
        if tool.verification_state != "paired_capture":
            raise ValueError(
                f"{action_type!r} is not a staged capture-backed action."
            )
        target = adapt_capture_target(
            action_type=action_type,
            target=item["target"],
            country_id=country_id,
        )
        encoded = {
            parameter.name: parameter.encode(target[parameter.name])
            for parameter in tool.parameters
        }
        result: dict[str, Any] = {
            "request_id": request_id,
            "action": action_type,
            "verification_state": tool.verification_state,
            "encoded_fields": tuple(encoded),
            "status": "validated_not_sent",
        }
        if args.execute:
            response = client.execute(
                request_id=request_id,
                action_type=action_type,
                action_version=1,
                country_id=country_id,
                target=target,
            )
            result["status"] = response.outcome.value
            result["detail"] = response.detail
            result["target_echo"] = response.target_echo
            if response.outcome not in {
                NativeRuntimeOutcome.CONFIRMED,
                NativeRuntimeOutcome.PENDING,
            }:
                exit_code = 1
                report["results"].append(result)
                break
        report["results"].append(result)

    output = args.output or args.plan.with_suffix(".report.json")
    write_report(output, report)
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
