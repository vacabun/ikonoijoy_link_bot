import sqlite3
import time
from pathlib import Path


class StateManager:
    """
    Persist sent messages and per-room cursor timestamps with SQLite.
    """

    def __init__(self, db_path: str, initial_cursor: int | None = None):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initial_cursor = int(time.time()) if initial_cursor is None else initial_cursor
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_messages (
                room_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (room_id, message_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_cursors (
                room_id        INTEGER PRIMARY KEY,
                last_posted_at INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_sent(self, room_id: int, message_id: int) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM sent_messages WHERE room_id=? AND message_id=?",
            (room_id, message_id),
        )
        return cursor.fetchone() is not None

    def mark_sent(self, room_id: int, message_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO sent_messages (room_id, message_id) VALUES (?, ?)",
            (room_id, message_id),
        )
        self._conn.commit()

    def get_cursor(self, room_id: int) -> int:
        cursor = self._conn.execute(
            "SELECT last_posted_at FROM room_cursors WHERE room_id=?",
            (room_id,),
        )
        row = cursor.fetchone()
        if row:
            return int(row["last_posted_at"])

        self.set_cursor(room_id, self._initial_cursor)
        return self._initial_cursor

    def set_cursor(self, room_id: int, posted_at: int) -> None:
        self._conn.execute(
            """
            INSERT INTO room_cursors (room_id, last_posted_at)
            VALUES (?, ?)
            ON CONFLICT(room_id) DO UPDATE SET last_posted_at=excluded.last_posted_at
            """,
            (room_id, posted_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
