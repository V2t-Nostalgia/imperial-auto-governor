#!/usr/bin/env python3
"""Run every repository-local unittest module without requiring pytest."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARTS = {
    ".git",
    ".tmp",
    ".venv",
    "build",
    "dist",
    "public_release",
    "runtime",
}


def test_modules(root: Path = ROOT) -> list[Path]:
    """Return maintained test modules in stable path order."""
    return sorted(
        path
        for path in root.rglob("test_*.py")
        if not any(part in IGNORED_PARTS for part in path.parts)
    )


def build_suite(paths: list[Path]) -> unittest.TestSuite:
    """Import path-isolated test modules and combine their suites."""
    suite = unittest.TestSuite()
    for index, path in enumerate(paths):
        module_name = f"iag_repository_test_{index}_{path.stem}"
        specification = importlib.util.spec_from_file_location(module_name, path)
        if specification is None or specification.loader is None:
            raise RuntimeError(f"Cannot load test module: {path}")
        module = importlib.util.module_from_spec(specification)
        sys.modules[module_name] = module
        specification.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    return suite


def selected_test_modules(values: list[Path] | None) -> list[Path]:
    """Resolve an explicit maintained subset, or return the complete suite."""
    if not values:
        return test_modules()
    root = ROOT.resolve()
    selected: list[Path] = []
    for value in values:
        candidate = value if value.is_absolute() else ROOT / value
        candidate = candidate.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Test path is outside the repository: {value}")
        if not candidate.is_file() or not candidate.name.startswith("test_"):
            raise ValueError(f"Not a maintained test module: {value}")
        selected.append(candidate)
    return sorted(set(selected))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        help="Run one repository-relative test module; may be repeated.",
    )
    values = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
    paths = selected_test_modules(values.path)
    suite = build_suite(paths)
    print(f"Loaded {len(paths)} modules / {suite.countTestCases()} tests")
    result = unittest.TextTestRunner(
        verbosity=1 if values.quiet else 2,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
