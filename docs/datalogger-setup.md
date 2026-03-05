# Datalogger Application Setup

## Prerequisites

- Raspberry Pi OS Lite installed (see [os-setup.md](os-setup.md))
- PiCAN 2 configured with CAN0 up (see [os-setup.md](os-setup.md))
- SIM7600E-H working with SIM PIN disabled (see [sim7600-setup.md](sim7600-setup.md))
- Supabase project created

## Installation

### 1. Clone the Repository

```bash
cd ~
git clone https://github.com/DanielTea/rpi-datalogger.git
cd rpi-datalogger
```

### 2. Create Virtual Environment and Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set Up Supabase

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run:
   - `migrations/001_create_can_frames.sql`
   - `migrations/002_create_gps_readings.sql`
3. Get your **Project URL** and **Service Role Key** from Settings → API

### 4. Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
DEVICE_ID=rpi-001
CAN_INTERFACE=can0
GPS_SERIAL_PORT=/dev/ttyUSB2
GPS_POLL_INTERVAL=1.0
```

### 5. Test Manually

```bash
PYTHONPATH=src .venv/bin/python3 -m datalogger
```

You should see:
```
2026-03-04T12:00:00 [INFO] datalogger.__main__: Starting rpi-datalogger (device=rpi-001)
2026-03-04T12:00:00 [INFO] datalogger.can_reader: Opening CAN bus on can0 (1 Hz sampling)
2026-03-04T12:00:00 [INFO] datalogger.gps_reader: Opening GPS serial on /dev/ttyUSB2 @ 115200
2026-03-04T12:00:00 [INFO] datalogger.__main__: All threads started
```

Press `Ctrl+C` to stop.

### 6. Install as Systemd Service

```bash
sudo cp systemd/rpi-datalogger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-datalogger
```

### 7. Monitor Logs

```bash
# Follow live logs
journalctl -u rpi-datalogger -f

# Last 50 lines
journalctl -u rpi-datalogger -n 50

# Errors only
journalctl -u rpi-datalogger -p err
```

### 8. Service Management

```bash
sudo systemctl start rpi-datalogger    # Start
sudo systemctl stop rpi-datalogger     # Stop
sudo systemctl restart rpi-datalogger  # Restart
sudo systemctl status rpi-datalogger   # Status
```

## Architecture

```
CAN Reader (1 Hz) ──> can_queue ──┐
                                   ├──> Uploader ──> Supabase
GPS Reader (1 Hz) ──> gps_queue ──┘        │
                                      (on failure)
                                           v
                                     SQLite Buffer
```

### Data Flow

1. **CAN Reader** reads all frames from `can0` but only emits the latest frame every 1 second
2. **GPS Reader** sends `AT+CGPSINFO` every 1 second, parses the NMEA response
3. **Uploader** takes records from both queues and inserts them to Supabase
4. On network failure, records are saved to a local **SQLite buffer**
5. When the network returns, the buffer is flushed first (FIFO order)

### Data Usage

At 1 Hz CAN + 1 Hz GPS with per-frame upload: **~5 GB/month**

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `SUPABASE_URL` | (required) | Supabase project URL |
| `SUPABASE_KEY` | (required) | Supabase service role key |
| `DEVICE_ID` | `rpi-001` | Unique device identifier |
| `CAN_INTERFACE` | `can0` | CAN bus interface name |
| `CAN_BITRATE` | `500000` | CAN bus bitrate |
| `GPS_SERIAL_PORT` | `/dev/ttyUSB2` | GPS AT command serial port |
| `GPS_SERIAL_BAUD` | `115200` | GPS serial baud rate |
| `GPS_POLL_INTERVAL` | `1.0` | GPS polling interval (seconds) |
| `BUFFER_DB_PATH` | `/var/lib/rpi-datalogger/buffer.db` | SQLite buffer path |
| `UPLOAD_QUEUE_MAXSIZE` | `1000` | Max queue depth before dropping |
| `UPLOAD_RETRY_INTERVAL` | `5.0` | Retry delay on upload failure |
