import logging
import queue
import struct
import threading
import time
from datetime import datetime, timezone

import can

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0

# OBD-II PIDs to poll: (pid, name, decode_func)
# Service 01 PIDs — sent as 7DF#0201XX00000000
_OBD_PIDS = [
    (0x0C, "rpm",          lambda a, b: ((a << 8) | b) / 4.0),
    (0x0D, "speed_kmh",    lambda a, b: a),
    (0x05, "coolant_temp", lambda a, b: a - 40),
    (0x11, "throttle_pct", lambda a, b: round(a * 100.0 / 255.0, 1)),
    (0x0F, "intake_temp",  lambda a, b: a - 40),
    (0x04, "engine_load",  lambda a, b: round(a * 100.0 / 255.0, 1)),
]

# OBD broadcast request/response IDs
_OBD_REQUEST_ID = 0x7DF
_OBD_RESPONSE_ID = 0x7E8

# Wake-up: send supported-PIDs request until gateway responds
_WAKE_TIMEOUT = 10.0
_WAKE_INTERVAL = 0.5


class CANReader(threading.Thread):
    """Polls OBD-II PIDs over CAN bus at 1 Hz.

    On VW vehicles the OBD CAN lines are behind a gateway that only
    activates after receiving a diagnostic request. This reader sends
    a wake-up sequence on startup, then continuously polls standard
    OBD-II PIDs and pushes decoded values to the output queue.
    """

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
                backoff = _MIN_BACKOFF
            except Exception:
                logger.exception(
                    "CAN reader crashed, restarting in %.0fs", backoff
                )
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    def _wake_gateway(self, bus: can.Bus) -> bool:
        """Send OBD requests until the gateway responds or timeout."""
        logger.info("Waking up OBD gateway...")
        wake_msg = can.Message(
            arbitration_id=_OBD_REQUEST_ID,
            data=b'\x02\x01\x00\x00\x00\x00\x00\x00',
            is_extended_id=False,
        )
        deadline = time.monotonic() + _WAKE_TIMEOUT
        attempt = 0
        while time.monotonic() < deadline and not self._stop_event.is_set():
            attempt += 1
            try:
                bus.send(wake_msg)
            except can.CanError:
                time.sleep(_WAKE_INTERVAL)
                continue

            # Listen briefly for a response
            listen_until = time.monotonic() + _WAKE_INTERVAL
            while time.monotonic() < listen_until:
                msg = bus.recv(timeout=listen_until - time.monotonic())
                if msg and msg.arbitration_id == _OBD_RESPONSE_ID:
                    logger.info(
                        "Gateway responded after %d attempts", attempt
                    )
                    return True
        logger.warning("Gateway did not respond after %.0fs", _WAKE_TIMEOUT)
        return False

    def _request_pid(self, bus: can.Bus, pid: int) -> can.Message | None:
        """Send a single OBD PID request and wait for the response."""
        msg = can.Message(
            arbitration_id=_OBD_REQUEST_ID,
            data=bytes([0x02, 0x01, pid, 0x00, 0x00, 0x00, 0x00, 0x00]),
            is_extended_id=False,
        )
        try:
            bus.send(msg)
        except can.CanError:
            return None

        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            resp = bus.recv(timeout=deadline - time.monotonic())
            if resp is None:
                break
            if (resp.arbitration_id == _OBD_RESPONSE_ID
                    and len(resp.data) >= 4
                    and resp.data[1] == 0x41
                    and resp.data[2] == pid):
                return resp
        return None

    def _read_loop(self):
        logger.info("Opening CAN bus on %s", self.config.can_interface)
        with can.Bus(
            channel=self.config.can_interface, interface="socketcan"
        ) as bus:
            logger.info("CAN bus opened successfully")

            if not self._wake_gateway(bus):
                logger.warning("Continuing anyway — will retry PIDs")

            while not self._stop_event.is_set():
                obd_data = {}
                for pid, name, decode in _OBD_PIDS:
                    if self._stop_event.is_set():
                        break
                    resp = self._request_pid(bus, pid)
                    if resp is not None:
                        a = resp.data[3]
                        b = resp.data[4] if len(resp.data) > 4 else 0
                        obd_data[name] = decode(a, b)

                if self._stop_event.is_set() or not obd_data:
                    # No responses — try waking again
                    if not obd_data:
                        self._wake_gateway(bus)
                    continue

                record = {
                    "type": "obd",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "device_id": self.config.device_id,
                    **obd_data,
                }
                try:
                    self.out_queue.put_nowait(record)
                except queue.Full:
                    logger.warning("CAN queue full, dropping OBD record")

                # ~1 Hz polling: sleep for remainder of the second
                self._stop_event.wait(0.5)
