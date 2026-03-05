"""Tests for GPS NMEA parsing and GPSReader."""

import queue
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from datalogger.gps_reader import GPSReader, _nmea_to_decimal


# --- NMEA coordinate conversion ---


class TestNmeaToDecimal:
    def test_latitude_north(self):
        result = _nmea_to_decimal("5232.352790", "N")
        assert round(result, 6) == 52.539213

    def test_latitude_south(self):
        result = _nmea_to_decimal("3350.000000", "S")
        assert result < 0
        assert round(abs(result), 6) == 33.833333

    def test_longitude_east(self):
        result = _nmea_to_decimal("01324.503530", "E")
        assert round(result, 6) == 13.408392

    def test_longitude_west(self):
        result = _nmea_to_decimal("11830.000000", "W")
        assert result < 0
        assert round(abs(result), 6) == 118.500000

    def test_zero_coordinates(self):
        assert _nmea_to_decimal("0000.000000", "N") == 0.0
        assert _nmea_to_decimal("00000.000000", "E") == 0.0

    def test_max_latitude(self):
        result = _nmea_to_decimal("9000.000000", "N")
        assert result == 90.0

    def test_max_longitude(self):
        result = _nmea_to_decimal("18000.000000", "E")
        assert result == 180.0


# --- RMC parsing ---


class TestParseRmc:
    def test_valid_rmc(self):
        sentence = "$GPRMC,123519,A,4807.038000,N,01131.000000,E,022.4,084.4,230394,003.1,W*6A"
        result = GPSReader._parse_rmc(sentence)
        assert result is not None
        assert round(result["latitude"], 4) == 48.1173
        assert round(result["longitude"], 4) == 11.5167
        assert result["speed"] == round(22.4 * 1.852, 2)
        assert result["course"] == 84.4

    def test_gnrmc_variant(self):
        sentence = "$GNRMC,123519,A,5232.352790,N,01324.503530,E,0.0,,040326,,,A*7F"
        result = GPSReader._parse_rmc(sentence)
        assert result is not None
        assert round(result["latitude"], 4) == 52.5392

    def test_void_status(self):
        sentence = "$GPRMC,123519,V,,,,,,,230394,,,N*7F"
        result = GPSReader._parse_rmc(sentence)
        assert result is None

    def test_empty_speed_course(self):
        sentence = "$GPRMC,123519,A,4807.038000,N,01131.000000,E,,,230394,,,A*7F"
        result = GPSReader._parse_rmc(sentence)
        assert result is not None
        assert result["speed"] is None
        assert result["course"] is None

    def test_too_few_fields(self):
        sentence = "$GPRMC,123519,A,4807.038"
        result = GPSReader._parse_rmc(sentence)
        assert result is None

    def test_invalid_lat(self):
        sentence = "$GPRMC,123519,A,XXXX,N,01131.000000,E,0.0,,230394,,,A*7F"
        result = GPSReader._parse_rmc(sentence)
        assert result is None


# --- GGA altitude parsing ---


class TestParseGgaAltitude:
    def test_valid_gga(self):
        sentence = "$GPGGA,123519,4807.038000,N,01131.000000,E,1,08,0.9,545.4,M,47.0,M,,*47"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result == 545.4

    def test_gngga_variant(self):
        sentence = "$GNGGA,123519,5232.352790,N,01324.503530,E,1,12,0.8,83.4,M,47.0,M,,*47"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result == 83.4

    def test_no_fix_quality_zero(self):
        sentence = "$GPGGA,123519,,,,,0,00,,,M,,M,,*6A"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result is None

    def test_empty_altitude_field(self):
        sentence = "$GPGGA,123519,4807.038000,N,01131.000000,E,1,08,0.9,,M,47.0,M,,*47"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result is None

    def test_too_few_fields(self):
        sentence = "$GPGGA,123519,4807"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result is None

    def test_invalid_quality(self):
        sentence = "$GPGGA,123519,4807.038000,N,01131.000000,E,X,08,0.9,545.4,M,47.0,M,,*47"
        result = GPSReader._parse_gga_altitude(sentence)
        assert result is None


# --- CGPSINFO parsing (legacy) ---


