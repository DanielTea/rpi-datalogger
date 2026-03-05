import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone

import serial

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0
_PORT_WAIT_INTERVAL = 1.0
_PORT_WAIT_MAX = 30.0


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
    """Reads GPS data from SIM7600E-H NMEA serial port.

    Parses standard NMEA sentences (GPRMC, GPGGA) streamed
    continuously from the modem's dedicated NMEA port.
    This avoids sharing the AT command port with PPP.
    """

    def __init__(self, config, out_queue: queue.Queue):
        super().__init__(name="GPSReader", daemon=True)
        self.config = config
        self.out_queue = out_queue
        self._stop_event = threading.Event()
        self._had_successful_read = False

    def stop(self):
        self._stop_event.set()

    def run(self):
        backoff = _MIN_BACKOFF
        while not self._stop_event.is_set():
            try:
                self._read_loop()
                backoff = _MIN_BACKOFF
            except Exception:
                if self._had_successful_read:
                    # Was working, likely a transient USB glitch — reset backoff
                    backoff = _MIN_BACKOFF
                    logger.warning(
                        "GPS reader lost connection after successful reads, "
                        "restarting in %.0fs",
                        backoff,
                    )
                else:
                    logger.error(
                        "GPS reader crashed, restarting in %.0fs", backoff
                    )
                self._wait_for_port()
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    def _wait_for_port(self):
        """Block until the serial port device file exists (USB re-enumeration)."""
        port = self.config.gps_serial_port
        if os.path.exists(port):
            return
        logger.info("Waiting for %s to reappear...", port)
        waited = 0.0
        while not self._stop_event.is_set() and waited < _PORT_WAIT_MAX:
            self._stop_event.wait(_PORT_WAIT_INTERVAL)
            waited += _PORT_WAIT_INTERVAL
            if os.path.exists(port):
                logger.info("%s reappeared after %.0fs", port, waited)
                return
        if not os.path.exists(port):
            logger.warning(
                "%s did not reappear after %.0fs", port, _PORT_WAIT_MAX
            )

    def _read_loop(self):
        self._had_successful_read = False
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
            logger.info("GPS serial opened (NMEA mode)")
            last_emit = 0.0
            lat = lon = alt = speed = course = None
            has_fix = False

            while not self._stop_event.is_set():
                try:
                    line = ser.readline().decode("ascii", errors="replace").strip()
                    if not line:
                        continue
                    self._had_successful_read = True

                    # Parse GPRMC for lat, lon, speed, course
                    if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                        parsed = self._parse_rmc(line)
                        if parsed:
                            lat = parsed["latitude"]
                            lon = parsed["longitude"]
                            speed = parsed.get("speed")
                            course = parsed.get("course")
                            has_fix = True

                    # Parse GPGGA for altitude
                    elif line.startswith("$GPGGA") or line.startswith("$GNGGA"):
                        parsed_alt = self._parse_gga_altitude(line)
                        if parsed_alt is not None:
                            alt = parsed_alt

                    # Emit at configured interval
                    now = time.monotonic()
                    if has_fix and (now - last_emit) >= self.config.gps_poll_interval:
                        record = {
                            "type": "gps",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "device_id": self.config.device_id,
                            "latitude": lat,
                            "longitude": lon,
                            "altitude": alt,
                            "speed": speed,
                            "course": course,
                            "raw_response": line,
                        }
                        try:
                            self.out_queue.put_nowait(record)
                        except queue.Full:
                            logger.warning("GPS queue full, dropping reading")
                        last_emit = now

                except serial.SerialException:
                    logger.warning("GPS serial error, port may have disconnected")
                    raise
                except Exception:
                    logger.exception("GPS parse error")

    @staticmethod
    def _parse_rmc(sentence: str) -> dict | None:
        """Parse $GPRMC / $GNRMC sentence.

        Format: $GPRMC,time,status,lat,N/S,lon,E/W,speed,course,date,...
        """
        try:
            parts = sentence.split(",")
            if len(parts) < 10:
                return None
            status = parts[2]
            if status != "A":  # A=active, V=void
                return None
            lat = _nmea_to_decimal(parts[3], parts[4])
            lon = _nmea_to_decimal(parts[5], parts[6])
            speed_knots = float(parts[7]) if parts[7] else None
            # Convert knots to km/h
            speed = round(speed_knots * 1.852, 2) if speed_knots is not None else None
            course = float(parts[8]) if parts[8] else None
            return {
                "latitude": lat,
                "longitude": lon,
                "speed": speed,
                "course": course,
            }
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_gga_altitude(sentence: str) -> float | None:
        """Extract altitude from $GPGGA / $GNGGA sentence.

        Format: $GPGGA,time,lat,N/S,lon,E/W,quality,sats,hdop,alt,M,...
        """
        try:
            parts = sentence.split(",")
            if len(parts) < 10:
                return None
            quality = int(parts[6]) if parts[6] else 0
            if quality == 0:
                return None
            return float(parts[9]) if parts[9] else None
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_cgpsinfo(response: str) -> dict | None:
        """Parse AT+CGPSINFO response (legacy, kept for compatibility)."""
        for line in response.splitlines():
            if "+CGPSINFO:" not in line:
                continue
            raw = line.split("+CGPSINFO:")[1].strip()
            if not raw or raw.startswith(","):
                return None
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
