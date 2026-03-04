import logging
import queue
import threading
import time
from datetime import datetime, timezone

import serial

logger = logging.getLogger(__name__)


def _nmea_to_decimal(raw_value: str, direction: str) -> float:
    """Convert NMEA ddmm.mmmmmm to decimal degrees."""
    if direction in ("N", "S"):
        degrees = float(raw_value[:2])
        minutes = float(raw_value[2:])
    else:
        degrees = float(raw_value[:3])
        minutes = float(raw_value[3:])
    decimal = degrees + minutes / 60.0
    if direction in ("S", "W"):
        decimal = -decimal
    return round(decimal, 8)


class GPSReader(threading.Thread):
    """Reads GPS data from SIM7600E-H via AT commands."""

    def __init__(self, config, out_queue: queue.Queue):
        super().__init__(name="GPSReader", daemon=True)
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
                logger.exception("GPS reader crashed, restarting in 5s")
                time.sleep(5)

    def _read_loop(self):
        logger.info(
            "Opening GPS serial on %s @ %d",
            self.config.gps_serial_port,
            self.config.gps_serial_baud,
        )
        with serial.Serial(
            self.config.gps_serial_port,
            self.config.gps_serial_baud,
            timeout=2,
        ) as ser:
            logger.info("GPS serial opened")
            while not self._stop_event.is_set():
                # Flush input buffer before sending command
                ser.reset_input_buffer()
                ser.write(b"AT+CGPSINFO\r\n")
                time.sleep(0.3)

                response = ser.read(ser.in_waiting or 256).decode(
                    "ascii", errors="replace"
                )

                parsed = self._parse_cgpsinfo(response)
                if parsed:
                    parsed["type"] = "gps"
                    parsed["timestamp"] = datetime.now(timezone.utc).isoformat()
                    parsed["device_id"] = self.config.device_id
                    parsed["raw_response"] = response.strip()
                    try:
                        self.out_queue.put_nowait(parsed)
                    except queue.Full:
                        logger.warning("GPS queue full, dropping reading")

                self._stop_event.wait(self.config.gps_poll_interval)

    @staticmethod
    def _parse_cgpsinfo(response: str) -> dict | None:
        """Parse AT+CGPSINFO response.

        Format: +CGPSINFO: lat,N/S,lon,E/W,date,time,alt,speed,course
        Example: +CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,0.0,
        """
        for line in response.splitlines():
            if "+CGPSINFO:" not in line:
                continue
            raw = line.split("+CGPSINFO:")[1].strip()
            if not raw or raw.startswith(","):
                return None  # No fix
            parts = raw.split(",")
            if len(parts) < 8:
                return None
            try:
                lat = _nmea_to_decimal(parts[0], parts[1])
                lon = _nmea_to_decimal(parts[2], parts[3])
                altitude = float(parts[6]) if parts[6] else None
                speed = float(parts[7]) if parts[7] else None
                course = (
                    float(parts[8])
                    if len(parts) > 8 and parts[8]
                    else None
                )
                return {
                    "latitude": lat,
                    "longitude": lon,
                    "altitude": altitude,
                    "speed": speed,
                    "course": course,
                }
            except (ValueError, IndexError):
                return None
        return None
