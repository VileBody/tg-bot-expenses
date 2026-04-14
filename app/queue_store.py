from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class QueueItem:
    row_id: int
    chat_id: int
    message_id: int
    text: str
    attempts: int


class QueueStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    UNIQUE(chat_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_message_queue_due
                ON message_queue(status, next_attempt_at, id)
                """
            )
            # If process was interrupted mid-processing, return these items back to pending.
            conn.execute(
                "UPDATE message_queue SET status='pending' WHERE status='processing'"
            )
            conn.commit()

    def _enqueue_sync(self, chat_id: int, message_id: int, text: str) -> bool:
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO message_queue(
                        chat_id, message_id, text, created_at_utc, status, attempts, next_attempt_at
                    ) VALUES(?, ?, ?, ?, 'pending', 0, 0)
                    """,
                    (
                        chat_id,
                        message_id,
                        text,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def _fetch_due_sync(self) -> QueueItem | None:
        now_epoch = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, chat_id, message_id, text, attempts
                FROM message_queue
                WHERE status='pending' AND next_attempt_at <= ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (now_epoch,),
            ).fetchone()
            if row is None:
                return None
            return QueueItem(
                row_id=int(row["id"]),
                chat_id=int(row["chat_id"]),
                message_id=int(row["message_id"]),
                text=str(row["text"]),
                attempts=int(row["attempts"]),
            )

    def _mark_processing_sync(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE message_queue SET status='processing' WHERE id=?",
                (row_id,),
            )
            conn.commit()

    def _mark_done_sync(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE message_queue SET status='done', last_error=NULL WHERE id=?",
                (row_id,),
            )
            conn.commit()

    def _mark_failed_sync(self, row_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE message_queue SET status='failed', last_error=? WHERE id=?",
                (error[:500], row_id),
            )
            conn.commit()

    def _schedule_retry_sync(self, row_id: int, delay_seconds: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_queue
                SET status='pending',
                    attempts=attempts+1,
                    next_attempt_at=?,
                    last_error=?
                WHERE id=?
                """,
                (int(time.time()) + delay_seconds, error[:500], row_id),
            )
            conn.commit()

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    async def enqueue(self, chat_id: int, message_id: int, text: str) -> bool:
        return await asyncio.to_thread(self._enqueue_sync, chat_id, message_id, text)

    async def fetch_due(self) -> QueueItem | None:
        return await asyncio.to_thread(self._fetch_due_sync)

    async def mark_processing(self, row_id: int) -> None:
        await asyncio.to_thread(self._mark_processing_sync, row_id)

    async def mark_done(self, row_id: int) -> None:
        await asyncio.to_thread(self._mark_done_sync, row_id)

    async def mark_failed(self, row_id: int, error: str) -> None:
        await asyncio.to_thread(self._mark_failed_sync, row_id, error)

    async def schedule_retry(self, row_id: int, delay_seconds: int, error: str) -> None:
        await asyncio.to_thread(self._schedule_retry_sync, row_id, delay_seconds, error)
