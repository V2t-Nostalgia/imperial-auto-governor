"""Cross-Application resource reservations tied to one authoritative save."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from iag.core.conversation_store import ConversationStore, now_iso


class ResourceReservationError(RuntimeError):
    """A spending action conflicts with reservations from the same save."""


def _normalized_amounts(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for resource, raw_amount in value.items():
        if not isinstance(raw_amount, (int, float)):
            continue
        amount = float(raw_amount)
        if not math.isfinite(amount) or amount <= 0:
            continue
        result[str(resource)] = amount
    return result


class ResourceReservationLedger:
    """Serialize stale-save spending across every Application.

    Reservations only apply to the exact save hash from which an action was
    validated. A different synchronized save is authoritative and supersedes
    all older reservations. Actions whose exact dynamic price is unavailable
    take an exclusive reservation, preventing any second stale-save spender.
    """

    def __init__(self, store: ConversationStore) -> None:
        self.path = Path(store.path)
        campaign_id = store.conversation_metadata().get("campaign_id")
        if not campaign_id:
            raise ResourceReservationError(
                "当前战役会话尚未绑定存档，无法建立资源预留。"
            )
        self.campaign_id = str(campaign_id)
        self._initialize()

    @contextmanager
    def _connect(self) -> Any:
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 15000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    source_save_sha256 TEXT NOT NULL,
                    source_game_date TEXT,
                    costs_json TEXT NOT NULL DEFAULT '{}',
                    exclusive INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS resource_reservations_campaign_state
                ON resource_reservations(campaign_id, state)
                """
            )

    def _supersede_stale(
        self,
        connection: sqlite3.Connection,
        source_save_sha256: str,
    ) -> None:
        connection.execute(
            """
            UPDATE resource_reservations
            SET state = 'superseded', updated_at = ?
            WHERE campaign_id = ? AND state = 'active'
              AND source_save_sha256 != ?
            """,
            (now_iso(), self.campaign_id, source_save_sha256),
        )

    def _active_rows(
        self,
        connection: sqlite3.Connection,
        source_save_sha256: str,
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT * FROM resource_reservations
                WHERE campaign_id = ? AND state = 'active'
                  AND source_save_sha256 = ?
                ORDER BY created_at, reservation_id
                """,
                (self.campaign_id, source_save_sha256),
            )
        )

    @staticmethod
    def _summary(
        rows: list[sqlite3.Row],
        stockpile: dict[str, Any],
        source_save_sha256: str,
    ) -> dict[str, Any]:
        reserved: dict[str, float] = {}
        reservations: list[dict[str, Any]] = []
        for row in rows:
            costs = _normalized_amounts(json.loads(str(row["costs_json"])))
            for resource, amount in costs.items():
                reserved[resource] = reserved.get(resource, 0.0) + amount
            reservations.append(
                {
                    "reservation_id": str(row["reservation_id"]),
                    "application_id": str(row["application_id"]),
                    "action": str(row["action"]),
                    "costs": costs,
                    "exclusive": bool(row["exclusive"]),
                    "source_game_date": row["source_game_date"],
                }
            )
        available: dict[str, float] = {}
        for resource, raw_amount in stockpile.items():
            if isinstance(raw_amount, (int, float)):
                available[str(resource)] = max(
                    float(raw_amount) - reserved.get(str(resource), 0.0),
                    0.0,
                )
        exclusive = any(item["exclusive"] for item in reservations)
        return {
            "schema": "iag.resource_reservations.v1",
            "source_save_sha256": source_save_sha256,
            "reserved": reserved,
            "available": available,
            "exclusive_spender_active": exclusive,
            "spending_allowed": not exclusive,
            "reservations": reservations,
        }

    def status(
        self,
        *,
        source_save_sha256: str,
        stockpile: dict[str, Any],
    ) -> dict[str, Any]:
        digest = str(source_save_sha256).strip()
        if not digest:
            raise ResourceReservationError("资源账本需要当前存档哈希。")
        with self._connect() as connection:
            # A status inspector is a pure read. Reservations from another
            # snapshot are already excluded by _active_rows; reserve() owns
            # the archival write while holding its immediate transaction.
            rows = self._active_rows(connection, digest)
            return self._summary(rows, stockpile, digest)

    def reserve(
        self,
        *,
        reservation_id: str,
        application_id: str,
        action: str,
        source_save_sha256: str,
        source_game_date: str | None,
        stockpile: dict[str, Any],
        costs: dict[str, Any] | None = None,
        exclusive: bool = False,
    ) -> dict[str, Any]:
        reservation = str(reservation_id).strip()
        digest = str(source_save_sha256).strip()
        normalized = _normalized_amounts(costs)
        if not reservation or not digest:
            raise ResourceReservationError(
                "资源预留需要 reservation_id 与当前存档哈希。"
            )
        if not normalized and not exclusive:
            return self.status(
                source_save_sha256=digest,
                stockpile=stockpile,
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._supersede_stale(connection, digest)
            existing = connection.execute(
                """
                SELECT * FROM resource_reservations
                WHERE reservation_id = ?
                """,
                (reservation,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["campaign_id"]) == self.campaign_id
                    and str(existing["source_save_sha256"]) == digest
                    and str(existing["state"]) == "active"
                ):
                    rows = self._active_rows(connection, digest)
                    return self._summary(rows, stockpile, digest)
                raise ResourceReservationError(
                    f"资源预留编号 {reservation} 已被其它状态占用。"
                )

            rows = self._active_rows(connection, digest)
            current = self._summary(rows, stockpile, digest)
            if current["exclusive_spender_active"]:
                raise ResourceReservationError(
                    "同一存档已有动态费用动作待确认；必须等待新存档后再支出。"
                )
            if exclusive and rows:
                raise ResourceReservationError(
                    "同一存档已有资源预留；动态费用动作必须等待新存档。"
                )
            for resource, amount in normalized.items():
                raw_available = current["available"].get(resource)
                if raw_available is None or float(raw_available) + 1e-9 < amount:
                    raise ResourceReservationError(
                        f"{resource} 可用 {raw_available!r}，不足以预留 {amount:g}。"
                    )

            timestamp = now_iso()
            connection.execute(
                """
                INSERT INTO resource_reservations (
                    reservation_id, campaign_id, application_id, action,
                    source_save_sha256, source_game_date, costs_json,
                    exclusive, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    reservation,
                    self.campaign_id,
                    str(application_id),
                    str(action),
                    digest,
                    source_game_date,
                    json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                    1 if exclusive else 0,
                    timestamp,
                    timestamp,
                ),
            )
            rows = self._active_rows(connection, digest)
            return self._summary(rows, stockpile, digest)

    def release(self, reservation_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE resource_reservations
                SET state = 'released', updated_at = ?
                WHERE reservation_id = ? AND campaign_id = ?
                  AND state = 'active'
                """,
                (now_iso(), str(reservation_id), self.campaign_id),
            )

    def apply_to_stockpile(
        self,
        *,
        source_save_sha256: str,
        stockpile: dict[str, Any],
    ) -> dict[str, Any]:
        status = self.status(
            source_save_sha256=source_save_sha256,
            stockpile=stockpile,
        )
        stockpile.update(status["available"])
        return status
