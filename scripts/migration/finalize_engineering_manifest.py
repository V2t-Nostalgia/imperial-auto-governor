#!/usr/bin/env python3
"""Finalize privacy-safe migration and engineering manifests.

`MIGRATION_MANIFEST.json` answers where each migrated file came from and what
its bytes were at copy time. `ENGINEERING_MANIFEST.json` answers what files are
present after package/import/documentation adjustments. Keeping these concerns
separate prevents normal engineering edits from looking like a failed copy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "MIGRATION_MANIFEST.json"
ENGINEERING_PATH = ROOT / "ENGINEERING_MANIFEST.json"
IGNORED_PARTS = {
    ".git",
    ".idea",
    ".tmp",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "runtime",
    "state",
    "logs",
    "captures",
    "saves",
    "databases",
    "artifacts",
    "public_release",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_migration() -> dict[str, object]:
    manifest = json.loads(MIGRATION_PATH.read_text(encoding="utf-8"))
    manifest["source_root"] = "<LEGACY_REPOSITORY>"
    manifest["target_root"] = "."
    for item in manifest.get("files", []):
        if "copied_sha256" in item:
            item["initial_copy_sha256"] = item.pop("copied_sha256")
    MIGRATION_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def maintained_files() -> list[Path]:
    values: list[Path] = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or any(part in IGNORED_PARTS for part in path.parts)
            or any(part.endswith(".egg-info") for part in path.parts)
        ):
            continue
        if path == ENGINEERING_PATH or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        values.append(path)
    return sorted(values, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> int:
    migration = normalize_migration()
    migrated = {
        str(item["target"]): item for item in migration.get("files", [])
    }
    records: list[dict[str, object]] = []
    changed_migrations = 0
    for path in maintained_files():
        relative = path.relative_to(ROOT).as_posix()
        digest = sha256(path)
        source = migrated.get(relative)
        if source is None:
            origin = "new_engineering_file"
        elif digest == source["initial_copy_sha256"]:
            origin = "migrated_unchanged"
        else:
            origin = "migrated_then_engineered"
            changed_migrations += 1
        records.append(
            {
                "path": relative,
                "sha256": digest,
                "bytes": path.stat().st_size,
                "origin": origin,
                "legacy_source": source["source"] if source else None,
            }
        )

    manifest = {
        "schema": "iag.engineering_manifest.v1",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"
        ),
        "project_root": ".",
        "migration_source_commit": migration["source_commit"],
        "policy": {
            "absolute_local_paths_recorded": False,
            "old_tests_copied": False,
            "runtime_data_copied": False,
            "self_hash_included": False,
        },
        "summary": {
            "maintained_file_count": len(records),
            "migrated_file_count": len(migrated),
            "migrated_files_engineered": changed_migrations,
        },
        "files": records,
    }
    ENGINEERING_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {ENGINEERING_PATH} with {len(records)} files; "
        f"{changed_migrations} migrated files changed during engineering."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
