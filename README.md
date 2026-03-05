# RPi Datalogger

Raspberry Pi vehicle datalogger that captures CAN bus frames and GPS coordinates in realtime and uploads them to Supabase over 4G LTE. Designed for unattended, always-on operation with automatic recovery from network outages, power cycles, and hardware disconnects.

## Hardware

- **Raspberry Pi 3B+** (or any model with 40-pin GPIO header)
- **PiCAN 2** — MCP2515 CAN bus controller over SPI, directly on the GPIO header
- **SIM7600E-H** — 4G LTE modem with built-in GPS, connected via USB
- **SIM card** with data plan (tested with Vodafone/LIDL Connect)
- **2.5A+ power supply** — the SIM7600 draws significant current; underpowered supplies cause USB resets

## Architecture

```
┌─────────────┐      ┌───────────┐      ┌───────────────┐
│  CAN Reader │─────>│ can_queue │──┐   │               │
│  (socketcan)│      └───────────┘  ├──>│   Uploader    │──> Supabase
└─────────────┘                     │   │               │    (4G LTE)
┌─────────────┐      ┌───────────┐  │   └───────┬───────┘
│  GPS Reader │─────>│ gps_queue │──┘           │
│  (NMEA/ser) │      └───────────┘        (on failure)
└─────────────┘                                 │
                                                v
                                        ┌───────────────┐
                                        │ SQLite Buffer │
                                        │  (offline Q)  │
                                        └───────────────┘
```

Three daemon threads run independently:

- **CAN Reader** reads frames from `can0` via python-can (socketcan), sampling at 1 Hz. Includes a noise threshold filter — requires at least 5 frames/second to distinguish real bus traffic from floating-pin artifacts on a disconnected bus. Optionally filters by arbitration ID whitelist.
- **GPS Reader** parses NMEA sentences (`$GPRMC`, `$GPGGA`) streamed from the SIM7600's dedicated NMEA serial port (`/dev/sim7600-nmea`). Extracts latitude, longitude, altitude, speed, and course at a configurable interval (default 1 Hz).
- **Uploader** drains both queues and inserts each record into Supabase via REST API. On failure, records are buffered to a local SQLite database (WAL mode, FIFO, max 100k records) and flushed automatically when connectivity returns.

All threads use exponential backoff on crashes and recover independently — a GPS outage doesn't block CAN uploads, and vice versa.

## Project Structure

```
rpi-datalogger/
├── src/datalogger/
│   ├── __main__.py       # Entry point, thread orchestration, signal handling
│   ├── config.py         # Dataclass config loaded from .env
│   ├── can_reader.py     # CAN bus reader thread with noise filtering
│   ├── gps_reader.py     # GPS NMEA parser thread
│   ├── uploader.py       # Supabase uploader with offline fallback
│   ├── buffer.py         # SQLite FIFO buffer for offline resilience
│   └── logger.py         # Logging config (stdout → journald)
├── tests/                # 83 unit tests (pytest)
├── migrations/           # Supabase SQL table definitions
├── systemd/              # Service files, udev rules, helper scripts
│   ├── rpi-datalogger.service
│   ├── sim7600-gps.service
│   ├── sim7600-lte.service
│   ├── 99-sim7600.rules  # udev rules for stable /dev/sim7600-* symlinks
│   ├── enable-gps.py     # GPS enable script (AT+CGPS=1 with retries)
│   └── lte-monitor.py    # LTE ECM watchdog (monitors usb0, restores route)
├── .env.example
├── requirements.txt
└── setup.py
```

## Setup

### 1. Clone and install dependencies

```bash
cd ~
git clone https://github.com/DanielTea/rpi-datalogger.git
cd rpi-datalogger
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create Supabase tables

Run the SQL migration files in order via the [Supabase SQL Editor](https://supabase.com/dashboard/project/_/sql):

1. `migrations/001_create_can_frames.sql` — CAN frames table with indexes and RLS
2. `migrations/002_create_gps_readings.sql` — GPS readings table with indexes and RLS
3. `migrations/003_create_device_logs.sql` — Device error/warning logs for remote monitoring

### 3. Configure environment

```bash
cp .env.example .env
nano .env
```

| Variable | Default | Description |
|---|---|---|
| `SUPABASE_URL` | _(required)_ | Your Supabase project URL |
| `SUPABASE_KEY` | _(required)_ | Supabase service role key |
| `DEVICE_ID` | `rpi-001` | Identifier for this Pi in the database |
| `CAN_INTERFACE` | `can0` | SocketCAN interface name |
| `CAN_BITRATE` | `500000` | CAN bus bitrate in bps |
| `CAN_FILTER_IDS` | _(empty)_ | Optional comma-separated hex IDs to whitelist (e.g. `7DF,7E8,100`) |
| `GPS_SERIAL_PORT` | `/dev/sim7600-nmea` | NMEA serial port for GPS data |
| `GPS_SERIAL_BAUD` | `115200` | Serial baud rate |
| `GPS_POLL_INTERVAL` | `1.0` | GPS emit rate in seconds |
| `BUFFER_DB_PATH` | `/var/lib/rpi-datalogger/buffer.db` | SQLite buffer location |
| `UPLOAD_QUEUE_MAXSIZE` | `1000` | Max in-memory queue size before dropping |

### 4. Install udev rules for SIM7600

The SIM7600 exposes multiple USB serial ports. Udev rules create stable symlinks (`/dev/sim7600-at`, `/dev/sim7600-nmea`, etc.) regardless of enumeration order. Rules support both standard mode (PID `9001`) and ECM mode (PID `9011`).

```bash
sudo cp systemd/99-sim7600.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 5. Install systemd services

