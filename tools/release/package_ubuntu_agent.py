#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Create the standalone Ubuntu/Linux agent source release."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from build_public_release import copy_with_release_header
from release_common import (
    DEFAULT_OUTPUT,
    HOST_BRIDGE_DOWNLOAD_NAME,
    ROOT,
    ReleaseValidationError,
    copy_host_bridge_download,
    copy_governance,
    create_tar_gz,
    extract_verified_archive,
    finalize_stage,
    iter_public_source_files,
    release_version,
    remove_generated_metadata,
    run_command,
    run_python_compile,
    run_unittest,
    safe_recreate,
    sha256_file,
    source_identity,
    verify_archive,
    verify_host_bridge_download,
    verify_stage,
)


DEFAULT_HOST_BRIDGE = (
    DEFAULT_OUTPUT / f"IAGHostBridge-{release_version()}-windows-x64.zip"
)


def copy_payload(stage: Path, host_bridge_archive: Path) -> None:
    for source in iter_public_source_files():
        relative = source.relative_to(ROOT)
        if relative.parts[:2] == ("tools", "agent_runtime"):
            target_relative = Path("agent_runtime", *relative.parts[2:])
        elif relative.parts[:2] == ("tools", "save_state"):
            target_relative = Path("save_state", *relative.parts[2:])
        elif (
            relative.parts[:2] == ("tools", "packet_interceptor")
            and source.suffix.casefold() == ".py"
            and not source.name.startswith("test_")
        ):
            target_relative = Path("packet_interceptor", *relative.parts[2:])
        else:
            continue
        copy_with_release_header(source, stage / target_relative, relative)

    packaged_readme = stage / "agent_runtime" / "README.md"
    text = packaged_readme.read_text(encoding="utf-8")
    packaged_readme.write_text(
        text.replace("../../docs/", "../docs/"),
        encoding="utf-8",
        newline="\n",
    )

    docs = stage / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for name in ("UBUNTU_AGENT_README.md", "RESEARCH_SERVICES.md"):
        shutil.copy2(ROOT / "docs" / name, docs / name)
    copy_host_bridge_download(host_bridge_archive, stage / "agent_runtime" / "web")
    copy_governance(stage)


def find_usable_bash() -> str | None:
    candidates: list[Path] = []
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidates.append(Path(git).resolve().parents[1] / "bin" / "bash.exe")
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(Path(discovered))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            result = subprocess.run(
                [str(resolved), "--version"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except OSError:
            continue
        if result.returncode == 0 and b"GNU bash" in result.stdout:
            return str(resolved)
    return None


def run_ubuntu_health(stage: Path) -> list[str]:
    required = (
        "agent_runtime/install_ubuntu.sh",
        "agent_runtime/install_systemd_service.sh",
        "agent_runtime/extract_game_state.py",
        f"agent_runtime/web/downloads/{HOST_BRIDGE_DOWNLOAD_NAME}",
        "packet_interceptor/iag_building_to_zone_replacer.py",
        "save_state/extract_planet_profiles.py",
        "docs/UBUNTU_AGENT_README.md",
    )
    missing = [name for name in required if not (stage / name).is_file()]
    if missing:
        raise ReleaseValidationError(f"Ubuntu package is incomplete: {missing}")
    verify_host_bridge_download(
        stage / "agent_runtime" / "web" / "downloads" / HOST_BRIDGE_DOWNLOAD_NAME
    )
    run_python_compile(stage)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(stage / "agent_runtime"), str(stage / "save_state")]
    )
    run_command(
        [
            sys.executable,
            "-c",
            (
                "import extract_game_state, extract_planet_profiles, planner; "
                "assert callable(extract_planet_profiles.extract_profiles)"
            ),
        ],
        cwd=stage,
        env=environment,
        timeout=120,
    )
    agent_count = run_unittest(stage, "agent_runtime/tests", "test_*.py")
    save_count = run_unittest(stage, "save_state", "test_*.py")
    bash = find_usable_bash()
    if not bash:
        raise ReleaseValidationError("bash is required to syntax-check Ubuntu installers.")
    run_command(
        [
            bash,
            "-n",
            "agent_runtime/install_ubuntu.sh",
            "agent_runtime/install_systemd_service.sh",
            "agent_runtime/start_console.sh",
        ],
        cwd=stage,
        timeout=120,
    )
    return [
        "python_compile: passed",
        "save parser import: passed",
        f"agent_runtime: passed ({agent_count} tests)",
        f"save_state: passed ({save_count} tests)",
        "embedded host bridge download: passed",
        "bash syntax: passed",
    ]


def build(
    output_root: Path,
    *,
    host_bridge_archive: Path,
    explicit_commit: str | None = None,
) -> tuple[Path, Path, dict]:
    version = release_version()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stage_name = f"IAGUbuntuAgent-{version}"
    stage = output_root / stage_name
    destination = output_root / f"IAGUbuntuAgent-{version}-linux-x86_64.tar.gz"
    safe_recreate(stage, output_root, stage_name)
    destination.unlink(missing_ok=True)
    identity = source_identity(explicit_commit)
    try:
        copy_payload(stage, host_bridge_archive)
        checks = run_ubuntu_health(stage)
        manifest = finalize_stage(
            stage,
            package_kind="ubuntu-agent",
            schema="iag.ubuntu_agent_release.v1",
            package_name="IAG Ubuntu Agent",
            platform="linux-x86_64",
            identity=identity,
            health_checks=checks,
        )
        create_tar_gz(stage, destination)
        verify_archive(
            destination,
            expected_root=stage_name,
            package_kind="ubuntu-agent",
        )
        with tempfile.TemporaryDirectory(prefix="iag-ubuntu-health-") as temporary:
            extracted = extract_verified_archive(
                destination,
                Path(temporary),
                stage_name,
            )
            verify_stage(extracted, "ubuntu-agent")
            run_ubuntu_health(extracted)
        return stage, destination, manifest
    except Exception:
        destination.unlink(missing_ok=True)
        remove_generated_metadata(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--host-bridge", type=Path, default=DEFAULT_HOST_BRIDGE)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        stage, destination, _manifest = build(
            args.output,
            host_bridge_archive=args.host_bridge,
            explicit_commit=args.source_commit,
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Ubuntu package tree: {stage}")
    print(f"Ubuntu archive:      {destination}")
    print(f"Archive SHA-256:     {sha256_file(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
