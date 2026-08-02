#!/usr/bin/env python3
"""Watch Windows Stellaris saves and upload stable host snapshots to IAG."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse
from uuid import uuid4

from iag_host_interceptor import (
    HostInterceptorError,
    is_windows_admin,
    run_host_interceptor,
)


GAME_DATE_RE = re.compile(r'(?m)^\s*date="([^"]+)"\s*$')
SAVE_NAME_RE = re.compile(r'(?m)^\s*name="([^"]*)"\s*$')
VALID_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")


class UploaderError(RuntimeError):
    """An operator-facing uploader failure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def log_event(log_path: Path, event: str, **fields: Any) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": now_iso(), "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def windows_documents() -> Path:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                (
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders"
                ),
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "Personal")
                return Path(os.path.expandvars(str(value))).expanduser()
        except (OSError, ImportError):
            pass
    return Path.home() / "Documents"


def default_save_root() -> Path:
    return windows_documents() / "Paradox Interactive" / "Stellaris" / "save games"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise UploaderError(f"配置文件不存在：{path}") from error
    if not isinstance(value, dict):
        raise UploaderError("上传器配置必须是 JSON 对象。")
    return value


def resolved_config_path(
    config_path: Path,
    value: str | None,
    default: Path,
) -> Path:
    path = Path(value).expanduser() if value else default
    if path.is_absolute():
        return path
    return config_path.parent / path


