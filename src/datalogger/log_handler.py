"""Custom logging handler that forwards WARNING+ log records to Supabase."""

import logging
import queue
import time
import traceback
from datetime import datetime, timezone


_RATE_LIMIT_SECONDS = 60.0


class SupabaseLogHandler(logging.Handler):
    """Logging handler that enqueues WARNING/ERROR records for Supabase upload.

    Attaches to the ``datalogger`` logger hierarchy. Each qualifying record
    is converted to a dict and placed on ``log_queue`` for the Uploader
    thread to drain into the ``device_logs`` table.

    Rate-limiting: duplicate (component, message) pairs are suppressed
    for 60 seconds to avoid flooding during crash-restart loops.
    """

    def __init__(self, device_id: str, log_queue: queue.Queue):
        super().__init__(level=logging.WARNING)
        self.device_id = device_id
        self.log_queue = log_queue
        self._recent: dict[str, float] = {}  # (component, msg) -> last_emit_time

    def emit(self, record: logging.LogRecord):
        try:
            component = record.name
            if component.startswith("datalogger."):
                component = component[len("datalogger."):]

            message = record.getMessage()

            # Rate-limit: skip duplicate component+message within window
            key = f"{component}:{message}"
            now = time.monotonic()
            last = self._recent.get(key)
            if last is not None and (now - last) < _RATE_LIMIT_SECONDS:
                return
            self._recent[key] = now

            # Prune stale entries periodically
            if len(self._recent) > 200:
                cutoff = now - _RATE_LIMIT_SECONDS
                self._recent = {
                    k: v for k, v in self._recent.items() if v > cutoff
                }

            detail = None
            if record.exc_info and record.exc_info[1] is not None:
                detail = "".join(
                    traceback.format_exception(*record.exc_info)
                )

            log_record = {
                "type": "log",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "device_id": self.device_id,
                "level": record.levelname,
                "component": component,
                "message": message,
                "detail": detail,
            }

            self.log_queue.put_nowait(log_record)
        except queue.Full:
            pass  # Drop silently — never block the logging thread
        except Exception:
            pass  # Handler must never raise