class TestParseCgpsinfo:
    def test_valid_full_response(self):
        response = "+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,0.0,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert round(result["latitude"], 4) == 52.5392
        assert round(result["longitude"], 4) == 13.4084
        assert result["altitude"] == 83.4
        assert result["speed"] == 0.0
        assert result["course"] is None

    def test_valid_with_course(self):
        response = "+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,12.5,180.0"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert result["speed"] == 12.5
        assert result["course"] == 180.0

    def test_no_fix_empty_fields(self):
        response = "+CGPSINFO: ,,,,,,,,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is None

    def test_empty_string(self):
        assert GPSReader._parse_cgpsinfo("") is None

    def test_no_cgpsinfo_line(self):
        assert GPSReader._parse_cgpsinfo("OK\r\n") is None

    def test_multiline_response(self):
        response = "\r\n+CGPSINFO: 4807.038000,N,01131.000000,E,040326,092345.0,545.4,2.3,270.0\r\nOK\r\n"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert round(result["latitude"], 4) == 48.1173

    def test_truncated_response(self):
        assert GPSReader._parse_cgpsinfo("+CGPSINFO: 5232.352790,N,01324.503530") is None

    def test_invalid_numbers(self):
        assert GPSReader._parse_cgpsinfo("+CGPSINFO: XXXX,N,YYYY,E,040326,123725.0,abc,def,") is None

    def test_southern_western_coords(self):
        response = "+CGPSINFO: 3344.000000,S,05812.000000,W,040326,100000.0,25.0,0.0,"
        result = GPSReader._parse_cgpsinfo(response)
        assert result is not None
        assert result["latitude"] < 0
        assert result["longitude"] < 0


# --- GPSReader thread ---


class TestGPSReaderThread:
    def test_stop_event(self):
        config = MagicMock()
        gps = GPSReader(config, queue.Queue())
        gps.stop()
        assert gps._stop_event.is_set()

    def test_daemon_thread(self):
        config = MagicMock()
        gps = GPSReader(config, queue.Queue())
        assert gps.daemon is True

    def test_thread_name(self):
        config = MagicMock()
        gps = GPSReader(config, queue.Queue())
        assert gps.name == "GPSReader"


