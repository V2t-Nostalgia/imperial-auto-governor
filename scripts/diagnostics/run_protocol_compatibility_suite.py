#!/usr/bin/env python3
"""Run offline and authorized live Stellaris protocol compatibility checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

from iag.infrastructure.llm.runtime_config import RuntimeConfig
from iag.stellaris.execution.protocol_compatibility import (
    COMMAND_SPEC_BY_ACTION,
    REPORT_SCHEMA,
    catalog_document,
    classify_network_result,
    compare_report_documents,
    load_plan,
    new_plan_document,
    new_report_document,
    now_iso,
    target_fingerprint,
    write_report,
)
from iag.stellaris.execution.session_proxy import build_action_probe
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)
from iag.stellaris.game_knowledge import detect_game_root, install_identity


DISPOSABLE_CONFIRMATION = "DISPOSABLE"
ROOM_READY_CONFIRMATION = "START"
ROOM_EXITED_CONFIRMATION = "ROOM EXITED"
PROTOCOL_TEST_MODULES = (
    "src/iag/stellaris/execution/tests/test_protocol_compatibility.py",
    "src/iag/stellaris/execution/tests/test_session_proxy.py",
    "src/iag/stellaris/execution/tests/test_session_proxy_controller.py",
    "src/iag/stellaris/execution/tests/test_fleet_reinforcement_commands.py",
    "src/iag/stellaris/execution/tests/test_supervisor_execution_modes.py",
    "src/iag/stellaris/state/tests/test_fleet_profiles.py",
    "src/iag/stellaris/state/tests/test_research_profiles.py",
    "src/iag/stellaris/tests/test_game_knowledge_technology.py",
    "src/iag/applications/fleet_operations/tests/test_agent_tools.py",
    "src/iag/applications/fleet_operations/tests/test_save_continuations.py",
    "src/iag/applications/research_strategy/tests/test_agent_tools.py",
)


def platform_version() -> str:
    try:
        import tomllib

        value = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return str(value["project"]["version"])
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return "unknown"


def run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_runtime_config(path: Path) -> dict[str, Any]:
    return dict(RuntimeConfig.load(path).snapshot().settings)


def detected_game_install(config: dict[str, Any]) -> dict[str, Any] | None:
    root = detect_game_root(config)
    if root is None:
        return None
    identity = install_identity(root)
    return {
        "version": identity.get("version"),
        "raw_version": identity.get("raw_version"),
        "normalized_version": identity.get("normalized_version"),
        "mod_compatibility_version": identity.get("mod_compatibility_version"),
        "distribution": identity.get("distribution"),
        "launcher_settings_sha256": identity.get("launcher_settings_sha256"),
    }


def scenario_probe(scenario: Mapping[str, Any]) -> dict[str, Any]:
    action = str(scenario["action"])
    spec = COMMAND_SPEC_BY_ACTION[action]
    result: dict[str, Any] = {
        "id": str(scenario["id"]),
        "action": action,
        "group": spec.group,
        "wire_family_hex": spec.wire_family_hex,
        "enabled": scenario.get("enabled") is True,
        "target_sha256": target_fingerprint(scenario["target"]),
        "offline_status": "skipped_disabled",
        "network_stage": "not_run",
        "operator_verdict": "not_checked",
    }
    if not result["enabled"]:
        return result
    try:
        probe = build_action_probe(
            action=action,
            target=dict(scenario["target"]),
            template_record_hex=(
                str(scenario["template_record_hex"])
                if scenario.get("template_record_hex")
                else None
            ),
        )
        result.update(probe)
        if probe["wire_family_hex"] != spec.wire_family_hex:
            result["offline_status"] = "wire_family_mismatch"
            result["offline_error"] = (
                f"catalog={spec.wire_family_hex}, built={probe['wire_family_hex']}"
            )
        else:
            result["offline_status"] = "passed"
    except Exception as error:
        result["offline_status"] = "build_failed"
        result["offline_error"] = f"{type(error).__name__}: {error}"
    return result


def run_repository_tests(output_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "validation" / "run_tests.py"),
        "--quiet",
    ]
    for module in PROTOCOL_TEST_MODULES:
        command.extend(["--path", module])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        completed.stdout + completed.stderr,
        encoding="utf-8",
    )
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "scope": "protocol_related",
        "module_count": len(PROTOCOL_TEST_MODULES),
        "log": output_path.name,
    }


def prepare_report(
    *,
    plan: Mapping[str, Any],
    mode: str,
    identifier: str,
) -> dict[str, Any]:
    report = new_report_document(
        run_id=identifier,
        mode=mode,
        platform_version=platform_version(),
        plan=plan,
    )
    report["scenarios"] = [scenario_probe(item) for item in plan["scenarios"]]
    return report


def apply_baseline(
    report: dict[str, Any],
    baseline_path: Path | None,
) -> None:
    if baseline_path is None:
        return
    baseline = read_json(baseline_path)
    if baseline.get("schema") != REPORT_SCHEMA:
        raise ValueError("Baseline is not an IAG protocol compatibility report.")
    report["baseline_comparison"] = compare_report_documents(report, baseline)


def enabled_report_items(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in report.get("scenarios", [])
        if isinstance(item, dict) and item.get("enabled") is True
    ]


def plan_scenario_by_id(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in plan["scenarios"]}


def safe_proxy_status(status: Mapping[str, Any]) -> dict[str, Any]:
    flow = status.get("flow")
    route = flow.get("route") if isinstance(flow, dict) else None
    return {
        "state": status.get("state"),
        "flow_locked": isinstance(flow, dict),
        "route": route,
        "source_actor": status.get("source_actor"),
        "candidate_source_actors": status.get("candidate_source_actors", []),
        "insertion_count": status.get("insertion_count", 0),
        "synthetic_serial_count": status.get("synthetic_serial_count", 0),
    }


def log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def request_events(path: Path, offset: int, request_id: str) -> list[dict[str, Any]]:
    """Extract a privacy-minimized event trail for one diagnostic action."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            values = [json.loads(line) for line in handle if line.strip()]
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    allowed = {
        "event",
        "timestamp",
        "request_id",
        "action",
        "serial_u32",
        "host_command_serial_u32",
        "injection_offset",
        "inserted_length",
        "command_record_length",
        "before_length",
        "after_length",
        "host_acknowledged_inserted_bytes",
        "response_retagged",
        "error",
    }
    relevant_events = {
        "proxy_armed",
        "request_injected",
        "host_acknowledged_inserted_bytes",
        "host_response_retagged",
        "host_response_timeout",
        "arm_file_rejected",
        "packet_error_original_forwarded",
    }
    result: list[dict[str, Any]] = []
    active = False
    for value in values:
        if not isinstance(value, dict):
            continue
        nested = value.get("request")
        nested_id = nested.get("request_id") if isinstance(nested, dict) else None
        event_id = value.get("request_id") or nested_id
        if event_id == request_id:
            active = True
        if not active or value.get("event") not in relevant_events:
            continue
        sanitized = {key: value[key] for key in allowed if key in value}
        if isinstance(nested, dict):
            for key in ("request_id", "action"):
                if key in nested:
                    sanitized[key] = nested[key]
        result.append(sanitized)
    return result