```bash
# GPS enable service (runs AT+CGPS=1 on boot)
sudo mkdir -p /opt/sim7600
sudo cp systemd/enable-gps.py /opt/sim7600/
sudo cp systemd/lte-monitor.py /opt/sim7600/
sudo cp systemd/sim7600-gps.service /etc/systemd/system/
sudo cp systemd/sim7600-lte.service /etc/systemd/system/

# Datalogger service
sudo cp systemd/rpi-datalogger.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable sim7600-gps sim7600-lte rpi-datalogger
```

### 6. Test manually

```bash
source .venv/bin/activate
PYTHONPATH=src python3 -m datalogger
```

Check the Supabase dashboard for incoming rows in `can_frames` and `gps_readings`.

### 7. Start services

```bash
sudo systemctl start sim7600-gps sim7600-lte rpi-datalogger
```

## Monitoring

```bash
# Follow datalogger logs
journalctl -u rpi-datalogger -f

# Check all three services
systemctl status sim7600-gps sim7600-lte rpi-datalogger

# Check for undervoltage (power supply issues)
vcgencmd get_throttled
```

## Supabase Tables

### `can_frames`

| Column | Type | Description |
|---|---|---|
| `id` | `BIGINT` | Auto-incrementing primary key |
| `timestamp` | `TIMESTAMPTZ` | Capture time (UTC) |
| `device_id` | `TEXT` | Device identifier |
| `arb_id` | `INTEGER` | CAN arbitration ID |
| `is_extended` | `BOOLEAN` | Extended frame flag |
| `is_remote` | `BOOLEAN` | Remote frame flag |
| `dlc` | `SMALLINT` | Data length code (0-8) |
| `data` | `BYTEA` | Raw CAN payload |
| `bus_time` | `DOUBLE PRECISION` | python-can hardware timestamp |

### `gps_readings`

| Column | Type | Description |
|---|---|---|
| `id` | `BIGINT` | Auto-incrementing primary key |
| `timestamp` | `TIMESTAMPTZ` | Capture time (UTC) |
| `device_id` | `TEXT` | Device identifier |
| `latitude` | `DOUBLE PRECISION` | Decimal degrees |
| `longitude` | `DOUBLE PRECISION` | Decimal degrees |
| `altitude` | `DOUBLE PRECISION` | Meters above sea level |
| `speed` | `DOUBLE PRECISION` | Speed in km/h |
| `course` | `DOUBLE PRECISION` | Heading in degrees |
| `raw_response` | `TEXT` | Raw NMEA sentence for debugging |

### `device_logs`

| Column | Type | Description |
|---|---|---|
| `id` | `BIGINT` | Auto-incrementing primary key |
| `timestamp` | `TIMESTAMPTZ` | When the event occurred (UTC) |
| `device_id` | `TEXT` | Device identifier |
| `level` | `TEXT` | Log level (`ERROR`, `WARNING`) |
| `component` | `TEXT` | Source module (`can_reader`, `gps_reader`, `uploader`, etc.) |
| `message` | `TEXT` | Human-readable error message |
| `detail` | `TEXT` | Exception traceback (if applicable) |

WARNING and ERROR log messages are automatically forwarded to this table for remote monitoring. Duplicate messages are rate-limited (1 per component+message per 60 seconds). Log upload failures are silently discarded to prevent infinite loops.

## Fault Tolerance

The system is designed to survive unattended operation:

