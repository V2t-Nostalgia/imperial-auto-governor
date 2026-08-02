#!/usr/bin/env python3
"""Platform facade for guarded fixed-coordinate Stellaris clicks."""

from __future__ import annotations

import os


if os.name == "nt":
    from windows_fixed_click import (  # noqa: F401
        WindowsControlError as FixedClickControlError,
        capture_calibration,
        commit_calibration,
        execute_fixed_click,
    )
else:
    from x11_fixed_click import (  # noqa: F401
        X11ControlError as FixedClickControlError,
        capture_calibration,
        commit_calibration,
        execute_fixed_click,
    )


__all__ = [
    "FixedClickControlError",
    "capture_calibration",
    "commit_calibration",
    "execute_fixed_click",
]
