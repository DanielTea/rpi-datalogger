"""Tests for local SQLite buffer."""

import json
import os
import tempfile

import pytest

from datalogger.buffer import LocalBuffer


@pytest.fixture
def buffer():
    """Create a temporary buffer for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        buf = LocalBuffer(os.path.join(tmpdir, "test.db"))
        yield buf
        buf.close()


class TestLocalBuffer:
    def test_push_and_count(self, buffer):
        """Push records and verify count."""
        assert buffer.count() == 0
        buffer.push("can_frames", {"arb_id": 123})
        assert buffer.count() == 1
        buffer.push("can_frames", {"arb_id": 456})
        assert buffer.count() == 2

    def test_push_and_peek(self, buffer):
        """Peek returns records in FIFO order."""
        buffer.push("can_frames", {"arb_id": 1})
        buffer.push("gps_readings", {"lat": 52.5})
        buffer.push("can_frames", {"arb_id": 2})

        items = buffer.peek(limit=10)
        assert len(items) == 3
        assert items[0][1] == "can_frames"
        assert items[0][2]["arb_id"] == 1
        assert items[1][1] == "gps_readings"
        assert items[2][2]["arb_id"] == 2

    def test_peek_limit(self, buffer):
        """Peek respects the limit parameter."""
        for i in range(10):
            buffer.push("can_frames", {"arb_id": i})

        items = buffer.peek(limit=3)
        assert len(items) == 3
        assert items[0][2]["arb_id"] == 0
        assert items[2][2]["arb_id"] == 2

    def test_peek_does_not_remove(self, buffer):
        """Peek should not remove records."""
        buffer.push("can_frames", {"arb_id": 1})
        buffer.peek(limit=10)
        assert buffer.count() == 1

    def test_delete_by_ids(self, buffer):
        """Delete specific records by ID."""
        buffer.push("can_frames", {"arb_id": 1})
        buffer.push("can_frames", {"arb_id": 2})
        buffer.push("can_frames", {"arb_id": 3})

        items = buffer.peek(limit=10)
        # Delete first and last
        buffer.delete([items[0][0], items[2][0]])

        assert buffer.count() == 1
        remaining = buffer.peek(limit=10)
        assert remaining[0][2]["arb_id"] == 2

    def test_delete_empty_list(self, buffer):
        """Delete with empty list should not error."""
        buffer.push("can_frames", {"arb_id": 1})
        buffer.delete([])
        assert buffer.count() == 1

    def test_delete_nonexistent_ids(self, buffer):
        """Delete with non-existent IDs should not error."""
        buffer.push("can_frames", {"arb_id": 1})
        buffer.delete([999, 1000])
        assert buffer.count() == 1

    def test_preserves_complex_data(self, buffer):
        """Buffer preserves nested dicts, lists, and special types."""
        record = {
            "timestamp": "2026-03-04T12:00:00+00:00",
            "data": "\\xDEADBEEF",
            "nested": {"key": "value"},
            "list": [1, 2, 3],
            "null_field": None,
            "bool_field": True,
            "float_field": 3.14159,
        }
        buffer.push("can_frames", record)
        items = buffer.peek(limit=1)
        assert items[0][2] == record

    def test_creates_directory(self):
        """Buffer creates parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = os.path.join(tmpdir, "a", "b", "c", "buffer.db")
            buf = LocalBuffer(deep_path)
            buf.push("test", {"key": "value"})
            assert buf.count() == 1
            buf.close()

    def test_fifo_order(self, buffer):
        """Records come out in insertion order."""
        for i in range(100):
            buffer.push("can_frames", {"seq": i})

        items = buffer.peek(limit=100)
        for idx, (_, _, payload) in enumerate(items):
            assert payload["seq"] == idx

    def test_mixed_tables(self, buffer):
        """Different table names are stored and returned correctly."""
        buffer.push("can_frames", {"type": "can"})
        buffer.push("gps_readings", {"type": "gps"})
        buffer.push("can_frames", {"type": "can2"})

        items = buffer.peek(limit=10)
        tables = [item[1] for item in items]
        assert tables == ["can_frames", "gps_readings", "can_frames"]

    def test_large_batch(self, buffer):
        """Handle a large number of records."""
        for i in range(1000):
            buffer.push("can_frames", {"arb_id": i})
        assert buffer.count() == 1000

        items = buffer.peek(limit=500)
        assert len(items) == 500

        ids = [item[0] for item in items]
        buffer.delete(ids)
        assert buffer.count() == 500
