"""Tests for startup logging and system status collection."""

import queue
from unittest.mock import MagicMock, patch, mock_open

import pytest

from datalogger.__main__ import collect_system_status, push_startup_logs


class TestCollectSystemStatus:
    @patch("datalogger.__main__._run_cmd")
    def test_cpu_temp(self, mock_cmd):
        mock_cmd.side_effect = lambda cmd: {
            ("vcgencmd", "measure_temp"): "temp=45.0'C",
            ("vcgencmd", "get_throttled"): None,
            ("uname", "-r"): None,
        }.get(tuple(cmd))
        status = collect_system_status()
        assert status["cpu_temp"] == "45.0°C"

    @patch("datalogger.__main__._run_cmd")
    def test_throttle_ok(self, mock_cmd):
        mock_cmd.side_effect = lambda cmd: {
            ("vcgencmd", "measure_temp"): None,
            ("vcgencmd", "get_throttled"): "throttled=0x0",
            ("uname", "-r"): None,
        }.get(tuple(cmd))
        status = collect_system_status()
        assert "OK" in status["throttled"]

    @patch("datalogger.__main__._run_cmd")
    def test_throttle_undervoltage(self, mock_cmd):
        mock_cmd.side_effect = lambda cmd: {
            ("vcgencmd", "measure_temp"): None,
            ("vcgencmd", "get_throttled"): "throttled=0x50005",
            ("uname", "-r"): None,
        }.get(tuple(cmd))
        status = collect_system_status()
        assert "UNDERVOLTAGE NOW" in status["throttled"]
        assert "undervoltage occurred" in status["throttled"]

    @patch("datalogger.__main__._run_cmd", return_value=None)
    def test_no_vcgencmd(self, mock_cmd):
        """On non-Pi systems, status should still return (possibly empty)."""
        status = collect_system_status()
        assert "cpu_temp" not in status
        assert "throttled" not in status

    @patch("datalogger.__main__._run_cmd", return_value=None)
    @patch("builtins.open", mock_open(read_data="MemTotal:        1024000 kB\nMemAvailable:     512000 kB\n"))
    def test_memory(self, mock_cmd):
        status = collect_system_status()
        assert "500MB free" in status["memory"]
        assert "1000MB total" in status["memory"]

    @patch("datalogger.__main__._run_cmd")
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_memory_not_available(self, mock_open_fn, mock_cmd):
        mock_cmd.return_value = None
        status = collect_system_status()
        assert "memory" not in status

    @patch("datalogger.__main__._run_cmd")
    def test_kernel(self, mock_cmd):
        mock_cmd.side_effect = lambda cmd: {
            ("vcgencmd", "measure_temp"): None,
            ("vcgencmd", "get_throttled"): None,
            ("uname", "-r"): "6.6.31+rpt-rpi-v7",
        }.get(tuple(cmd))
        status = collect_system_status()
        assert status["kernel"] == "6.6.31+rpt-rpi-v7"


class TestPushStartupLogs:
    def _make_config(self, **overrides):
        config = MagicMock()
        config.device_id = "rpi-001"
        config.can_interface = "can0"
        config.gps_serial_port = "/dev/sim7600-nmea"
        config.supabase_url = "https://test.supabase.co"
        config.can_filter_ids = overrides.get("can_filter_ids", [])
        return config

    @patch("datalogger.__main__.collect_system_status", return_value={})
    def test_startup_record_pushed(self, mock_status):
        config = self._make_config()
        log_q = queue.Queue()
        push_startup_logs(config, log_q)
        records = []
        while not log_q.empty():
            records.append(log_q.get_nowait())
        assert len(records) == 2  # startup config + system status
        assert records[0]["level"] == "INFO"
        assert records[0]["component"] == "system"
        assert "Datalogger started" in records[0]["message"]
        assert "device=rpi-001" in records[0]["message"]
        assert "CAN=can0" in records[0]["message"]

    @patch("datalogger.__main__.collect_system_status", return_value={})
    def test_supabase_not_set(self, mock_status):
        config = self._make_config()
        config.supabase_url = ""
        log_q = queue.Queue()
        push_startup_logs(config, log_q)
        record = log_q.get_nowait()
        assert "supabase=NO" in record["message"]

    @patch("datalogger.__main__.collect_system_status", return_value={})
    def test_can_filter_ids_included(self, mock_status):
        config = self._make_config(can_filter_ids=[0x7DF, 0x7E8])
        log_q = queue.Queue()
        push_startup_logs(config, log_q)
        record = log_q.get_nowait()
        assert "0x7DF" in record["message"]
        assert "0x7E8" in record["message"]

    @patch("datalogger.__main__.collect_system_status")
    def test_system_status_in_detail(self, mock_status):
        mock_status.return_value = {
            "cpu_temp": "42.0°C",
            "throttled": "0x0 — OK",
            "memory": "300MB free / 900MB total",
        }
        config = self._make_config()
        log_q = queue.Queue()
        push_startup_logs(config, log_q)
        # Skip startup config record
        log_q.get_nowait()
        status_record = log_q.get_nowait()
        assert status_record["message"] == "System status at startup"
        assert "cpu_temp: 42.0°C" in status_record["detail"]
        assert "throttled: 0x0" in status_record["detail"]
        assert "memory: 300MB" in status_record["detail"]

    @patch("datalogger.__main__.collect_system_status", return_value={})
    def test_queue_full_no_crash(self, mock_status):
        """push_startup_logs should not block or crash on a full queue."""
        config = self._make_config()
        log_q = queue.Queue(maxsize=1)
        log_q.put({"dummy": True})  # fill it
        push_startup_logs(config, log_q)  # should not raise
        assert log_q.qsize() == 1

    @patch("datalogger.__main__.collect_system_status", return_value={})
    def test_records_have_timestamp(self, mock_status):
        config = self._make_config()
        log_q = queue.Queue()
        push_startup_logs(config, log_q)
        record = log_q.get_nowait()
        assert "timestamp" in record
        assert record["type"] == "log"
        assert record["device_id"] == "rpi-001"
