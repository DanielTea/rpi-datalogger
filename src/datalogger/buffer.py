import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECORDS = 100_000


class LocalBuffer:
    """SQLite-backed FIFO queue for offline resilience.

    Automatically prunes oldest records when max_records is exceeded
    to prevent unbounded growth on the SD card.
    """

    def __init__(self, db_path: str, max_records: int = _DEFAULT_MAX_RECORDS):
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
        self.max_records = max_records

    def push(self, table_name: str, record: dict):
        """Push a record to the buffer for later upload."""
        self.conn.execute(
            "INSERT INTO pending (table_name, payload) VALUES (?, ?)",
            (table_name, json.dumps(record, default=str)),
        )
        self.conn.commit()
        self._prune()

    def _prune(self):
        """Remove oldest records if buffer exceeds max size."""
        count = self.count()
        if count > self.max_records:
            excess = count - self.max_records
            self.conn.execute(
                "DELETE FROM pending WHERE id IN "
                "(SELECT id FROM pending ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
            self.conn.commit()
            logger.warning(
                "Buffer pruned %d oldest records (max=%d)", excess, self.max_records
            )

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