def wait_for_flow(
    controller: SessionProxyController,
    *,
    source_actor_is_automatic: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_message_at = 0.0
    while time.monotonic() < deadline:
        status = controller.status()
        if not status.get("running"):
            raise SessionProxyError("会话代理在等待联机流量时退出。")
        actor_candidates = status.get("candidate_source_actors", [])
        actor_ready = not source_actor_is_automatic or (
            isinstance(actor_candidates, list) and len(actor_candidates) == 1
        )
        if status.get("flow") and actor_ready:
            return status
        if time.monotonic() - last_message_at >= 5:
            print(
                "等待双向可靠流与合作端 actor："
                f"flow={bool(status.get('flow'))}, candidates={actor_candidates}",
                flush=True,
            )
            last_message_at = time.monotonic()
        time.sleep(0.25)
    raise SessionProxyError(
        "等待联机流或唯一合作端 actor 超时。自动 actor 模式下请让合作端先"
        "自然执行一条无害命令。"
    )


def operator_verdict(prompt_text: str) -> str:
    print(f"玩家核验：{prompt_text}")
    while True:
        answer = input(
            "输入 p=符合预期，n=动作未发生，u=结果异常，o=不同步/时间停滞： "
        ).strip().lower()
        verdicts = {
            "p": "passed",
            "n": "action_not_applied",
            "u": "unexpected_result",
            "o": "desync_or_stalled_clock",
        }
        if answer in verdicts:
            return verdicts[answer]


def confirm_disposable_session(already_acknowledged: bool) -> None:
    if already_acknowledged:
        return
    print(
        "联机模式会真实修改游戏状态，只能用于所有参与者已授权的可丢弃测试存档。"
    )
    answer = input(f"请输入 {DISPOSABLE_CONFIRMATION} 继续：").strip()
    if answer != DISPOSABLE_CONFIRMATION:
        raise RuntimeError("未确认可丢弃测试会话。")


def finish_proxy_safely(
    controller: SessionProxyController,
    report: dict[str, Any],
) -> None:
    status = controller.status()
    if not status.get("running"):
        return
    if int(status.get("insertion_count") or 0) == 0:
        controller.stop()
        return
    print(
        "代理已经改写可靠流，不能在仍连接房间时关闭。请先让合作端退出多人房间。"
    )
    answer = input(f"退出后请输入 {ROOM_EXITED_CONFIRMATION}：").strip()
    if answer == ROOM_EXITED_CONFIRMATION:
        controller.stop(room_exited=True)
    else:
        report["notes"].append(
            "代理仍在运行：未收到 ROOM EXITED 确认。退出房间后使用 stop 子命令。"
        )


def run_live_suite(
    *,
    plan: dict[str, Any],
    config: dict[str, Any],
    report: dict[str, Any],
    output_directory: Path,
    flow_timeout_seconds: int,
) -> str:
    controller = SessionProxyController(config)
    scenario_by_id = plan_scenario_by_id(plan)
    halted = False
    try:
        started = controller.start()
        if int(started.get("insertion_count") or 0) != 0:
            raise SessionProxyError(
                "兼容性验收要求一条尚未插入命令的新代理会话。请先退出当前房间并"
                "停止旧代理，再重新开始。"
            )
        report["flow"] = safe_proxy_status(started)
        write_report(output_directory, report)
        print("会话代理已在进房前启动。现在进入测试房间并让合作端产生双向流量。")
        if int(config.get("session_proxy_source_actor") or 0) == 0:
            print("actor 为自动识别：合作端请先自然执行一条无害命令。")
        if input(f"房间准备完毕后输入 {ROOM_READY_CONFIRMATION}：").strip() != (
            ROOM_READY_CONFIRMATION
        ):
            raise RuntimeError("玩家取消联机验收。")
        locked = wait_for_flow(
            controller,
            source_actor_is_automatic=(
                int(config.get("session_proxy_source_actor") or 0) == 0
            ),
            timeout_seconds=flow_timeout_seconds,
        )
        report["flow"] = safe_proxy_status(locked)
        write_report(output_directory, report)
        print(f"已锁定联机流：route={report['flow'].get('route')}")

        for index, item in enumerate(enabled_report_items(report), start=1):
            if item.get("offline_status") != "passed":
                halted = True
                item["network_stage"] = "blocked_by_offline_probe"
                report["notes"].append(
                    f"{item['id']} 离线构包失败，未向网络发送任何内容。"
                )
                write_report(output_directory, report)
                break
            scenario = scenario_by_id[str(item["id"])]
            spec = COMMAND_SPEC_BY_ACTION[str(item["action"])]
            print("\n" + "=" * 72)
            print(f"[{index}] {spec.title} / {spec.action} / {spec.wire_family_hex}")
            print(f"准备条件：{scenario.get('setup') or spec.setup}")
            print("目标：" + json.dumps(scenario["target"], ensure_ascii=False))
            choice = input("按 Enter 执行，输入 s 跳过，输入 a 中止：").strip().lower()
            if choice == "s":
                item["network_stage"] = "skipped_by_operator"
                item["operator_verdict"] = "skipped"
                write_report(output_directory, report)
                continue
            if choice == "a":
                item["network_stage"] = "aborted_by_operator"
                halted = True
                write_report(output_directory, report)
                break

            current = controller.status()
            if not current.get("flow") or current.get("armed"):
                item["network_stage"] = "proxy_preflight_failed"
                item["network_error"] = "flow missing or another request is armed"
                halted = True
                write_report(output_directory, report)
                break
            request_id = f"compat-{report['run_id']}-{index}-{item['action']}"
            offset = log_offset(controller.log_path)
            item["requested_at"] = now_iso()
            write_report(output_directory, report)
            try:
                result = controller.arm_and_wait(
                    action=str(item["action"]),
                    target=dict(scenario["target"]),
                    request_id=request_id,
                    timeout_seconds=int(
                        config.get("session_proxy_response_timeout_seconds", 30)
                    )
                    + 15,
                )
                item["network_result"] = result
                item["network_stage"] = classify_network_result(result)
                response = result.get("response")
                if isinstance(response, dict) and response.get("carrier_length"):
                    item["response_record_length"] = int(response["carrier_length"])
                    item["request_response_length_match"] = (
                        int(item["record_length"])
                        == int(response["carrier_length"])
                    )
            except Exception as error:
                item["network_stage"] = "controller_error"
                item["network_error"] = f"{type(error).__name__}: {error}"
            item["proxy_events"] = request_events(
                controller.log_path,
                offset,
                request_id,
            )
            item["operator_verdict"] = operator_verdict(
                str(scenario.get("operator_check") or spec.operator_check)
            )
            item["finished_at"] = now_iso()
            write_report(output_directory, report)
            if (
                item["network_stage"] != "network_confirmed"
                or item["operator_verdict"] != "passed"
            ):
                halted = True
                report["notes"].append(
                    f"{item['id']} 出现异常；按安全规则停止后续命令。"
                )
                write_report(output_directory, report)
                break
    except (KeyboardInterrupt, EOFError) as error:
        halted = True
        report["notes"].append(f"联机验收被中断：{type(error).__name__}。")
    except Exception as error:
        halted = True
        report["notes"].append(f"联机验收失败：{type(error).__name__}: {error}")
    finally:
        try:
            finish_proxy_safely(controller, report)
        except (KeyboardInterrupt, EOFError) as error:
            report["notes"].append(
                f"关闭代理的玩家确认被中断：{type(error).__name__}。"
            )
        except Exception as error:
            report["notes"].append(
                f"关闭代理失败：{type(error).__name__}: {error}"
            )
        write_report(output_directory, report)
    if halted:
        return "failed"
    completed = all(
        item.get("network_stage") == "network_confirmed"
        and item.get("operator_verdict") == "passed"
        for item in enabled_report_items(report)
    )
    if completed:
        return "passed"
    report["notes"].append("至少一个已启用场景被跳过，验收结果不完整。")
    return "incomplete"


def command_catalog(args: argparse.Namespace) -> int:
    document = catalog_document()
    if args.output:
        write_json(args.output, document)
        print(args.output)
    else:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


def command_init_plan(args: argparse.Namespace) -> int:
    if args.output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite {args.output}; use --force.")
    write_json(
        args.output,
        new_plan_document(game_version=args.game_version, game_build=args.game_build),
    )
    print(f"已生成安全模板：{args.output}")
    print("所有真实动作默认关闭；请从当前测试存档填写目标，再逐项启用。")
    return 0


def report_directory(args: argparse.Namespace, identifier: str) -> Path:
    if args.output_dir:
        return args.output_dir
    return ROOT / "artifacts" / "protocol_compatibility" / identifier


def command_check(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    identifier = run_id()
    directory = report_directory(args, identifier)
    report = prepare_report(plan=plan, mode="offline", identifier=identifier)
    write_json(directory / "plan.snapshot.json", plan)
    if not args.skip_repository_tests:
        report["offline_tests"] = run_repository_tests(directory / "tests.log")
    failures = [
        item
        for item in enabled_report_items(report)
        if item.get("offline_status") != "passed"
    ]
    tests_failed = (
        isinstance(report.get("offline_tests"), dict)
        and report["offline_tests"].get("status") != "passed"
    )
    report["status"] = "failed" if failures or tests_failed else "passed"
    report["finished_at"] = now_iso()
    apply_baseline(report, args.baseline)
    write_report(directory, report)
    print(f"离线验收：{report['status']}，报告：{directory / 'report.md'}")
    return 1 if report["status"] == "failed" else 0


def command_live(args: argparse.Namespace) -> int:
    confirm_disposable_session(args.acknowledge_disposable_session)
    plan = load_plan(args.plan, require_live_acknowledgements=True)
    config = load_runtime_config(args.config)
    enabled = [item for item in plan["scenarios"] if item.get("enabled") is True]
    if not enabled:
        raise ValueError("Live plan has no enabled scenarios.")
    identifier = run_id()
    directory = (
        args.output_dir
        or Path(str(config["runtime_root"])).expanduser()
        / "artifacts"
        / "protocol_compatibility"
        / identifier
    )
    report = prepare_report(plan=plan, mode="live", identifier=identifier)
    write_json(directory / "plan.snapshot.json", plan)
    report["game_install"] = detected_game_install(config)
    if report["game_install"] is None:
        report["status"] = "failed"
        report["finished_at"] = now_iso()
        report["notes"].append(
            "无法从 game_root 或 Steam 库确认当前 Stellaris 安装版本；未启动代理。"
        )
        write_report(directory, report)
        return 1
    detected_version = str(
        report["game_install"].get("normalized_version") or ""
    )
    if detected_version != str(plan["game_version"]):
        report["status"] = "failed"
        report["finished_at"] = now_iso()
        report["notes"].append(
            "计划版本与本机游戏版本不一致："
            f"plan={plan['game_version']}, detected={detected_version or 'unknown'}；"
            "未启动代理。"
        )
        write_report(directory, report)
        return 1
    if not args.skip_repository_tests:
        report["offline_tests"] = run_repository_tests(directory / "tests.log")
        if report["offline_tests"]["status"] != "passed":
            report["status"] = "failed"
            report["finished_at"] = now_iso()
            report["notes"].append(
                "仓库自动测试失败；按安全规则未启动代理、未发送任何命令。"
            )
            write_report(directory, report)
            print(f"测试失败，未触碰联机流量。报告：{directory / 'report.md'}")
            return 1
    offline_failures = [
        item
        for item in enabled_report_items(report)
        if item.get("offline_status") != "passed"
    ]
    if offline_failures:
        report["status"] = "failed"
        report["finished_at"] = now_iso()
        report["notes"].append(
            "至少一个已启用场景无法离线构包；未启动代理、未发送任何命令。"
        )
        write_report(directory, report)
        print(f"离线构包失败，报告：{directory / 'report.md'}")
        return 1

    live_status = run_live_suite(
        plan=plan,
        config=config,
        report=report,
        output_directory=directory,
        flow_timeout_seconds=args.flow_timeout_seconds,
    )
    report["status"] = live_status
    report["finished_at"] = now_iso()
    apply_baseline(report, args.baseline)
    write_report(directory, report)
    print(f"联机验收：{report['status']}，报告：{directory / 'report.md'}")
    return 0 if live_status == "passed" else 1


def command_stop(args: argparse.Namespace) -> int:
    config = load_runtime_config(args.config)
    controller = SessionProxyController(config)
    status = controller.stop(room_exited=args.room_exited)
    print(json.dumps(safe_proxy_status(status), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stellaris version-update protocol acceptance suite. Offline checks are "
            "the default; live mode requires an authorized disposable room."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog = subparsers.add_parser("catalog", help="show the command catalog")
    catalog.add_argument("--output", type=Path)
    catalog.set_defaults(handler=command_catalog)

    init_plan = subparsers.add_parser("init-plan", help="create a disabled plan")
    init_plan.add_argument("--game-version", required=True)
    init_plan.add_argument("--game-build", default="")
    init_plan.add_argument("--output", type=Path, required=True)
    init_plan.add_argument("--force", action="store_true")
    init_plan.set_defaults(handler=command_init_plan)

    check = subparsers.add_parser("check", help="build enabled commands offline")
    check.add_argument("--plan", type=Path, required=True)
    check.add_argument("--output-dir", type=Path)
    check.add_argument("--baseline", type=Path)
    check.add_argument("--skip-repository-tests", action="store_true")
    check.set_defaults(handler=command_check)

    live = subparsers.add_parser("live", help="run a confirmed live acceptance test")
    live.add_argument("--plan", type=Path, required=True)
    live.add_argument("--config", type=Path, required=True)
    live.add_argument("--output-dir", type=Path)
    live.add_argument("--baseline", type=Path)
    live.add_argument("--flow-timeout-seconds", type=int, default=180)
    live.add_argument("--skip-repository-tests", action="store_true")
    live.add_argument("--acknowledge-disposable-session", action="store_true")
    live.set_defaults(handler=command_live)

    stop = subparsers.add_parser("stop", help="stop a proxy left after interruption")
    stop.add_argument("--config", type=Path, required=True)
    stop.add_argument("--room-exited", action="store_true", required=True)
    stop.set_defaults(handler=command_stop)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
