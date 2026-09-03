"""Lifecycle and request API for the Windows session proxy."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from iag.stellaris.execution.host_executor_protocol import local_ipv4_for_remote


class SessionProxyError(RuntimeError):
    """Raised when the experimental proxy cannot be used safely."""


def windows_process_is_elevated() -> bool:
    """Return whether the current Windows process may open WinDivert."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def session_proxy_worker_command(arguments: list[str]) -> list[str]:
    """Build the worker command for source and frozen Windows deployments."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--session-proxy-worker", *arguments]
    return [
        sys.executable,
        "-m",
        "iag.stellaris.execution.session_proxy",
        *arguments,
    ]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class SessionProxyController:
    """Start one proxy before room join and serialize commands through it."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        runtime_root = Path(config["runtime_root"]).expanduser()
        self.root = runtime_root / "state" / "session_proxy"
        self.ready_path = self.root / "ready.json"
        self.status_path = self.root / "status.json"
        self.arm_path = self.root / "arm.json"
        self.log_path = self.root / "session_proxy.jsonl"
        self.stdout_path = self.root / "stdout.log"
        self.stderr_path = self.root / "stderr.log"
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None

    def _configured_ips(self) -> tuple[str, str]:
        host_ip = str(
            self.config.get("session_proxy_host_ip")
            or self.config.get("host_ip")
            or ""
        ).strip()
        if not host_ip:
            raise SessionProxyError(
                "代理模式必须配置房主 IP；代理需要在加入房间前启动。"
            )
        local_ip = str(self.config.get("session_proxy_local_ip") or "").strip()
        if not local_ip:
            local_ip = local_ipv4_for_remote(host_ip)
        return local_ip, host_ip

    def status(self) -> dict[str, Any]:
        ready = _read_json(self.ready_path) or {}
        telemetry = _read_json(self.status_path) or {}
        pid = int(telemetry.get("pid") or ready.get("pid") or 0)
        running = bool(pid and ready and psutil.pid_exists(pid))
        if not running and self.ready_path.exists():
            self.ready_path.unlink(missing_ok=True)
        return {
            "schema": "iag.session_proxy_status.v1",
            "running": running,
            "pid": pid or None,
            "ready": bool(running and ready),
            "process_elevated": windows_process_is_elevated(),
            **telemetry,
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if os.name != "nt":
                raise SessionProxyError(
                    "当前会话代理只实现了 Windows WinDivert 后端。"
                )
            if not bool(self.config.get("session_proxy_acknowledged", False)):
                raise SessionProxyError(
                    "请先在前端确认代理模式仍属于实验功能。"
                )
            if not windows_process_is_elevated():
                raise SessionProxyError(
                    "会话代理需要管理员权限打开 WinDivert。请关闭 Windows "
                    "Agent，右键选择“以管理员身份运行”，再在进房前启动代理。"
                )
            current = self.status()
            if current["running"]:
                return current
            local_ip, host_ip = self._configured_ips()
            self.root.mkdir(parents=True, exist_ok=True)
            for path in (self.ready_path, self.status_path, self.arm_path):
                path.unlink(missing_ok=True)
            worker_arguments = [
                "--local-ip",
                local_ip,
                "--host-ip",
                host_ip,
                "--log",
                str(self.log_path),
                "--ready-file",
                str(self.ready_path),
                "--status-file",
                str(self.status_path),
                "--arm-file",
                str(self.arm_path),
                "--session-seconds",
                str(int(self.config.get("session_proxy_session_seconds", 43200))),
                "--response-timeout-seconds",
                str(int(self.config.get("session_proxy_response_timeout_seconds", 30))),
                "--acknowledge-disposable-session",
            ]
            configured_process_names = self.config.get(
                "process_names",
                ["stellaris"],
            )
            if isinstance(configured_process_names, str):
                configured_process_names = [configured_process_names]
            for process_name in configured_process_names:
                normalized = str(process_name).strip()
                if normalized:
                    worker_arguments.extend(["--process-name", normalized])
            configured_transport_names = self.config.get(
                "transport_process_names",
                ["steam"],
            )
            if isinstance(configured_transport_names, str):
                configured_transport_names = [configured_transport_names]
            for process_name in configured_transport_names:
                normalized = str(process_name).strip()
                if normalized:
                    worker_arguments.extend(
                        ["--transport-process-name", normalized]
                    )
            command = session_proxy_worker_command(worker_arguments)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            stdout_handle = self.stdout_path.open("w", encoding="utf-8")
            stderr_handle = self.stderr_path.open("w", encoding="utf-8")
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    creationflags=creationflags,
                )
            finally:
                stdout_handle.close()
                stderr_handle.close()

            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    error = self.stderr_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ).strip()
                    raise SessionProxyError(
                        "会话代理在 READY 前退出：" + (error or "未知错误")
                    )
                if self.ready_path.is_file():
                    return self.status()
                time.sleep(0.1)
            self._process.terminate()
            raise SessionProxyError("会话代理未能在 12 秒内进入 READY。")

    def stop(self, *, room_exited: bool = False, force: bool = False) -> dict[str, Any]:
        with self._lock:
            current = self.status()
            if not current["running"]:
                return current
            if int(current.get("insertion_count") or 0) > 0 and not (
                room_exited or force
            ):
                raise SessionProxyError(
                    "代理已改写本局可靠流。请先退出多人房间，再确认停止；"
                    "连接仍在时强停会立即破坏同步。"
                )
            pid = int(current["pid"])
            try:
                if self._process is not None and self._process.pid == pid:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                else:
                    os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, psutil.NoSuchProcess):
                pass
            except subprocess.TimeoutExpired:
                self._process.kill()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and psutil.pid_exists(pid):
                time.sleep(0.1)
            self.ready_path.unlink(missing_ok=True)
            return self.status()

    def arm_and_wait(
        self,
        *,
        action: str,
        target: dict[str, Any],
        request_id: str | None = None,
        timeout_seconds: int | None = None,
        template_record_hex: str | None = None,
    ) -> dict[str, Any]:
        """Submit one action and wait for its correlated host result."""
        with self._lock:
            current = self.status()
            if not current["running"] or not current.get("ready"):
                raise SessionProxyError("会话代理尚未启动。")
            if not current.get("flow"):
                raise SessionProxyError(
                    "代理尚未锁定本局双向可靠流。请确认代理在合作端进房前"
                    "启动，并让游戏产生双向流量；公网/Steam 中继会按本机 "
                    "Stellaris/Steam UDP 端口自动识别实际对端。"
                )
            if current.get("armed"):
                raise SessionProxyError("会话代理已有一项等待执行的命令。")
            selected_id = request_id or uuid.uuid4().hex
            source_actor = int(self.config.get("session_proxy_source_actor") or 0)
            request = {
                "request_id": selected_id,
                "session_id": str(current["session_id"]),
                "action": action,
                "source_actor": source_actor,
                "host_actor": int(self.config.get("session_proxy_host_actor", 1)),
                "request_origin": 0,
                "previous_serial_floor": int(
                    self.config.get("session_proxy_previous_serial_floor", 0)
                ),
                "target": target,
            }
            if template_record_hex:
                request["template_record_hex"] = template_record_hex
            _atomic_write_json(self.arm_path, request)

        timeout = timeout_seconds or int(
            self.config.get("session_proxy_response_timeout_seconds", 30)
        ) + 10
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.status()
            if not current["running"]:
                raise SessionProxyError("会话代理在等待房主回包时退出。")
            result = current.get("last_request")
            if isinstance(result, dict) and result.get("request_id") == selected_id:
                return result
            time.sleep(0.1)
        raise SessionProxyError("等待会话代理动作结果超时。")
