import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Supabase
    supabase_url: str = field(
        default_factory=lambda: os.environ.get("SUPABASE_URL", "")
    )
    supabase_key: str = field(
        default_factory=lambda: os.environ.get("SUPABASE_KEY", "")
    )

    # Device
    device_id: str = field(
        default_factory=lambda: os.environ.get("DEVICE_ID", "rpi-001")
    )

    # CAN
    can_interface: str = field(
        default_factory=lambda: os.environ.get("CAN_INTERFACE", "can0")
    )
    can_bitrate: int = field(
        default_factory=lambda: int(os.environ.get("CAN_BITRATE", "500000"))
    )
    can_enabled: bool = field(
        default_factory=lambda: os.environ.get("CAN_ENABLED", "true").lower() in ("true", "1", "yes")
    )
    can_filter_ids: list = field(
        default_factory=lambda: [
            int(x.strip(), 16)
            for x in os.environ.get("CAN_FILTER_IDS", "").split(",")
            if x.strip()
        ]
    )

    # GPS
    gps_serial_port: str = field(
        default_factory=lambda: os.environ.get("GPS_SERIAL_PORT", "/dev/ttyUSB2")
    )
    gps_serial_baud: int = field(
        default_factory=lambda: int(os.environ.get("GPS_SERIAL_BAUD", "115200"))
    )
    gps_poll_interval: float = field(
        default_factory=lambda: float(os.environ.get("GPS_POLL_INTERVAL", "1.0"))
    )

    # Buffering
    buffer_db_path: str = field(
        default_factory=lambda: os.environ.get(
            "BUFFER_DB_PATH", "/var/lib/rpi-datalogger/buffer.db"
        )
    )
    upload_queue_maxsize: int = field(
        default_factory=lambda: int(os.environ.get("UPLOAD_QUEUE_MAXSIZE", "1000"))
    )

    # Upload
    upload_retry_interval: float = field(
        default_factory=lambda: float(os.environ.get("UPLOAD_RETRY_INTERVAL", "5.0"))
    )
