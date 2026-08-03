#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Create and verify the Windows Host Bridge archive."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from release_common import (
    DEFAULT_OUTPUT,
    ROOT,
    ReleaseValidationError,
    copy_governance,
    create_zip,
    extract_verified_archive,
    finalize_stage,
    release_version,
    remove_generated_metadata,
    run_host_bridge_https_health,
    safe_recreate,
    sha256_file,
    source_identity,
    verify_archive,
    verify_stage,
)


DEFAULT_DIST = (
    ROOT / "tools" / "windows_save_uploader" / "windows_dist" / "IAGHostBridgeGUI"
)


def copy_payload(stage: Path, dist: Path) -> None:
    if not (dist / "IAGHostBridgeGUI.exe").is_file() or not (dist / "_internal").is_dir():
        raise ReleaseValidationError(f"A complete fresh Host Bridge build was not found at {dist}")
    shutil.copytree(dist, stage, dirs_exist_ok=True)
    host_root = ROOT / "tools" / "windows_save_uploader"
    shutil.copy2(host_root / "README.md", stage / "README.md")
    shutil.copy2(
        host_root / "iag_save_uploader.example.json",
        stage / "iag_save_uploader.example.json",
    )
    copy_governance(stage)


def build(
    dist: Path,
    output_root: Path,
    *,
    explicit_commit: str | None = None,
) -> tuple[Path, Path, dict]:
    version = release_version()
    dist = dist.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stage_name = f"IAGHostBridge-{version}-windows-x64"
    stage = output_root / stage_name
    destination = output_root / f"{stage_name}.zip"
    safe_recreate(stage, output_root, stage_name)
    destination.unlink(missing_ok=True)
    identity = source_identity(explicit_commit)
    try:
        copy_payload(stage, dist)
        run_host_bridge_https_health(stage)
        manifest = finalize_stage(
            stage,
            package_kind="windows-host",
            schema="iag.windows_host_bridge_release.v2",
            package_name="IAG Windows Host Bridge",
            platform="windows-x64",
            identity=identity,
            health_checks=[
                "packaged psutil and WinDivert relay-route filter health check: passed",
                "certificate-pinned HTTPS console health check: passed",
            ],
        )
        create_zip(stage, destination)
        verify_archive(
            destination,
            expected_root=stage_name,
            package_kind="windows-host",
        )
        with tempfile.TemporaryDirectory(prefix="iag-host-bridge-health-") as temporary:
            extracted = extract_verified_archive(destination, Path(temporary), stage_name)
            verify_stage(extracted, "windows-host")
            run_host_bridge_https_health(extracted)
        return stage, destination, manifest
    except Exception:
        destination.unlink(missing_ok=True)
        remove_generated_metadata(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        stage, destination, _manifest = build(
            args.dist,
            args.output,
            explicit_commit=args.source_commit,
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Host Bridge package tree: {stage}")
    print(f"Host Bridge archive:      {destination}")
    print(f"Archive SHA-256:          {sha256_file(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
