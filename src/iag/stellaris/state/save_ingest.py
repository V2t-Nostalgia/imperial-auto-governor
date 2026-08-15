#!/usr/bin/env python3
"""Receive authenticated host saves and expose one verified current save."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping
from urllib.parse import unquote

from iag.stellaris.state.extract_game_state import load_save_metadata, newest_save


CAMPAIGN_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
GAME_DATE_RE = re.compile(r"^\d{1,6}\.\d{1,2}\.\d{1,2}$")
VALID_REVIEW_INTERVALS = {1, 3, 6, 12}
MAX_SOURCE_SAVE_LAG_VERSIONS = 24


class SaveIngestError(RuntimeError):
    """Raised when an uploaded save cannot be accepted safely."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def configured_path(
    config: Mapping[str, Any],
    key: str,
    default: Path,
) -> Path:
    value = Path(str(config.get(key, default))).expanduser()
    if value.is_absolute():
        return value
    return Path(str(config["runtime_root"])).expanduser() / value


def upload_root(config: Mapping[str, Any]) -> Path:
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    return configured_path(
        config,
        "uploaded_save_root",
        runtime_root / "state" / "uploaded_saves",
    )


def manifest_path(config: Mapping[str, Any]) -> Path:
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    return configured_path(
        config,
        "save_manifest_path",
        runtime_root / "state" / "current_save.json",
    )


def campaign_manifest_path(
    config: Mapping[str, Any],
    campaign_id: str,
) -> Path:
    selected = str(campaign_id).strip().lower()
    if not CAMPAIGN_ID_RE.fullmatch(selected):
        raise SaveIngestError("战役标识格式无效。")
    return upload_root(config) / selected / "current_save.json"


def upload_token_path(config: Mapping[str, Any]) -> Path:
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    return configured_path(
        config,
        "save_upload_token_file",
        runtime_root / "secrets" / "save_upload_token",
    )


def review_interval_months(config: Mapping[str, Any]) -> int:
    try:
        value = int(config.get("save_review_interval_months", 1))
    except (TypeError, ValueError) as error:
        raise SaveIngestError("存档读取周期必须是整数。") from error
    if value not in VALID_REVIEW_INTERVALS:
        raise SaveIngestError("存档读取周期只支持 1、3、6 或 12 个月。")
    return value


def maximum_source_save_lag_versions(config: Mapping[str, Any]) -> int:
    """Return the player-selected number of newer uploads a plan may tolerate."""
    try:
        value = int(config.get("maximum_source_save_lag_versions", 2))
    except (TypeError, ValueError) as error:
        raise SaveIngestError("存档版本容差必须是整数。") from error
    if not 0 <= value <= MAX_SOURCE_SAVE_LAG_VERSIONS:
        raise SaveIngestError(
            "存档版本容差必须在 0 到 "
            f"{MAX_SOURCE_SAVE_LAG_VERSIONS} 之间。"
        )
    return value


def bearer_token_matches(authorization: str | None, token: str) -> bool:
    if not authorization or not token:
        return False
    scheme, separator, candidate = authorization.partition(" ")
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and bool(candidate)
        and hmac.compare_digest(candidate, token)
    )


