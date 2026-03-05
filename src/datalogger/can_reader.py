import logging
import queue
import threading
import time
from datetime import datetime, timezone

import can

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0
_NOISE_THRESHOLD = 5  # need at least this many frames/sec to consider bus active


class CANReader(threading.Thread):
    """Reads CAN frames from socketcan, sampling one frame per second.

    Filters out floating-bus noise: when the CAN bus is disconnected,
    the transceiver picks up sporadic random frames. Real CAN traffic
    produces many frames per second consistently. We require at least
    _NOISE_THRESHOLD frames in a 1-second window before forwarding.

    Optional: set CAN_FILTER_IDS in .env to only accept specific
    arbitration IDs (comma-separated hex, e.g. "7DF,7E8,100").
    """

    def __init__(self, config, out_queue: queue.Queue):
        super().__init__(name="CANReader", daemon=True)
        self.config = config
        self.out_queue = out_queue
        self._stop_event = threading.Event()
        self._filter_ids = set(config.can_filter_ids) if config.can_filter_ids else None
        if self._filter_ids:
            logger.info("CAN ID filter: %s", ", ".join("0x" + format(x, "X") for x in self._filter_ids))

    def stop(self):
        self._stop_event.set()

    def run(self):
        backoff = _MIN_BACKOFF
        while not self._stop_event.is_set():
            try:
                self._read_loop()
                backoff = _MIN_BACKOFF
            except Exception:
                logger.exception(
                    "CAN reader crashed, restarting in %.0fs", backoff
                )
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    def _read_loop(self):
        logger.info("Opening CAN bus on %s (1 Hz sampling)", self.config.can_interface)
        with can.Bus(
            channel=self.config.can_interface, interface="socketcan"
        ) as bus:
            logger.info("CAN bus opened successfully")
            while not self._stop_event.is_set():
                frames = []
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and not self._stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    msg = bus.recv(timeout=remaining)
                    if msg is not None and not msg.is_error_frame:
                        if self._filter_ids is None or msg.arbitration_id in self._filter_ids:
                            frames.append(msg)

                if self._stop_event.is_set() or not frames:
                    continue

                # Noise filter: floating bus produces sporadic frames,
                # real traffic produces many. Skip if below threshold.
                if len(frames) < _NOISE_THRESHOLD:
                    continue

                latest = frames[-1]
                record = {
                    "type": "can",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "device_id": self.config.device_id,
                    "arb_id": latest.arbitration_id,
                    "is_extended": latest.is_extended_id,
                    "is_remote": latest.is_remote_frame,
                    "dlc": latest.dlc,
                    "data": bytes(latest.data),
                    "bus_time": latest.timestamp,
                }
                try:
                    self.out_queue.put_nowait(record)
                except queue.Full:
                    logger.warning(
                        "CAN queue full, dropping frame arb_id=0x%X",
                        latest.arbitration_id,
                    )
