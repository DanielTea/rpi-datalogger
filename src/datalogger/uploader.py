import logging
import queue
import threading
import time

from supabase import create_client, Client

logger = logging.getLogger(__name__)

_MIN_BACKOFF = 5.0
_MAX_BACKOFF = 120.0


class Uploader(threading.Thread):
    """Consumes CAN and GPS queues and uploads to Supabase per-frame.

    Falls back to local SQLite buffer when Supabase is unreachable.
    Uses exponential backoff when uploads fail to avoid tight loops.
    """

    def __init__(self, config, can_queue: queue.Queue, gps_queue: queue.Queue,
                 buffer, log_queue: queue.Queue | None = None):
        super().__init__(name="Uploader", daemon=True)
        self.config = config
        self.can_queue = can_queue
        self.gps_queue = gps_queue
        self.log_queue = log_queue
        self.buffer = buffer
        self._stop_event = threading.Event()
        self.supabase: Client | None = None
        self._backoff = _MIN_BACKOFF
        self._offline = False

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
                if self._offline:
                    # Backoff: buffer incoming data, don't attempt uploads
                    self._buffer_queues()
                    logger.info(
                        "Offline — retrying in %.0fs (buffered=%d)",
                        self._backoff, self.buffer.count(),
                    )
                    self._stop_event.wait(self._backoff)
                    # Try to reconnect
                    if self._connect() and self._test_connection():
                        logger.info("Back online, resuming uploads")
                        self._offline = False
                        self._backoff = _MIN_BACKOFF
                    else:
                        self._backoff = min(self._backoff * 2, _MAX_BACKOFF)
                    continue

                self._flush_buffer()
                self._drain_queue(self.can_queue, "obd_readings", self._obd_to_row)
                self._drain_queue(self.gps_queue, "gps_readings", self._gps_to_row)
                self._drain_logs()
                self._stop_event.wait(0.05)
            except Exception:
                logger.exception("Uploader loop error")
                self._go_offline()

    def _go_offline(self):
        """Enter offline mode with backoff."""
        self._offline = True
        self.supabase = None

    def _test_connection(self) -> bool:
        """Quick connectivity check against Supabase."""
        if self.supabase is None:
            return False
        try:
            self.supabase.table("obd_readings").select("id").limit(1).execute()
            return True
        except Exception:
            self.supabase = None
            return False

    def _buffer_queues(self):
        """Drain data queues into local buffer while offline.

        Log records are discarded when offline — they are ephemeral
        and not worth buffering to disk.
        """
        for q, table, transform in [
            (self.can_queue, "obd_readings", self._obd_to_row),
            (self.gps_queue, "gps_readings", self._gps_to_row),
        ]:
            while not q.empty():
                try:
                    record = q.get_nowait()
                except queue.Empty:
                    break
                self.buffer.push(table, transform(record))
        # Discard log records while offline
        if self.log_queue is not None:
            while not self.log_queue.empty():
                try:
                    self.log_queue.get_nowait()
                except queue.Empty:
                    break

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
                # Buffer the rest without attempting uploads
                self._go_offline()
                while not q.empty():
                    try:
                        record = q.get_nowait()
                    except queue.Empty:
                        break
                    self.buffer.push(table, transform(record))
                return

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
            self.supabase = None
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
                self._go_offline()
                break
        self.buffer.delete(uploaded_ids)

    def _drain_logs(self):
        """Drain log queue and upload to device_logs table.

        Unlike data queues, log upload failures are silently ignored
        to prevent infinite loops (a failed log upload would generate
        a new warning log record, which would be re-enqueued).
        """
        if self.log_queue is None or self.supabase is None:
            return
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
            except queue.Empty:
                break
            row = self._log_to_row(record)
            try:
                self.supabase.table("device_logs").insert(row).execute()
            except Exception:
                # Silently discard — never go offline or buffer for logs
                break

    @staticmethod
    def _log_to_row(record: dict) -> dict:
        return {
            "timestamp": record["timestamp"],
            "device_id": record["device_id"],
            "level": record["level"],
            "component": record["component"],
            "message": record["message"],
            "detail": record.get("detail"),
        }

    @staticmethod
    def _can_to_row(record: dict) -> dict:
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
    def _obd_to_row(record: dict) -> dict:
        row = {
            "timestamp": record["timestamp"],
            "device_id": record["device_id"],
        }
        # Copy all OBD fields (everything except type, timestamp, device_id)
        for k, v in record.items():
            if k not in ("type", "timestamp", "device_id"):
                row[k] = v
        return row

    @staticmethod
    def _gps_to_row(record: dict) -> dict:
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