def read_manifest(config: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        value = json.loads(manifest_path(config).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_campaign_manifest(
    config: Mapping[str, Any],
    campaign_id: str,
) -> dict[str, Any] | None:
    try:
        value = json.loads(
            campaign_manifest_path(config, campaign_id).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_manifest_revision(
    config: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
) -> int | None:
    """Return a campaign-local upload revision, including pre-revision manifests."""
    selected = manifest if manifest is not None else read_manifest(config)
    if not isinstance(selected, Mapping):
        return None
    try:
        revision = int(selected.get("revision", 0))
    except (TypeError, ValueError):
        revision = 0
    if revision > 0:
        return revision

    stored_value = selected.get("stored_path")
    if not stored_value:
        return None
    stored = Path(str(stored_value)).expanduser().resolve()
    root = upload_root(config).resolve()
    if not stored.is_relative_to(root) or stored.parent.parent != root:
        return None
    # Old manifests did not carry a monotonic revision. The number of retained
    # campaign saves is a conservative migration baseline for the next upload.
    return max(sum(1 for path in stored.parent.glob("*.sav") if path.is_file()), 1)


def _verified_manifest_path(
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> Path:
    root = upload_root(config).resolve()
    stored_value = manifest.get("stored_path")
    if not stored_value:
        raise FileNotFoundError("上传存档清单没有 stored_path。")
    stored = Path(str(stored_value)).expanduser().resolve()
    if not stored.is_relative_to(root):
        raise PermissionError("上传存档清单指向了接收目录之外。")
    if not stored.is_file():
        raise FileNotFoundError(stored)
    expected_size = manifest.get("size")
    if expected_size is not None and stored.stat().st_size != int(expected_size):
        raise FileNotFoundError("上传存档大小与清单不一致。")
    return stored


def resolve_current_save(
    config: Mapping[str, Any],
    *,
    expected_campaign_id: str | None = None,
) -> Path:
    mode = str(config.get("save_source_mode", "host_upload")).strip()
    if mode == "host_upload":
        manifest = read_manifest(config)
        if not manifest:
            raise FileNotFoundError("尚未收到房主端存档。")
        if expected_campaign_id is not None:
            selected = str(expected_campaign_id).strip().lower()
            if not CAMPAIGN_ID_RE.fullmatch(selected):
                raise SaveIngestError("当前会话绑定的战役标识无效。")
            actual = str(manifest.get("campaign_id", "")).strip().lower()
            if actual != selected:
                raise SaveIngestError(
                    "当前上传存档不属于所选战役会话；已禁止读档与建设。"
                )
        return _verified_manifest_path(config, manifest)
    if mode == "legacy_local":
        return newest_save(Path(str(config["save_root"])).expanduser())
    raise SaveIngestError(f"不支持的存档来源模式：{mode}")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _decoded_header(
    headers: Mapping[str, str],
    name: str,
    *,
    maximum_length: int,
) -> str:
    raw = str(headers.get(name, "")).strip()
    value = unquote(raw)
    if not value or len(value) > maximum_length:
        raise SaveIngestError(f"缺少或无效的 {name}。")
    if any(ord(character) < 32 for character in value):
        raise SaveIngestError(f"{name} 含有控制字符。")
    return value


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def receive_uploaded_save(
    config: Mapping[str, Any],
    stream: BinaryIO,
    *,
    content_length: int,
    headers: Mapping[str, str],
) -> dict[str, Any]:
    maximum_bytes = int(config.get("save_upload_max_bytes", 268_435_456))
    if content_length <= 0 or content_length > maximum_bytes:
        raise SaveIngestError(
            f"存档大小必须在 1 到 {maximum_bytes} 字节之间。"
        )

    normalized = _normalized_headers(headers)
    original_name = _decoded_header(
        normalized,
        "x-iag-filename",
        maximum_length=180,
    )
    if (
        not original_name.lower().endswith(".sav")
        or "/" in original_name
        or "\\" in original_name
    ):
        raise SaveIngestError("上传文件名必须是无路径分隔符的 .sav 文件。")
    campaign_id = normalized.get("x-iag-campaign-id", "").strip().lower()
    expected_sha256 = normalized.get("x-iag-sha256", "").strip().lower()
    if not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise SaveIngestError("战役标识格式无效。")
    if not SHA256_RE.fullmatch(expected_sha256):
        raise SaveIngestError("缺少有效的 SHA-256。")
    source_id = _decoded_header(
        normalized,
        "x-iag-source-id",
        maximum_length=128,
    )
    campaign_label = _decoded_header(
        normalized,
        "x-iag-campaign-label",
        maximum_length=180,
    )
    try:
        source_mtime_ns = int(normalized.get("x-iag-source-mtime-ns", "0"))
    except ValueError as error:
        raise SaveIngestError("源存档修改时间格式无效。") from error
    if source_mtime_ns < 0:
        raise SaveIngestError("源存档修改时间不能为负数。")

    root = upload_root(config)
    staging = root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    temporary = staging / f"{campaign_id}.{secrets.token_hex(8)}.part"
    digest = hashlib.sha256()
    remaining = content_length
    try:
        with temporary.open("xb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise SaveIngestError("上传在声明长度之前中断。")
                handle.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        actual_sha256 = digest.hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise SaveIngestError("上传存档的 SHA-256 与请求头不一致。")
        try:
            with zipfile.ZipFile(temporary) as archive:
                members = set(archive.namelist())
                if not {"meta", "gamestate"}.issubset(members):
                    raise SaveIngestError(
                        "文件不是包含 meta 与 gamestate 的 Stellaris 存档。"
                    )
                archive.getinfo("meta")
                archive.getinfo("gamestate")
        except zipfile.BadZipFile as error:
            raise SaveIngestError("文件不是有效的 ZIP Stellaris 存档。") from error

        metadata = load_save_metadata(temporary)
        game_date = str(metadata.get("date") or "")
        if not GAME_DATE_RE.fullmatch(game_date):
            raise SaveIngestError("Stellaris 存档 meta 中缺少有效游戏日期。")

        destination_dir = root / campaign_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        previous_manifest = read_campaign_manifest(config, campaign_id)
        previous_revision = save_manifest_revision(config, previous_manifest) or 0
        date_stem = game_date.replace(".", "-")
        destination = destination_dir / f"{date_stem}_{actual_sha256[:16]}.sav"
        duplicate = destination.exists()
        if duplicate:
            temporary.unlink()
            if not previous_manifest or str(
                previous_manifest.get("sha256") or ""
            ) != actual_sha256:
                # A deliberate rollback to retained content is a new current
                # revision and must not inherit an old wall-clock timestamp.
                os.utime(destination, None)
        else:
            temporary.replace(destination)

        same_as_previous = bool(
            previous_manifest
            and str(previous_manifest.get("sha256") or "") == actual_sha256
        )
        revision = (
            max(previous_revision, 1)
            if same_as_previous
            else previous_revision + 1
        )
        received_at = now_iso()
        manifest = {
            "schema": "iag.host_save.v1",
            "revision": revision,
            "received_at": received_at,
            "source_id": source_id,
            "campaign_id": campaign_id,
            "campaign_label": campaign_label,
            "original_name": original_name,
            "source_mtime_ns": source_mtime_ns,
            "stored_path": str(destination.resolve()),
            "size": content_length,
            "sha256": actual_sha256,
            "metadata": metadata,
            "duplicate": duplicate,
        }
        _atomic_write_json(campaign_manifest_path(config, campaign_id), manifest)
        _atomic_write_json(manifest_path(config), manifest)
        return manifest
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
