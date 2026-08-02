#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Create a runtime-paired Host Bridge archive from the public release archive."""

from __future__ import annotations

import json
import os
import re
import ssl
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


PAIRED_HOST_BRIDGE_ARCHIVE = "IAGHostBridge-paired-windows-x64.zip"
PUBLIC_HOST_BRIDGE_ARCHIVE = "IAGHostBridge-windows-x64.zip"
VALID_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class HostBridgePairingError(RuntimeError):
    """Raised when a safe paired Host Bridge archive cannot be produced."""


def certificate_sha256(certificate_path: Path) -> str:
    """Return the SHA-256 fingerprint of a PEM certificate's DER bytes."""
    import hashlib

    try:
        pem = certificate_path.read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, UnicodeError, ValueError) as error:
        raise HostBridgePairingError(
            f"无法读取 Agent TLS 证书：{certificate_path}"
        ) from error
    return hashlib.sha256(der).hexdigest()


def pairing_server_url(host_header: str) -> str:
    """Convert a validated HTTPS Host header into the Host Bridge server URL."""
    host = host_header.strip()
    if not host or any(character.isspace() for character in host):
        raise HostBridgePairingError("下载请求没有提供有效的 Agent 主机地址。")
    if any(character in host for character in "/\\?#@"):
        raise HostBridgePairingError("Agent 主机地址包含无效字符。")
    try:
        parsed = urlparse(f"https://{host}")
        _ = parsed.port
    except ValueError as error:
        raise HostBridgePairingError("Agent 主机端口无效。") from error
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise HostBridgePairingError("Agent 主机地址无效。")
    return f"https://{host}"


def _safe_member_name(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if member.is_absolute() or not member.parts or ".." in member.parts:
        raise HostBridgePairingError(f"Host Bridge 包含不安全路径：{name}")
    return member


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    return ((member.external_attr >> 16) & 0o170000) == 0o120000


def build_paired_host_bridge_archive(
    source_archive: Path,
    destination_archive: Path,
    *,
    server_url: str,
    certificate_fingerprint: str,
    upload_token: str,
) -> Path:
    """Copy a public Host Bridge package and inject this Agent's pairing data."""
    fingerprint = certificate_fingerprint.replace(":", "").strip().lower()
    if not VALID_FINGERPRINT_RE.fullmatch(fingerprint):
        raise HostBridgePairingError("Agent TLS 证书指纹不是 64 位十六进制。")
    token = upload_token.strip()
    if not token:
        raise HostBridgePairingError("Agent 桥接令牌为空。")
    normalized_url = server_url.strip().rstrip("/")
    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise HostBridgePairingError("配对包只能使用有效的 HTTPS Agent 地址。")
    if not source_archive.is_file():
        raise HostBridgePairingError(f"缺少公开 Host Bridge 包：{source_archive}")

    destination_archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_archive.with_name(destination_archive.name + ".tmp")
    try:
        with zipfile.ZipFile(source_archive, "r") as source:
            members = source.infolist()
            roots = {
                _safe_member_name(member.filename).parts[0]
                for member in members
                if member.filename and not member.is_dir()
            }
            if len(roots) != 1:
                raise HostBridgePairingError(
                    "公开 Host Bridge 包必须只有一个顶层目录。"
                )
            root = next(iter(roots))
            example_name = f"{root}/iag_save_uploader.example.json"
            try:
                config = json.loads(source.read(example_name).decode("utf-8"))
            except (KeyError, UnicodeError, json.JSONDecodeError) as error:
                raise HostBridgePairingError(
                    "公开 Host Bridge 包缺少有效的示例配置。"
                ) from error
            if not isinstance(config, dict):
                raise HostBridgePairingError("Host Bridge 示例配置不是 JSON 对象。")
            config.update(
                {
                    "server_url": normalized_url,
                    "server_certificate_sha256": fingerprint,
                    "upload_token_file": "secrets/save_upload_token",
                }
            )

            replaced = {
                f"{root}/iag_save_uploader.json",
                f"{root}/secrets/save_upload_token",
            }
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as target:
                for member in members:
                    _safe_member_name(member.filename)
                    if _is_symlink(member):
                        raise HostBridgePairingError(
                            f"Host Bridge 包含符号链接：{member.filename}"
                        )
                    if member.filename in replaced:
                        continue
                    target.writestr(member, source.read(member.filename))
                target.writestr(
                    f"{root}/iag_save_uploader.json",
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                )
                token_info = zipfile.ZipInfo(f"{root}/secrets/save_upload_token")
                token_info.compress_type = zipfile.ZIP_DEFLATED
                token_info.external_attr = 0o600 << 16
                target.writestr(token_info, token + "\n")
        os.replace(temporary, destination_archive)
    finally:
        temporary.unlink(missing_ok=True)
    return destination_archive
