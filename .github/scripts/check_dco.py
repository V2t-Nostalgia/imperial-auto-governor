#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Fail when a commit message read from stdin lacks a DCO sign-off."""

from __future__ import annotations

import re
import sys


SIGN_OFF = re.compile(r"(?im)^Signed-off-by:\s+.+\s+<[^<>\s]+@[^<>\s]+>\s*$")


def main() -> int:
    failed: list[str] = []
    for record in sys.stdin.read().split("\0"):
        record = record.strip()
        if not record:
            continue
        commit, _, message = record.partition("\n")
        if not SIGN_OFF.search(message):
            failed.append(commit)
    if failed:
        print("Missing valid Signed-off-by line:", *failed, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
