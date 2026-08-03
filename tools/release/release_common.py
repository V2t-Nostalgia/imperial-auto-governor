#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Shared source selection, package manifests, privacy scans, and verification."""

from __future__ import annotations

import hashlib
import http.server
import ipaddress
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "public_release"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
SUMS_NAME = "SHA256SUMS.txt"
HOST_BRIDGE_DOWNLOAD_NAME = "IAGHostBridge-windows-x64.zip"

ROOT_FILES = {
    ".gitignore",
    ".zenodo.json",
    "AUTHORS.md",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "DCO",
    "LICENSE",
    "NOTICE",
    "ORIGIN.md",
    "README.md",
    "REUSE.toml",
    "SECURITY.md",
    "TRADEMARKS.md",
    "descriptor.mod",
}
ROOT_DIRECTORIES = {".github", "common", "events", "localisation", "LICENSES"}
PUBLIC_DOCS = {
    "DEMO_VIDEO_PLAN.md",
    "PUBLICATION.md",
    "RESEARCH_SERVICES.md",
    "TECHNICAL_OVERVIEW.md",
    "UBUNTU_AGENT_README.md",
    "WINDOWS_AGENT_README.md",
    "WINDOWS_FULL_DEPLOYMENT.md",
}
HOST_SOURCE_FILES = {
    "Build-IAGHostBridge.ps1",
    "IAGSaveUploaderGUI.spec",
    "README.md",
    "iag_host_interceptor.py",
    "iag_save_uploader.example.json",
    "iag_save_uploader.py",
    "iag_save_uploader_gui.py",
    "requirements-windows.txt",
}
PACKET_SOURCE_SUFFIXES = {".md", ".ps1", ".py"}
SAVE_STATE_SUFFIXES = {".ps1", ".py"}

SOURCE_EXCLUDED_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".pyi_tmp",
    ".ruff_cache",
    ".test_tmp",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "calibration",
    "captures",
    "conversation",
    "databases",
    "dist",
    "logs",
    "operator",
    "public_release",
    "release",
    "runs",
    "saves",
    "secrets",
    "state",
    "test_saves",
    "runtime_test_data",
    "vendor",
    "vendor_runtime",
    "venv",
    "windows_build",
    "windows_dist",
}
SOURCE_EXCLUDED_PREFIXES = (
    "build_",
    "dist_",
    "release_",
    "hostbridge7",
    "hostbridge8",
    "hostbridge9",
)
SOURCE_EXCLUDED_SUFFIXES = {
    ".env",
    ".7z",
    ".dll",
    ".etl",
    ".exe",
    ".gz",
    ".jsonl",
    ".key",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pyd",
    ".pyc",
    ".sav",
    ".sqlite",
    ".sqlite3",
    ".sys",
    ".tar",
    ".whl",
    ".zip",
}

FORBIDDEN_RUNTIME_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "captures",
    "databases",
    "dist",
    "logs",
    "runs",
    "saves",
    "secrets",
    "state",
    "venv",
}
FORBIDDEN_RUNTIME_SUFFIXES = {
    ".env",
    ".7z",
    ".db",
    ".etl",
    ".jsonl",
    ".key",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pyc",
    ".sav",
    ".sqlite",
    ".sqlite3",
    ".tar",
}
FORBIDDEN_CONFIG_NAMES = {
    ".env",
    "agent_config.json",
    "iag_save_uploader.json",
}

