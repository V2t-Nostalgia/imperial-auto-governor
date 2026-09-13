"""Bounded, recoverable thread workers for pure state reads."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from time import monotonic
from typing import Any


class ReadTaskTimeoutError(TimeoutError):
    """One read task exceeded its bounded wall-clock budget."""


@dataclass(frozen=True)
class ReadTask:
    key: str
    fn: Callable[[], Any]


class _WorkerGeneration:
    def __init__(self, workers: int, generation: int) -> None:
        self.workers = workers
        self.generation = generation
        self.queue: queue.Queue[tuple[Future[Any], Callable[[], Any]] | None] = (
            queue.Queue()
        )
        self.retired = threading.Event()
        self.threads = [
            threading.Thread(
                target=self._run,
                name=f"iag-read-{generation}-{index + 1}",
                daemon=True,
            )
            for index in range(workers)
        ]
        for thread in self.threads:
            thread.start()

    def _run(self) -> None:
        while not self.retired.is_set():
            item = self.queue.get()
            if item is None:
                return
            future, fn = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as error:  # noqa: BLE001 - report worker death
                future.set_exception(error)

    def submit(self, fn: Callable[[], Any]) -> Future[Any]:
        future: Future[Any] = Future()
        self.queue.put((future, fn))
        return future

    def retire(self) -> None:
        self.retired.set()
        for _ in self.threads:
            self.queue.put(None)


class ReadTaskPool:
    """Run independent reads with four shared-memory workers by default.

    Threads intentionally share a single WorldSnapshot on Windows. If a task
    times out, its daemon generation is retired and later batches use a fresh
    generation; a stuck parser therefore cannot permanently exhaust the pool.
    """

    def __init__(self, *, max_workers: int = 4, timeout_seconds: float = 45.0) -> None:
        self.max_workers = max(1, min(int(max_workers), 8))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._lock = threading.RLock()
        self._generation_number = 0
        self._batches = 0
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._timed_out = 0
        self._wall_seconds = 0.0
        self._generation = self._new_generation()

    def _new_generation(self) -> _WorkerGeneration:
        self._generation_number += 1
        return _WorkerGeneration(self.max_workers, self._generation_number)

    def _replace_generation(self, expected: _WorkerGeneration) -> None:
        with self._lock:
            if self._generation is not expected:
                return
            expected.retire()
            self._generation = self._new_generation()

    def run(
        self,
        tasks: Iterable[ReadTask],
        *,
        timeout_seconds: float | None = None,
    ) -> list[Any]:
        selected = list(tasks)
        if not selected:
            return []
        started = monotonic()
        with self._lock:
            generation = self._generation
            self._batches += 1
            self._submitted += len(selected)
        futures = [generation.submit(task.fn) for task in selected]
        timeout = self.timeout_seconds if timeout_seconds is None else max(
            0.1,
            float(timeout_seconds),
        )
        deadline = monotonic() + timeout
        results: list[Any] = []
        timed_out = False
        for task, future in zip(selected, futures):
            remaining = deadline - monotonic()
            try:
                results.append(future.result(timeout=max(0.0, remaining)))
            except TimeoutError:
                timed_out = True
                results.append(
                    ReadTaskTimeoutError(
                        f"Read task {task.key!r} exceeded {timeout:.1f}s."
                    )
                )
            except BaseException as error:  # noqa: BLE001 - isolate sibling reads
                # Preserve successful sibling results and report this task at
                # its own tool boundary instead of failing the whole batch.
                results.append(error)
        if timed_out:
            self._replace_generation(generation)
        completed = sum(not isinstance(item, BaseException) for item in results)
        timed_out_count = sum(
            isinstance(item, ReadTaskTimeoutError) for item in results
        )
        with self._lock:
            self._completed += completed
            self._timed_out += timed_out_count
            self._failed += len(results) - completed - timed_out_count
            self._wall_seconds += monotonic() - started
        return results

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": "iag.read_task_pool.v1",
                "max_workers": self.max_workers,
                "timeout_seconds": self.timeout_seconds,
                "generation": self._generation_number,
                "batches": self._batches,
                "submitted": self._submitted,
                "completed": self._completed,
                "failed": self._failed,
                "timed_out": self._timed_out,
                "wall_seconds": round(self._wall_seconds, 6),
            }

    def close(self) -> None:
        with self._lock:
            self._generation.retire()


def tool_is_read_only(name: str, toolbox: Any) -> bool:
    """Return whether a toolbox explicitly allows this tool in read workers.

    Names are not evidence of purity: some legacy ``inspect_*`` tools also
    reconcile pending actions or update durable bookkeeping. Each toolbox must
    opt a tool in after auditing its concurrency semantics.
    """
    declared = getattr(toolbox, "parallel_read_tools", ())
    if callable(declared):
        declared = declared()
    if not isinstance(declared, (set, frozenset, tuple, list)):
        return False
    return str(name) in declared
