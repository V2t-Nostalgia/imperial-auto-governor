#!/usr/bin/env python3
"""Persistent campaign conversation storage for the IAG agent."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONVERSATION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
CAMPAIGN_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class ConversationStore:
    """Store complete protocol messages while exposing a redacted UI transcript."""

    def __init__(
        self,
        path: Path,
        *,
        conversation_id: str = "campaign",
    ):
        self.path = path
        self.conversation_id = conversation_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._repair_incomplete_tool_calls()

    @contextmanager
    def _connect(self) -> Any:
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 15000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    reasoning_content TEXT,
                    tool_calls_json TEXT,
                    tool_call_id TEXT,
                    tool_name TEXT,
                    kind TEXT NOT NULL DEFAULT 'message',
                    visible INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS messages_conversation_id
                    ON messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS state (
                    conversation_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, key)
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    campaign_id TEXT,
                    campaign_label TEXT,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_opened_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS conversations_campaign_id
                    ON conversations(campaign_id)
                    WHERE campaign_id IS NOT NULL AND campaign_id != '';

                CREATE TABLE IF NOT EXISTS application_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            timestamp = now_iso()
            first_message = connection.execute(
                """
                SELECT MIN(created_at) FROM messages
                WHERE conversation_id = ?
                """,
                (self.conversation_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations (
                    conversation_id, title, campaign_id, campaign_label,
                    archived, created_at, updated_at, last_opened_at
                ) VALUES (?, ?, NULL, NULL, 0, ?, ?, ?)
                """,
                (
                    self.conversation_id,
                    "当前战役" if self.conversation_id == "campaign" else "未命名战役",
                    first_message or timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO application_state (
                    key, value_json, updated_at
                ) VALUES ('active_conversation_id', ?, ?)
                """,
                (
                    json.dumps(self.conversation_id, ensure_ascii=False),
                    timestamp,
                ),
            )

    def _repair_incomplete_tool_calls(self) -> None:
        rows = self._rows()
        completed = {
            str(row["tool_call_id"])
            for row in rows
            if row["role"] == "tool" and row["tool_call_id"]
        }
        missing: list[tuple[str, str]] = []
        for row in rows:
            if row["role"] != "assistant":
                continue
            for call in self._json_value(row["tool_calls_json"], []):
                if not isinstance(call, dict) or not call.get("id"):
                    continue
                call_id = str(call["id"])
                if call_id in completed:
                    continue
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                missing.append((call_id, str(function.get("name") or "unknown")))
        for call_id, tool_name in missing:
            error = {
                "schema": "iag.tool_error.v1",
                "success": False,
                "tool": tool_name,
                "error": "The previous service process ended before the tool returned.",
            }
            self.append(
                "tool",
                json.dumps(error, ensure_ascii=False, separators=(",", ":")),
                tool_call_id=call_id,
                tool_name=tool_name,
                kind="tool_result",
                visible=True,
                metadata={
                    "public_summary": (
                        f"工具 {tool_name} 在服务重启前未返回，已标记为失败。"
                    ),
                    "success": False,
                },
            )

    def append(
        self,
        role: str,
        content: str | None = "",
        *,
        reasoning_content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        kind: str = "message",
        visible: bool = True,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        if role not in {"user", "assistant", "tool", "system"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        rendered_calls = (
            json.dumps(tool_calls, ensure_ascii=False, separators=(",", ":"))
            if tool_calls
            else None
        )
        rendered_metadata = json.dumps(
            metadata or {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    conversation_id, created_at, role, content,
                    reasoning_content, tool_calls_json, tool_call_id,
                    tool_name, kind, visible, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.conversation_id,
                    created_at or now_iso(),
                    role,
                    content or "",
                    reasoning_content,
                    rendered_calls,
                    tool_call_id,
                    tool_name,
                    kind,
                    1 if visible else 0,
                    rendered_metadata,
                ),
            )
            message_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (created_at or now_iso(), self.conversation_id),
            )
            return message_id

    def _rows(self, *, after_id: int = 0) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ? AND id > ?
                    ORDER BY id ASC
                    """,
                    (self.conversation_id, max(int(after_id), 0)),
                )
            )

    @staticmethod
    def _json_value(raw: str | None, fallback: Any) -> Any:
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback

    def public_messages(
        self,
        *,
        after_id: int = 0,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ? AND visible = 1 AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (self.conversation_id, max(int(after_id), 0), safe_limit),
                )
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._json_value(row["metadata_json"], {})
            content = str(row["content"])
            if row["role"] == "tool":
                content = str(
                    metadata.get("public_summary")
                    or f"工具 {row['tool_name'] or 'unknown'} 已完成。"
                )
            result.append(
                {
                    "id": int(row["id"]),
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "content": content,
                    "kind": row["kind"],
                    "tool_name": row["tool_name"],
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if key in {
                            "public_summary",
                            "success",
                            "run_id",
                            "trigger",
                            "application_id",
                            "application_display_name",
                        }
                    },
                }
            )
        return result

    @staticmethod
    def _row_cost(row: sqlite3.Row) -> int:
        return (
            len(str(row["content"] or ""))
            + len(str(row["reasoning_content"] or ""))
            + len(str(row["tool_calls_json"] or ""))
            + 96
        )

    @staticmethod
    def _segments(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
        """Keep an assistant tool-call and all corresponding results together."""
        segments: list[list[sqlite3.Row]] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            tool_calls = ConversationStore._json_value(
                row["tool_calls_json"],
                [],
            )
            if row["role"] == "assistant" and tool_calls:
                call_ids = {
                    str(item.get("id"))
                    for item in tool_calls
                    if isinstance(item, dict) and item.get("id")
                }
                segment = [row]
                index += 1
                while index < len(rows):
                    candidate = rows[index]
                    if (
                        candidate["role"] == "tool"
                        and str(candidate["tool_call_id"]) in call_ids
                    ):
                        segment.append(candidate)
                        index += 1
                        continue
                    break
                segments.append(segment)
                continue
            segments.append([row])
            index += 1
        return segments

    @staticmethod
    def _protocol_message(row: sqlite3.Row) -> dict[str, Any]:
        role = str(row["role"])
        message: dict[str, Any] = {
            "role": role,
            "content": str(row["content"] or ""),
        }
        tool_calls = ConversationStore._json_value(
            row["tool_calls_json"],
            [],
        )
        if role == "assistant" and tool_calls:
            message["tool_calls"] = tool_calls
            # DeepSeek requires this field to be replayed for tool-call turns.
            if row["reasoning_content"] is not None:
                message["reasoning_content"] = str(row["reasoning_content"])
        if role == "tool":
            message["tool_call_id"] = str(row["tool_call_id"] or "")
        return message

    def protocol_messages(
        self,
        *,
        max_chars: int = 120_000,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows = self._rows()
        segments = self._segments(rows)
        selected: list[list[sqlite3.Row]] = []
        used = 0
        for segment in reversed(segments):
            cost = sum(self._row_cost(row) for row in segment)
            if selected and used + cost > max_chars:
                break
            if not selected and cost > max_chars:
                # Keep the latest complete protocol group even when it is large.
                selected.append(segment)
                used += cost
                break
            selected.append(segment)
            used += cost
        selected.reverse()
        kept_rows = [row for segment in selected for row in segment]
        messages = [self._protocol_message(row) for row in kept_rows]
        return messages, {
            "stored_messages": len(rows),
            "included_messages": len(kept_rows),
            "omitted_messages": len(rows) - len(kept_rows),
            "estimated_chars": used,
        }

    def protocol_segments(self, *, after_id: int = 0) -> list[dict[str, Any]]:
        """Return indivisible protocol groups with database boundaries."""
        result: list[dict[str, Any]] = []
        for segment in self._segments(self._rows(after_id=after_id)):
            result.append(
                {
                    "first_id": int(segment[0]["id"]),
                    "last_id": int(segment[-1]["id"]),
                    "messages": [self._protocol_message(row) for row in segment],
                    "estimated_chars": sum(self._row_cost(row) for row in segment),
                }
            )
        return result

    def overlay_messages(self, *, limit: int = 250) -> list[dict[str, Any]]:
        """Return only human input and model-visible text for the game overlay."""
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT id, created_at, role, content, kind
                    FROM messages
                    WHERE conversation_id = ?
                      AND visible = 1
                      AND content != ''
                      AND (
                        (role = 'user' AND kind = 'operator_message')
                        OR
                        (role = 'assistant' AND kind IN (
                          'assistant_message', 'tool_call'
                        ))
                      )
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (self.conversation_id, safe_limit),
                )
            )
        rows.reverse()
        return [
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
                "kind": str(row["kind"]),
            }
            for row in rows
        ]

    def set_state(self, key: str, value: Any) -> None:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO state (conversation_id, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (self.conversation_id, key, rendered, now_iso()),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (now_iso(), self.conversation_id),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json FROM state
                WHERE conversation_id = ? AND key = ?
                """,
                (self.conversation_id, key),
            ).fetchone()
        return self._json_value(row["value_json"], default) if row else default

    @staticmethod
    def _validate_conversation_id(conversation_id: str) -> str:
        value = str(conversation_id).strip()
        if not CONVERSATION_ID_RE.fullmatch(value):
            raise ValueError("Invalid conversation ID.")
        return value

    @staticmethod
    def _validate_title(title: str) -> str:
        value = str(title).strip()
        if not value or len(value) > 80:
            raise ValueError("Conversation title must contain 1 to 80 characters.")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Conversation title contains control characters.")
        return value

    @staticmethod
    def _validate_campaign_id(campaign_id: str | None) -> str | None:
        if campaign_id is None:
            return None
        value = str(campaign_id).strip().lower()
        if not CAMPAIGN_ID_RE.fullmatch(value):
            raise ValueError("Invalid campaign ID.")
        return value

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "conversation_id": str(row["conversation_id"]),
            "title": str(row["title"]),
            "campaign_id": row["campaign_id"],
            "campaign_label": row["campaign_label"],
            "archived": bool(row["archived"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_opened_at": str(row["last_opened_at"]),
            "stored_messages": (
                int(row["stored_messages"]) if "stored_messages" in keys else 0
            ),
            "last_message_at": (
                row["last_message_at"] if "last_message_at" in keys else None
            ),
        }

    def conversation_metadata(
        self,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        selected = self._validate_conversation_id(
            conversation_id or self.conversation_id
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, COUNT(m.id) AS stored_messages,
                       MAX(m.created_at) AS last_message_at
                FROM conversations c
                LEFT JOIN messages m
                    ON m.conversation_id = c.conversation_id
                WHERE c.conversation_id = ?
                GROUP BY c.conversation_id
                """,
                (selected,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown conversation: {selected}")
        return self._metadata_from_row(row)

    def list_conversations(
        self,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT c.*, COUNT(m.id) AS stored_messages,
                           MAX(m.created_at) AS last_message_at
                    FROM conversations c
                    LEFT JOIN messages m
                        ON m.conversation_id = c.conversation_id
                    WHERE ? = 1 OR c.archived = 0
                    GROUP BY c.conversation_id
                    ORDER BY c.archived ASC,
                             COALESCE(MAX(m.created_at), c.updated_at) DESC,
                             c.created_at DESC
                    """,
                    (1 if include_archived else 0,),
                )
            )
        return [self._metadata_from_row(row) for row in rows]

    def create_conversation(
        self,
        title: str,
        *,
        campaign_id: str | None = None,
        campaign_label: str | None = None,
    ) -> dict[str, Any]:
        selected_title = self._validate_title(title)
        selected_campaign = self._validate_campaign_id(campaign_id)
        label = str(campaign_label or "").strip()[:180] or None
        conversation_id = uuid.uuid4().hex
        timestamp = now_iso()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        conversation_id, title, campaign_id, campaign_label,
                        archived, created_at, updated_at, last_opened_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        selected_title,
                        selected_campaign,
                        label,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "This campaign is already bound to a conversation."
            ) from error
        return self.conversation_metadata(conversation_id)

    def rename_conversation(
        self,
        conversation_id: str,
        title: str,
    ) -> dict[str, Any]:
        selected = self._validate_conversation_id(conversation_id)
        selected_title = self._validate_title(title)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (selected_title, now_iso(), selected),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown conversation: {selected}")
        return self.conversation_metadata(selected)

    def set_conversation_archived(
        self,
        conversation_id: str,
        archived: bool,
    ) -> dict[str, Any]:
        selected = self._validate_conversation_id(conversation_id)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET archived = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (1 if archived else 0, now_iso(), selected),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown conversation: {selected}")
        return self.conversation_metadata(selected)

    def bind_campaign(
        self,
        conversation_id: str,
        campaign_id: str | None,
        *,
        campaign_label: str | None = None,
    ) -> dict[str, Any]:
        selected = self._validate_conversation_id(conversation_id)
        selected_campaign = self._validate_campaign_id(campaign_id)
        label = str(campaign_label or "").strip()[:180] or None
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE conversations
                    SET campaign_id = ?, campaign_label = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (selected_campaign, label, now_iso(), selected),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Unknown conversation: {selected}")
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "This campaign is already bound to a conversation."
            ) from error
        return self.conversation_metadata(selected)

    def assign_campaign_to_conversation(
        self,
        campaign_id: str,
        conversation_id: str | None,
        *,
        campaign_label: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly assign one save campaign without changing the active chat."""
        selected_campaign = self._validate_campaign_id(campaign_id)
        selected_conversation = (
            self._validate_conversation_id(conversation_id)
            if conversation_id is not None
            else None
        )
        label = str(campaign_label or "").strip()[:180] or None
        timestamp = now_iso()
        with self._connect() as connection:
            current_holder = connection.execute(
                """
                SELECT conversation_id FROM conversations
                WHERE campaign_id = ?
                """,
                (selected_campaign,),
            ).fetchone()
            displaced_conversation_id = (
                str(current_holder["conversation_id"])
                if current_holder is not None
                else None
            )
            previous_campaign_id = None

            if selected_conversation is not None:
                target = connection.execute(
                    """
                    SELECT campaign_id, archived FROM conversations
                    WHERE conversation_id = ?
                    """,
                    (selected_conversation,),
                ).fetchone()
                if target is None:
                    raise KeyError(
                        f"Unknown conversation: {selected_conversation}"
                    )
                if bool(target["archived"]):
                    raise ValueError(
                        "Archived conversations must be restored before binding."
                    )
                previous_campaign_id = target["campaign_id"]

            if (
                displaced_conversation_id is not None
                and displaced_conversation_id != selected_conversation
            ):
                connection.execute(
                    """
                    UPDATE conversations
                    SET campaign_id = NULL, campaign_label = NULL,
                        updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (timestamp, displaced_conversation_id),
                )

            if selected_conversation is not None:
                connection.execute(
                    """
                    UPDATE conversations
                    SET campaign_id = ?, campaign_label = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (
                        selected_campaign,
                        label,
                        timestamp,
                        selected_conversation,
                    ),
                )
            elif displaced_conversation_id is not None:
                connection.execute(
                    """
                    UPDATE conversations
                    SET campaign_id = NULL, campaign_label = NULL,
                        updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (timestamp, displaced_conversation_id),
                )

        return {
            "campaign_id": selected_campaign,
            "conversation_id": selected_conversation,
            "displaced_conversation_id": (
                displaced_conversation_id
                if displaced_conversation_id != selected_conversation
                else None
            ),
            "previous_target_campaign_id": (
                previous_campaign_id
                if previous_campaign_id != selected_campaign
                else None
            ),
        }

    def conversation_for_campaign(
        self,
        campaign_id: str,
    ) -> dict[str, Any] | None:
        selected = self._validate_campaign_id(campaign_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, COUNT(m.id) AS stored_messages,
                       MAX(m.created_at) AS last_message_at
                FROM conversations c
                LEFT JOIN messages m
                    ON m.conversation_id = c.conversation_id
                WHERE c.campaign_id = ?
                GROUP BY c.conversation_id
                """,
                (selected,),
            ).fetchone()
        return self._metadata_from_row(row) if row else None

    def active_conversation_id(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json FROM application_state
                WHERE key = 'active_conversation_id'
                """
            ).fetchone()
        selected = self._json_value(
            row["value_json"] if row else None,
            self.conversation_id,
        )
        try:
            metadata = self.conversation_metadata(str(selected))
        except (KeyError, ValueError):
            metadata = self.conversation_metadata(self.conversation_id)
        if metadata["archived"]:
            available = self.list_conversations(include_archived=False)
            if available:
                return str(available[0]["conversation_id"])
        return str(metadata["conversation_id"])

    def set_active_conversation(self, conversation_id: str) -> dict[str, Any]:
        selected = self._validate_conversation_id(conversation_id)
        metadata = self.conversation_metadata(selected)
        if metadata["archived"]:
            raise ValueError(
                "Archived conversations must be restored before opening."
            )
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO application_state (key, value_json, updated_at)
                VALUES ('active_conversation_id', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(selected, ensure_ascii=False), timestamp),
            )
            connection.execute(
                """
                UPDATE conversations
                SET last_opened_at = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (timestamp, timestamp, selected),
            )
        return self.conversation_metadata(selected)

    def public_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM messages
                    WHERE conversation_id = ?
                    """,
                    (self.conversation_id,),
                ).fetchone()[0]
            )
        return {
            "conversation_id": self.conversation_id,
            "metadata": self.conversation_metadata(),
            "stored_messages": count,
            "next_review": self.get_state("next_review", None),
            "last_autonomy": self.get_state("last_autonomy", None),
            "autonomy_mode": self.get_state("autonomy_mode", None),
            "review_interval_months": self.get_state(
                "review_interval_months",
                None,
            ),
            "context_summary": self.get_state("context_summary", None),
            "decade_planning": self.get_state("decade_planning", None),
            "strategic_emergency": self.get_state("strategic_emergency", None),
            "pending_execution_confirmations": self.get_state(
                "pending_execution_confirmations",
                [],
            ),
            "execution_confirmation_history": self.get_state(
                "execution_confirmation_history",
                [],
            ),
        }
