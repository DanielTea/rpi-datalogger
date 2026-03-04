import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


class LocalBuffer:
    """SQLite-backed FIFO queue for offline resilience."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        self.conn.commit()

    def push(self, table_name: str, record: dict):
        """Push a record to the buffer for later upload."""
        self.conn.execute(
            "INSERT INTO pending (table_name, payload) VALUES (?, ?)",
            (table_name, json.dumps(record, default=str)),
        )
        self.conn.commit()

    def peek(self, limit: int = 50) -> list[tuple[int, str, dict]]:
        """Peek at the oldest buffered records without removing them."""
        rows = self.conn.execute(
            "SELECT id, table_name, payload FROM pending ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(row[0], row[1], json.loads(row[2])) for row in rows]

    def delete(self, ids: list[int]):
        """Delete records by ID after successful upload."""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(
            f"DELETE FROM pending WHERE id IN ({placeholders})", ids
        )
        self.conn.commit()

    def count(self) -> int:
        """Return the number of pending records."""
        return self.conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def close(self):
        """Close the database connection."""
        self.conn.close()