TEXT_SUFFIXES = {
    ".cff",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mod",
    ".ps1",
    ".py",
    ".sh",
    ".spec",
    ".txt",
    ".yml",
    ".yaml",
}
EMAIL_RE = re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
IPV4_RE = re.compile(rb"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
WINDOWS_USER_PATH_RE = re.compile(
    rb"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?P<user>[^\\/\s<>:\"|?*]+)"
)
LINUX_USER_PATH_RE = re.compile(rb"/home/(?P<user>[A-Za-z0-9._-]+)(?:/|\b)")
THIRD_PARTY_CI_USERS = {b"runneradmin"}
CONTAINER_COMPOSE_PATHS = {
    "research_services/compose.yaml",
    "tools/research_services/compose.yaml",
}
CRAWL4AI_CONTAINER_HOME_PREFIXES = (
    b"/home/" + b"appuser/.crawl4ai",
    b"/home/" + b"appuser/.config",
    b"/home/" + b"appuser/.gunicorn",
)
CONTAINER_PATH_BOUNDARIES = (
    b"",
    b"/",
    b":",
    b" ",
    b"\t",
    b"\r",
    b"\n",
    b'"',
    b"'",
)
TOKEN_PATTERNS = {
    "OpenAI-style token": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"
    ),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN RSA " + b"PRIVATE KEY-----",
    b"-----BEGIN EC " + b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
)
_PRIVATE_NETWORK_SPECS = (
    (10, 0, 0, 0, 8),
    (172, 16, 0, 0, 12),
    (192, 168, 0, 0, 16),
    (100, 64, 0, 0, 10),
)
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(".".join(str(part) for part in spec[:4]) + f"/{spec[4]}")
    for spec in _PRIVATE_NETWORK_SPECS
)
KNOWN_PUBLIC_ENDPOINT = (45, 121, 184, 23)


class ReleaseValidationError(RuntimeError):
    """Raised when an artifact cannot be represented as a verified release."""


