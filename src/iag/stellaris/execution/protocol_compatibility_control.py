"""Control-panel orchestration for deterministic protocol acceptance runs.

The coordinator never calls an LLM.  It persists a declarative plan, performs
offline command-builder probes, and advances a live session one explicitly
confirmed action at a time through :class:`SessionProxyController`.
"""

from __future__ import annotations

import importlib.metadata
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from iag.stellaris.execution.protocol_compatibility import (
    COMMAND_SPEC_BY_ACTION,
    OFFLINE_FIXTURE_TARGETS,
    REPORT_SCHEMA,
    SUPPORTED_SESSION_PROXY_ACTIONS,
    catalog_document,
    classify_network_result,
    compare_report_documents,
    new_plan_document,
    new_report_document,
    now_iso,
    target_fingerprint,
    target_placeholder_paths,
    validate_plan_document,
    write_report,
)
from iag.stellaris.execution.session_proxy import build_action_probe
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)
from iag.stellaris.game_knowledge import (
    detect_game_root,
    install_identity,
    normalized_version,
)


CONTROL_SCHEMA = "iag.protocol_compatibility_control.v1"
LIVE_PHASES = frozenset(
    {
        "waiting_for_room",
        "ready_for_action",
        "executing_action",
        "awaiting_operator_verdict",
        "halted",
        "completed",
    }
)
OPERATOR_VERDICTS = frozenset(
    {
        "passed",
        "action_not_applied",
        "unexpected_result",
        "desync_or_stalled_clock",
    }
)


class ProtocolCompatibilityError(RuntimeError):
    """An operator-facing compatibility-suite error."""


def _run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _platform_version() -> str:
    try:
        return importlib.metadata.version("imperial-auto-governor")
    except importlib.metadata.PackageNotFoundError:
        return "development"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_proxy_status(status: Mapping[str, Any]) -> dict[str, Any]:
    flow = status.get("flow")
    flow = flow if isinstance(flow, dict) else {}
    candidates = status.get("candidate_source_actors", [])
    return {
        "running": status.get("running") is True,
        "ready": status.get("ready") is True,
        "state": status.get("state"),
        "flow_locked": bool(flow),
        "route": flow.get("route"),
        "source_actor": status.get("source_actor"),
        "candidate_source_actors": (
            candidates if isinstance(candidates, list) else []
        ),
        "armed": status.get("armed") is True,
        "insertion_count": int(status.get("insertion_count") or 0),
        "synthetic_serial_count": int(
            status.get("synthetic_serial_count") or 0
        ),
    }


def _game_install(config: Mapping[str, Any]) -> dict[str, Any] | None:
    game_root = detect_game_root(dict(config))
    if game_root is None:
        return None
    identity = install_identity(game_root)
    return {
        "version": identity.get("version"),
        "raw_version": identity.get("raw_version"),
        "normalized_version": identity.get("normalized_version"),
        "mod_compatibility_version": identity.get(
            "mod_compatibility_version"
        ),
        "distribution": identity.get("distribution"),
        "launcher_settings_sha256": identity.get(
            "launcher_settings_sha256"
        ),
    }


def _scenario_probe(scenario: Mapping[str, Any]) -> dict[str, Any]:
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
    placeholder_paths = target_placeholder_paths(scenario["target"])
    if placeholder_paths:
        result["offline_status"] = "unconfigured"
        result["placeholder_paths"] = placeholder_paths
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
                f"catalog={spec.wire_family_hex}, "
                f"built={probe['wire_family_hex']}"
            )
        else:
            result["offline_status"] = "passed"
    except Exception as error:
        result["offline_status"] = "build_failed"
        result["offline_error"] = f"{type(error).__name__}: {error}"
    return result


