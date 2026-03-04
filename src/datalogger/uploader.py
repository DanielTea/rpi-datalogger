import logging
import queue
import threading
import time

from supabase import create_client, Client

logger = logging.getLogger(__name__)


class Uploader(threading.Thread):
    """Consumes CAN and GPS queues and uploads to Supabase.

    Falls back to local SQLite buffer when Supabase is unreachable.
    """

    def __init__(self, config, can_queue: queue.Queue, gps_queue: queue.Queue, buffer):
        super().__init__(name="Uploader", daemon=True)
        self.config = config
        self.can_queue = can_queue
        self.gps_queue = gps_queue
        self.buffer = buffer
        self._stop_event = threading.Event()
        self.supabase: Client | None = None

    def stop(self):
        self._stop_event.set()

    def _connect(self) -> bool:
        if not self.config.supabase_url or not self.config.supabase_key:
            logger.warning("Supabase URL/key not configured, buffering locally")
            return False
        try:
            self.supabase = create_client(
                self.config.supabase_url, self.config.supabase_key
            )
            logger.info("Connected to Supabase")
            return True
        except Exception:
            logger.exception("Failed to connect to Supabase")
            self.supabase = None
            return False

    def run(self):
        self._connect()
        while not self._stop_event.is_set():
            try:
                # 1. Drain any buffered records first (offline recovery)
                self._flush_buffer()

                # 2. Process CAN queue (higher priority, higher volume)
                self._drain_queue(self.can_queue, "can_frames", self._can_to_row)

                # 3. Process GPS queue
                self._drain_queue(self.gps_queue, "gps_readings", self._gps_to_row)

                # Small sleep to prevent busy-waiting when queues are empty
                time.sleep(0.05)

            except Exception:
                logger.exception("Uploader loop error")
                time.sleep(self.config.upload_retry_interval)

    def _drain_queue(self, q: queue.Queue, table: str, transform):
        """Drain all items from a queue and upload them."""
        while not q.empty():
            try:
                record = q.get_nowait()
            except queue.Empty:
                break
            row = transform(record)
            if not self._upload(table, row):
                self.buffer.push(table, row)

    def _upload(self, table: str, row: dict) -> bool:
        """Upload a single row to Supabase. Returns True on success."""
        if self.supabase is None:
            if not self._connect():
                return False
        try:
            self.supabase.table(table).insert(row).execute()
            return True
        except Exception:
            logger.warning("Upload to %s failed, buffering locally", table)
            self.supabase = None  # force reconnect on next attempt
            return False

    def _flush_buffer(self):
        """Upload buffered records from SQLite (FIFO order)."""
        count = self.buffer.count()
        if count == 0:
            return
        logger.info("Flushing %d buffered records", count)
        pending = self.buffer.peek(limit=50)
        uploaded_ids = []
        for record_id, table, payload in pending:
            if self._upload(table, payload):
                uploaded_ids.append(record_id)
            else:
                break  # network still down, stop trying
        self.buffer.delete(uploaded_ids)

    @staticmethod
    def _can_to_row(record: dict) -> dict:
        """Transform a CAN record dict to a Supabase row."""
        return {
            "timestamp": record["timestamp"],
            "device_id": record["device_id"],
            "arb_id": record["arb_id"],
            "is_extended": record["is_extended"],
            "is_remote": record["is_remote"],
            "dlc": record["dlc"],
            "data": "\\x" + record["data"].hex(),
            "bus_time": record["bus_time"],
        }

    @staticmethod
    def _gps_to_row(record: dict) -> dict:
        """Transform a GPS record dict to a Supabase row."""
        return {
            "timestamp": record["timestamp"],
            "device_id": record["device_id"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
            "altitude": record.get("altitude"),
            "speed": record.get("speed"),
            "course": record.get("course"),
            "raw_response": record.get("raw_response"),
        }