def load_secret(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise UploaderError(f"{label}文件不存在：{path}") from error
    if not value:
        raise UploaderError(f"{label}不能为空。")
    return value


def parse_save_metadata(path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not {"meta", "gamestate"}.issubset(names):
                raise UploaderError(f"不是完整 Stellaris 存档：{path}")
            text = archive.read("meta").decode("utf-8-sig", errors="replace")
    except (zipfile.BadZipFile, OSError, KeyError) as error:
        raise UploaderError(f"存档尚未完整写入：{path}") from error
    date_match = GAME_DATE_RE.search(text)
    if not date_match:
        raise UploaderError(f"存档 meta 中没有游戏日期：{path}")
    name_match = SAVE_NAME_RE.search(text)
    return {
        "date": date_match.group(1),
        "name": name_match.group(1) if name_match else "",
    }


def campaign_identity(save_root: Path, campaign_directory: Path) -> str:
    relative = campaign_directory.resolve().relative_to(save_root.resolve())
    material = f"{socket.gethostname().casefold()}\0{str(relative).casefold()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class SaveCandidate:
    path: Path
    size: int
    mtime_ns: int
    campaign_directory: Path


class StableSaveDiscovery:
    def __init__(self, save_root: Path, stability_seconds: float):
        self.save_root = save_root.resolve()
        self.stability_seconds = stability_seconds
        self.observed: dict[Path, tuple[int, int, float]] = {}

    def scan(self, now_mono: float | None = None) -> list[SaveCandidate]:
        now_value = time.monotonic() if now_mono is None else now_mono
        current_paths: set[Path] = set()
        stable: list[SaveCandidate] = []
        for path in self.save_root.rglob("*.sav"):
            try:
                resolved = path.resolve()
                stat = resolved.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            current_paths.add(resolved)
            previous = self.observed.get(resolved)
            signature = (stat.st_size, stat.st_mtime_ns)
            if previous is None or previous[:2] != signature:
                self.observed[resolved] = (*signature, now_value)
                continue
            if now_value - previous[2] < self.stability_seconds:
                continue
            stable.append(
                SaveCandidate(
                    path=resolved,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    campaign_directory=resolved.parent,
                )
            )
        for missing in set(self.observed) - current_paths:
            del self.observed[missing]
        return sorted(stable, key=lambda item: item.mtime_ns, reverse=True)


class PinnedHTTPSUploader:
    def __init__(
        self,
        server_url: str,
        certificate_sha256: str,
        token: str,
        timeout_seconds: float,
    ):
        parsed = urlparse(server_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise UploaderError("server_url 必须是有效的 https:// 地址。")
        fingerprint = certificate_sha256.replace(":", "").strip().lower()
        if not VALID_FINGERPRINT_RE.fullmatch(fingerprint):
            raise UploaderError("服务器证书 SHA-256 指纹格式无效。")
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.base_path = parsed.path.rstrip("/")
        self.upload_path = self.base_path + "/api/save/upload"
        self.fingerprint = fingerprint
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _verified_connection(self) -> http.client.HTTPSConnection:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
            context=context,
        )
        try:
            connection.connect()
            if connection.sock is None:
                raise UploaderError("HTTPS 连接没有可验证的套接字。")
            certificate = connection.sock.getpeercert(binary_form=True)
            actual_fingerprint = hashlib.sha256(certificate).hexdigest()
            if actual_fingerprint != self.fingerprint:
                raise UploaderError(
                    "Ubuntu 服务器证书指纹不匹配，已拒绝发送数据。"
                )
            return connection
        except Exception:
            connection.close()
            raise

    def upload(
        self,
        candidate: SaveCandidate,
        *,
        campaign_id: str,
        campaign_label: str,
        sha256: str,
    ) -> dict[str, Any]:
        connection = self._verified_connection()
        try:
            connection.putrequest("POST", self.upload_path)
            connection.putheader("Authorization", f"Bearer {self.token}")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(candidate.size))
            connection.putheader("X-IAG-Filename", quote(candidate.path.name, safe=""))
            connection.putheader("X-IAG-Campaign-ID", campaign_id)
            connection.putheader(
                "X-IAG-Campaign-Label",
                quote(campaign_label, safe=""),
            )
            connection.putheader(
                "X-IAG-Source-ID",
                quote(socket.gethostname(), safe=""),
            )
            connection.putheader(
                "X-IAG-Source-Mtime-Ns",
                str(candidate.mtime_ns),
            )
            connection.putheader("X-IAG-SHA256", sha256)
            connection.endheaders()
            with candidate.path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    connection.send(chunk)
            response = connection.getresponse()
            raw = response.read()
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {"raw_response": raw.decode("utf-8", errors="replace")}
            if response.status not in {200, 201}:
                raise UploaderError(
                    f"服务器拒绝上传：HTTP {response.status} {value}"
                )
            if not isinstance(value, dict):
                raise UploaderError("服务器上传响应不是 JSON 对象。")
            return value
        finally:
            connection.close()

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection = self._verified_connection()
        try:
            connection.putrequest("POST", self.base_path + path)
            connection.putheader("Authorization", f"Bearer {self.token}")
            connection.putheader("Content-Type", "application/json; charset=utf-8")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            raw = response.read()
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {"raw_response": raw.decode("utf-8", errors="replace")}
            if response.status != 200:
                raise UploaderError(
                    f"服务器拒绝{operation}：HTTP {response.status} {value}"
                )
            if not isinstance(value, dict):
                raise UploaderError(f"服务器{operation}响应不是 JSON 对象。")
            return value
        finally:
            connection.close()

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json(
            "/api/save/client-heartbeat",
            payload,
            operation="客户端心跳",
        )

    def host_executor_ready(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json(
            "/api/host-executor/ready",
            payload,
            operation="房主执行桥就绪确认",
        )

    def host_executor_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json(
            "/api/host-executor/result",
            payload,
            operation="房主执行结果",
        )

class SaveUploader:
    def __init__(self, config_path: Path):
        self.config_path = config_path.resolve()
        self.config = read_json(self.config_path)
        self.save_root = resolved_config_path(
            self.config_path,
            self.config.get("save_root"),
            default_save_root(),
        ).resolve()
        self.state_path = resolved_config_path(
            self.config_path,
            self.config.get("state_file"),
            self.config_path.parent / "state" / "uploader_state.json",
        )
        self.log_path = resolved_config_path(
            self.config_path,
            self.config.get("log_file"),
            self.config_path.parent / "logs" / "save_uploader.jsonl",
        )
        token_path = resolved_config_path(
            self.config_path,
            self.config.get("upload_token_file"),
            self.config_path.parent / "secrets" / "save_upload_token",
        )
        token = load_secret(token_path, "上传令牌")
        self.poll_seconds = max(float(self.config.get("poll_seconds", 2)), 0.5)
        self.discovery = StableSaveDiscovery(
            self.save_root,
            max(float(self.config.get("stability_seconds", 5)), 1),
        )
        self.maximum_initial_age_seconds = max(
            int(self.config.get("maximum_initial_save_age_seconds", 7200)),
            60,
        )
        self.bootstrap_latest = bool(self.config.get("bootstrap_latest", True))
        self.transport = PinnedHTTPSUploader(
            str(self.config.get("server_url", "")),
            str(self.config.get("server_certificate_sha256", "")),
            token,
            float(self.config.get("timeout_seconds", 180)),
        )
        self.heartbeat_seconds = max(
            float(self.config.get("heartbeat_seconds", 5)),
            2,
        )
        self.executor_lock = threading.Lock()
        self.executor_thread: threading.Thread | None = None
        self.active_executor_id: str | None = None
        self.handled_executor_ids: set[str] = set()
        self.state = self._load_state()
        atomic_write_json(self.state_path, self.state)

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "schema": "iag.windows_save_uploader.v2",
            "client_id": value.get("client_id") or uuid4().hex,
            "campaign_directory": value.get("campaign_directory"),
            "campaign_id": value.get("campaign_id"),
            "campaign_label": value.get("campaign_label"),
            "last_uploaded_path": value.get("last_uploaded_path"),
            "last_uploaded_sha256": value.get("last_uploaded_sha256"),
            "last_uploaded_mtime_ns": int(value.get("last_uploaded_mtime_ns", 0)),
            "last_success_at": value.get("last_success_at"),
            "last_game_date": value.get("last_game_date"),
        }

    def reset_campaign(self) -> None:
        client_id = self.state.get("client_id") or uuid4().hex
        self.state = {
            "schema": "iag.windows_save_uploader.v2",
            "client_id": client_id,
            "campaign_directory": None,
            "campaign_id": None,
            "campaign_label": None,
            "last_uploaded_path": None,
            "last_uploaded_sha256": None,
            "last_uploaded_mtime_ns": 0,
            "last_success_at": None,
        }
        atomic_write_json(self.state_path, self.state)
        log_event(self.log_path, "campaign_lock_reset")

    def _eligible_candidate(
        self,
        candidates: list[SaveCandidate],
    ) -> SaveCandidate | None:
        if not candidates:
            return None
        locked_value = self.state.get("campaign_directory")
        locked = Path(str(locked_value)).resolve() if locked_value else None
        last_mtime = int(self.state.get("last_uploaded_mtime_ns", 0))

        if locked is not None:
            newer_elsewhere = [
                item
                for item in candidates
                if item.campaign_directory != locked and item.mtime_ns > last_mtime
            ]
            if newer_elsewhere:
                return newer_elsewhere[0]
            locked_candidates = [
                item
                for item in candidates
                if item.campaign_directory == locked and item.mtime_ns >= last_mtime
            ]
            return locked_candidates[0] if locked_candidates else None

        if not self.bootstrap_latest:
            return None
        newest = candidates[0]
        age_seconds = max((time.time_ns() - newest.mtime_ns) / 1_000_000_000, 0)
        if age_seconds > self.maximum_initial_age_seconds:
            return None
        return newest

    def _lock_campaign(self, candidate: SaveCandidate) -> None:
        campaign_id = campaign_identity(
            self.save_root,
            candidate.campaign_directory,
        )
        self.state.update(
            {
                "campaign_directory": str(candidate.campaign_directory),
                "campaign_id": campaign_id,
                "campaign_label": candidate.campaign_directory.name,
            }
        )
        atomic_write_json(self.state_path, self.state)
        log_event(
            self.log_path,
            "campaign_locked",
            campaign_id=campaign_id,
            campaign_label=candidate.campaign_directory.name,
            campaign_directory=str(candidate.campaign_directory),
        )

    def cycle(self, now_mono: float | None = None) -> dict[str, Any] | None:
        if not self.save_root.is_dir():
            raise UploaderError(f"Stellaris 存档目录不存在：{self.save_root}")
        candidate = self._eligible_candidate(self.discovery.scan(now_mono))
        if candidate is None:
            return None
        if str(candidate.campaign_directory) != self.state.get(
            "campaign_directory"
        ):
            self._lock_campaign(candidate)

        metadata = parse_save_metadata(candidate.path)
        digest = sha256_file(candidate.path)
        if (
            digest == self.state.get("last_uploaded_sha256")
            and candidate.mtime_ns == self.state.get("last_uploaded_mtime_ns")
        ):
            return None
        log_event(
            self.log_path,
            "upload_started",
            path=str(candidate.path),
            size=candidate.size,
            game_date=metadata["date"],
            campaign_id=self.state["campaign_id"],
        )
        result = self.transport.upload(
            candidate,
            campaign_id=str(self.state["campaign_id"]),
            campaign_label=str(self.state["campaign_label"]),
            sha256=digest,
        )
        self.state.update(
            {
                "last_uploaded_path": str(candidate.path),
                "last_uploaded_sha256": digest,
                "last_uploaded_mtime_ns": candidate.mtime_ns,
                "last_success_at": now_iso(),
                "last_game_date": metadata["date"],
            }
        )
        atomic_write_json(self.state_path, self.state)
        log_event(
            self.log_path,
            "upload_succeeded",
            path=str(candidate.path),
            game_date=metadata["date"],
            sha256=digest,
            duplicate=bool(result.get("duplicate")),
        )
        return result

    def _executor_event(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        value: dict[str, Any],
    ) -> None:
        event = str(value.get("event", "host_executor_event"))
        fields = {key: item for key, item in value.items() if key != "event"}
        log_event(self.log_path, event, **fields)
        self._notify(callback, event, **fields)

    def _run_executor_request(
        self,
        request: dict[str, Any],
        *,
        stop_event: threading.Event,
        status_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        request_id = str(request.get("request_id", ""))
        client_id = str(self.state["client_id"])
        self._notify(
            status_callback,
            "host_executor_started",
            request_id=request_id,
            action=request.get("action"),
        )
        try:
            result = run_host_interceptor(
                request,
                client_id=client_id,
                ready_callback=self.transport.host_executor_ready,
                stop_event=stop_event,
                event_callback=lambda value: self._executor_event(
                    status_callback,
                    value,
                ),
            )
        except Exception as error:
            result = {
                "success": False,
                "phase": "host_interceptor_error",
                "error": f"{type(error).__name__}: {error}",
                "carrier_seen": False,
                "rewritten": False,
                "authoritative_confirmation": False,
            }
            log_event(
                self.log_path,
                "host_executor_failed",
                request_id=request_id,
                error_type=type(error).__name__,
                error=str(error),
            )
        payload = {
            "request_id": request_id,
            "client_id": client_id,
            "success": result.get("success") is True,
            "phase": str(result.get("phase", "")),
            "error": str(result.get("error", "")),
            "carrier_seen": result.get("carrier_seen") is True,
            "rewritten": result.get("rewritten") is True,
            "authoritative_confirmation": (
                result.get("authoritative_confirmation") is True
            ),
            "telemetry": result,
        }
        try:
            self.transport.host_executor_result(payload)
            self._notify(
                status_callback,
                "host_executor_completed",
                request_id=request_id,
                success=payload["success"],
                phase=payload["phase"],
                error=payload["error"],
            )
        except Exception as error:
            log_event(
                self.log_path,
                "host_executor_result_failed",
                request_id=request_id,
                error_type=type(error).__name__,
                error=str(error),
            )
            self._notify(
                status_callback,
                "host_executor_failed",
                request_id=request_id,
                error=str(error),
            )
        finally:
            with self.executor_lock:
                self.handled_executor_ids.add(request_id)
                if len(self.handled_executor_ids) > 100:
                    self.handled_executor_ids = set(
                        sorted(self.handled_executor_ids)[-50:]
                    )
                if self.active_executor_id == request_id:
                    self.active_executor_id = None
                self.executor_thread = None

    def _accept_executor_request(
        self,
        request: Any,
        *,
        stop_event: threading.Event,
        status_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        if not isinstance(request, dict):
            return
        request_id = str(request.get("request_id", ""))
        if not request_id:
            return
        with self.executor_lock:
            if (
                request_id in self.handled_executor_ids
                or self.active_executor_id is not None
            ):
                return
            self.active_executor_id = request_id
            self.executor_thread = threading.Thread(
                target=self._run_executor_request,
                kwargs={
                    "request": request,
                    "stop_event": stop_event,
                    "status_callback": status_callback,
                },
                name=f"iag-host-executor-{request_id}",
                daemon=True,
            )
            self.executor_thread.start()

    def _heartbeat_payload(self, state: str) -> dict[str, Any]:
        capabilities = ["save_upload_v1"]
        if is_windows_admin():
            capabilities.append("host_inbound_rewrite_v1")
        return {
            "client_id": self.state["client_id"],
            "hostname": socket.gethostname(),
            "app_version": "2026.08.02-hostbridge8",
            "capabilities": capabilities,
            "state": state,
            "campaign_id": self.state.get("campaign_id"),
            "campaign_label": self.state.get("campaign_label"),
            "last_game_date": self.state.get("last_game_date"),
            "last_success_at": self.state.get("last_success_at"),
        }

    @staticmethod
    def _notify(
        callback: Callable[[dict[str, Any]], None] | None,
        event: str,
        **fields: Any,
    ) -> None:
        if callback is None:
            return
        try:
            callback({"event": event, "timestamp": now_iso(), **fields})
        except Exception:
            pass

    def run(
        self,
        *,
        once: bool = False,
        stop_event: threading.Event | None = None,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> int:
        stop = stop_event or threading.Event()
        log_event(
            self.log_path,
            "uploader_started",
            save_root=str(self.save_root),
            server=f"https://{self.transport.host}:{self.transport.port}",
            once=once,
        )
        self._notify(
            status_callback,
            "started",
            save_root=str(self.save_root),
            server=f"https://{self.transport.host}:{self.transport.port}",
        )
        consecutive_failures = 0
        last_heartbeat = 0.0
        try:
            while not stop.is_set():
                now_mono = time.monotonic()
                if now_mono - last_heartbeat >= self.heartbeat_seconds:
                    try:
                        heartbeat = self.transport.heartbeat(
                            self._heartbeat_payload("running")
                        )
                        self._notify(
                            status_callback,
                            "connected",
                            source_ip=heartbeat.get("source_ip"),
                            observed_at=heartbeat.get("observed_at"),
                            host_bridge_elevated=is_windows_admin(),
                        )
                        self._accept_executor_request(
                            heartbeat.get("executor_request"),
                            stop_event=stop,
                            status_callback=status_callback,
                        )
                    except Exception as error:
                        log_event(
                            self.log_path,
                            "heartbeat_failed",
                            error_type=type(error).__name__,
                            error=str(error),
                        )
                        self._notify(
                            status_callback,
                            "connection_error",
                            error=str(error),
                        )
                    last_heartbeat = now_mono

                try:
                    result = self.cycle(now_mono=now_mono)
                    consecutive_failures = 0
                    if result is not None:
                        self._notify(
                            status_callback,
                            "upload_succeeded",
                            game_date=self.state.get("last_game_date"),
                            path=self.state.get("last_uploaded_path"),
                            campaign_label=self.state.get("campaign_label"),
                        )
                    if once and result is not None:
                        return 0
                except KeyboardInterrupt:
                    return 0
                except Exception as error:
                    consecutive_failures += 1
                    log_event(
                        self.log_path,
                        "cycle_failed",
                        error_type=type(error).__name__,
                        error=str(error),
                        consecutive_failures=consecutive_failures,
                    )
                    self._notify(
                        status_callback,
                        "cycle_error",
                        error=str(error),
                        consecutive_failures=consecutive_failures,
                    )
                    if once:
                        return 1
                delay = min(
                    self.poll_seconds * (2 ** min(consecutive_failures, 5)),
                    60,
                )
                stop.wait(delay)
            return 0
        finally:
            stop.set()
            with self.executor_lock:
                executor_thread = self.executor_thread
            if executor_thread is not None:
                executor_thread.join(timeout=5)
            if not once:
                try:
                    self.transport.heartbeat(self._heartbeat_payload("stopped"))
                except Exception:
                    pass
            log_event(self.log_path, "uploader_stopped", reason="requested")
            self._notify(status_callback, "stopped")
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reset-campaign", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    uploader = SaveUploader(args.config)
    if args.reset_campaign:
        uploader.reset_campaign()
        if not args.once:
            return 0
    if args.status:
        print(json.dumps(uploader.state, ensure_ascii=False, indent=2))
        return 0
    return uploader.run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
