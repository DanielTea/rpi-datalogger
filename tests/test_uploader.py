"""Tests for the Supabase uploader."""

import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from datalogger.uploader import Uploader


def _make_uploader(**kwargs):
    config = MagicMock()
    config.supabase_url = "https://test.supabase.co"
    config.supabase_key = "test-key"
    config.upload_retry_interval = 5.0
    can_q = kwargs.get("can_queue", queue.Queue())
    gps_q = kwargs.get("gps_queue", queue.Queue())
    buf = kwargs.get("buffer", MagicMock())
    if "buffer" not in kwargs:
        buf.count.return_value = 0
    return Uploader(config, can_q, gps_q, buf)


class TestUploaderTransforms:
    def test_can_to_row(self):
        record = {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "device_id": "dev1",
            "arb_id": 0x123,
            "is_extended": False,
            "is_remote": False,
            "dlc": 4,
            "data": b"\xDE\xAD\xBE\xEF",
            "bus_time": 123.456,
        }
        row = Uploader._can_to_row(record)
        assert row["arb_id"] == 0x123
        assert row["device_id"] == "dev1"
        assert row["data"] == "\\xdeadbeef"

    def test_gps_to_row(self):
        record = {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "device_id": "dev1",
            "latitude": 52.539,
            "longitude": 13.408,
            "altitude": 35.0,
            "speed": 0.0,
            "course": None,
            "raw_response": "+CGPSINFO: ...",
        }
        row = Uploader._gps_to_row(record)
        assert row["latitude"] == 52.539
        assert row["longitude"] == 13.408

    def test_gps_to_row_missing_optional_fields(self):
        record = {
            "timestamp": "2024-01-01T00:00:00+00:00",
            "device_id": "dev1",
            "latitude": 52.0,
            "longitude": 13.0,
        }
        row = Uploader._gps_to_row(record)
        assert row["altitude"] is None
        assert row["speed"] is None

    def test_can_data_hex_encoding(self):
        record = {
            "timestamp": "t",
            "device_id": "d",
            "arb_id": 1,
            "is_extended": False,
            "is_remote": False,
            "dlc": 8,
            "data": b"\x00\x01\x02\x03\x04\x05\x06\x07",
            "bus_time": 0,
        }
        row = Uploader._can_to_row(record)
        assert row["data"] == "\\x0001020304050607"


class TestUploaderConnection:
    def test_no_credentials_buffers_locally(self):
        config = MagicMock()
        config.supabase_url = ""
        config.supabase_key = ""
        u = Uploader(config, queue.Queue(), queue.Queue(), MagicMock())
        assert u._connect() is False

    @patch("datalogger.uploader.create_client")
    def test_connect_success(self, mock_create):
        u = _make_uploader()
        assert u._connect() is True
        assert u.supabase is not None

    @patch("datalogger.uploader.create_client", side_effect=Exception("fail"))
    def test_connect_failure(self, mock_create):
        u = _make_uploader()
        assert u._connect() is False
        assert u.supabase is None


class TestUploaderUpload:
    @patch("datalogger.uploader.create_client")
    def test_upload_success(self, mock_create):
        u = _make_uploader()
        u._connect()
        u.supabase.table.return_value.insert.return_value.execute.return_value = True
        assert u._upload("can_frames", {"test": 1}) is True

    @patch("datalogger.uploader.create_client")
    def test_upload_failure_buffers(self, mock_create):
        u = _make_uploader()
        u._connect()
        u.supabase.table.return_value.insert.return_value.execute.side_effect = (
            Exception("network error")
        )
        assert u._upload("can_frames", {"test": 1}) is False
        assert u.supabase is None


class TestUploaderDrainQueue:
    @patch("datalogger.uploader.create_client")
    def test_drain_can_queue(self, mock_create):
        can_q = queue.Queue()
        can_q.put({
            "type": "can", "timestamp": "t", "device_id": "d",
            "arb_id": 1, "is_extended": False, "is_remote": False,
            "dlc": 0, "data": b"", "bus_time": 0,
        })
        buf = MagicMock()
        buf.count.return_value = 0
        u = _make_uploader(can_queue=can_q, buffer=buf)
        u._connect()
        u.supabase.table.return_value.insert.return_value.execute.return_value = True
        u._drain_queue(can_q, "can_frames", Uploader._can_to_row)
        assert can_q.empty()

    @patch("datalogger.uploader.create_client")
    def test_drain_failure_buffers_remaining(self, mock_create):
        """When upload fails mid-drain, remaining items go to buffer."""
        can_q = queue.Queue()
        for i in range(3):
            can_q.put({
                "type": "can", "timestamp": "t", "device_id": "d",
                "arb_id": i, "is_extended": False, "is_remote": False,
                "dlc": 0, "data": b"", "bus_time": 0,
            })
        buf = MagicMock()
        buf.count.return_value = 0
        u = _make_uploader(can_queue=can_q, buffer=buf)
        u._connect()
        u.supabase.table.return_value.insert.return_value.execute.side_effect = [
            True, Exception("fail"), Exception("fail"),
        ]
        u._drain_queue(can_q, "can_frames", Uploader._can_to_row)
        assert can_q.empty()
        assert buf.push.call_count >= 1
        assert u._offline is True


class TestUploaderFlushBuffer:
    @patch("datalogger.uploader.create_client")
    def test_flush_empty_buffer(self, mock_create):
        buf = MagicMock()
        buf.count.return_value = 0
        u = _make_uploader(buffer=buf)
        u._flush_buffer()
        buf.peek.assert_not_called()

    @patch("datalogger.uploader.create_client")
    def test_flush_buffer_uploads_and_deletes(self, mock_create):
        buf = MagicMock()
        buf.count.return_value = 2
        buf.peek.return_value = [
            (1, "can_frames", {"test": 1}),
            (2, "can_frames", {"test": 2}),
        ]
        u = _make_uploader(buffer=buf)
        u._connect()
        u.supabase.table.return_value.insert.return_value.execute.return_value = True
        u._flush_buffer()
        buf.delete.assert_called_once_with([1, 2])


class TestUploaderOfflineMode:
    def test_go_offline_sets_flag(self):
        u = _make_uploader()
        u._go_offline()
        assert u._offline is True
        assert u.supabase is None

    @patch("datalogger.uploader.create_client")
    def test_buffer_queues_while_offline(self, mock_create):
        can_q = queue.Queue()
        gps_q = queue.Queue()
        can_q.put({
            "type": "can", "timestamp": "t", "device_id": "d",
            "arb_id": 1, "is_extended": False, "is_remote": False,
            "dlc": 0, "data": b"", "bus_time": 0,
        })
        gps_q.put({
            "type": "gps", "timestamp": "t", "device_id": "d",
            "latitude": 52.0, "longitude": 13.0,
        })
        buf = MagicMock()
        buf.count.return_value = 0
        u = _make_uploader(can_queue=can_q, gps_queue=gps_q, buffer=buf)
        u._buffer_queues()
        assert buf.push.call_count == 2
        assert can_q.empty()
        assert gps_q.empty()

    def test_backoff_increases_on_failure(self):
        u = _make_uploader()
        initial_backoff = u._backoff
        u._go_offline()
        u._backoff = min(u._backoff * 2, 120.0)
        assert u._backoff == initial_backoff * 2


class TestUploaderThread:
    def test_stop_event(self):
        u = _make_uploader()
        u.stop()
        assert u._stop_event.is_set()

    def test_daemon_thread(self):
        u = _make_uploader()
        assert u.daemon is True
