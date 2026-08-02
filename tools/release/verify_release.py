#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Independently verify IAG release archives after extraction."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from build_public_release import (
    run_source_health,
    validate_markdown_links,
    validate_required_layout,
)
from package_ubuntu_agent import run_ubuntu_health
from release_common import (
    DEFAULT_OUTPUT,
    ReleaseValidationError,
    extract_verified_archive,
    release_version,
    run_host_bridge_https_health,
    run_windows_agent_health,
    sha256_file,
    verify_archive,
    verify_stage,
)


def artifact_metadata(path: Path) -> tuple[str, str]:
    version = re.escape(release_version())
    patterns = (
        (rf"ImperialAutoGovernor-{version}-source\.zip", f"ImperialAutoGovernor-{release_version()}", "source"),
        (rf"IAGWindowsAgent-{version}-windows-x64\.zip", f"IAGWindowsAgent-{release_version()}-windows-x64", "windows-agent"),
        (rf"IAGHostBridge-{version}-windows-x64\.zip", f"IAGHostBridge-{release_version()}-windows-x64", "windows-host"),
        (rf"IAGUbuntuAgent-{version}-linux-x86_64\.tar\.gz", f"IAGUbuntuAgent-{release_version()}", "ubuntu-agent"),
    )
    for pattern, root, kind in patterns:
        if re.fullmatch(pattern, path.name):
            return root, kind
    raise ReleaseValidationError(f"Unexpected release attachment name: {path.name}")


def verify_artifact(path: Path, *, run_health: bool = True) -> dict[str, Any]:
    expected_root, kind = artifact_metadata(path)
    manifest = verify_archive(path, expected_root=expected_root, package_kind=kind)
    if not run_health:
        return manifest
    with tempfile.TemporaryDirectory(prefix="iag-final-verify-") as temporary:
        root = extract_verified_archive(path, Path(temporary), expected_root)
        verify_stage(root, kind)
        if kind == "source":
            validate_required_layout(root)
            validate_markdown_links(root)
            run_source_health(root)
        elif kind == "ubuntu-agent":
            run_ubuntu_health(root)
        elif kind == "windows-agent":
            run_windows_agent_health(root)
        elif kind == "windows-host":
            run_host_bridge_https_health(root)
    return manifest


def expected_attachments(directory: Path) -> list[Path]:
    version = release_version()
    return [
        directory / f"ImperialAutoGovernor-{version}-source.zip",
        directory / f"IAGWindowsAgent-{version}-windows-x64.zip",
        directory / f"IAGHostBridge-{version}-windows-x64.zip",
        directory / f"IAGUbuntuAgent-{version}-linux-x86_64.tar.gz",
    ]


def verify_top_level_sums(directory: Path) -> None:
    version = release_version()
    sums_path = directory / f"SHA256SUMS-{version}.txt"
    if not sums_path.is_file():
        raise ReleaseValidationError(f"Missing top-level checksum file: {sums_path}")
    parsed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if not match or match.group(2) in parsed:
            raise ReleaseValidationError(f"Malformed top-level checksum line: {line!r}")
        parsed[match.group(2)] = match.group(1)
    attachments = expected_attachments(directory)
    expected = {path.name: sha256_file(path) for path in attachments if path.is_file()}
    if parsed != expected or len(expected) != len(attachments):
        raise ReleaseValidationError("Top-level SHA256SUMS does not match all four archives.")


def verify_directory(directory: Path, *, run_health: bool = True) -> list[dict[str, Any]]:
    directory = directory.resolve()
    expected = expected_attachments(directory)
    sums = directory / f"SHA256SUMS-{release_version()}.txt"
    actual = sorted(path.name for path in directory.iterdir() if path.is_file())
    expected_names = sorted([*(path.name for path in expected), sums.name])
    if actual != expected_names:
        raise ReleaseValidationError(
            f"Final release directory mismatch; expected={expected_names}, actual={actual}"
        )
    manifests = [verify_artifact(path, run_health=run_health) for path in expected]
    verify_top_level_sums(directory)
    return manifests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--no-health", action="store_true")
    args = parser.parse_args()
    try:
        if args.directory:
            manifests = verify_directory(args.directory, run_health=not args.no_health)
        else:
            paths = args.paths or expected_attachments(DEFAULT_OUTPUT)
            manifests = [
                verify_artifact(path.resolve(), run_health=not args.no_health)
                for path in paths
            ]
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            [
                {
                    "package": item["package"],
                    "privacy_scan": item["privacy_scan"],
                    "release_ready": item["release_ready"],
                }
                for item in manifests
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
