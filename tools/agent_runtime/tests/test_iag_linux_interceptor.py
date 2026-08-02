from __future__ import annotations

import sys
import unittest
from pathlib import Path
from uuid import uuid4


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from iag_linux_interceptor import (  # noqa: E402
    CAP_NET_ADMIN,
    has_effective_capability,
    interceptor_lock_path,
)


class LinuxInterceptorTests(unittest.TestCase):
    def test_reads_effective_net_admin_capability(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / uuid4().hex
        root.mkdir(parents=True)
        status_path = root / "status"
        mask = 1 << CAP_NET_ADMIN
        status_path.write_text(
            f"Name:\tpython\nCapEff:\t{mask:016x}\n",
            encoding="ascii",
        )
        try:
            self.assertTrue(
                has_effective_capability(
                    CAP_NET_ADMIN,
                    status_path=status_path,
                )
            )
            self.assertFalse(
                has_effective_capability(
                    CAP_NET_ADMIN + 1,
                    status_path=status_path,
                )
            )
        finally:
            status_path.unlink(missing_ok=True)
            root.rmdir()

    def test_lock_lives_beside_runtime_status(self) -> None:
        path = interceptor_lock_path(
            4242,
            status_path=Path("/runtime/state/interceptor_status.json"),
            log_path=Path("/runtime/runs/current/interceptor.jsonl"),
        )

        self.assertEqual(
            path,
            Path("/runtime/state/iag_agent_4242.lock"),
        )


if __name__ == "__main__":
    unittest.main()