- **Network outages**: Records buffer to SQLite (up to 100k entries). When connectivity returns, the buffer flushes in FIFO order before processing new data.
- **Power cycles**: All three systemd services are enabled at boot. GPS enable script retries up to 5 times. LTE monitor restores the default route if lost.
- **CAN bus disconnect**: Floating-pin noise is filtered out (threshold: 5+ frames/sec required). When the bus is reconnected, data flows immediately.
- **GPS cold start**: GPS data uploads begin as soon as a satellite fix is acquired. CAN data uploads independently without waiting for GPS.
- **USB resets**: Udev rules ensure stable device symlinks across USB re-enumeration. Services restart automatically via systemd.
- **Crash recovery**: All threads use exponential backoff (CAN: 2-60s, GPS: 2-60s, Uploader: 5-120s) to avoid tight crash loops.

## Running Tests

```bash
source .venv/bin/activate
PYTHONPATH=src python -m pytest tests/ -v
```

83 tests covering CAN reader (noise filtering, ID filtering, backoff), GPS reader (NMEA parsing, throttling), uploader (online/offline transitions, buffering), and the SQLite buffer (FIFO ordering, pruning).

## Power Optimization

The SIM7600 modem draws significant current (~200-400mA) and can cause undervoltage on the Pi's 5V rail, leading to USB bus resets and modem disconnects. A **3A power supply** is recommended, but the following optimizations reduce draw by ~130-200mA and make the system stable even on weaker supplies.

### Quick Setup

Apply all power optimizations at once:

```bash
# 1. Add power saving options to /boot/firmware/config.txt
sudo tee -a /boot/firmware/config.txt << 'EOF'

# --- Power saving ---
hdmi_blanking=2
dtoverlay=disable-bt
dtparam=audio=off
camera_auto_detect=0
display_auto_detect=0
gpu_mem=16
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
EOF

# Comment out conflicting defaults (if present)
sudo sed -i 's/^dtparam=audio=on/# dtparam=audio=on/' /boot/firmware/config.txt
sudo sed -i 's/^camera_auto_detect=1/# camera_auto_detect=1/' /boot/firmware/config.txt
sudo sed -i 's/^display_auto_detect=1/# display_auto_detect=1/' /boot/firmware/config.txt
sudo sed -i 's/^arm_boost=1/# arm_boost=1/' /boot/firmware/config.txt

# 2. Set CPU governor to powersave (persistent)
sudo tee /etc/systemd/system/cpu-powersave.service << 'EOF'
[Unit]
Description=Set CPU governor to powersave
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable cpu-powersave

# 3. Disable Bluetooth services
sudo systemctl disable --now hciuart bluetooth 2>/dev/null

# 4. Blacklist unused USB WiFi drivers (if using onboard WiFi)
echo 'blacklist rtl8192cu' | sudo tee /etc/modprobe.d/blacklist-edimax.conf

# 5. Reboot to apply
sudo reboot
```

### What Each Change Saves

| Change | Savings | Notes |
|---|---|---|
| Disable HDMI | ~30mA | `hdmi_blanking=2` in config.txt |
| Disable Bluetooth | ~20mA | `dtoverlay=disable-bt` + disable services |
| Remove USB WiFi dongle | ~50-80mA | Use onboard WiFi instead |
| CPU powersave (600MHz) | ~20-50mA | Sufficient for 1 Hz data collection |
| Disable audio subsystem | ~5mA | `dtparam=audio=off` |
| GPU memory 16MB | ~5-10mA | Headless operation, no GPU needed |
| Disable arm_boost | reduces spikes | Prevents transient current draw |

### Checking Power Status

```bash
# Check current throttle state
vcgencmd get_throttled

# Decode the result:
# 0x0     = clean, no issues
# 0x1     = undervoltage RIGHT NOW
# 0x50000 = undervoltage occurred since boot (historical)
# 0x50005 = undervoltage active + throttled
```

### USB Recovery

If the modem drops off USB due to undervoltage (no `/dev/ttyUSB*` or `/dev/sim7600-*`), you can try a USB rebind before rebooting:

```bash
# Find the modem's USB path
MODEM_PORT=$(ls /sys/bus/usb/devices/ | grep '1-1\.' | head -1)

# Rebind
sudo sh -c "echo $MODEM_PORT > /sys/bus/usb/drivers/usb/unbind"
sleep 2
sudo sh -c "echo $MODEM_PORT > /sys/bus/usb/drivers/usb/bind"
sleep 5

# Restart services
sudo systemctl restart sim7600-gps sim7600-lte rpi-datalogger
```

## LTE Connectivity

The SIM7600 runs in **ECM mode** (USB Ethernet), appearing as a `usb0` network interface. This is more reliable than PPP on this hardware. The `lte-monitor.py` watchdog keeps the interface up and restores the default route if it disappears.

To switch the modem to ECM mode (one-time, survives reboots):

```
AT+CUSBPIDSWITCH=9011,1,1
```

**Warning**: This command reboots the modem. Ensure a stable power supply (2.5A+) to avoid USB bus crashes during the switch.
