import logging
import queue
import threading
import time
from datetime import datetime, timezone

import can

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0


class CANReader(threading.Thread):
    """Reads CAN frames from socketcan, sampling one frame per second."""

    def __init__(self, config, out_queue: queue.Queue):
        super().__init__(name="CANReader", daemon=True)
        self.config = config
        self.out_queue = out_queue
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        backoff = _MIN_BACKOFF
        while not self._stop_event.is_set():
            try:
                self._read_loop()
                backoff = _MIN_BACKOFF  # reset on clean exit
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
                latest = None
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and not self._stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    msg = bus.recv(timeout=remaining)
                    if msg is not None:
                        latest = msg

                if latest is None or self._stop_event.is_set():
                    continue

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