class TestGPSReaderReadLoop:
    """Test the NMEA _read_loop integration."""

    def _make_reader(self, lines, poll_interval=0.0):
        config = MagicMock()
        config.gps_serial_port = "/dev/null"
        config.gps_serial_baud = 115200
        config.gps_poll_interval = poll_interval
        config.device_id = "test-001"
        out_queue = queue.Queue()
        reader = GPSReader(config, out_queue)
        return reader, out_queue

    def test_rmc_produces_record(self):
        """A valid RMC sentence should produce a GPS record."""
        rmc = "$GPRMC,123519,A,4807.038000,N,01131.000000,E,022.4,084.4,230394,003.1,W*6A"
        reader, out_queue = self._make_reader([rmc])

        mock_serial = MagicMock()
        call_count = 0

        def readline_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (rmc + "\r\n").encode()
            reader.stop()
            return b""

        mock_serial.readline = readline_side_effect
        mock_serial.__enter__ = lambda s: s
        mock_serial.__exit__ = MagicMock(return_value=False)

        with patch("datalogger.gps_reader.serial.Serial", return_value=mock_serial):
            with patch("datalogger.gps_reader.time.monotonic", return_value=100.0):
                reader._read_loop()

        assert not out_queue.empty()
        record = out_queue.get_nowait()
        assert record["type"] == "gps"
        assert record["device_id"] == "test-001"
        assert round(record["latitude"], 4) == 48.1173
        assert record["speed"] == round(22.4 * 1.852, 2)

    def test_gga_adds_altitude(self):
        """GGA sentence after RMC should add altitude to record."""
        rmc = "$GPRMC,123519,A,4807.038000,N,01131.000000,E,0.0,,230394,,,A*7F"
        gga = "$GPGGA,123519,4807.038000,N,01131.000000,E,1,08,0.9,545.4,M,47.0,M,,*47"
        reader, out_queue = self._make_reader([rmc, gga])

        mock_serial = MagicMock()
        call_count = 0

        def readline_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (rmc + "\r\n").encode()
            if call_count == 2:
                return (gga + "\r\n").encode()
            reader.stop()
            return b""

        mock_serial.readline = readline_side_effect
        mock_serial.__enter__ = lambda s: s
        mock_serial.__exit__ = MagicMock(return_value=False)

        with patch("datalogger.gps_reader.serial.Serial", return_value=mock_serial):
            with patch("datalogger.gps_reader.time.monotonic", return_value=100.0):
                reader._read_loop()

        # Should have emitted after RMC, then again after GGA
        records = []
        while not out_queue.empty():
            records.append(out_queue.get_nowait())
        # At least the last record should have altitude
        last = records[-1]
        assert last["altitude"] == 545.4

    def test_void_rmc_no_fix(self):
        """Void RMC (status=V) should not produce a record."""
        rmc = "$GPRMC,123519,V,,,,,,,230394,,,N*7F"
        reader, out_queue = self._make_reader([rmc])

        mock_serial = MagicMock()
        call_count = 0

        def readline_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (rmc + "\r\n").encode()
            reader.stop()
            return b""

        mock_serial.readline = readline_side_effect
        mock_serial.__enter__ = lambda s: s
        mock_serial.__exit__ = MagicMock(return_value=False)

        with patch("datalogger.gps_reader.serial.Serial", return_value=mock_serial):
            reader._read_loop()

        assert out_queue.empty()

    def test_poll_interval_throttling(self):
        """Records should only emit at gps_poll_interval rate."""
        rmc = "$GPRMC,123519,A,4807.038000,N,01131.000000,E,0.0,,230394,,,A*7F"
        reader, out_queue = self._make_reader([rmc], poll_interval=10.0)

        mock_serial = MagicMock()
        call_count = 0

        def readline_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return (rmc + "\r\n").encode()
            reader.stop()
            return b""

        mock_serial.readline = readline_side_effect
        mock_serial.__enter__ = lambda s: s
        mock_serial.__exit__ = MagicMock(return_value=False)

        # monotonic() is called once per loop iteration (for the emit check).
        # readline 1 (RMC) -> monotonic returns 0.0 -> 0.0-0.0=0 < 10 -> no emit
        # readline 2 (RMC) -> monotonic returns 1.0 -> 1.0-0.0=1 < 10 -> no emit
        # readline 3 (RMC) -> monotonic returns 100.0 -> 100-0=100 >= 10 -> emit, last_emit=100
        # readline 4 -> stop() called, returns b"" -> empty, continue
        # stop_event is set -> exit loop
        mono_values = iter([0.0, 1.0, 100.0])

        with patch("datalogger.gps_reader.serial.Serial", return_value=mock_serial):
            with patch("datalogger.gps_reader.time.monotonic", side_effect=mono_values):
                reader._read_loop()

        records = []
        while not out_queue.empty():
            records.append(out_queue.get_nowait())
        assert len(records) == 1

    def test_serial_exception_raises(self):
        """SerialException should propagate to trigger backoff."""
        import serial as pyserial

        reader, out_queue = self._make_reader([])

        mock_serial = MagicMock()
        mock_serial.readline.side_effect = pyserial.SerialException("port gone")
        mock_serial.__enter__ = lambda s: s
        mock_serial.__exit__ = MagicMock(return_value=False)

        with patch("datalogger.gps_reader.serial.Serial", return_value=mock_serial):
            with pytest.raises(pyserial.SerialException):
                reader._read_loop()

    def test_backoff_on_crash(self):
        """run() should apply exponential backoff when _read_loop crashes."""
        reader, _ = self._make_reader([])
        crash_count = 0

        def fake_read_loop():
            nonlocal crash_count
            crash_count += 1
            if crash_count >= 2:
                reader.stop()
            raise RuntimeError("boom")

        reader._read_loop = fake_read_loop
        waits = []
        original_wait = reader._stop_event.wait

        def capture_wait(timeout=None):
            if timeout is not None:
                waits.append(timeout)
            return original_wait(0)

        reader._stop_event.wait = capture_wait
        reader.run()

        assert crash_count == 2
        assert waits[0] == 2.0  # _MIN_BACKOFF
        assert waits[1] == 4.0  # doubled
