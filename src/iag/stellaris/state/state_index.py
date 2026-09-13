"""Thread-safe, snapshot-local indexes over one Stellaris gamestate string."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, TypeVar, cast

from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    parse_numeric_map,
)

T = TypeVar("T")


class WorldStateIndex:
    """Build expensive common save sections once and share them read-only.

    Values are computed with a single-flight primitive: concurrent inspectors
    requesting the same section wait for its first construction rather than
    repeating it. Callers must treat returned mappings and strings as immutable.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self._lock = threading.RLock()
        self._values: dict[tuple[Any, ...], Future[Any]] = {}

    def _memo(self, key: tuple[Any, ...], factory: Callable[[], T]) -> T:
        owner = False
        with self._lock:
            future = self._values.get(key)
            if future is None:
                future = Future()
                self._values[key] = future
                owner = True
        if owner:
            try:
                future.set_result(factory())
            except BaseException as error:  # noqa: BLE001 - wake all waiters
                future.set_exception(error)
                with self._lock:
                    if self._values.get(key) is future:
                        self._values.pop(key, None)
        return cast(T, future.result())

    def section(self, name: str, *, allow_indent: bool = False) -> str:
        return self._memo(
            ("section", name, allow_indent),
            lambda: find_braced_section(
                self.text,
                name,
                allow_indent=allow_indent,
            ),
        )

    def optional_section(self, name: str, *, allow_indent: bool = False) -> str:
        def build() -> str:
            try:
                return self.section(name, allow_indent=allow_indent)
            except ValueError:
                return ""

        return self._memo(("optional_section", name, allow_indent), build)

    def numeric_map(self, name: str) -> dict[int, str | None]:
        return self._memo(
            ("numeric_map", name),
            lambda: parse_numeric_map(self.section(name).strip()),
        )

    def optional_numeric_map(self, name: str) -> dict[int, str | None]:
        def build() -> dict[int, str | None]:
            try:
                return self.numeric_map(name)
            except ValueError:
                return {}

        return self._memo(("optional_numeric_map", name), build)

    def player_countries(self) -> list[int]:
        return self._memo(
            ("player_countries",),
            lambda: sorted(
                {
                    int(value)
                    for value in re.findall(
                        r"\bcountry=(\d+)",
                        self.optional_section("player", allow_indent=True),
                    )
                }
            ),
        )

    def planets(self) -> dict[int, str | None]:
        def build() -> dict[int, str | None]:
            collection = self.section("planets")
            planets = parse_numeric_map(collection.strip())
            if planets:
                return planets
            return parse_numeric_map(
                find_braced_section(
                    collection,
                    "planet",
                    allow_indent=True,
                ).strip()
            )

        return self._memo(("planets",), build)

    def starbases(self) -> dict[int, str | None]:
        def build() -> dict[int, str | None]:
            manager = self.section("starbase_mgr")
            collection = find_braced_section(
                manager,
                "starbases",
                allow_indent=True,
            )
            return parse_numeric_map(collection.strip())

        return self._memo(("starbases",), build)

    def construction_queues(self) -> dict[int, str | None]:
        def build() -> dict[int, str | None]:
            construction = self.optional_section("construction", allow_indent=True)
            if not construction:
                return {}
            manager = find_braced_section(
                construction,
                "queue_mgr",
                allow_indent=True,
            )
            queues = find_braced_section(manager, "queues", allow_indent=True)
            return parse_numeric_map(queues.strip())

        return self._memo(("construction_queues",), build)

    def construction_items(self) -> dict[int, str | None]:
        def build() -> dict[int, str | None]:
            construction = self.optional_section("construction", allow_indent=True)
            if not construction:
                return {}
            manager = find_braced_section(
                construction,
                "item_mgr",
                allow_indent=True,
            )
            items = find_braced_section(manager, "items", allow_indent=True)
            return parse_numeric_map(items.strip())

        return self._memo(("construction_items",), build)

    def cache_entry_count(self) -> int:
        with self._lock:
            return len(self._values)
