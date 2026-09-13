from __future__ import annotations

import threading
import time
import unittest

from iag.core.read_tasks import (
    ReadTask,
    ReadTaskPool,
    ReadTaskTimeoutError,
    tool_is_read_only,
)


class ReadTaskPoolTests(unittest.TestCase):
    def test_parallel_reads_require_an_explicit_toolbox_declaration(self) -> None:
        class Toolbox:
            parallel_read_tools = frozenset({"inspect_safe"})

        toolbox = Toolbox()

        self.assertTrue(tool_is_read_only("inspect_safe", toolbox))
        self.assertFalse(tool_is_read_only("inspect_side_effect", toolbox))
        self.assertFalse(tool_is_read_only("inspect_safe", object()))

    def test_limits_concurrency_and_preserves_result_order(self) -> None:
        pool = ReadTaskPool(max_workers=4, timeout_seconds=2)
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def work(value: int) -> int:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.04)
                return value
            finally:
                with lock:
                    active -= 1

        try:
            started = time.monotonic()
            results = pool.run(
                [
                    ReadTask(str(value), lambda value=value: work(value))
                    for value in range(8)
                ]
            )
            elapsed = time.monotonic() - started
        finally:
            pool.close()

        self.assertEqual(results, list(range(8)))
        self.assertEqual(maximum_active, 4)
        self.assertLess(elapsed, 0.25)

    def test_failure_does_not_discard_successful_siblings(self) -> None:
        pool = ReadTaskPool(max_workers=2, timeout_seconds=1)
        try:
            results = pool.run(
                [
                    ReadTask("first", lambda: "first"),
                    ReadTask("failure", lambda: 1 / 0),
                    ReadTask("last", lambda: "last"),
                ]
            )
        finally:
            pool.close()

        self.assertEqual(results[0], "first")
        self.assertIsInstance(results[1], ZeroDivisionError)
        self.assertEqual(results[2], "last")

    def test_timeout_retires_generation_and_next_batch_runs(self) -> None:
        pool = ReadTaskPool(max_workers=1, timeout_seconds=0.05)
        release = threading.Event()
        try:
            result = pool.run([ReadTask("blocked", release.wait)])
            recovered = pool.run([ReadTask("fresh", lambda: "ready")])
        finally:
            release.set()
            pool.close()

        self.assertIsInstance(result[0], ReadTaskTimeoutError)
        self.assertEqual(recovered, ["ready"])
        self.assertEqual(pool.status()["generation"], 2)
        self.assertEqual(pool.status()["timed_out"], 1)


if __name__ == "__main__":
    unittest.main()
