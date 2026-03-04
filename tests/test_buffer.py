"""Tests for local SQLite buffer."""

import os
import tempfile
from datalogger.buffer import LocalBuffer


def test_push_and_peek():
    with tempfile.TemporaryDirectory() as tmpdir:
        buf = LocalBuffer(os.path.join(tmpdir, "test.db"))
        buf.push("can_frames", {"arb_id": 123, "data": "deadbeef"})
        buf.push("gps_readings", {"lat": 52.5, "lon": 13.4})

        assert buf.count() == 2
        items = buf.peek(limit=10)
        assert len(items) == 2
        assert items[0][1] == "can_frames"
        assert items[1][1] == "gps_readings"
        buf.close()


def test_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        buf = LocalBuffer(os.path.join(tmpdir, "test.db"))
        buf.push("can_frames", {"arb_id": 1})
        buf.push("can_frames", {"arb_id": 2})

        items = buf.peek(limit=10)
        buf.delete([items[0][0]])

        assert buf.count() == 1
        remaining = buf.peek(limit=10)
        assert remaining[0][2]["arb_id"] == 2
        buf.close()
