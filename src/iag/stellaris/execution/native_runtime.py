"""Private client for a platform-native Stellaris in-process runtime.

The runtime describes the semantic tools compiled from ``actions/*.cpp``.
Applications never consume this transport directly: the Broker intersects the
runtime manifest with its authority and semantic contracts first.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import threading
import time
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ConfigDict, Field, model_validator

from iag.core.contracts import FrozenContract
from iag.core.paths import project_root

LINUX_NATIVE_RUNTIME_BUILD_ID: Final = "c6969e60fd81d738948222a94c0b5a0841abbffc"
WINDOWS_NATIVE_RUNTIME_BUILD_ID: Final = (
    "stellaris-4.4.6-windows-steam-24109497-bc451c72"
)
# Compatibility name retained for fixtures written before platform profiles.
NATIVE_RUNTIME_BUILD_ID: Final = LINUX_NATIVE_RUNTIME_BUILD_ID
SUPPORTED_NATIVE_RUNTIME_BUILD_IDS: Final = frozenset(
    {LINUX_NATIVE_RUNTIME_BUILD_ID, WINDOWS_NATIVE_RUNTIME_BUILD_ID}
)
DEFAULT_NATIVE_RUNTIME_SOCKET: Final = Path("/tmp/iag-stellaris-native.sock")
DEFAULT_NATIVE_RUNTIME_PIPE: Final = r"\\.\pipe\iag-stellaris-native"
_PROTOCOL_VERSION: Final = "IAG1"
_DESCRIBE_ACTION: Final = "describe_tools"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TECHNOLOGY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_RESPONSE_BYTES = 65_536


class NativeRuntimeError(RuntimeError):
    """The native runtime was unavailable or violated its private contract."""


class NativeRuntimeOutcome(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FAILED = "failed"
    PENDING = "pending"
    CONFLICT = "conflict"
    BUSY = "busy"


class NativeRuntimeParameter(FrozenContract):
    """One ordered semantic argument accepted by an action implementation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    parameter_type: Literal["uint32", "string", "boolean"] = Field(alias="type")
    required: bool
    description: str = Field(min_length=1, max_length=500)
    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)
    maximum_length: int | None = Field(default=None, ge=1, le=60_000)

    @model_validator(mode="after")
    def validate_type_limits(self) -> NativeRuntimeParameter:
        if self.parameter_type == "uint32":
            if self.minimum is None or self.maximum is None:
                raise ValueError("uint32 runtime parameters require numeric bounds.")
            if self.maximum > 0xFFFFFFFF or self.minimum > self.maximum:
                raise ValueError("Invalid uint32 runtime parameter bounds.")
        elif self.parameter_type == "string" and self.maximum_length is None:
            raise ValueError("String runtime parameters require maximum_length.")
        return self

    def encode(self, value: object) -> str:
        if self.parameter_type == "uint32":
            if isinstance(value, bool) or not isinstance(value, int):
                raise NativeRuntimeError(f"{self.name} must be an integer.")
            minimum = int(self.minimum or 0)
            maximum = int(self.maximum or 0)
            if value < minimum or value > maximum:
                raise NativeRuntimeError(f"{self.name} is outside runtime bounds.")
            return str(value)
        if self.parameter_type == "boolean":
            if not isinstance(value, bool):
                raise NativeRuntimeError(f"{self.name} must be a boolean.")
            return "1" if value else "0"
        if not isinstance(value, str):
            raise NativeRuntimeError(f"{self.name} must be a string.")
        maximum = int(self.maximum_length or 0)
        if not value or len(value) > maximum or "\t" in value or "\n" in value:
            raise NativeRuntimeError(f"{self.name} is not a valid runtime string.")
        return value


