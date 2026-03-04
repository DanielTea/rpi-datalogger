"""Entry point for the RPi datalogger.

Usage: python -m datalogger
"""

import logging
import queue
import signal
import threading

from datalogger.buffer import LocalBuffer
from datalogger.can_reader import CANReader
from datalogger.config import Config
from datalogger.gps_reader import GPSReader
from datalogger.logger import setup_logging
from datalogger.uploader import Uploader

logger = logging.getLogger(__name__)


def main():
    setup_logging()
    config = Config()

    logger.info("Starting rpi-datalogger (device=%s)", config.device_id)
    logger.info("CAN interface: %s", config.can_interface)
    logger.info("GPS port: %s", config.gps_serial_port)
    logger.info("Supabase URL: %s", config.supabase_url[:30] + "..." if config.supabase_url else "NOT SET")

    # Create shared queues
    can_queue = queue.Queue(maxsize=config.upload_queue_maxsize)
    gps_queue = queue.Queue(maxsize=100)

    # Create local buffer for offline resilience
    buffer = LocalBuffer(config.buffer_db_path)
    buffered = buffer.count()
    if buffered > 0:
        logger.info("Found %d buffered records from previous session", buffered)

    # Create worker threads
    can_reader = CANReader(config, can_queue)
    gps_reader = GPSReader(config, gps_queue)
    uploader = Uploader(config, can_queue, gps_queue, buffer)

    # Start all threads
    can_reader.start()
    gps_reader.start()
    uploader.start()

    logger.info("All threads started")

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
