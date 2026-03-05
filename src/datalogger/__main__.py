"""Entry point for the RPi datalogger.

Usage: python -m datalogger
"""

import logging
import os
import queue
import signal
import subprocess
import threading
from datetime import datetime, timezone

from datalogger.buffer import LocalBuffer
from datalogger.can_reader import CANReader
from datalogger.config import Config
from datalogger.gps_reader import GPSReader
from datalogger.log_handler import SupabaseLogHandler
from datalogger.logger import setup_logging
from datalogger.uploader import Uploader

logger = logging.getLogger(__name__)


def _run_cmd(cmd: list[str]) -> str | None:
    """Run a command and return stdout, or None on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def collect_system_status() -> dict[str, str]:
    """Collect Raspberry Pi system status (temp, throttle, memory, disk, uptime)."""
    status: dict[str, str] = {}

    # CPU temperature
    val = _run_cmd(["vcgencmd", "measure_temp"])
    if val:
        status["cpu_temp"] = val.replace("temp=", "").replace("'C", "°C")

    # Throttle state
    val = _run_cmd(["vcgencmd", "get_throttled"])
    if val:
        raw = val.split("=")[-1]
        flags = int(raw, 16)
        parts = [raw]
        if flags & 0x1:
            parts.append("UNDERVOLTAGE NOW")
        if flags & 0x2:
            parts.append("ARM FREQ CAPPED")
        if flags & 0x4:
            parts.append("THROTTLED NOW")
        if flags & 0x50000:
            parts.append("undervoltage occurred")
        if flags == 0:
            parts.append("OK")
        status["throttled"] = " — ".join(parts)

    # Memory
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    k, v = line.split(":")[0], int(line.split()[1])
                    mem[k] = v
        if "MemTotal" in mem and "MemAvailable" in mem:
            total = mem["MemTotal"] // 1024
            avail = mem["MemAvailable"] // 1024
            status["memory"] = f"{avail}MB free / {total}MB total"
    except Exception:
        pass

    # Disk usage
    try:
        st = os.statvfs("/")
        total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
        free_gb = (st.f_bfree * st.f_frsize) / (1024**3)
        status["disk"] = f"{free_gb:.1f}GB free / {total_gb:.1f}GB total"
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        h, m = int(secs // 3600), int((secs % 3600) // 60)
        status["uptime"] = f"{h}h {m}m"
    except Exception:
        pass

    # Kernel
    val = _run_cmd(["uname", "-r"])
    if val:
        status["kernel"] = val

    return status


def push_startup_logs(config, log_queue: queue.Queue):
    """Push startup config and system status to the log queue.

    These bypass the WARNING-level handler filter so that INFO-level
    startup records reach the device_logs table.
    """
    now = datetime.now(timezone.utc).isoformat()

    def _push(message: str, detail: str | None = None):
        try:
            log_queue.put_nowait({
                "type": "log",
                "timestamp": now,
                "device_id": config.device_id,
                "level": "INFO",
                "component": "system",
                "message": message,
                "detail": detail,
            })
        except queue.Full:
            pass

    # Startup config summary
    parts = [
        f"device={config.device_id}",
        f"CAN={config.can_interface}",
        f"GPS={config.gps_serial_port}",
        f"supabase={'yes' if config.supabase_url else 'NO'}",
    ]
    if config.can_filter_ids:
        ids = ",".join("0x" + format(x, "X") for x in config.can_filter_ids)
        parts.append(f"CAN_filters={ids}")
    _push(f"Datalogger started — {', '.join(parts)}")

    # System status
    status = collect_system_status()
    if status:
        detail = "\n".join(f"{k}: {v}" for k, v in status.items())
        _push("System status at startup", detail)
    else:
        _push("System status at startup", "Could not collect (non-Pi environment?)")


def main():
    setup_logging()
    config = Config()

    logger.info("Starting rpi-datalogger (device=%s)", config.device_id)
    logger.info("CAN interface: %s", config.can_interface)
    logger.info("GPS port: %s", config.gps_serial_port)
    logger.info("Supabase URL: %s", config.supabase_url[:30] + "..." if config.supabase_url else "NOT SET")
    if config.can_filter_ids:
        logger.info("CAN filter IDs: %s", ", ".join("0x" + format(x, "X") for x in config.can_filter_ids))

    # Create shared queues
    can_queue = queue.Queue(maxsize=config.upload_queue_maxsize)
    gps_queue = queue.Queue(maxsize=100)
    log_queue = queue.Queue(maxsize=100)

    # Attach log handler that forwards WARNING+ to Supabase
    log_handler = SupabaseLogHandler(config.device_id, log_queue)
    logging.getLogger("datalogger").addHandler(log_handler)

    # Create local buffer for offline resilience
    buffer = LocalBuffer(config.buffer_db_path)
    buffered = buffer.count()
    if buffered > 0:
        logger.info("Found %d buffered records from previous session", buffered)

    # Create worker threads
    can_reader = CANReader(config, can_queue)
    gps_reader = GPSReader(config, gps_queue)
    uploader = Uploader(config, can_queue, gps_queue, buffer, log_queue)

    # Start all threads
    can_reader.start()
    gps_reader.start()
    uploader.start()

    logger.info("All threads started")

    # Push startup info to Supabase device_logs (bypasses WARNING filter)
    push_startup_logs(config, log_queue)

    # Handle SIGTERM/SIGINT for clean shutdown
    shutdown_event = threading.Event()

    def shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        can_reader.stop()
        gps_reader.stop()
        uploader.stop()
        shutdown_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Block main thread until shutdown signal
    shutdown_event.wait()

    # Wait for threads to finish
    can_reader.join(timeout=5)
    gps_reader.join(timeout=5)
    uploader.join(timeout=5)

    buffer.close()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
