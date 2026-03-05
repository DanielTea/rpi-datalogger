"""Tests for CAN bus reader."""

import queue
from unittest.mock import MagicMock, patch

from datalogger.can_reader import CANReader, _NOISE_THRESHOLD


def _make_config(**overrides):
    config = MagicMock()
    config.can_interface = overrides.get("can_interface", "vcan0")
    config.device_id = overrides.get("device_id", "test-device")
    config.can_filter_ids = overrides.get("can_filter_ids", [])
    return config


def _make_msg(arb_id=0x123, data=b'\xDE\xAD\xBE\xEF', dlc=4,
              is_extended=False, is_remote=False, is_error=False,
              timestamp=1234567890.123):
    msg = MagicMock()
    msg.arbitration_id = arb_id
    msg.is_extended_id = is_extended
    msg.is_remote_frame = is_remote
    msg.is_error_frame = is_error
    msg.dlc = dlc
    msg.data = bytearray(data)
    msg.timestamp = timestamp
    return msg


def _make_bus():
    bus = MagicMock()
    bus.__enter__ = MagicMock(return_value=bus)
    bus.__exit__ = MagicMock(return_value=False)
    return bus


def _mono_two_windows(n_recv_w1):
    """Build monotonic values for two windows.

    Window 1: n_recv_w1 inner-loop iterations then deadline expires.
    Window 2: 1 recv call (which should set stop), then exits.

    Each inner iteration consumes 2 monotonic() calls:
      while time.monotonic() < deadline  (check)
      remaining = deadline - time.monotonic()  (calc)
    Plus 1 call for deadline calc per window, and 1 for the final
    while-check that exits the inner loop.
    """
    vals = [0.0]  # deadline = 1.0
    for _ in range(n_recv_w1):
        vals.extend([0.1, 0.2])  # while check + remaining
    vals.append(2.0)  # while check → 2.0 >= 1.0 → exit inner

    # Window 2
    vals.append(3.0)  # deadline = 4.0
    vals.extend([3.1, 3.2])  # while check + remaining → recv sets stop
    vals.append(3.3)  # while check → stop set → exit inner
    return iter(vals)


def _run_read_loop(reader, bus, recv_fn, mono_iter):
    bus.recv = recv_fn
    with patch('datalogger.can_reader.can.Bus', return_value=bus), \
         patch('datalogger.can_reader.time.monotonic', side_effect=mono_iter):
        reader._read_loop()


class TestCANReader:
    def test_stop_event(self):
        reader = CANReader(_make_config(), queue.Queue())
        reader.stop()
        assert reader._stop_event.is_set()

    def test_daemon_thread(self):
        reader = CANReader(_make_config(), queue.Queue())
        assert reader.daemon is True

    def test_thread_name(self):
        reader = CANReader(_make_config(), queue.Queue())
        assert reader.name == "CANReader"

    def test_record_format(self):
        """Enough frames in a window should produce a correctly-shaped record."""
        out_queue = queue.Queue()
        mock_msg = _make_msg()
        bus = _make_bus()
        reader = CANReader(_make_config(), out_queue)

        # Window 1: return _NOISE_THRESHOLD msgs, then 1 None (deadline expires)
        # Window 2: 1 recv call that sets stop
        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= _NOISE_THRESHOLD:
                return mock_msg
            if call_count[0] > _NOISE_THRESHOLD + 1:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn,
                       _mono_two_windows(_NOISE_THRESHOLD + 1))

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

    def test_noise_filtered(self):
        """Fewer than _NOISE_THRESHOLD frames should be silently dropped."""
        out_queue = queue.Queue()
        mock_msg = _make_msg()
        bus = _make_bus()
        reader = CANReader(_make_config(), out_queue)

        noise_count = _NOISE_THRESHOLD - 1
        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= noise_count:
                return mock_msg
            if call_count[0] > noise_count + 1:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn,
                       _mono_two_windows(noise_count + 1))

        assert out_queue.empty(), "Noise frames should not produce a record"

    def test_error_frames_excluded(self):
        """Error frames should not be counted toward the threshold."""
        out_queue = queue.Queue()
        error_msg = _make_msg(is_error=True)
        bus = _make_bus()
        reader = CANReader(_make_config(), out_queue)

        n_errors = _NOISE_THRESHOLD + 2
        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= n_errors:
                return error_msg
            if call_count[0] > n_errors + 1:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn,
                       _mono_two_windows(n_errors + 1))

        assert out_queue.empty(), "Error frames should be excluded"

    def test_filter_ids(self):
        """Only messages matching CAN_FILTER_IDS should be accepted."""
        out_queue = queue.Queue()
        bus = _make_bus()
        config = _make_config(can_filter_ids=[0x100, 0x200])
        reader = CANReader(config, out_queue)

        wanted_msg = _make_msg(arb_id=0x100)
        unwanted_msg = _make_msg(arb_id=0x999)

        # Window 1: _NOISE_THRESHOLD wanted + 3 unwanted, then None
        n_wanted = _NOISE_THRESHOLD
        n_unwanted = 3
        n_total = n_wanted + n_unwanted
        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= n_wanted:
                return wanted_msg
            if call_count[0] <= n_total:
                return unwanted_msg
            if call_count[0] > n_total + 1:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn,
                       _mono_two_windows(n_total + 1))

        assert not out_queue.empty()
        record = out_queue.get_nowait()
        assert record["arb_id"] == 0x100

    def test_filter_ids_below_threshold(self):
        """If filtered frames don't meet threshold, nothing is queued."""
        out_queue = queue.Queue()
        bus = _make_bus()
        config = _make_config(can_filter_ids=[0x100])
        reader = CANReader(config, out_queue)

        wanted_msg = _make_msg(arb_id=0x100)
        unwanted_msg = _make_msg(arb_id=0x999)

        # Only 2 matching, lots of non-matching → below threshold
        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= 2:
                return wanted_msg
            if call_count[0] <= 15:
                return unwanted_msg
            if call_count[0] > 16:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn, _mono_two_windows(16))

        assert out_queue.empty(), "Below-threshold filtered frames should be dropped"

    def test_queue_full_drops_frame(self):
        """When queue is full, the frame should be dropped without blocking."""
        small_queue = queue.Queue(maxsize=1)
        small_queue.put({"dummy": True})

        mock_msg = _make_msg(arb_id=0x456)
        bus = _make_bus()
        reader = CANReader(_make_config(), small_queue)

        call_count = [0]
        def recv_fn(timeout=1.0):
            call_count[0] += 1
            if call_count[0] <= _NOISE_THRESHOLD:
                return mock_msg
            if call_count[0] > _NOISE_THRESHOLD + 1:
                reader.stop()
            return None

        _run_read_loop(reader, bus, recv_fn,
                       _mono_two_windows(_NOISE_THRESHOLD + 1))

        # Queue should still have just the original item
        assert small_queue.qsize() == 1

    def test_backoff_on_crash(self):
        """run() should retry with exponential backoff on exception."""
        reader = CANReader(_make_config(), queue.Queue())
        attempts = []

        def fake_read_loop():
            attempts.append(1)
            if len(attempts) >= 2:
                reader.stop()
            raise RuntimeError("bus error")

        reader._read_loop = fake_read_loop
        with patch.object(reader._stop_event, 'wait'):
            reader.run()

        assert len(attempts) == 2
