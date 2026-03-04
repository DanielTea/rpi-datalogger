# RPi Datalogger

Raspberry Pi datalogger that captures CAN bus frames and GPS data, uploading them in realtime to Supabase.

## Hardware

- Raspberry Pi (any model with 40-pin GPIO)
- PiCAN 2 — MCP2515 CAN controller (SPI)
- SIM7600E-H — 4G LTE / GPS module (USB)

## Setup

### 1. Install dependencies

```bash
cd ~/rpi-datalogger
pip install -r requirements.txt
```

### 2. Create Supabase tables

Run the SQL files in `migrations/` in order via the Supabase SQL editor:

- `001_create_can_frames.sql`
- `002_create_gps_readings.sql`

### 3. Configure environment

```bash
cp .env.example .env
nano .env  # Fill in your Supabase URL and service role key
```

### 4. Test manually

```bash
PYTHONPATH=src python3 -m datalogger
```

### 5. Install as systemd service

```bash
sudo cp systemd/rpi-datalogger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-datalogger
```

### 6. View logs

```bash
journalctl -u rpi-datalogger -f
```

## Architecture

```
CAN Reader ──> can_queue ──┐
                            ├──> Uploader ──> Supabase
GPS Reader ──> gps_queue ──┘        │
                               (on failure)
                                    v
                              SQLite Buffer
```

- **CAN Reader**: Reads frames from `can0` via python-can (socketcan)
- **GPS Reader**: Polls SIM7600E-H via AT+CGPSINFO over serial
- **Uploader**: Inserts each record to Supabase in realtime
- **Buffer**: SQLite fallback when network is unavailable; flushes on reconnect
