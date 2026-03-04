"""Tests for GPS NMEA parsing."""

from datalogger.gps_reader import GPSReader, _nmea_to_decimal


def test_nmea_to_decimal_north():
    assert round(_nmea_to_decimal("5232.352790", "N"), 6) == 52.539213


def test_nmea_to_decimal_east():
    assert round(_nmea_to_decimal("01324.503530", "E"), 6) == 13.408392


def test_nmea_to_decimal_south():
    result = _nmea_to_decimal("3350.000000", "S")
    assert result < 0


def test_nmea_to_decimal_west():
    result = _nmea_to_decimal("11830.000000", "W")
    assert result < 0


def test_parse_cgpsinfo_valid():
    response = "+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,0.0,"
    result = GPSReader._parse_cgpsinfo(response)
    assert result is not None
    assert round(result["latitude"], 4) == 52.5392
    assert round(result["longitude"], 4) == 13.4084
    assert result["altitude"] == 83.4
    assert result["speed"] == 0.0


def test_parse_cgpsinfo_no_fix():
    response = "+CGPSINFO: ,,,,,,,,"
    result = GPSReader._parse_cgpsinfo(response)
    assert result is None


def test_parse_cgpsinfo_empty():
    result = GPSReader._parse_cgpsinfo("")
    assert result is None
