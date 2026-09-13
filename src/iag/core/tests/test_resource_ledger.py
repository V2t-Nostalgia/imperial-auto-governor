from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from iag.core.conversation_store import ConversationStore
from iag.core.resource_ledger import (
    ResourceReservationError,
    ResourceReservationLedger,
)


class ResourceReservationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = ConversationStore(Path(temporary.name) / "campaign.sqlite3")
        self.store.bind_campaign("campaign", "a" * 16, campaign_label="test")
        self.ledger = ResourceReservationLedger(self.store)
        self.stockpile = {"minerals": 400.0, "alloys": 100.0}

    def test_known_costs_are_shared_and_expire_on_fresh_save(self) -> None:
        self.ledger.reserve(
            reservation_id="economy:one",
            application_id="economy_governance",
            action="build_building",
            source_save_sha256="save-a",
            source_game_date="2200.01.01",
            stockpile=self.stockpile,
            costs={"minerals": 250},
        )
        status = self.ledger.status(
            source_save_sha256="save-a",
            stockpile=self.stockpile,
        )
        self.assertEqual(status["available"]["minerals"], 150.0)
        with self.assertRaises(ResourceReservationError):
            self.ledger.reserve(
                reservation_id="fleet:two",
                application_id="fleet_operations",
                action="recruit_armies",
                source_save_sha256="save-a",
                source_game_date="2200.01.01",
                stockpile=self.stockpile,
                costs={"minerals": 300},
            )

        fresh = self.ledger.status(
            source_save_sha256="save-b",
            stockpile={"minerals": 175.0},
        )
        self.assertEqual(fresh["available"]["minerals"], 175.0)
        self.assertEqual(fresh["reservations"], [])

    def test_unknown_dynamic_cost_excludes_every_other_spender(self) -> None:
        status = self.ledger.reserve(
            reservation_id="fleet:colonize",
            application_id="fleet_operations",
            action="order_colony_ship_and_colonize",
            source_save_sha256="save-a",
            source_game_date="2200.01.01",
            stockpile=self.stockpile,
            exclusive=True,
        )
        self.assertTrue(status["exclusive_spender_active"])
        with self.assertRaises(ResourceReservationError):
            self.ledger.reserve(
                reservation_id="economy:building",
                application_id="economy_governance",
                action="build_building",
                source_save_sha256="save-a",
                source_game_date="2200.01.01",
                stockpile=self.stockpile,
                costs={"minerals": 100},
            )

    def test_status_does_not_run_reservation_cleanup_writes(self) -> None:
        self.ledger._supersede_stale = lambda *_args: self.fail(  # type: ignore[method-assign]
            "status must remain a read-only operation"
        )

        status = self.ledger.status(
            source_save_sha256="save-a",
            stockpile=self.stockpile,
        )

        self.assertEqual(status["available"]["minerals"], 400.0)

    def test_concurrent_reservations_cannot_overspend(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def reserve(name: str) -> None:
            ledger = ResourceReservationLedger(self.store)
            barrier.wait()
            try:
                ledger.reserve(
                    reservation_id=name,
                    application_id=name,
                    action="test",
                    source_save_sha256="save-a",
                    source_game_date="2200.01.01",
                    stockpile=self.stockpile,
                    costs={"minerals": 300},
                )
            except ResourceReservationError:
                outcomes.append("rejected")
            else:
                outcomes.append("accepted")

        threads = [
            threading.Thread(target=reserve, args=("one",)),
            threading.Thread(target=reserve, args=("two",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["accepted", "rejected"])


if __name__ == "__main__":
    unittest.main()
