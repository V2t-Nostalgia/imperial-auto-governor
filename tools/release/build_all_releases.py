#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Build all four artifacts from one source state and leave five final files."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import build_public_release
import package_ubuntu_agent
import package_windows_agent
import package_windows_host_bridge
from release_common import (
    DEFAULT_OUTPUT,
    ROOT,
    ReleaseValidationError,
    release_version,
    sha256_file,
)
from verify_release import verify_directory


def clean_output(output_root: Path) -> None:
    resolved = output_root.resolve()
    expected = (ROOT / "public_release").resolve()
    if resolved != expected:
        raise ReleaseValidationError(
            f"The all-release builder only cleans the dedicated output directory: {expected}"
        )
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise ReleaseValidationError(f"Unsafe release output object: {resolved}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    obsolete_bundle = (ROOT / "public_release.zip").resolve()
    if obsolete_bundle.parent != ROOT.resolve() or obsolete_bundle.name != "public_release.zip":
        raise ReleaseValidationError("Refusing unsafe obsolete bundle cleanup.")
    obsolete_bundle.unlink(missing_ok=True)


def build_windows_binaries() -> None:
    commands = (
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "tools" / "agent_runtime" / "Build-IAGWindowsAgent.ps1"),
            "-Python",
            sys.executable,
        ],
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "tools" / "windows_save_uploader" / "Build-IAGHostBridge.ps1"),
            "-Python",
            sys.executable,
        ],
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            raise ReleaseValidationError(
                f"Fresh Windows binary build failed ({result.returncode}): {command[-3]}"
            )


def write_top_level_sums(output_root: Path, archives: list[Path]) -> Path:
    path = output_root / f"SHA256SUMS-{release_version()}.txt"
    path.write_text(
        "".join(f"{sha256_file(item)}  {item.name}\n" for item in archives),
        encoding="utf-8",
        newline="\n",
    )
    return path


def remove_stages(output_root: Path, stages: list[Path]) -> None:
    parent = output_root.resolve()
    for stage in stages:
        resolved = stage.resolve()
        if resolved.parent != parent or not resolved.is_dir() or resolved.is_symlink():
            raise ReleaseValidationError(f"Refusing unsafe staging cleanup: {resolved}")
        shutil.rmtree(resolved)


def build_all(output_root: Path, source_commit: str | None) -> list[Path]:
    clean_output(output_root)
    stages: list[Path] = []
    archives: list[Path] = []
    try:
        source_stage, source_archive, _ = build_public_release.build(
            output_root,
            explicit_commit=source_commit,
        )
        stages.append(source_stage)
        archives.append(source_archive)

        ubuntu_stage, ubuntu_archive, _ = package_ubuntu_agent.build(
            output_root,
            explicit_commit=source_commit,
        )
        stages.append(ubuntu_stage)

        build_windows_binaries()
        windows_stage, windows_archive, _ = package_windows_agent.build(
            package_windows_agent.DEFAULT_DIST,
            output_root,
            explicit_commit=source_commit,
        )
        stages.append(windows_stage)
        host_stage, host_archive, _ = package_windows_host_bridge.build(
            package_windows_host_bridge.DEFAULT_DIST,
            output_root,
            explicit_commit=source_commit,
        )
        stages.append(host_stage)

        archives.extend([windows_archive, host_archive, ubuntu_archive])
        write_top_level_sums(output_root, archives)
        remove_stages(output_root, stages)
        verify_directory(output_root, run_health=True)
        return [*archives, output_root / f"SHA256SUMS-{release_version()}.txt"]
    except Exception:
        for path in output_root.iterdir():
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        attachments = build_all(args.output.resolve(), args.source_commit)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    for path in attachments:
        print(f"{path.name}\t{path.stat().st_size}\t{sha256_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
