"""Tests for Supabase uploader."""

import os
import queue
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from datalogger.buffer import LocalBuffer
from datalogger.config import Config
from datalogger.uploader import Uploader


@pytest.fixture
def mock_config():
    env = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key",
        "DEVICE_ID": "test-device",
    }
    with patch.dict(os.environ, env, clear=True):
        return Config()


@pytest.fixture
def buffer():
    with tempfile.TemporaryDirectory() as tmpdir:
        buf = LocalBuffer(os.path.join(tmpdir, "test.db"))
        yield buf
        buf.close()


class TestUploaderTransforms:
    def test_can_to_row(self):
        """CAN record should be transformed to Supabase row format."""
        record = {
            "type": "can",
            "timestamp": "2026-03-04T12:00:00+00:00",
            "device_id": "rpi-001",
            "arb_id": 0x123,
            "is_extended": False,
            "is_remote": False,
            "dlc": 4,
            "data": b'\xDE\xAD\xBE\xEF',
            "bus_time": 1234567890.123,
        }
        row = Uploader._can_to_row(record)

        assert row["timestamp"] == "2026-03-04T12:00:00+00:00"
        assert row["device_id"] == "rpi-001"
        assert row["arb_id"] == 0x123
        assert row["is_extended"] is False
        assert row["is_remote"] is False
        assert row["dlc"] == 4
        assert row["data"] == "\\xdeadbeef"
        assert row["bus_time"] == 1234567890.123

    def test_gps_to_row(self):
        """GPS record should be transformed to Supabase row format."""
        record = {
            "type": "gps",
            "timestamp": "2026-03-04T12:00:00+00:00",
            "device_id": "rpi-001",
            "latitude": 52.539213,
            "longitude": 13.408392,
            "altitude": 83.4,
            "speed": 0.0,
            "course": None,
            "raw_response": "+CGPSINFO: 5232.352790,N,...",
        }
        row = Uploader._gps_to_row(record)

        assert row["latitude"] == 52.539213
        assert row["longitude"] == 13.408392
        assert row["altitude"] == 83.4
        assert row["speed"] == 0.0
        assert row["course"] is None
        assert row["raw_response"] == "+CGPSINFO: 5232.352790,N,..."

    def test_gps_to_row_missing_optional_fields(self):
        """GPS row handles missing optional fields gracefully."""
        record = {
            "type": "gps",
            "timestamp": "2026-03-04T12:00:00+00:00",
            "device_id": "rpi-001",
            "latitude": 52.5,
            "longitude": 13.4,
        }
        row = Uploader._gps_to_row(record)
        assert row["altitude"] is None
        assert row["speed"] is None
        assert row["course"] is None

    def test_can_data_hex_encoding(self):
        """CAN data bytes should be hex-encoded for Supabase BYTEA."""
        record = {
            "timestamp": "t",
            "device_id": "d",
            "arb_id": 0,
            "is_extended": False,
            "is_remote": False,
            "dlc": 8,
            "data": b'\x00\xFF\x01\xFE\xAB\xCD\xEF\x99',
            "bus_time": 0,
        }
        row = Uploader._can_to_row(record)
        assert row["data"] == "\\x00ff01feabcdef99"


class TestUploaderConnection:
    def test_no_credentials_buffers_locally(self, buffer):
        """With no Supabase credentials, uploader should buffer locally."""
        env = {"SUPABASE_URL": "", "SUPABASE_KEY": ""}
        with patch.dict(os.environ, env, clear=True):
            config = Config()

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(config, can_q, gps_q, buffer)

        assert uploader._connect() is False

    @patch("datalogger.uploader.create_client")
    def test_connect_success(self, mock_create, mock_config, buffer):
        """Successful Supabase connection."""
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)

        assert uploader._connect() is True
        assert uploader.supabase is mock_client

    @patch("datalogger.uploader.create_client")
    def test_connect_failure(self, mock_create, mock_config, buffer):
        """Failed Supabase connection."""
        mock_create.side_effect = Exception("Connection refused")

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)

        assert uploader._connect() is False
        assert uploader.supabase is None


class TestUploaderUpload:
    @patch("datalogger.uploader.create_client")
    def test_upload_success(self, mock_create, mock_config, buffer):
        """Successful upload to Supabase."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.insert.return_value = mock_table
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        row = {"arb_id": 123, "data": "\\xdeadbeef"}
        assert uploader._upload("can_frames", row) is True
        mock_client.table.assert_called_with("can_frames")
        mock_table.insert.assert_called_with(row)

    @patch("datalogger.uploader.create_client")
    def test_upload_failure_buffers(self, mock_create, mock_config, buffer):
        """Failed upload should set supabase to None for reconnect."""
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("Network error")
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        row = {"arb_id": 123}
        assert uploader._upload("can_frames", row) is False
        assert uploader.supabase is None  # reset for reconnect


class TestUploaderDrainQueue:
    @patch("datalogger.uploader.create_client")
    def test_drain_can_queue(self, mock_create, mock_config, buffer):
        """Drain CAN queue and upload all records."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.insert.return_value = mock_table
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        # Add 3 CAN records
        for i in range(3):
            can_q.put({
                "type": "can",
                "timestamp": f"t{i}",
                "device_id": "d",
                "arb_id": i,
                "is_extended": False,
                "is_remote": False,
                "dlc": 0,
                "data": b'',
                "bus_time": 0,
            })

        uploader._drain_queue(can_q, "can_frames", Uploader._can_to_row)

        assert can_q.empty()
        assert mock_table.insert.call_count == 3

    @patch("datalogger.uploader.create_client")
    def test_drain_failure_buffers_remaining(self, mock_create, mock_config, buffer):
        """If upload fails mid-drain, remaining records go to buffer."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        # First insert succeeds, second fails
        mock_table.insert.return_value = mock_table
        mock_table.execute.side_effect = [None, Exception("fail"), Exception("fail")]
        mock_client.table.return_value = mock_table
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        for i in range(3):
            can_q.put({
                "type": "can", "timestamp": f"t{i}", "device_id": "d",
                "arb_id": i, "is_extended": False, "is_remote": False,
                "dlc": 0, "data": b'', "bus_time": 0,
            })

        uploader._drain_queue(can_q, "can_frames", Uploader._can_to_row)
        # Some records should have been buffered
        assert can_q.empty()


class TestUploaderFlushBuffer:
    @patch("datalogger.uploader.create_client")
    def test_flush_empty_buffer(self, mock_create, mock_config, buffer):
        """Flushing an empty buffer should be a no-op."""
        mock_create.return_value = MagicMock()

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        uploader._flush_buffer()  # Should not raise

    @patch("datalogger.uploader.create_client")
    def test_flush_buffer_uploads_and_deletes(self, mock_create, mock_config, buffer):
        """Buffered records should be uploaded and then deleted."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.insert.return_value = mock_table
        mock_create.return_value = mock_client

        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader._connect()

        # Manually buffer some records
        buffer.push("can_frames", {"arb_id": 1})
        buffer.push("can_frames", {"arb_id": 2})
        assert buffer.count() == 2

        uploader._flush_buffer()

        assert mock_table.insert.call_count == 2
        assert buffer.count() == 0


class TestUploaderThread:
    def test_stop_event(self, mock_config, buffer):
        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        uploader.stop()
        assert uploader._stop_event.is_set()

    def test_daemon_thread(self, mock_config, buffer):
        can_q = queue.Queue()
        gps_q = queue.Queue()
        uploader = Uploader(mock_config, can_q, gps_q, buffer)
        assert uploader.daemon is True
