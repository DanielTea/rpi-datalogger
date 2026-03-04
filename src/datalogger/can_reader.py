import logging
import queue
import threading
import time
from datetime import datetime, timezone

import can

logger = logging.getLogger(__name__)


class CANReader(threading.Thread):
    """Reads CAN frames from socketcan and places them on a queue."""

    def __init__(self, config, out_queue: queue.Queue):
        super().__init__(name="CANReader", daemon=True)
        self.config = config
        self.out_queue = out_queue
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._read_loop()
            except Exception:
                logger.exception("CAN reader crashed, restarting in 2s")
                time.sleep(2)

    def _read_loop(self):
        logger.info("Opening CAN bus on %s", self.config.can_interface)
        with can.Bus(
            channel=self.config.can_interface, interface="socketcan"
        ) as bus:
            logger.info("CAN bus opened successfully")
            while not self._stop_event.is_set():
                msg = bus.recv(timeout=1.0)
                if msg is None:
                    continue

                record = {
                    "type": "can",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "device_id": self.config.device_id,
                    "arb_id": msg.arbitration_id,
                    "is_extended": msg.is_extended_id,
                    "is_remote": msg.is_remote_frame,
                    "dlc": msg.dlc,
                    "data": bytes(msg.data),
                    "bus_time": msg.timestamp,
                }
                try:
                    self.out_queue.put_nowait(record)
                except queue.Full:
                    logger.warning(
                        "CAN queue full, dropping frame arb_id=0x%X",
                        msg.arbitration_id,
                    )
