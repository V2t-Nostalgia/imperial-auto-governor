"""Pinned save snapshots and shared, lazy world-state derivations."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar, cast

from iag.stellaris.game_knowledge import technology_rule
from iag.stellaris.state.campaign_routes import CampaignRoutePlanner
from iag.stellaris.state.expansion_profiles import extract_expansion_profiles
from iag.stellaris.state.extract_game_state import extract_game_state, load_gamestate
from iag.stellaris.state.fleet_profiles import extract_fleet_profiles
from iag.stellaris.state.invasion_profiles import extract_invasion_profiles
from iag.stellaris.state.research_profiles import extract_research_profile
from iag.stellaris.state.save_ingest import (
    read_manifest,
    resolve_current_save,
    save_manifest_revision,
)
from iag.stellaris.state.ship_profiles import extract_ship_profiles
from iag.stellaris.state.state_index import WorldStateIndex

T = TypeVar("T")


class StaleWorldSnapshotError(RuntimeError):
    """Raised before mutation when the pinned save is no longer current."""


@dataclass(frozen=True)
class SnapshotIdentity:
    path: Path
    size: int
    modified_ns: int
    sha256: str
    campaign_id: str | None
    revision: int | None

    @property
    def cache_key(self) -> tuple[str, int, int, str, str | None, int | None]:
        return (
            str(self.path),
            self.size,
            self.modified_ns,
            self.sha256,
            self.campaign_id,
            self.revision,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class WorldSnapshot:
    """Immutable save identity with lazily shared text, indexes, and profiles."""

    def __init__(self, identity: SnapshotIdentity) -> None:
        self.identity = identity
        self._lock = threading.RLock()
        self._values: dict[tuple[Any, ...], Future[Any]] = {}

    @property
    def path(self) -> Path:
        return self.identity.path

    def source_identity(self) -> dict[str, Any]:
        """Return the scheduler identity for this exact pinned upload."""
        return {
            "path": str(self.identity.path.resolve()),
            "modified_ns": self.identity.modified_ns,
            "size": self.identity.size,
        }

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

    @property
    def text(self) -> str:
        return self._memo(("gamestate",), lambda: load_gamestate(self.path))

    @property
    def index(self) -> WorldStateIndex:
        return self._memo(("index",), lambda: WorldStateIndex(self.text))

    def fleet_profile(self, *, owner: int | None = None) -> dict[str, Any]:
        return self._memo(
            ("fleet_profile", owner),
            lambda: extract_fleet_profiles(
                self.text,
                owner=owner,
                state_index=self.index,
            ),
        )

    def research_profile(
        self,
        *,
        owner: int | None = None,
        game_root: Path | None = None,
    ) -> dict[str, Any]:
        root_key = str(game_root.resolve()) if game_root is not None else None

        def area_for(technology_id: str) -> str | None:
            if game_root is None:
                return None
            rule = technology_rule(game_root, technology_id)
            return str(rule.get("area") or "") or None

        return self._memo(
            ("research_profile", owner, root_key),
            lambda: extract_research_profile(
                self.text,
                owner=owner,
                technology_area=area_for,
                state_index=self.index,
            ),
        )

    def ship_profile(
        self,
        *,
        owner: int | None = None,
        game_root: Path | None = None,
    ) -> dict[str, Any]:
        root_key = str(game_root.resolve()) if game_root is not None else None
        return self._memo(
            ("ship_profile", owner, root_key),
            lambda: extract_ship_profiles(
                self.text,
                owner=owner,
                game_root=game_root,
                state_index=self.index,
                research_profile=self.research_profile(
                    owner=owner,
                    game_root=game_root,
                ),
            ),
        )

    def invasion_profile(
        self,
        *,
        owner: int | None = None,
        game_root: Path | None = None,
        maximum_recruitment_count: int = 5,
    ) -> dict[str, Any]:
        root_key = str(game_root.resolve()) if game_root is not None else None
        return self._memo(
            ("invasion_profile", owner, root_key, maximum_recruitment_count),
            lambda: extract_invasion_profiles(
                self.text,
                owner=owner,
                game_root=game_root,
                maximum_recruitment_count=maximum_recruitment_count,
                fleet_profile=self.fleet_profile(owner=owner),
                research_profile=self.research_profile(
                    owner=owner,
                    game_root=game_root,
                ),
                state_index=self.index,
            ),
        )

    def expansion_profile(
        self,
        *,
        owner: int | None = None,
        game_root: Path,
        minimum_habitability: float = 0.30,
    ) -> dict[str, Any]:
        root_key = str(game_root.resolve())
        return self._memo(
            ("expansion_profile", owner, root_key, minimum_habitability),
            lambda: extract_expansion_profiles(
                self.text,
                owner=owner,
                game_root=game_root,
                minimum_habitability=minimum_habitability,
                research_profile=self.research_profile(
                    owner=owner,
                    game_root=game_root,
                ),
                ship_profile=self.ship_profile(
                    owner=owner,
                    game_root=game_root,
                ),
                state_index=self.index,
            ),
        )

    def _economy_state_base(self, *, owner: int | None = None) -> dict[str, Any]:
        return self._memo(
            ("economy_state", owner),
            lambda: extract_game_state(
                self.text,
                save_path=self.path,
                owner=owner,
                state_index=self.index,
            ),
        )

    def economy_state(self, *, owner: int | None = None) -> dict[str, Any]:
        # Economy planning applies virtual mutations, so callers receive a
        # private copy while the expensive parsed base remains snapshot-local.
        return copy.deepcopy(self._economy_state_base(owner=owner))

    def campaign_planner(
        self,
        *,
        owner: int | None = None,
        game_root: Path | None = None,
        maximum_recruitment_count: int = 5,
        deployment_commitments: list[dict[str, Any]] | None = None,
        minimum_space_force_ratio: float = 1.20,
    ) -> CampaignRoutePlanner:
        commitments = [
            dict(item)
            for item in (deployment_commitments or [])
            if isinstance(item, dict)
        ]
        commitment_key = hashlib.sha256(
            json.dumps(
                commitments,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        root_key = str(game_root.resolve()) if game_root is not None else None
        return self._memo(
            (
                "campaign_planner",
                owner,
                root_key,
                maximum_recruitment_count,
                commitment_key,
                minimum_space_force_ratio,
            ),
            lambda: CampaignRoutePlanner(
                self.text,
                owner=owner,
                game_root=game_root,
                fleet_profile=self.fleet_profile(owner=owner),
                invasion_profile=self.invasion_profile(
                    owner=owner,
                    game_root=game_root,
                    maximum_recruitment_count=maximum_recruitment_count,
                ),
                maximum_recruitment_count=maximum_recruitment_count,
                deployment_commitments=commitments,
                minimum_space_force_ratio=minimum_space_force_ratio,
                state_index=self.index,
            ),
        )

    def cache_entry_count(self) -> int:
        with self._lock:
            return len(self._values)

    def warm_for_applications(
        self,
        application_ids: list[str],
        *,
        game_root: Path | None,
        maximum_recruitment_count: int = 5,
        minimum_habitability: float = 0.30,
        minimum_space_force_ratio: float = 1.20,
    ) -> dict[str, Any]:
        """Build shared CPU-heavy state once before Applications fan out.

        These parsers are mostly Python CPU work. On Windows, running their
        cold starts in four threads adds GIL contention while duplicating none
        of the useful work. A dependency-aware serial warmup is faster; the
        bounded workers then serve independent hot reads and model calls.
        """
        requested = set(application_ids)
        timings: dict[str, float] = {}
        errors: dict[str, str] = {}

        def warm(name: str, factory: Callable[[], Any]) -> None:
            started = perf_counter()
            try:
                factory()
            except Exception as error:  # noqa: BLE001 - isolate one derivation
                errors[name] = f"{type(error).__name__}: {error}"
            finally:
                timings[name] = round(perf_counter() - started, 6)

        started = perf_counter()
        # Loading and indexing are mandatory. If either fails there is no
        # coherent world snapshot for any Application to inspect.
        _ = self.text
        _ = self.index
        if "economy_governance" in requested:
            warm("economy", self._economy_state_base)
        if requested.intersection({"research_strategy", "fleet_operations"}):
            warm("research", lambda: self.research_profile(game_root=game_root))
        if "fleet_operations" in requested:
            warm("fleet", self.fleet_profile)
            warm(
                "invasion",
                lambda: self.invasion_profile(
                    game_root=game_root,
                    maximum_recruitment_count=maximum_recruitment_count,
                ),
            )
            if game_root is not None:
                warm("ships", lambda: self.ship_profile(game_root=game_root))
                warm(
                    "expansion",
                    lambda: self.expansion_profile(
                        game_root=game_root,
                        minimum_habitability=minimum_habitability,
                    ),
                )
            warm(
                "campaign_routes",
                lambda: self.campaign_planner(
                    game_root=game_root,
                    maximum_recruitment_count=maximum_recruitment_count,
                    minimum_space_force_ratio=minimum_space_force_ratio,
                ),
            )
        return {
            "schema": "iag.world_snapshot_warmup.v1",
            "snapshot_sha256": self.identity.sha256,
            "snapshot_revision": self.identity.revision,
            "applications": sorted(requested),
            "timings_seconds": timings,
            "errors": errors,
            "elapsed_seconds": round(perf_counter() - started, 6),
            "cache_entries": self.cache_entry_count(),
        }


class WorldStateService:
    """Pin autonomous work to one save while retaining a small hot cache."""

    def __init__(self, *, retained_snapshots: int = 1) -> None:
        self.retained_snapshots = max(1, min(int(retained_snapshots), 4))
        self._lock = threading.RLock()
        self._snapshots: dict[tuple[Any, ...], WorldSnapshot] = {}
        self._order: list[tuple[Any, ...]] = []

    @staticmethod
    def identity(
        config: dict[str, Any],
        *,
        expected_campaign_id: str | None,
    ) -> SnapshotIdentity:
        path: Path | None = None
        manifest: dict[str, Any] = {}
        manifest_matches = False
        # The manifest is atomically replaced. Retry if it changes between
        # path resolution and identity capture rather than constructing an
        # identity from two different uploads.
        for _attempt in range(3):
            candidate = resolve_current_save(
                config,
                expected_campaign_id=expected_campaign_id,
            ).resolve()
            selected_manifest = read_manifest(config) or {}
            manifest_path = str(selected_manifest.get("stored_path") or "")
            selected_matches = (
                bool(manifest_path)
                and Path(manifest_path).expanduser().resolve() == candidate
            )
            path = candidate
            manifest = selected_manifest
            manifest_matches = selected_matches
            if str(config.get("save_source_mode", "host_upload")) != "host_upload":
                break
            if selected_matches:
                break
        if path is None:
            raise FileNotFoundError("No current Stellaris save is available.")
        stat = path.stat()
        if not manifest_matches:
            manifest = {}
        sha256 = str(manifest.get("sha256") or "") if manifest_matches else ""
        if not sha256:
            sha256 = _sha256_file(path)
        campaign_id = str(manifest.get("campaign_id") or "") or None
        revision = (
            save_manifest_revision(config, manifest) if manifest_matches else None
        )
        return SnapshotIdentity(
            path=path,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            sha256=sha256,
            campaign_id=campaign_id,
            revision=revision,
        )

    def pin(
        self,
        config: dict[str, Any],
        *,
        expected_campaign_id: str | None,
    ) -> WorldSnapshot:
        identity = self.identity(
            config,
            expected_campaign_id=expected_campaign_id,
        )
        key = identity.cache_key
        with self._lock:
            snapshot = self._snapshots.get(key)
            if snapshot is None:
                snapshot = WorldSnapshot(identity)
                self._snapshots[key] = snapshot
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            while len(self._order) > self.retained_snapshots:
                expired = self._order.pop(0)
                self._snapshots.pop(expired, None)
            return snapshot

    def is_current(
        self,
        snapshot: WorldSnapshot,
        config: dict[str, Any],
        *,
        expected_campaign_id: str | None,
    ) -> bool:
        return snapshot.identity == self.identity(
            config,
            expected_campaign_id=expected_campaign_id,
        )

    def assert_current(
        self,
        snapshot: WorldSnapshot,
        config: dict[str, Any],
        *,
        expected_campaign_id: str | None,
    ) -> None:
        if not self.is_current(
            snapshot,
            config,
            expected_campaign_id=expected_campaign_id,
        ):
            raise StaleWorldSnapshotError(
                "The synchronized save changed during this application turn; "
                "re-read the new snapshot before preparing or executing an action."
            )
