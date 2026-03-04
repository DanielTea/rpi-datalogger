"""Tests for CAN bus reader."""

import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from datalogger.can_reader import CANReader


class TestCANReader:
    def test_stop_event(self):
        """CANReader should stop when stop() is called."""
        config = MagicMock()
        config.can_interface = "vcan0"
        reader = CANReader(config, queue.Queue())
        reader.stop()
        assert reader._stop_event.is_set()

    def test_daemon_thread(self):
        """CANReader should be a daemon thread."""
        config = MagicMock()
        reader = CANReader(config, queue.Queue())
        assert reader.daemon is True

    def test_thread_name(self):
        config = MagicMock()
        reader = CANReader(config, queue.Queue())
        assert reader.name == "CANReader"

    def test_record_format(self):
        """Verify the record dict structure produced by the reader."""
        config = MagicMock()
        config.can_interface = "vcan0"
        config.device_id = "test-device"
        out_queue = queue.Queue()

        # Create a mock CAN message
        mock_msg = MagicMock()
        mock_msg.arbitration_id = 0x123
        mock_msg.is_extended_id = False
        mock_msg.is_remote_frame = False
        mock_msg.dlc = 4
        mock_msg.data = bytearray(b'\xDE\xAD\xBE\xEF')
        mock_msg.timestamp = 1234567890.123

        mock_bus = MagicMock()
        mock_bus.__enter__ = MagicMock(return_value=mock_bus)
        mock_bus.__exit__ = MagicMock(return_value=False)

        reader = CANReader(config, out_queue)

        # recv: first call returns msg, second call (next window) stops reader
        recv_call = [0]
        def recv_side_effect(timeout=1.0):
            recv_call[0] += 1
            if recv_call[0] == 1:
                return mock_msg
            reader.stop()
            return None

        mock_bus.recv = recv_side_effect

        # Mock time.monotonic to control the 1-second deadline windows.
        # The _read_loop has nested loops that call monotonic() multiple times:
        #   deadline = time.monotonic() + 1.0
        #   while time.monotonic() < deadline and not stop_event:
        #       remaining = deadline - time.monotonic()
        #       ...recv()...
        #   while time.monotonic() < deadline and not stop_event:  (re-check)
        #
        # Window 1: recv returns mock_msg, deadline expires, record is queued
        # Window 2: recv calls stop(), loop exits
        mono_values = iter([
            0.0,    # 1: deadline = 0.0 + 1.0 = 1.0
            0.1,    # 2: inner while: 0.1 < 1.0 -> True
            0.2,    # 3: remaining = 1.0 - 0.2 = 0.8 -> recv returns mock_msg
            2.0,    # 4: inner while: 2.0 < 1.0 -> False -> exit inner loop
            # latest=mock_msg, stop_event=False -> queues record
            2.0,    # 5: deadline = 2.0 + 1.0 = 3.0
            2.1,    # 6: inner while: 2.1 < 3.0 -> True
            2.2,    # 7: remaining = 3.0 - 2.2 = 0.8 -> recv calls stop()
            4.0,    # 8: inner while: 4.0 < 3.0 -> False -> exit inner loop
            # latest=None, stop_event=True -> continue -> outer while exits
        ])

        with patch('datalogger.can_reader.can.Bus', return_value=mock_bus), \
             patch('datalogger.can_reader.time.monotonic', side_effect=mono_values):
            reader._read_loop()

        # Check record was queued
        assert not out_queue.empty()
        record = out_queue.get_nowait()
        assert record["type"] == "can"
        assert record["device_id"] == "test-device"
        assert record["arb_id"] == 0x123
        assert record["is_extended"] is False
        assert record["is_remote"] is False
        assert record["dlc"] == 4
        assert record["data"] == b'\xDE\xAD\xBE\xEF'
        assert record["bus_time"] == 1234567890.123
        assert "timestamp" in record

    def test_queue_full_drops_frame(self):
        """When queue is full, frames should be dropped without blocking."""
        config = MagicMock()
        config.can_interface = "vcan0"
        config.device_id = "test"

        small_queue = queue.Queue(maxsize=1)
        # Fill the queue
        small_queue.put({"dummy": True})

        mock_msg = MagicMock()
        mock_msg.arbitration_id = 0x456
        mock_msg.is_extended_id = False
        mock_msg.is_remote_frame = False
        mock_msg.dlc = 0
        mock_msg.data = bytearray()
        mock_msg.timestamp = 0

        mock_bus = MagicMock()
        mock_bus.__enter__ = MagicMock(return_value=mock_bus)
        mock_bus.__exit__ = MagicMock(return_value=False)

        reader = CANReader(config, small_queue)

        call_count = [0]
        def recv_side_effect(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= 1:
                return mock_msg
            reader.stop()
            return None

        mock_bus.recv = recv_side_effect

        with patch('datalogger.can_reader.can.Bus', return_value=mock_bus):
            # Should not raise, just drop the frame
            reader._read_loop()

        # Queue should still have just the original item
        assert small_queue.qsize() == 1
