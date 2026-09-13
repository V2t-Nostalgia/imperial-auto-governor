from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from iag.stellaris.state.state_index import WorldStateIndex
from iag.stellaris.state.world_snapshot import SnapshotIdentity, WorldSnapshot


class WorldStateIndexTests(unittest.TestCase):
    def test_numeric_map_is_single_flight_for_concurrent_readers(self) -> None:
        text = "country=\n{\n1=\n{\nvalue=7\n}\n}\n"
        index = WorldStateIndex(text)
        calls = 0
        calls_lock = threading.Lock()

        from iag.stellaris.state import state_index as state_index_module

        original = state_index_module.parse_numeric_map

        def counted(section: str) -> dict[int, str | None]:
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.03)
            return original(section)

        with (
            patch.object(state_index_module, "parse_numeric_map", counted),
            ThreadPoolExecutor(max_workers=8) as executor,
        ):
            results = list(
                executor.map(lambda _value: index.numeric_map("country"), range(8))
            )

        self.assertEqual(calls, 1)
        self.assertTrue(all(result is results[0] for result in results))
        self.assertEqual(results[0][1], "value=7")


class WorldSnapshotTests(unittest.TestCase):
    def test_gamestate_and_derived_profile_are_single_flight(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.sav"
            path.write_bytes(b"placeholder")
            identity = SnapshotIdentity(
                path=path,
                size=path.stat().st_size,
                modified_ns=path.stat().st_mtime_ns,
                sha256="a" * 64,
                campaign_id="b" * 16,
                revision=1,
            )
            snapshot = WorldSnapshot(identity)
            profile_calls = 0
            profile_lock = threading.Lock()

            def profile(*_args: object, **_kwargs: object) -> dict[str, object]:
                nonlocal profile_calls
                with profile_lock:
                    profile_calls += 1
                time.sleep(0.03)
                return {"fleets": []}

            with (
                patch(
                    "iag.stellaris.state.world_snapshot.load_gamestate",
                    return_value="player=\n{\ncountry=1\n}\n",
                ) as load,
                patch(
                    "iag.stellaris.state.world_snapshot.extract_fleet_profiles",
                    side_effect=profile,
                ),
                ThreadPoolExecutor(max_workers=8) as executor,
            ):
                results = list(
                    executor.map(lambda _value: snapshot.fleet_profile(), range(8))
                )

            self.assertEqual(load.call_count, 1)
            self.assertEqual(profile_calls, 1)
            self.assertTrue(all(result is results[0] for result in results))

    def test_application_warmup_builds_shared_profiles_once(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.sav"
            path.write_bytes(b"placeholder")
            snapshot = WorldSnapshot(
                SnapshotIdentity(
                    path=path,
                    size=path.stat().st_size,
                    modified_ns=path.stat().st_mtime_ns,
                    sha256="c" * 64,
                    campaign_id="d" * 16,
                    revision=4,
                )
            )
            calls: dict[str, int] = {
                "economy": 0,
                "research": 0,
                "fleet": 0,
                "invasion": 0,
                "ships": 0,
                "expansion": 0,
                "campaign_routes": 0,
            }

            def counted(name: str, value: object) -> object:
                calls[name] += 1
                return value

            with (
                patch(
                    "iag.stellaris.state.world_snapshot.load_gamestate",
                    return_value="player=\n{\ncountry=1\n}\n",
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_game_state",
                    side_effect=lambda *_args, **_kwargs: counted("economy", {}),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_research_profile",
                    side_effect=lambda *_args, **_kwargs: counted("research", {}),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_fleet_profiles",
                    side_effect=lambda *_args, **_kwargs: counted(
                        "fleet", {"fleets": []}
                    ),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_invasion_profiles",
                    side_effect=lambda *_args, **_kwargs: counted("invasion", {}),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_ship_profiles",
                    side_effect=lambda *_args, **_kwargs: counted("ships", {}),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.extract_expansion_profiles",
                    side_effect=lambda *_args, **_kwargs: counted("expansion", {}),
                ),
                patch(
                    "iag.stellaris.state.world_snapshot.CampaignRoutePlanner",
                    side_effect=lambda *_args, **_kwargs: counted(
                        "campaign_routes", object()
                    ),
                ),
            ):
                first = snapshot.warm_for_applications(
                    [
                        "fleet_operations",
                        "research_strategy",
                        "economy_governance",
                    ],
                    game_root=Path(temporary),
                )
                snapshot.warm_for_applications(
                    [
                        "fleet_operations",
                        "research_strategy",
                        "economy_governance",
                    ],
                    game_root=Path(temporary),
                )

        self.assertEqual(first["errors"], {})
        self.assertEqual(set(first["timings_seconds"]), set(calls))
        self.assertEqual(calls, dict.fromkeys(calls, 1))


if __name__ == "__main__":
    unittest.main()
