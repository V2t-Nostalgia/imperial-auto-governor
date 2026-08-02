#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Build and verify the complete public source archive."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from release_common import (
    DEFAULT_OUTPUT,
    ROOT,
    ReleaseValidationError,
    create_zip,
    finalize_stage,
    iter_public_source_files,
    release_version,
    remove_generated_metadata,
    run_python_compile,
    run_unittest,
    safe_recreate,
    sha256_file,
    source_identity,
    verify_archive,
)


def license_for(relative: Path) -> str:
    if relative.suffix.casefold() == ".md":
        if relative.parts[:3] == ("tools", "agent_runtime", "strategy"):
            return "GPL-3.0-only"
        return "CC-BY-SA-4.0"
    return "GPL-3.0-only"


def release_copyright_text() -> str:
    """Build staged SPDX ownership from the publisher-controlled NOTICE file."""
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8-sig")
    match = re.search(r"^Copyright \(C\) 2026 (.+?)\s*$", notice, re.MULTILINE)
    if not match:
        raise ReleaseValidationError(
            "NOTICE must contain 'Copyright (C) 2026 <creator alias>'."
        )
    creator = match.group(1).strip()
    if not creator:
        raise ReleaseValidationError("NOTICE contains an empty creator alias.")
    return (
        f"SPDX-FileCopyrightText: 2026 {creator} "
        "and Imperial Auto Governor contributors"
    )


def header_for(relative: Path, license_id: str) -> str | None:
    if relative.parts[:1] == ("LICENSES",):
        return None
    copyright_text = release_copyright_text()
    license_text = f"SPDX-License-Identifier: {license_id}"
    suffix = relative.suffix.casefold()
    if suffix in {".py", ".ps1", ".sh", ".spec", ".txt", ".yml", ".yaml"}:
        return f"# {copyright_text}\n# {license_text}\n"
    if suffix == ".js":
        return f"// {copyright_text}\n// {license_text}\n"
    if suffix == ".css":
        return f"/* {copyright_text}\n * {license_text} */\n"
    if suffix in {".html", ".md"}:
        return f"<!-- {copyright_text} -->\n<!-- {license_text} -->\n"
    return None


def copy_with_release_header(source: Path, target: Path, relative: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    header = header_for(relative, license_for(relative))
    if not header:
        return
    try:
        text = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return
    if "SPDX-License-Identifier:" in text[:800]:
        return
    if text.startswith("#!"):
        first, separator, remainder = text.partition("\n")
        text = first + separator + header + remainder
    else:
        text = header + text
    target.write_text(text, encoding="utf-8", newline="\n")


def validate_required_layout(stage: Path) -> None:
    required = (
        "AUTHORS.md",
        "CITATION.cff",
        "DCO",
        "LICENSE",
        "LICENSES/GPL-3.0-only.txt",
        "LICENSES/CC-BY-SA-4.0.txt",
        "NOTICE",
        "ORIGIN.md",
        "TRADEMARKS.md",
        "docs/UBUNTU_AGENT_README.md",
        "tools/agent_runtime/extract_game_state.py",
        "tools/agent_runtime/install_ubuntu.sh",
        "tools/release/package_ubuntu_agent.py",
        "tools/release/verify_release.py",
        "tools/save_state/extract_planet_profiles.py",
        "tools/save_state/test_extract_planet_profiles.py",
    )
    missing = [name for name in required if not (stage / name).is_file()]
    if missing:
        raise ReleaseValidationError(f"Public source layout is incomplete: {missing}")


def validate_markdown_links(stage: Path) -> None:
    errors: list[str] = []
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in sorted(stage.rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig")
        for raw in link_re.findall(text):
            value = raw.strip().strip("<>").split("#", 1)[0]
            if not value or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
                continue
            candidate = (path.parent / value).resolve()
            try:
                candidate.relative_to(stage.resolve())
            except ValueError:
                errors.append(f"link escapes package: {path.relative_to(stage)} -> {raw}")
                continue
            if not candidate.exists():
                errors.append(f"missing link: {path.relative_to(stage)} -> {raw}")
    if errors:
        raise ReleaseValidationError("Broken public documentation links:\n- " + "\n- ".join(errors))


def run_source_health(stage: Path) -> list[str]:
    run_python_compile(stage)
    results = {
        "agent_runtime": run_unittest(stage, "tools/agent_runtime/tests", "test_*.py"),
        "packet_interceptor": run_unittest(stage, "tools/packet_interceptor", "test_*.py"),
        "windows_host_uploader": run_unittest(
            stage, "tools/windows_save_uploader/tests", "test_*.py"
        ),
        "save_state": run_unittest(stage, "tools/save_state", "test_*.py"),
    }
    return [
        "python_compile: passed",
        *(f"{name}: passed ({count} tests)" for name, count in results.items()),
    ]


def build(
    output_root: Path,
    *,
    explicit_commit: str | None = None,
) -> tuple[Path, Path, dict]:
    version = release_version()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stage_name = f"ImperialAutoGovernor-{version}"
    stage = output_root / stage_name
    destination = output_root / f"ImperialAutoGovernor-{version}-source.zip"
    safe_recreate(stage, output_root, stage_name)
    destination.unlink(missing_ok=True)
    identity = source_identity(explicit_commit)
    try:
        for source in iter_public_source_files():
            relative = source.relative_to(ROOT)
            copy_with_release_header(source, stage / relative, relative)
        validate_required_layout(stage)
        validate_markdown_links(stage)
        checks = run_source_health(stage)
        manifest = finalize_stage(
            stage,
            package_kind="source",
            schema="iag.public_source_release.v2",
            package_name="Imperial Auto Governor public source",
            platform="source",
            identity=identity,
            health_checks=checks,
        )
        create_zip(stage, destination)
        verify_archive(destination, expected_root=stage_name, package_kind="source")
        return stage, destination, manifest
    except Exception:
        destination.unlink(missing_ok=True)
        remove_generated_metadata(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        stage, destination, _manifest = build(
            args.output,
            explicit_commit=args.source_commit,
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Public source tree: {stage}")
    print(f"Source archive:     {destination}")
    print(f"Archive SHA-256:    {sha256_file(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
