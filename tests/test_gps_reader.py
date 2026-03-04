"""Tests for GPS NMEA parsing and GPSReader."""

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from datalogger.gps_reader import GPSReader, _nmea_to_decimal


# --- NMEA coordinate conversion ---

class TestNmeaToDecimal:
    def test_latitude_north(self):
        """Berlin latitude: 52°32.352790'N = 52.539213°"""
        result = _nmea_to_decimal("5232.352790", "N")
        assert round(result, 6) == 52.539213

    def test_latitude_south(self):
        """Southern hemisphere should be negative."""
        result = _nmea_to_decimal("3350.000000", "S")
        assert result < 0
        assert round(abs(result), 6) == 33.833333

    def test_longitude_east(self):
        """Berlin longitude: 13°24.503530'E = 13.408392°"""
        result = _nmea_to_decimal("01324.503530", "E")
        assert round(result, 6) == 13.408392

    def test_longitude_west(self):
        """Western hemisphere should be negative."""
        result = _nmea_to_decimal("11830.000000", "W")
        assert result < 0
        assert round(abs(result), 6) == 118.500000

    def test_zero_coordinates(self):
        """Null Island (0,0)."""
        assert _nmea_to_decimal("0000.000000", "N") == 0.0
        assert _nmea_to_decimal("00000.000000", "E") == 0.0

    def test_max_latitude(self):
        """North pole: 90°00'N."""
        result = _nmea_to_decimal("9000.000000", "N")
        assert result == 90.0

    def test_max_longitude(self):
        """Antimeridian: 180°00'E."""
        result = _nmea_to_decimal("18000.000000", "E")
        assert result == 180.0


# --- CGPSINFO parsing ---

class TestParseCgpsinfo:
    def test_valid_full_response(self):
        """Parse a complete CGPSINFO response with all fields."""
        response = "+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,0.0,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert round(result["latitude"], 4) == 52.5392
        assert round(result["longitude"], 4) == 13.4084
        assert result["altitude"] == 83.4
        assert result["speed"] == 0.0
        assert result["course"] is None  # trailing comma = empty

    def test_valid_with_course(self):
        """Parse response that includes course."""
        response = "+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,12.5,180.0"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert result["speed"] == 12.5
        assert result["course"] == 180.0

    def test_no_fix_empty_fields(self):
        """No GPS fix returns commas only."""
        response = "+CGPSINFO: ,,,,,,,,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is None

    def test_empty_string(self):
        result = GPSReader._parse_cgpsinfo("")
        assert result is None

    def test_no_cgpsinfo_line(self):
        """Response without CGPSINFO marker."""
        result = GPSReader._parse_cgpsinfo("OK\r\n")
        assert result is None

    def test_multiline_response(self):
        """CGPSINFO embedded in multiline AT response."""
        response = (
            "\r\n"
            "+CGPSINFO: 4807.038000,N,01131.000000,E,040326,092345.0,545.4,2.3,270.0\r\n"
            "\r\n"
            "OK\r\n"
        )
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert round(result["latitude"], 4) == 48.1173

    def test_truncated_response(self):
        """Response with fewer than 8 fields."""
        response = "+CGPSINFO: 5232.352790,N,01324.503530"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is None

    def test_invalid_numbers(self):
        """Non-numeric values in fields."""
        response = "+CGPSINFO: XXXX,N,YYYY,E,040326,123725.0,abc,def,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is None

    def test_southern_western_coords(self):
        """Parse coordinates in southern and western hemisphere."""
        response = "+CGPSINFO: 3344.000000,S,05812.000000,W,040326,100000.0,25.0,0.0,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert result["latitude"] < 0
        assert result["longitude"] < 0


# --- GPSReader thread ---

class TestGPSReaderThread:
    def test_stop_event(self):
        """GPSReader should stop when stop() is called."""
        config = MagicMock()
        config.gps_serial_port = "/dev/null"
        config.gps_serial_baud = 115200
        config.gps_poll_interval = 0.1

        gps = GPSReader(config, queue.Queue())
        gps.stop()
        assert gps._stop_event.is_set()

    def test_daemon_thread(self):
        """GPSReader should be a daemon thread."""
        config = MagicMock()
        gps = GPSReader(config, queue.Queue())
        assert gps.daemon is True

    def test_thread_name(self):
        config = MagicMock()
        gps = GPSReader(config, queue.Queue())
        assert gps.name == "GPSReader"
