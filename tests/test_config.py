"""Tests for configuration loading."""

import os
from unittest.mock import patch

import pytest

from datalogger.config import Config


class TestConfig:
    def test_defaults(self):
        """Config should have sensible defaults."""
        with patch.dict(os.environ, {}, clear=True):
            config = Config()
            assert config.can_interface == "can0"
            assert config.can_bitrate == 500000
            assert config.gps_serial_port == "/dev/ttyUSB2"
            assert config.gps_serial_baud == 115200
            assert config.gps_poll_interval == 1.0
            assert config.upload_queue_maxsize == 1000
            assert config.upload_retry_interval == 5.0
            assert config.device_id == "rpi-001"
            assert config.supabase_url == ""
            assert config.supabase_key == ""

    def test_env_override(self):
        """Environment variables should override defaults."""
        env = {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_KEY": "test-key-123",
            "DEVICE_ID": "rpi-test",
            "CAN_INTERFACE": "vcan0",
            "CAN_BITRATE": "250000",
            "GPS_SERIAL_PORT": "/dev/ttyUSB5",
            "GPS_SERIAL_BAUD": "9600",
            "GPS_POLL_INTERVAL": "2.5",
            "BUFFER_DB_PATH": "/tmp/test.db",
            "UPLOAD_QUEUE_MAXSIZE": "500",
            "UPLOAD_RETRY_INTERVAL": "10.0",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config()
            assert config.supabase_url == "https://test.supabase.co"
            assert config.supabase_key == "test-key-123"
            assert config.device_id == "rpi-test"
            assert config.can_interface == "vcan0"
            assert config.can_bitrate == 250000
            assert config.gps_serial_port == "/dev/ttyUSB5"
            assert config.gps_serial_baud == 9600
            assert config.gps_poll_interval == 2.5
            assert config.buffer_db_path == "/tmp/test.db"
            assert config.upload_queue_maxsize == 500
            assert config.upload_retry_interval == 10.0

    def test_frozen(self):
        """Config should be immutable (frozen dataclass)."""
        config = Config()
        with pytest.raises(AttributeError):
            config.device_id = "changed"

    def test_numeric_env_parsing(self):
        """Numeric environment variables should parse correctly."""
        env = {
            "CAN_BITRATE": "1000000",
            "GPS_POLL_INTERVAL": "0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config()
            assert config.can_bitrate == 1000000
            assert config.gps_poll_interval == 0.1
