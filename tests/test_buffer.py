"""Tests for local SQLite buffer."""

import json
import os
import tempfile

import pytest

from datalogger.buffer import LocalBuffer


class TestLocalBuffer:
    def _make_buffer(self, **kwargs):
        """Create a buffer with a temp file."""
        self._tmpdir = tempfile.mkdtemp()
        path = os.path.join(self._tmpdir, "test.db")
        return LocalBuffer(path, **kwargs)

    def test_push_and_count(self):
        buf = self._make_buffer()
        assert buf.count() == 0
        buf.push("can_frames", {"arb_id": 123})
        assert buf.count() == 1
        buf.push("can_frames", {"arb_id": 456})
        assert buf.count() == 2

    def test_push_and_peek(self):
        buf = self._make_buffer()
        buf.push("can_frames", {"arb_id": 123})
        buf.push("gps_readings", {"lat": 52.0})
        rows = buf.peek(limit=10)
        assert len(rows) == 2
        assert rows[0][1] == "can_frames"
        assert rows[0][2] == {"arb_id": 123}
        assert rows[1][1] == "gps_readings"

    def test_peek_limit(self):
        buf = self._make_buffer()
        for i in range(10):
            buf.push("can_frames", {"i": i})
        rows = buf.peek(limit=3)
        assert len(rows) == 3

    def test_peek_does_not_remove(self):
        buf = self._make_buffer()
        buf.push("can_frames", {"a": 1})
        buf.peek(limit=10)
        assert buf.count() == 1

    def test_delete_by_ids(self):
        buf = self._make_buffer()
        buf.push("t", {"a": 1})
        buf.push("t", {"a": 2})
        rows = buf.peek()
        buf.delete([rows[0][0]])
        assert buf.count() == 1

    def test_delete_empty_list(self):
        buf = self._make_buffer()
        buf.delete([])
        assert buf.count() == 0

    def test_delete_nonexistent_ids(self):
        buf = self._make_buffer()
        buf.push("t", {"a": 1})
        buf.delete([9999])
        assert buf.count() == 1

    def test_preserves_complex_data(self):
        buf = self._make_buffer()
        data = {"nested": {"key": [1, 2, 3]}, "flag": True, "val": 3.14}
        buf.push("test", data)
        rows = buf.peek()
        assert rows[0][2] == data

    def test_creates_directory(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "sub", "dir", "test.db")
        buf = LocalBuffer(path)
        buf.push("t", {"a": 1})
        assert buf.count() == 1

    def test_fifo_order(self):
        buf = self._make_buffer()
        for i in range(100):
            buf.push("t", {"i": i})
        rows = buf.peek(limit=100)
        values = [r[2]["i"] for r in rows]
        assert values == list(range(100))

    def test_mixed_tables(self):
        buf = self._make_buffer()
        buf.push("can_frames", {"type": "can"})
        buf.push("gps_readings", {"type": "gps"})
        rows = buf.peek()
        tables = [r[1] for r in rows]
        assert tables == ["can_frames", "gps_readings"]

    def test_large_batch(self):
        buf = self._make_buffer()
        for i in range(1000):
            buf.push("t", {"i": i})
        assert buf.count() == 1000

    def test_prune_oldest_when_max_exceeded(self):
        """Buffer should prune oldest records when max_records is exceeded."""
        buf = self._make_buffer(max_records=10)
        for i in range(15):
            buf.push("t", {"i": i})
        assert buf.count() == 10
        rows = buf.peek(limit=10)
        # Oldest 5 should have been pruned, keeping i=5..14
        values = [r[2]["i"] for r in rows]
        assert values == list(range(5, 15))

    def test_prune_does_not_trigger_under_limit(self):
        """Buffer should not prune when under max_records."""
        buf = self._make_buffer(max_records=100)
        for i in range(50):
            buf.push("t", {"i": i})
        assert buf.count() == 50