def release_version() -> str:
    text = (ROOT / "descriptor.mod").read_text(encoding="utf-8-sig")
    match = re.search(r'^version="([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ReleaseValidationError("descriptor.mod has no version.")
    return match.group(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_excluded(relative: Path) -> bool:
    lowered = [part.casefold() for part in relative.parts]
    if any(part in SOURCE_EXCLUDED_PARTS for part in lowered):
        return True
    if any(
        any(part.startswith(prefix) for prefix in SOURCE_EXCLUDED_PREFIXES)
        for part in lowered[:-1]
    ):
        return True
    return relative.suffix.casefold() in SOURCE_EXCLUDED_SUFFIXES


def iter_public_source_files() -> Iterable[Path]:
    """Yield the audited public source boundary without staging-time redaction."""
    yielded: set[Path] = set()

    def emit(path: Path) -> Iterable[Path]:
        resolved = path.resolve()
        if path.is_file() and resolved not in yielded:
            yielded.add(resolved)
            yield path

    for name in sorted(ROOT_FILES):
        yield from emit(ROOT / name)
    for name in sorted(ROOT_DIRECTORIES):
        directory = ROOT / name
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not source_excluded(path.relative_to(ROOT)):
                    yield from emit(path)
    for name in sorted(PUBLIC_DOCS):
        yield from emit(ROOT / "docs" / name)

    agent = ROOT / "tools" / "agent_runtime"
    for path in sorted(agent.rglob("*")):
        if path.is_file() and not source_excluded(path.relative_to(ROOT)):
            yield from emit(path)

    save_state = ROOT / "tools" / "save_state"
    for path in sorted(save_state.iterdir() if save_state.is_dir() else ()):
        if path.is_file() and path.suffix.casefold() in SAVE_STATE_SUFFIXES:
            yield from emit(path)

    host = ROOT / "tools" / "windows_save_uploader"
    for name in sorted(HOST_SOURCE_FILES):
        yield from emit(host / name)
    host_tests = host / "tests"
    for path in sorted(host_tests.rglob("*.py") if host_tests.is_dir() else ()):
        if not source_excluded(path.relative_to(ROOT)):
            yield from emit(path)

    packet = ROOT / "tools" / "packet_interceptor"
    for path in sorted(packet.iterdir() if packet.is_dir() else ()):
        if path.is_file() and path.suffix.casefold() in PACKET_SOURCE_SUFFIXES:
            yield from emit(path)

    research_services = ROOT / "tools" / "research_services"
    for path in sorted(
        research_services.rglob("*") if research_services.is_dir() else ()
    ):
        if path.is_file() and not source_excluded(path.relative_to(ROOT)):
            yield from emit(path)

    release_tools = ROOT / "tools" / "release"
    for path in sorted(release_tools.glob("*.py")):
        yield from emit(path)


def public_source_digest(files: Iterable[Path] | None = None) -> str:
    digest = hashlib.sha256()
    selected = list(files if files is not None else iter_public_source_files())
    for path in sorted(selected, key=lambda item: item.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def detect_git_commit() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    candidate = result.stdout.strip().lower()
    return candidate if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", candidate) else None


def source_identity(explicit_commit: str | None = None) -> dict[str, Any]:
    commit = explicit_commit.strip().lower() if explicit_commit else detect_git_commit()
    if commit and not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ReleaseValidationError("The source commit must be a 40-64 character hexadecimal ID.")
    return {
        "git_commit": commit,
        "source_state": "committed" if commit else "uncommitted-review-build",
        "public_source_sha256": public_source_digest(),
    }


def safe_recreate(stage: Path, output_root: Path, expected_name: str) -> None:
    resolved = stage.resolve()
    parent = output_root.resolve()
    if resolved.parent != parent or resolved.name != expected_name:
        raise ReleaseValidationError(f"Refusing to replace unsafe staging path: {resolved}")
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise ReleaseValidationError(f"Unsafe staging object: {resolved}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def copy_governance(stage: Path) -> None:
    for name in (
        "AUTHORS.md",
        "CITATION.cff",
        "LICENSE",
        "NOTICE",
        "ORIGIN.md",
        "TRADEMARKS.md",
    ):
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / "LICENSES", stage / "LICENSES")


def _relative_files(stage: Path) -> list[tuple[Path, Path]]:
    return [
        (path, path.relative_to(stage))
        for path in sorted(stage.rglob("*"))
        if path.is_file()
    ]


def _authored_text(relative: Path, package_kind: str) -> bool:
    if relative.name in {"LICENSE", "DCO"} or relative.parts[:1] == ("LICENSES",):
        return False
    if package_kind.startswith("windows-") and relative.parts[:1] == ("_internal",):
        return False
    return relative.suffix.casefold() in TEXT_SUFFIXES or relative.name in {
        "NOTICE",
        "README",
    }


def _third_party_internal(relative: Path, package_kind: str) -> bool:
    return package_kind.startswith("windows-") and relative.parts[:1] == ("_internal",)


def _allowed_embedded_host_bridge(relative: Path, package_kind: str) -> bool:
    return package_kind == "ubuntu-agent" and relative.as_posix() == (
        f"agent_runtime/web/downloads/{HOST_BRIDGE_DOWNLOAD_NAME}"
    )


def _scan_ipv4(data: bytes, relative: Path, errors: list[str]) -> None:
    for match in IPV4_RE.finditer(data):
        value = match.group().decode("ascii")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version != 4:
            continue
        octets = tuple(int(part) for part in value.split("."))
        if any(address in network for network in PRIVATE_NETWORKS):
            errors.append(f"private or carrier network address {value}: {relative.as_posix()}")
        elif octets == KNOWN_PUBLIC_ENDPOINT:
            errors.append(f"known deployment endpoint {value}: {relative.as_posix()}")


def scan_tree(stage: Path, package_kind: str) -> list[str]:
    """Scan the exact staged payload; this function never mutates files."""
    errors: list[str] = []
    for path in sorted(stage.rglob("*")):
        relative = path.relative_to(stage)
        lowered_parts = tuple(part.casefold() for part in relative.parts)
        if path.is_symlink():
            errors.append(f"symbolic link: {relative.as_posix()}")
            continue
        if any(part in FORBIDDEN_RUNTIME_DIRECTORIES for part in lowered_parts[:-1]):
            errors.append(f"forbidden runtime directory: {relative.as_posix()}")
            continue
        if not path.is_file():
            continue
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        if name in FORBIDDEN_CONFIG_NAMES:
            errors.append(f"runtime configuration: {relative.as_posix()}")
        if suffix in FORBIDDEN_RUNTIME_SUFFIXES:
            errors.append(f"forbidden runtime artifact: {relative.as_posix()}")
        if package_kind in {"source", "ubuntu-agent"} and suffix in {
            ".dll",
            ".exe",
            ".pyd",
            ".sys",
            ".whl",
            ".zip",
        } and not _allowed_embedded_host_bridge(relative, package_kind):
            errors.append(f"binary or nested archive in source package: {relative.as_posix()}")

        data = path.read_bytes()
        lowered = data.lower()
        for marker in PRIVATE_KEY_MARKERS:
            if marker.lower() in lowered:
                errors.append(f"private key material: {relative.as_posix()}")
        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{label}: {relative.as_posix()}")
        if (b"god" + b"bless") in lowered:
            errors.append(f"known private credential: {relative.as_posix()}")
        for label, pattern in (
            ("personal Windows path", WINDOWS_USER_PATH_RE),
            ("personal Linux path", LINUX_USER_PATH_RE),
        ):
            for match in pattern.finditer(data):
                user = match.group("user").lower()
                if _third_party_internal(relative, package_kind) and user in THIRD_PARTY_CI_USERS:
                    continue
                if (
                    label == "personal Linux path"
                    and relative.as_posix() in CONTAINER_COMPOSE_PATHS
                    and user == b"appuser"
                    and any(
                        data[match.start() :].startswith(prefix)
                        and data[
                            match.start() + len(prefix) :
                            match.start() + len(prefix) + 1
                        ]
                        in CONTAINER_PATH_BOUNDARIES
                        for prefix in CRAWL4AI_CONTAINER_HOME_PREFIXES
                    )
                ):
                    continue
                errors.append(f"{label}: {relative.as_posix()}")
        _scan_ipv4(data, relative, errors)
        if _authored_text(relative, package_kind) and EMAIL_RE.search(data):
            errors.append(f"email address in authored content: {relative.as_posix()}")
    return sorted(set(errors))


def require_clean_tree(stage: Path, package_kind: str) -> None:
    errors = scan_tree(stage, package_kind)
    if errors:
        raise ReleaseValidationError(
            f"{package_kind} privacy/content verification failed:\n- " + "\n- ".join(errors)
        )


def _publisher_placeholders(stage: Path) -> list[str]:
    tokens = ("<" + "ORIGINAL_" + "CREATOR_" + "ALIAS>",)
    found: set[str] = set()
    for path, _relative in _relative_files(stage):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        for token in tokens:
            if token in text:
                found.add(token)
    return sorted(found)


def finalize_stage(
    stage: Path,
    *,
    package_kind: str,
    schema: str,
    package_name: str,
    platform: str,
    identity: dict[str, Any],
    health_checks: Sequence[str],
) -> dict[str, Any]:
    """Write a manifest only after payload scans and stage health checks passed."""
    for generated in (stage / MANIFEST_NAME, stage / SUMS_NAME):
        generated.unlink(missing_ok=True)
    require_clean_tree(stage, package_kind)
    payload = _relative_files(stage)
    files = [
        {
            "path": relative.as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path, relative in payload
    ]
    unresolved = _publisher_placeholders(stage)
    manifest = {
        "schema": schema,
        "package": package_name,
        "version": release_version(),
        "platform": platform,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": identity,
        "code_license": "GPL-3.0-only",
        "documentation_license": "CC-BY-SA-4.0",
        "privacy_scan": "passed",
        "health_checks": list(health_checks),
        "release_ready": bool(identity.get("git_commit")) and not unresolved,
        "unresolved_publisher_fields": unresolved,
        "file_count": len(files),
        "files": files,
    }
    manifest_path = stage / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    checksum_files = [*payload, (manifest_path, Path(MANIFEST_NAME))]
    sums = "".join(
        f"{sha256_file(path)}  {relative.as_posix()}\n"
        for path, relative in checksum_files
    )
    (stage / SUMS_NAME).write_text(sums, encoding="utf-8", newline="\n")
    verify_stage(stage, package_kind)
    return manifest


def verify_stage(stage: Path, package_kind: str) -> dict[str, Any]:
    manifest_path = stage / MANIFEST_NAME
    sums_path = stage / SUMS_NAME
    if not manifest_path.is_file() or not sums_path.is_file():
        raise ReleaseValidationError(f"Missing manifest or checksum file in {stage}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("privacy_scan") != "passed":
        raise ReleaseValidationError("Manifest does not record a passed privacy scan.")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ReleaseValidationError("Manifest files is not a list.")
    expected: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ReleaseValidationError("Malformed manifest file entry.")
        name = entry["path"]
        if name in expected:
            raise ReleaseValidationError(f"Duplicate manifest path: {name}")
        expected[name] = entry
    actual = {
        relative.as_posix(): path
        for path, relative in _relative_files(stage)
        if relative.as_posix() not in {MANIFEST_NAME, SUMS_NAME}
    }
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ReleaseValidationError(f"Manifest mismatch; missing={missing}, extra={extra}")
    for name, path in actual.items():
        entry = expected[name]
        if entry.get("size") != path.stat().st_size or entry.get("sha256") != sha256_file(path):
            raise ReleaseValidationError(f"Manifest hash or size mismatch: {name}")
    expected_sums = {
        **{name: entry["sha256"] for name, entry in expected.items()},
        MANIFEST_NAME: sha256_file(manifest_path),
    }
    parsed_sums: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match.group(2) in parsed_sums:
            raise ReleaseValidationError(f"Malformed checksum line: {line!r}")
        parsed_sums[match.group(2)] = match.group(1)
    if parsed_sums != expected_sums:
        raise ReleaseValidationError("SHA256SUMS.txt does not match the manifest payload.")
    require_clean_tree(stage, package_kind)
    return manifest


def _safe_archive_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise ReleaseValidationError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ReleaseValidationError(f"Unsafe archive path: {name!r}")
    return path


def create_zip(stage: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path, relative in _relative_files(stage):
            archive.write(path, (Path(stage.name) / relative).as_posix())


def create_tar_gz(stage: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    with tarfile.open(destination, "w:gz", compresslevel=9) as archive:
        for path, relative in _relative_files(stage):
            info = archive.gettarinfo(
                str(path),
                arcname=(Path(stage.name) / relative).as_posix(),
            )
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mode = 0o755 if path.suffix == ".sh" else 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)


def _inspect_zip(path: Path, expected_root: str) -> None:
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member = _safe_archive_name(info.filename)
            if member.parts[0] != expected_root:
                raise ReleaseValidationError(f"Unexpected ZIP root: {info.filename}")
            if info.filename in seen:
                raise ReleaseValidationError(f"Duplicate ZIP member: {info.filename}")
            seen.add(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ReleaseValidationError(f"ZIP symbolic link: {info.filename}")
        bad = archive.testzip()
        if bad:
            raise ReleaseValidationError(f"Corrupt ZIP member: {bad}")


def _inspect_tar(path: Path, expected_root: str) -> None:
    seen: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        for info in archive.getmembers():
            member = _safe_archive_name(info.name)
            if member.parts[0] != expected_root:
                raise ReleaseValidationError(f"Unexpected tar root: {info.name}")
            if info.name in seen:
                raise ReleaseValidationError(f"Duplicate tar member: {info.name}")
            seen.add(info.name)
            if info.issym() or info.islnk() or info.isdev():
                raise ReleaseValidationError(f"Unsafe tar member: {info.name}")


def extract_verified_archive(path: Path, destination: Path, expected_root: str) -> Path:
    if path.suffix.casefold() == ".zip":
        _inspect_zip(path, expected_root)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(destination)
    elif path.name.casefold().endswith(".tar.gz"):
        _inspect_tar(path, expected_root)
        with tarfile.open(path, "r:gz") as archive:
            archive.extractall(destination, filter="data")
    else:
        raise ReleaseValidationError(f"Unsupported archive: {path}")
    root = destination / expected_root
    top_level = sorted(item.name for item in destination.iterdir())
    if top_level != [expected_root] or not root.is_dir():
        raise ReleaseValidationError(f"Unexpected extracted layout: {top_level}")
    return root


def verify_archive(path: Path, *, expected_root: str, package_kind: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="iag-release-verify-") as temporary:
        root = extract_verified_archive(path, Path(temporary), expected_root)
        return verify_stage(root, package_kind)


def verify_host_bridge_download(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReleaseValidationError(f"Host Bridge archive is missing: {path}")
    return verify_archive(
        path,
        expected_root=f"IAGHostBridge-{release_version()}-windows-x64",
        package_kind="windows-host",
    )


def copy_host_bridge_download(source: Path, web_root: Path) -> Path:
    source = source.resolve()
    verify_host_bridge_download(source)
    downloads = web_root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target = downloads / HOST_BRIDGE_DOWNLOAD_NAME
    shutil.copy2(source, target)
    return target


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy() if env is None else env.copy()
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout + "\n" + result.stderr).strip()
        raise ReleaseValidationError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{output[-12000:]}"
        )
    return result


def run_python_compile(root: Path) -> None:
    script = (
        "from pathlib import Path\n"
        "for path in sorted(Path('.').rglob('*.py')):\n"
        "    compile(path.read_bytes(), str(path), 'exec')\n"
    )
    run_command([sys.executable, "-c", script], cwd=root, timeout=300)


def run_unittest(root: Path, start: str, pattern: str = "test*.py") -> int:
    result = run_command(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            start,
            "-p",
            pattern,
            "-v",
        ],
        cwd=root,
        timeout=600,
    )
    output = result.stdout + "\n" + result.stderr
    matches = re.findall(r"Ran (\d+) tests?", output)
    return int(matches[-1]) if matches else 0


def run_windows_agent_health(stage: Path) -> None:
    executable = stage / "IAGWindowsAgent.exe"
    if not executable.is_file():
        raise ReleaseValidationError("IAGWindowsAgent.exe is missing.")
    research_root = stage / "research_services"
    required_research_files = (
        research_root / "compose.yaml",
        research_root / "Manage-IAGResearchServices.ps1",
        research_root / "searxng" / "settings.yml",
    )
    missing_research = [
        str(path.relative_to(stage))
        for path in required_research_files
        if not path.is_file()
    ]
    if missing_research:
        raise ReleaseValidationError(
            "Windows Agent research service payload is incomplete: "
            + ", ".join(missing_research)
        )
    powershell = shutil.which("powershell")
    if not powershell:
        raise ReleaseValidationError(
            "Windows PowerShell is required for the Windows Agent health check."
        )
    manager = required_research_files[1]
    quoted_manager = str(manager).replace("'", "''")
    parser_check = (
        "$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{quoted_manager}',[ref]$null,[ref]$errors)|Out-Null;"
        "if($errors.Count){$errors|ForEach-Object{Write-Error $_};exit 1}"
    )
    run_command(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", parser_check],
        cwd=stage,
        timeout=30,
    )
    verify_host_bridge_download(
        stage / "_internal" / "web" / "downloads" / HOST_BRIDGE_DOWNLOAD_NAME
    )
    run_command([str(executable), "--health-check"], cwd=stage, timeout=90)


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    expected_token = ""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        authorized = self.headers.get("Authorization") == f"Bearer {self.expected_token}"
        valid = self.path == "/api/save/client-heartbeat" and authorized and isinstance(payload, dict)
        response = json.dumps(
            {"ok": bool(valid), "status": "ok" if valid else "rejected"}
        ).encode("utf-8")
        self.send_response(200 if valid else 403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_host_bridge_https_health(stage: Path) -> None:
    executable = stage / "IAGHostBridgeGUI.exe"
    if not executable.is_file():
        raise ReleaseValidationError("IAGHostBridgeGUI.exe is missing.")
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as error:
        raise ReleaseValidationError("cryptography is required for the HTTPS health check.") from error

    token = "release-health-token"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    fingerprint = hashlib.sha256(
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()

    with tempfile.TemporaryDirectory(prefix="iag-host-health-") as temporary:
        root = Path(temporary)
        cert_path = root / "health.crt"
        key_path = root / "health.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        handler = type("HealthHandler", (_HealthHandler,), {"expected_token": token})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            run_command(
                [
                    str(executable),
                    "--health-check",
                    "--server-url",
                    f"https://127.0.0.1:{port}",
                    "--certificate-sha256",
                    fingerprint,
                    "--token",
                    token,
                ],
                cwd=stage,
                timeout=90,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def remove_generated_metadata(stage: Path) -> None:
    (stage / MANIFEST_NAME).unlink(missing_ok=True)
    (stage / SUMS_NAME).unlink(missing_ok=True)
