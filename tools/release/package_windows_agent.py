#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Create and verify the standalone Windows agent archive."""

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
    run_windows_agent_health,
    safe_recreate,
    sha256_file,
    source_identity,
    verify_archive,
    verify_stage,
)


DEFAULT_DIST = ROOT / "tools" / "agent_runtime" / "windows_dist" / "IAGWindowsAgent"


def copy_payload(stage: Path, dist: Path) -> None:
    if not (dist / "IAGWindowsAgent.exe").is_file() or not (dist / "_internal").is_dir():
        raise ReleaseValidationError(f"A complete fresh PyInstaller build was not found at {dist}")
    shutil.copytree(dist, stage, dirs_exist_ok=True)
    for metadata in stage.rglob("DELVEWHEEL"):
        metadata.unlink()
    shutil.copy2(ROOT / "docs" / "WINDOWS_AGENT_README.md", stage / "README.md")
    docs = stage / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docs" / "RESEARCH_SERVICES.md", docs / "RESEARCH_SERVICES.md")
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
    stage_name = f"IAGWindowsAgent-{version}-windows-x64"
    stage = output_root / stage_name
    destination = output_root / f"{stage_name}.zip"
    safe_recreate(stage, output_root, stage_name)
    destination.unlink(missing_ok=True)
    identity = source_identity(explicit_commit)
    try:
        copy_payload(stage, dist)
        run_windows_agent_health(stage)
        manifest = finalize_stage(
            stage,
            package_kind="windows-agent",
            schema="iag.windows_agent_release.v2",
            package_name="IAG Windows Agent",
            platform="windows-x64",
            identity=identity,
            health_checks=["bundled resource and import health check: passed"],
        )
        create_zip(stage, destination)
        verify_archive(
            destination,
            expected_root=stage_name,
            package_kind="windows-agent",
        )
        with tempfile.TemporaryDirectory(prefix="iag-windows-agent-health-") as temporary:
            extracted = extract_verified_archive(destination, Path(temporary), stage_name)
            verify_stage(extracted, "windows-agent")
            run_windows_agent_health(extracted)
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
    print(f"Windows package tree: {stage}")
    print(f"Windows archive:      {destination}")
    print(f"Archive SHA-256:      {sha256_file(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
