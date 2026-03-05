"""Tests for the Supabase log handler."""

import logging
import queue
import time
from unittest.mock import patch

import pytest

from datalogger.log_handler import SupabaseLogHandler


@pytest.fixture
def log_queue():
    return queue.Queue(maxsize=100)


@pytest.fixture
def handler(log_queue):
    return SupabaseLogHandler(device_id="test-device", log_queue=log_queue)


@pytest.fixture
def test_logger(handler):
    lgr = logging.getLogger("datalogger.test_component")
    lgr.addHandler(handler)
    lgr.setLevel(logging.DEBUG)
    yield lgr
    lgr.removeHandler(handler)


class TestSupabaseLogHandler:
    def test_warning_enqueued(self, test_logger, log_queue):
        test_logger.warning("something went wrong")
        assert not log_queue.empty()
        record = log_queue.get_nowait()
        assert record["type"] == "log"
        assert record["device_id"] == "test-device"
        assert record["level"] == "WARNING"
        assert record["component"] == "test_component"
        assert record["message"] == "something went wrong"
        assert record["detail"] is None
        assert "timestamp" in record

    def test_error_enqueued(self, test_logger, log_queue):
        test_logger.error("fatal crash")
        assert not log_queue.empty()
        record = log_queue.get_nowait()
        assert record["level"] == "ERROR"
        assert record["message"] == "fatal crash"

    def test_info_ignored(self, test_logger, log_queue):
        test_logger.info("just info")
        assert log_queue.empty(), "INFO should not be captured"

    def test_debug_ignored(self, test_logger, log_queue):
        test_logger.debug("debug noise")
        assert log_queue.empty(), "DEBUG should not be captured"

    def test_component_extraction(self, handler, log_queue):
        """Component name should strip 'datalogger.' prefix."""
        lgr = logging.getLogger("datalogger.can_reader")
        lgr.addHandler(handler)
        lgr.setLevel(logging.WARNING)
        lgr.warning("bus error")
        lgr.removeHandler(handler)

        record = log_queue.get_nowait()
        assert record["component"] == "can_reader"

    def test_component_no_prefix(self, handler, log_queue):
        """Non-datalogger logger names pass through as-is."""
        lgr = logging.getLogger("some_other_module")
        lgr.addHandler(handler)
        lgr.setLevel(logging.WARNING)
        lgr.warning("oops")
        lgr.removeHandler(handler)

        record = log_queue.get_nowait()
        assert record["component"] == "some_other_module"

    def test_exception_detail(self, test_logger, log_queue):
        """Exception traceback should appear in the detail field."""
        try:
            raise ValueError("bad value")
        except ValueError:
            test_logger.exception("caught error")

        record = log_queue.get_nowait()
        assert record["level"] == "ERROR"
        assert record["message"] == "caught error"
        assert record["detail"] is not None
        assert "ValueError: bad value" in record["detail"]

    def test_rate_limiting(self, test_logger, log_queue):
        """Duplicate messages within 60s should be suppressed."""
        test_logger.warning("repeated error")
        test_logger.warning("repeated error")
        test_logger.warning("repeated error")

        assert log_queue.qsize() == 1, "Duplicates should be rate-limited"

    def test_different_messages_not_limited(self, test_logger, log_queue):
        """Different messages should not be rate-limited."""
        test_logger.warning("error A")
        test_logger.warning("error B")
        test_logger.warning("error C")

        assert log_queue.qsize() == 3

    def test_rate_limit_expires(self, handler, log_queue):
        """After rate limit window, same message should be allowed again."""
        lgr = logging.getLogger("datalogger.test_expire")
        lgr.addHandler(handler)
        lgr.setLevel(logging.WARNING)

        lgr.warning("flaky error")
        assert log_queue.qsize() == 1

        # Expire the rate limit by manipulating _recent timestamps
        for key in handler._recent:
            handler._recent[key] = time.monotonic() - 61.0

        lgr.warning("flaky error")
        assert log_queue.qsize() == 2

        lgr.removeHandler(handler)

    def test_queue_full_no_block(self, handler):
        """Handler should not block or crash when queue is full."""
        tiny_queue = queue.Queue(maxsize=1)
        handler.log_queue = tiny_queue
        tiny_queue.put({"dummy": True})  # fill it

        lgr = logging.getLogger("datalogger.test_full")
        lgr.addHandler(handler)
        lgr.setLevel(logging.WARNING)

        # Should not raise or block
        lgr.warning("this will be dropped")

        assert tiny_queue.qsize() == 1  # still just the dummy
        lgr.removeHandler(handler)

    def test_recent_cache_pruning(self, handler, log_queue):
        """_recent dict should be pruned when it grows too large."""
        lgr = logging.getLogger("datalogger.test_prune")
        lgr.addHandler(handler)
        lgr.setLevel(logging.WARNING)

        # Fill _recent with 201 stale entries
        now = time.monotonic()
        for i in range(201):
            handler._recent[f"stale:{i}"] = now - 120.0  # expired

        # Next emit triggers pruning
        lgr.warning("trigger prune")
        assert len(handler._recent) < 201
        lgr.removeHandler(handler)


class TestLogToRow:
    def test_transform(self):
        """Uploader._log_to_row should produce the correct dict shape."""
        from datalogger.uploader import Uploader

        record = {
            "type": "log",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "device_id": "rpi-001",
            "level": "ERROR",
            "component": "can_reader",
            "message": "bus crashed",
            "detail": "Traceback...",
        }
        row = Uploader._log_to_row(record)
        assert row == {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "device_id": "rpi-001",
            "level": "ERROR",
            "component": "can_reader",
            "message": "bus crashed",
            "detail": "Traceback...",
        }

    def test_transform_no_detail(self):
        from datalogger.uploader import Uploader

        record = {
            "type": "log",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "device_id": "rpi-001",
            "level": "WARNING",
            "component": "gps_reader",
            "message": "no fix",
        }
        row = Uploader._log_to_row(record)
        assert row["detail"] is None