class NativeRuntimeTool(FrozenContract):
    """Self-described compiled semantic action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    action_version: int = Field(ge=1)
    application_id: str = Field(
        default="etc", pattern=r"^[a-z][a-z0-9_]{1,63}$"
    )
    description: str = Field(min_length=1, max_length=500)
    risk_class: Literal[
        "state_change",
        "destructive_state_change",
        "state_change_requires_fresh_save",
    ]
    verification_state: Literal["live_verified", "paired_capture"]
    parameters: tuple[NativeRuntimeParameter, ...]

    @model_validator(mode="after")
    def validate_unique_parameters(self) -> NativeRuntimeTool:
        names = tuple(item.name for item in self.parameters)
        if len(names) != len(set(names)):
            raise ValueError("Runtime tool parameter names must be unique.")
        if any(not item.required for item in self.parameters[:-1]):
            raise ValueError("Optional runtime parameters must follow required ones.")
        return self

    @property
    def semantic_capability(self) -> str:
        return f"stellaris.action.{self.action_type}.v{self.action_version}"


class NativeRuntimeManifest(FrozenContract):
    """Versioned tool catalog emitted by one loaded runtime build."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["iag.native_tool_manifest.v1"]
    platform: Literal["linux-x86_64", "windows-x64"]
    game_version: str = Field(min_length=1, max_length=120)
    build_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    tools: tuple[NativeRuntimeTool, ...]

    @model_validator(mode="after")
    def validate_catalog(self) -> NativeRuntimeManifest:
        keys = tuple((item.action_type, item.action_version) for item in self.tools)
        if len(keys) != len(set(keys)):
            raise ValueError("Runtime manifest contains duplicate actions.")
        return self

    def get(self, action_type: str, action_version: int = 1) -> NativeRuntimeTool:
        for tool in self.tools:
            if tool.action_type == action_type and tool.action_version == action_version:
                return tool
        raise KeyError(f"Runtime does not expose {action_type}.v{action_version}.")