def _fixture_probe(action: str) -> dict[str, Any]:
    spec = COMMAND_SPEC_BY_ACTION[action]
    result: dict[str, Any] = {
        "action": action,
        "wire_family_hex": spec.wire_family_hex,
        "status": "failed",
    }
    try:
        probe = build_action_probe(
            action=action,
            target=dict(OFFLINE_FIXTURE_TARGETS[action]),
        )
        result.update(probe)
        result["status"] = (
            "passed"
            if probe["wire_family_hex"] == spec.wire_family_hex
            else "wire_family_mismatch"
        )
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def _validate_complete_plan(
    value: Any,
    *,
    require_live_acknowledgements: bool = False,
) -> dict[str, Any]:
    plan = validate_plan_document(
        value,
        require_live_acknowledgements=require_live_acknowledgements,
    )
    actions = {str(item["action"]) for item in plan["scenarios"]}
    expected = set(SUPPORTED_SESSION_PROXY_ACTIONS)
    if actions != expected:
        missing = sorted(expected - actions)
        extra = sorted(actions - expected)
        raise ValueError(
            "Compatibility plan must contain the complete command catalog; "
            f"missing={missing}, extra={extra}."
        )
    return plan


class ProtocolCompatibilityControl:
    """Persist and advance one control-panel compatibility workflow."""

    def __init__(
        self,
        runtime_root: Path,
        config_getter: Callable[[], Mapping[str, Any]],
    ) -> None:
        self.runtime_root = runtime_root.expanduser()
        self.root = self.runtime_root / "state" / "protocol_compatibility"
        self.artifact_root = (
            self.runtime_root / "artifacts" / "protocol_compatibility"
        )
        self.plan_path = self.root / "plan.json"
        self.state_path = self.root / "control.json"
        self.baseline_path = self.root / "baseline.json"
        self._config_getter = config_getter
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._save_state(self._default_state())

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "schema": CONTROL_SCHEMA,
            "phase": "idle",
            "plan_sha256": None,
            "offline_plan_sha256": None,
            "offline_report_run_id": None,
            "run_id": None,
            "report_directory": None,
            "enabled_ids": [],
            "current_index": 0,
            "owns_proxy": False,
            "last_error": "",
            "updated_at": now_iso(),
        }

    def _config(self) -> dict[str, Any]:
        return dict(self._config_getter())

    def _controller(self) -> SessionProxyController:
        return SessionProxyController(self._config())

    def _load_state(self) -> dict[str, Any]:
        value = _read_json(self.state_path)
        if not isinstance(value, dict) or value.get("schema") != CONTROL_SCHEMA:
            value = self._default_state()
        return value

    def _save_state(self, value: dict[str, Any]) -> None:
        value["schema"] = CONTROL_SCHEMA
        value["updated_at"] = now_iso()
        _atomic_write_json(self.state_path, value)

    def _load_plan(self) -> dict[str, Any] | None:
        value = _read_json(self.plan_path)
        if value is None:
            return None
        try:
            return _validate_complete_plan(value)
        except ValueError as error:
            raise ProtocolCompatibilityError(str(error)) from error

    @staticmethod
    def _plan_sha256(plan: Mapping[str, Any]) -> str:
        return target_fingerprint(plan)

    def _report_directory(self, state: Mapping[str, Any]) -> Path | None:
        raw = str(state.get("report_directory") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser().resolve()
        root = self.artifact_root.resolve()
        if path != root and root not in path.parents:
            raise ProtocolCompatibilityError("验收报告路径超出运行目录。")
        return path

    def _load_report(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        directory = self._report_directory(state)
        if directory is None:
            return None
        value = _read_json(directory / "report.json")
        if value is None or value.get("schema") != REPORT_SCHEMA:
            return None
        return value

    def _write_report(
        self,
        state: Mapping[str, Any],
        report: Mapping[str, Any],
    ) -> None:
        directory = self._report_directory(state)
        if directory is None:
            raise ProtocolCompatibilityError("当前验收尚未建立报告目录。")
        write_report(directory, report)

    @staticmethod
    def _report_summary(
        report: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if report is None:
            return None
        return {
            "run_id": report.get("run_id"),
            "mode": report.get("mode"),
            "status": report.get("status"),
            "started_at": report.get("started_at"),
            "finished_at": report.get("finished_at"),
            "notes": list(report.get("notes", [])),
            "scenarios": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "action",
                        "enabled",
                        "offline_status",
                        "network_stage",
                        "operator_verdict",
                        "network_error",
                    )
                    if key in item
                }
                for item in report.get("scenarios", [])
                if isinstance(item, dict)
            ],
        }

    def live_active(self) -> bool:
        with self._lock:
            return str(self._load_state().get("phase")) in LIVE_PHASES

    def new_plan(
        self,
        *,
        game_version: str = "",
        game_build: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if self.live_active():
                raise ProtocolCompatibilityError(
                    "联机验收仍在进行，不能替换当前计划。"
                )
            install = _game_install(self._config())
            detected = str(
                (install or {}).get("normalized_version") or ""
            ).strip()
            selected_version = game_version.strip() or detected or "unknown"
            plan = new_plan_document(
                game_version=selected_version,
                game_build=game_build.strip(),
            )
            _atomic_write_json(self.plan_path, plan)
            state = self._load_state()
            state.update(
                {
                    "phase": "plan_ready",
                    "plan_sha256": self._plan_sha256(plan),
                    "offline_plan_sha256": None,
                    "offline_report_run_id": None,
                    "run_id": None,
                    "report_directory": None,
                    "enabled_ids": [],
                    "current_index": 0,
                    "owns_proxy": False,
                    "last_error": "",
                }
            )
            self._save_state(state)
            return self.payload()

    def save_plan(self, value: Any) -> dict[str, Any]:
        with self._lock:
            if self.live_active():
                raise ProtocolCompatibilityError(
                    "联机验收期间计划已冻结，结束代理后才能修改。"
                )
            try:
                plan = _validate_complete_plan(value)
            except ValueError as error:
                raise ProtocolCompatibilityError(str(error)) from error
            _atomic_write_json(self.plan_path, plan)
            state = self._load_state()
            state.update(
                {
                    "phase": "plan_ready",
                    "plan_sha256": self._plan_sha256(plan),
                    "offline_plan_sha256": None,
                    "offline_report_run_id": None,
                    "run_id": None,
                    "report_directory": None,
                    "enabled_ids": [],
                    "current_index": 0,
                    "owns_proxy": False,
                    "last_error": "",
                }
            )
            self._save_state(state)
            return self.payload()

    def offline_check(self) -> dict[str, Any]:
        with self._lock:
            if self.live_active():
                raise ProtocolCompatibilityError(
                    "联机验收期间不能重新运行离线构包。"
                )
            plan = self._load_plan()
            if plan is None:
                raise ProtocolCompatibilityError("请先创建验收计划。")
            identifier = _run_id()
            directory = self.artifact_root / identifier
            state = self._load_state()
            state.update(
                {
                    "phase": "offline_running",
                    "run_id": identifier,
                    "report_directory": str(directory),
                    "plan_sha256": self._plan_sha256(plan),
                    "last_error": "",
                }
            )
            self._save_state(state)

            report = new_report_document(
                run_id=identifier,
                mode="offline",
                platform_version=_platform_version(),
                plan=plan,
            )
            report["game_install"] = _game_install(self._config())
            fixtures = [
                _fixture_probe(action)
                for action in SUPPORTED_SESSION_PROXY_ACTIONS
            ]
            fixture_failures = [
                item for item in fixtures if item.get("status") != "passed"
            ]
            report["offline_tests"] = {
                "status": "failed" if fixture_failures else "passed",
                "catalog_fixture_count": len(fixtures),
                "passed": len(fixtures) - len(fixture_failures),
                "failed": len(fixture_failures),
                "fixtures": fixtures,
            }
            report["scenarios"] = [
                _scenario_probe(item) for item in plan["scenarios"]
            ]
            scenario_failures = [
                item
                for item in report["scenarios"]
                if item.get("enabled") is True
                and item.get("offline_status")
                not in {"passed", "unconfigured"}
            ]
            passed = not fixture_failures and not scenario_failures
            report["status"] = "passed" if passed else "failed"
            report["finished_at"] = now_iso()
            baseline = _read_json(self.baseline_path)
            if baseline and baseline.get("schema") == REPORT_SCHEMA:
                report["baseline_comparison"] = compare_report_documents(
                    report,
                    baseline,
                )
            directory.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(directory / "plan.snapshot.json", plan)
            write_report(directory, report)
            state.update(
                {
                    "phase": "offline_passed" if passed else "offline_failed",
                    "offline_plan_sha256": (
                        self._plan_sha256(plan) if passed else None
                    ),
                    "offline_report_run_id": identifier if passed else None,
                    "last_error": (
                        ""
                        if passed
                        else "至少一项离线命令构包检查失败。"
                    ),
                }
            )
            self._save_state(state)
            return self.payload()

    def start_live(self, *, disposable_authorized: bool) -> dict[str, Any]:
        with self._lock:
            if self.live_active():
                raise ProtocolCompatibilityError("已有一轮联机验收尚未结束。")
            if not disposable_authorized:
                raise ProtocolCompatibilityError(
                    "必须确认这是所有参与者已授权的可丢弃测试房间。"
                )
            plan = self._load_plan()
            if plan is None:
                raise ProtocolCompatibilityError("请先创建验收计划。")
            try:
                plan = _validate_complete_plan(
                    plan,
                    require_live_acknowledgements=True,
                )
            except ValueError as error:
                raise ProtocolCompatibilityError(str(error)) from error
            enabled = [
                item for item in plan["scenarios"] if item.get("enabled") is True
            ]
            if not enabled:
                raise ProtocolCompatibilityError("至少启用一个联机验收动作。")
            unconfigured = [
                str(item["id"])
                for item in enabled
                if target_placeholder_paths(item["target"])
            ]
            if unconfigured:
                raise ProtocolCompatibilityError(
                    "以下启用动作仍含示例占位符，不能启动联机验收："
                    + ", ".join(unconfigured)
                    + "。请从当前存档填写真实目标，或禁用这些动作。"
                )
            state = self._load_state()
            plan_sha256 = self._plan_sha256(plan)
            if state.get("offline_plan_sha256") != plan_sha256:
                raise ProtocolCompatibilityError(
                    "当前计划尚未通过对应的离线全量构包检查。"
                )
            config = self._config()
            if str(config.get("execution_mode", "")) != "session_proxy":
                raise ProtocolCompatibilityError(
                    "请先保存并选择会话代理执行模式。"
                )
            if config.get("session_proxy_acknowledged") is not True:
                raise ProtocolCompatibilityError(
                    "请先确认会话代理实验功能风险。"
                )
            install = _game_install(config)
            if install is None:
                raise ProtocolCompatibilityError(
                    "无法确认本机 Stellaris 版本，未启动代理。"
                )
            expected_version = normalized_version(plan.get("game_version"))
            detected_version = str(
                install.get("normalized_version") or ""
            ).strip()
            if not expected_version or expected_version != detected_version:
                raise ProtocolCompatibilityError(
                    "计划版本与本机游戏版本不一致："
                    f"plan={plan.get('game_version')}, "
                    f"detected={detected_version or 'unknown'}。"
                )
            controller = SessionProxyController(config)
            current = controller.status()
            if current.get("running"):
                raise ProtocolCompatibilityError(
                    "已有会话代理正在运行。请退出旧房间并停止它，再从本页"
                    "一键启动验收。"
                )

            identifier = _run_id()
            directory = self.artifact_root / identifier
            report = new_report_document(
                run_id=identifier,
                mode="live",
                platform_version=_platform_version(),
                plan=plan,
            )
            report["game_install"] = install
            report["offline_tests"] = {
                "status": "passed",
                "plan_sha256": plan_sha256,
                "source_report_run_id": state.get("offline_report_run_id"),
            }
            report["scenarios"] = [
                _scenario_probe(item) for item in plan["scenarios"]
            ]
            try:
                started = controller.start()
                if int(started.get("insertion_count") or 0) != 0:
                    raise ProtocolCompatibilityError(
                        "兼容性验收要求全新的零注入代理会话。"
                    )
            except Exception:
                try:
                    status = controller.status()
                    if status.get("running") and int(
                        status.get("insertion_count") or 0
                    ) == 0:
                        controller.stop()
                except Exception:
                    pass
                raise
            report["flow"] = _safe_proxy_status(started)
            directory.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(directory / "plan.snapshot.json", plan)
            write_report(directory, report)
            state.update(
                {
                    "phase": "waiting_for_room",
                    "run_id": identifier,
                    "report_directory": str(directory),
                    "enabled_ids": [str(item["id"]) for item in enabled],
                    "current_index": 0,
                    "owns_proxy": True,
                    "last_error": "",
                }
            )
            self._save_state(state)
            return self.payload()

    def confirm_room(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            if state.get("phase") != "waiting_for_room":
                raise ProtocolCompatibilityError("当前并未等待测试房间锁流。")
            config = self._config()
            status = SessionProxyController(config).status()
            if not status.get("running") or not status.get("ready"):
                raise ProtocolCompatibilityError("会话代理已经退出或尚未就绪。")
            if not isinstance(status.get("flow"), dict):
                raise ProtocolCompatibilityError(
                    "尚未锁定双向可靠流；请让合作端在房间内产生自然双向流量。"
                )
            if int(config.get("session_proxy_source_actor") or 0) == 0:
                candidates = status.get("candidate_source_actors", [])
                actor_ready = bool(status.get("source_actor")) or (
                    isinstance(candidates, list) and len(candidates) == 1
                )
                if not actor_ready:
                    raise ProtocolCompatibilityError(
                        "自动 actor 尚未收敛到唯一合作端；请先自然执行一条"
                        "无害命令。"
                    )
            report = self._load_report(state)
            if report is None:
                raise ProtocolCompatibilityError("联机验收报告已经丢失。")
            report["flow"] = _safe_proxy_status(status)
            self._write_report(state, report)
            state["phase"] = "ready_for_action"
            state["last_error"] = ""
            self._save_state(state)
            return self.payload()

    def _current_scenario(
        self,
        state: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        enabled_ids = list(state.get("enabled_ids", []))
        index = int(state.get("current_index") or 0)
        if index < 0 or index >= len(enabled_ids):
            return None
        current_id = str(enabled_ids[index])
        return next(
            (
                item
                for item in plan["scenarios"]
                if str(item["id"]) == current_id
            ),
            None,
        )

    @staticmethod
    def _report_item(
        report: Mapping[str, Any],
        scenario_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in report.get("scenarios", [])
                if isinstance(item, dict) and str(item.get("id")) == scenario_id
            ),
            None,
        )

    def execute_current(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            if state.get("phase") != "ready_for_action":
                raise ProtocolCompatibilityError("当前动作尚未进入可执行阶段。")
            plan = self._load_plan()
            report = self._load_report(state)
            if plan is None or report is None:
                raise ProtocolCompatibilityError("验收计划或报告已经丢失。")
            scenario = self._current_scenario(state, plan)
            if scenario is None:
                raise ProtocolCompatibilityError("没有待执行的验收动作。")
            item = self._report_item(report, str(scenario["id"]))
            if item is None or item.get("offline_status") != "passed":
                raise ProtocolCompatibilityError(
                    "当前动作没有通过离线构包，未触碰网络。"
                )
            controller = self._controller()
            proxy_status = controller.status()
            if not proxy_status.get("flow") or proxy_status.get("armed"):
                raise ProtocolCompatibilityError(
                    "代理流已丢失或已有另一条命令等待执行。"
                )
            request_id = (
                f"compat-{state['run_id']}-"
                f"{int(state.get('current_index') or 0) + 1}-"
                f"{scenario['action']}"
            )
            item["requested_at"] = now_iso()
            state["phase"] = "executing_action"
            state["last_error"] = ""
            self._write_report(state, report)
            self._save_state(state)

        try:
            result = controller.arm_and_wait(
                action=str(scenario["action"]),
                target=dict(scenario["target"]),
                request_id=request_id,
                timeout_seconds=int(
                    self._config().get(
                        "session_proxy_response_timeout_seconds",
                        30,
                    )
                )
                + 15,
                template_record_hex=(
                    str(scenario["template_record_hex"])
                    if scenario.get("template_record_hex")
                    else None
                ),
            )
            network_stage = classify_network_result(result)
            network_error = ""
        except Exception as error:
            result = None
            network_stage = "controller_error"
            network_error = f"{type(error).__name__}: {error}"

        with self._lock:
            state = self._load_state()
            report = self._load_report(state)
            if report is None:
                raise ProtocolCompatibilityError("联机验收报告已经丢失。")
            item = self._report_item(report, str(scenario["id"]))
            if item is None:
                raise ProtocolCompatibilityError("当前报告动作已经丢失。")
            if result is not None:
                item["network_result"] = result
                response = result.get("response")
                if isinstance(response, dict) and response.get("carrier_length"):
                    item["response_record_length"] = int(
                        response["carrier_length"]
                    )
                    item["request_response_length_match"] = (
                        int(item["record_length"])
                        == int(response["carrier_length"])
                    )
            item["network_stage"] = network_stage
            if network_error:
                item["network_error"] = network_error
            state["phase"] = "awaiting_operator_verdict"
            state["last_error"] = network_error
            self._write_report(state, report)
            self._save_state(state)
            return self.payload()

    def record_verdict(self, verdict: str) -> dict[str, Any]:
        with self._lock:
            if verdict not in OPERATOR_VERDICTS:
                raise ProtocolCompatibilityError("不支持的玩家验收结论。")
            state = self._load_state()
            if state.get("phase") != "awaiting_operator_verdict":
                raise ProtocolCompatibilityError("当前动作不在等待玩家判定。")
            plan = self._load_plan()
            report = self._load_report(state)
            if plan is None or report is None:
                raise ProtocolCompatibilityError("验收计划或报告已经丢失。")
            scenario = self._current_scenario(state, plan)
            if scenario is None:
                raise ProtocolCompatibilityError("当前验收动作已经丢失。")
            item = self._report_item(report, str(scenario["id"]))
            if item is None:
                raise ProtocolCompatibilityError("当前报告动作已经丢失。")
            item["operator_verdict"] = verdict
            item["finished_at"] = now_iso()
            if (
                item.get("network_stage") != "network_confirmed"
                or verdict != "passed"
            ):
                state["phase"] = "halted"
                state["last_error"] = (
                    f"{scenario['id']} 未同时通过网络确认和玩家观察；"
                    "后续动作已停止。"
                )
                report["notes"].append(state["last_error"])
            else:
                state["current_index"] = int(
                    state.get("current_index") or 0
                ) + 1
                state["phase"] = (
                    "completed"
                    if state["current_index"] >= len(state["enabled_ids"])
                    else "ready_for_action"
                )
                state["last_error"] = ""
            self._write_report(state, report)
            self._save_state(state)
            return self.payload()

    def skip_current(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            if state.get("phase") != "ready_for_action":
                raise ProtocolCompatibilityError("当前动作不能跳过。")
            plan = self._load_plan()
            report = self._load_report(state)
            if plan is None or report is None:
                raise ProtocolCompatibilityError("验收计划或报告已经丢失。")
            scenario = self._current_scenario(state, plan)
            if scenario is None:
                raise ProtocolCompatibilityError("没有待跳过的验收动作。")
            item = self._report_item(report, str(scenario["id"]))
            if item is None:
                raise ProtocolCompatibilityError("当前报告动作已经丢失。")
            item["network_stage"] = "skipped_by_operator"
            item["operator_verdict"] = "skipped"
            item["finished_at"] = now_iso()
            state["current_index"] = int(state.get("current_index") or 0) + 1
            state["phase"] = (
                "completed"
                if state["current_index"] >= len(state["enabled_ids"])
                else "ready_for_action"
            )
            self._write_report(state, report)
            self._save_state(state)
            return self.payload()

    def finish(self, *, room_exited: bool) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            if str(state.get("phase")) not in LIVE_PHASES:
                raise ProtocolCompatibilityError("当前没有待结束的联机验收。")
            if not room_exited:
                raise ProtocolCompatibilityError(
                    "请先让合作端退出多人房间，再结束验收代理。"
                )
            controller = self._controller()
            try:
                controller.stop(room_exited=True)
            except SessionProxyError as error:
                state["last_error"] = str(error)
                self._save_state(state)
                raise ProtocolCompatibilityError(str(error)) from error
            report = self._load_report(state)
            if report is not None:
                enabled = [
                    item
                    for item in report.get("scenarios", [])
                    if isinstance(item, dict) and item.get("enabled") is True
                ]
                complete = bool(enabled) and all(
                    item.get("network_stage") == "network_confirmed"
                    and item.get("operator_verdict") == "passed"
                    for item in enabled
                )
                any_failure = any(
                    item.get("network_stage")
                    in {
                        "controller_error",
                        "authoritative_response_not_matched",
                        "host_ack_or_authoritative_response_missing",
                        "confirmation_metadata_incomplete",
                        "proxy_result_unrecognized",
                    }
                    or item.get("operator_verdict")
                    in {
                        "action_not_applied",
                        "unexpected_result",
                        "desync_or_stalled_clock",
                    }
                    for item in enabled
                )
                report["status"] = (
                    "passed"
                    if complete
                    else ("failed" if any_failure else "incomplete")
                )
                report["finished_at"] = now_iso()
                baseline = _read_json(self.baseline_path)
                if baseline and baseline.get("schema") == REPORT_SCHEMA:
                    report["baseline_comparison"] = compare_report_documents(
                        report,
                        baseline,
                    )
                self._write_report(state, report)
                if report["status"] == "passed":
                    _atomic_write_json(self.baseline_path, report)
            state.update(
                {
                    "phase": "finished",
                    "owns_proxy": False,
                    "last_error": "",
                }
            )
            self._save_state(state)
            return self.payload()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            controller = self._controller()
            if controller.status().get("running"):
                raise ProtocolCompatibilityError(
                    "会话代理仍在运行；请先退出房间并结束验收。"
                )
            plan = self._load_plan()
            state = self._load_state()
            plan_sha256 = self._plan_sha256(plan) if plan else None
            offline_current = (
                plan_sha256 is not None
                and state.get("offline_plan_sha256") == plan_sha256
            )
            state.update(
                {
                    "phase": (
                        "offline_passed"
                        if offline_current
                        else ("plan_ready" if plan else "idle")
                    ),
                    "enabled_ids": [],
                    "current_index": 0,
                    "owns_proxy": False,
                    "last_error": "",
                }
            )
            self._save_state(state)
            return self.payload()

    def report_path(self, format_name: str) -> Path:
        with self._lock:
            state = self._load_state()
            directory = self._report_directory(state)
            if directory is None:
                raise ProtocolCompatibilityError("当前没有可下载的验收报告。")
            names = {"json": "report.json", "markdown": "report.md"}
            if format_name not in names:
                raise ProtocolCompatibilityError("报告格式必须是 json 或 markdown。")
            path = directory / names[format_name]
            if not path.is_file():
                raise ProtocolCompatibilityError("验收报告尚未生成。")
            return path

    def payload(self) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
            plan = self._load_plan()
            report = self._load_report(state)
            try:
                proxy = _safe_proxy_status(self._controller().status())
            except Exception as error:
                proxy = {
                    "running": False,
                    "ready": False,
                    "flow_locked": False,
                    "error": f"{type(error).__name__}: {error}",
                }
            plan_sha256 = self._plan_sha256(plan) if plan else None
            enabled_ids = list(state.get("enabled_ids", []))
            current_index = int(state.get("current_index") or 0)
            current_id = (
                str(enabled_ids[current_index])
                if 0 <= current_index < len(enabled_ids)
                else None
            )
            phase = str(state.get("phase") or "idle")
            live_active = phase in LIVE_PHASES
            enabled_count = sum(
                item.get("enabled") is True
                for item in (plan or {}).get("scenarios", [])
            )
            offline_current = bool(
                plan_sha256
                and state.get("offline_plan_sha256") == plan_sha256
            )
            unconfigured_enabled_ids = [
                str(item["id"])
                for item in (plan or {}).get("scenarios", [])
                if item.get("enabled") is True
                and target_placeholder_paths(item.get("target", {}))
            ]
            live_targets_ready = bool(
                enabled_count and not unconfigured_enabled_ids
            )
            workflow_messages = {
                "idle": "尚未创建协议验收计划。",
                "plan_ready": "计划可编辑；请填写当前存档目标并运行离线检查。",
                "offline_running": "正在执行离线构包检查。",
                "offline_passed": (
                    "离线构包器检查通过；请先填写或禁用仍含占位符的联机目标。"
                    if unconfigured_enabled_ids
                    else "当前计划离线检查通过，可以进房前启动代理。"
                ),
                "offline_failed": "离线检查失败；不会启动代理或触碰网络。",
                "waiting_for_room": "代理已启动；现在进入测试房间并确认锁流。",
                "ready_for_action": "可靠流已锁定；检查当前动作后单次执行。",
                "executing_action": "当前动作正在等待房主权威结果。",
                "awaiting_operator_verdict": "请根据游戏内实际结果判定当前动作。",
                "halted": "检测到失败或异常；后续动作已停止，请退出房间。",
                "completed": "已完成全部启用动作；请退出房间后结束代理。",
                "finished": "本轮验收已经结束，报告可供下载。",
            }
            return {
                "schema": "iag.protocol_compatibility_console.v1",
                "llm_calls": False,
                "catalog": catalog_document(),
                "plan": plan,
                "plan_sha256": plan_sha256,
                "offline_current": offline_current,
                "state": {
                    **state,
                    "live_active": live_active,
                    "current_scenario_id": current_id,
                    "enabled_count": enabled_count,
                    "live_targets_ready": live_targets_ready,
                    "unconfigured_enabled_ids": unconfigured_enabled_ids,
                    "workflow_message": workflow_messages.get(
                        phase,
                        phase,
                    ),
                },
                "proxy": proxy,
                "game_install": _game_install(self._config()),
                "report": self._report_summary(report),
                "actions": {
                    "can_edit_plan": not live_active,
                    "can_run_offline": plan is not None and not live_active,
                    "can_start_live": (
                        plan is not None
                        and not live_active
                        and offline_current
                        and enabled_count > 0
                        and live_targets_ready
                    ),
                    "can_confirm_room": phase == "waiting_for_room",
                    "can_execute": phase == "ready_for_action",
                    "can_skip": phase == "ready_for_action",
                    "can_record_verdict": (
                        phase == "awaiting_operator_verdict"
                    ),
                    "can_finish": live_active,
                    "can_reset": not proxy.get("running", False),
                },
            }