class NativeRuntimeResponse(FrozenContract):
    """Strictly parsed evidence returned by the in-process runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(min_length=1, max_length=96)
    action: str = Field(min_length=1, max_length=64)
    outcome: NativeRuntimeOutcome
    build_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    country_id: int = Field(ge=0)
    target_echo: str = Field(min_length=1, max_length=60_000)
    detail: str = Field(max_length=240)

    @property
    def technology_id(self) -> str | None:
        """Compatibility view for the original research-only runtime client."""

        return self.target_echo if self.action == "stop_research" else None


def _require_token(value: str, *, maximum: int, label: str) -> str:
    if not value or len(value) > maximum or _TOKEN_RE.fullmatch(value) is None:
        raise NativeRuntimeError(f"Invalid {label} for native runtime protocol.")
    return value


class NativeRuntimeClient:
    """Synchronous client for a Unix socket or Windows named pipe runtime."""

    def __init__(
        self,
        endpoint: str | os.PathLike[str] | None = None,
        *,
        timeout_seconds: float = 20.0,
        loader_path: str | os.PathLike[str] | None = None,
        library_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.endpoint = str(
            endpoint
            if endpoint is not None
            else (
                DEFAULT_NATIVE_RUNTIME_PIPE
                if os.name == "nt"
                else DEFAULT_NATIVE_RUNTIME_SOCKET
            )
        )
        self.socket_path = Path(self.endpoint)
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        native_root = project_root() / "native_runtime"
        self.loader_path = Path(
            loader_path
            or native_root / "iag_stellaris_native_runtime_loader.exe"
        )
        self.library_path = Path(
            library_path or native_root / "iag_stellaris_native_runtime.dll"
        )
        self._manifest: NativeRuntimeManifest | None = None
        self._manifest_lock = threading.Lock()

    def available(self) -> bool:
        if os.name == "posix":
            try:
                return stat.S_ISSOCK(Path(self.endpoint).stat().st_mode)
            except OSError:
                return False
        if os.name == "nt":
            try:
                import ctypes

                if ctypes.windll.kernel32.WaitNamedPipeW(self.endpoint, 0):
                    return True
                # A busy single-instance pipe is already a loaded runtime, not
                # a reason to inject the DLL again.
                return int(ctypes.windll.kernel32.GetLastError()) in {121, 231}
            except (AttributeError, OSError):
                return False
        return False

    def ensure_available(self) -> bool:
        """Load the exact-build Windows runtime once when explicitly enabled."""

        if self.available():
            return True
        if os.name != "nt":
            return False
        if not self.loader_path.is_file() or not self.library_path.is_file():
            return False
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            completed = subprocess.run(
                (str(self.loader_path), str(self.library_path)),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode != 0:
            return False
        deadline = time.monotonic() + min(self.timeout_seconds, 10.0)
        while time.monotonic() < deadline:
            if self.available():
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _receive_line(connection: socket.socket) -> str:
        payload = bytearray()
        while len(payload) <= _MAX_RESPONSE_BYTES:
            chunk = connection.recv(4096)
            if not chunk:
                break
            payload.extend(chunk)
            newline = payload.find(b"\n")
            if newline >= 0:
                del payload[newline + 1 :]
                break
        return NativeRuntimeClient._decode_line(bytes(payload))

    @staticmethod
    def _decode_line(payload: bytes) -> str:
        if not payload.endswith(b"\n"):
            raise NativeRuntimeError("Native runtime returned an incomplete response.")
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise NativeRuntimeError("Native runtime response exceeded its size limit.")
        try:
            return payload[:-1].decode("ascii")
        except UnicodeDecodeError as error:
            raise NativeRuntimeError(
                "Native runtime response was not ASCII."
            ) from error

    def _exchange(self, request: bytes) -> str:
        if os.name == "posix":
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(self.timeout_seconds)
                    connection.connect(self.endpoint)
                    connection.sendall(request)
                    return self._receive_line(connection)
            except OSError as error:
                raise NativeRuntimeError(
                    f"Native runtime request failed: {error}"
                ) from error
        if os.name == "nt":
            try:
                with open(self.endpoint, "r+b", buffering=0) as pipe:  # noqa: PTH123
                    pipe.write(request)
                    payload = bytearray()
                    while len(payload) <= _MAX_RESPONSE_BYTES:
                        chunk = pipe.read(4096)
                        if not chunk:
                            break
                        payload.extend(chunk)
                        if b"\n" in chunk:
                            break
                return self._decode_line(bytes(payload))
            except OSError as error:
                raise NativeRuntimeError(
                    f"Native runtime request failed: {error}"
                ) from error
        raise NativeRuntimeError("Native runtime transport is unsupported.")

    @staticmethod
    def _parse_response(
        line: str,
        *,
        request_id: str,
        action: str,
        country_id: int,
        target_echo: str | None = None,
    ) -> NativeRuntimeResponse:
        fields = line.split("\t")
        if len(fields) != 8 or fields[0] != _PROTOCOL_VERSION:
            raise NativeRuntimeError("Native runtime returned an invalid response.")
        if fields[1] != request_id or fields[2] != action:
            raise NativeRuntimeError("Native runtime response correlation failed.")
        try:
            response_country_id = int(fields[5], 10)
        except ValueError as error:
            raise NativeRuntimeError(
                "Native runtime returned an invalid country id."
            ) from error
        response = NativeRuntimeResponse(
            request_id=fields[1],
            action=fields[2],
            outcome=NativeRuntimeOutcome(fields[3]),
            build_id=fields[4],
            country_id=response_country_id,
            target_echo=fields[6],
            detail=fields[7],
        )
        if response.build_id not in SUPPORTED_NATIVE_RUNTIME_BUILD_IDS:
            raise NativeRuntimeError("Native runtime build identity is unsupported.")
        if response.outcome not in {
            NativeRuntimeOutcome.REJECTED,
            NativeRuntimeOutcome.FAILED,
            NativeRuntimeOutcome.CONFLICT,
            NativeRuntimeOutcome.BUSY,
        }:
            if response.country_id != country_id:
                raise NativeRuntimeError(
                    "Native runtime echoed a different country id."
                )
            if target_echo is not None and response.target_echo != target_echo:
                raise NativeRuntimeError("Native runtime echoed a different target.")
        return response

    def _request(
        self,
        *,
        request_id: str,
        action: str,
        country_id: int,
        target_fields: tuple[str, ...],
        target_echo: str | None = None,
    ) -> NativeRuntimeResponse:
        request = (
            "\t".join(
                (_PROTOCOL_VERSION, request_id, action, str(country_id), *target_fields)
            ).encode("ascii")
            + b"\n"
        )
        return self._parse_response(
            self._exchange(request),
            request_id=request_id,
            action=action,
            country_id=country_id,
            target_echo=target_echo,
        )

    def describe_tools(self, *, refresh: bool = False) -> NativeRuntimeManifest:
        """Return the runtime-owned catalog, cached for one backend lifetime."""

        with self._manifest_lock:
            if self._manifest is not None and not refresh:
                return self._manifest
            request_id = "runtime-tool-manifest"
            request = (
                f"{_PROTOCOL_VERSION}\t{request_id}\t{_DESCRIBE_ACTION}\t0\n"
            ).encode("ascii")
            response = self._parse_response(
                self._exchange(request),
                request_id=request_id,
                action=_DESCRIBE_ACTION,
                country_id=0,
            )
            if response.outcome != NativeRuntimeOutcome.CONFIRMED:
                raise NativeRuntimeError(
                    f"Runtime tool manifest failed: {response.detail}"
                )
            try:
                manifest = NativeRuntimeManifest.model_validate_json(
                    response.target_echo
                )
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise NativeRuntimeError(
                    "Native runtime returned an invalid tool manifest."
                ) from error
            if manifest.build_id != response.build_id:
                raise NativeRuntimeError("Runtime manifest build identity changed.")
            self._manifest = manifest
            return manifest

    def execute(
        self,
        *,
        request_id: str,
        action_type: str,
        action_version: int,
        country_id: int,
        target: Mapping[str, Any],
    ) -> NativeRuntimeResponse:
        """Encode one reviewed semantic target using runtime-declared ordering."""

        request_id = _require_token(str(request_id), maximum=96, label="request id")
        action_type = _require_token(
            str(action_type), maximum=64, label="action type"
        )
        if country_id < 0 or country_id > 0xFFFFFFFF:
            raise NativeRuntimeError("Invalid country id for native runtime protocol.")
        try:
            tool = self.describe_tools().get(action_type, action_version)
        except KeyError as error:
            raise NativeRuntimeError(str(error)) from error
        fields: list[str] = []
        for parameter in tool.parameters:
            value = target.get(parameter.name)
            if parameter.name not in target or value is None:
                if parameter.required:
                    raise NativeRuntimeError(
                        f"Missing runtime parameter {parameter.name!r}."
                    )
                fields.append("")
                continue
            fields.append(parameter.encode(value))
        return self._request(
            request_id=request_id,
            action=action_type,
            country_id=country_id,
            target_fields=tuple(fields),
        )

    # Compatibility wrappers remain private to the backend migration. New
    # action implementations use execute() and need no Python dispatch branch.
    def stop_research(
        self,
        *,
        request_id: str,
        country_id: int,
        technology_id: str,
    ) -> NativeRuntimeResponse:
        if (
            not technology_id
            or len(technology_id) > 160
            or _TECHNOLOGY_RE.fullmatch(technology_id) is None
        ):
            raise NativeRuntimeError(
                "Invalid technology id for native runtime protocol."
            )
        return self.execute(
            request_id=request_id,
            action_type="stop_research",
            action_version=1,
            country_id=country_id,
            target={"technology_id": technology_id},
        )

    def move_fleet(
        self,
        *,
        request_id: str,
        country_id: int,
        fleet_id: int,
        destination_system_id: int,
    ) -> NativeRuntimeResponse:
        return self.execute(
            request_id=request_id,
            action_type="move_fleet",
            action_version=1,
            country_id=country_id,
            target={
                "fleet_id": fleet_id,
                "destination_system_id": destination_system_id,
            },
        )

    def attack_fleet(
        self,
        *,
        request_id: str,
        country_id: int,
        fleet_id: int,
        target_fleet_id: int,
    ) -> NativeRuntimeResponse:
        return self.execute(
            request_id=request_id,
            action_type="attack_fleet",
            action_version=1,
            country_id=country_id,
            target={"fleet_id": fleet_id, "target_fleet_id": target_fleet_id},
        )
