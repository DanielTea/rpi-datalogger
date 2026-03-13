# RPi Datalogger

Raspberry Pi vehicle datalogger that captures OBD-II vehicle data and GPS coordinates in realtime and uploads them to Supabase over 4G LTE. Designed for unattended, always-on operation with automatic recovery from network outages, power cycles, and hardware disconnects.

## Hardware

- **Raspberry Pi 3B+** (or any model with 40-pin GPIO header)
- **PiCAN 2** — MCP2515 CAN bus controller over SPI, directly on the GPIO header
- **OBD-II breakout box** (e.g. DUOYI DY29) — breaks out the 16 OBD-II pins to banana sockets with activity LEDs
- **SIM7600E-H** — 4G LTE modem with built-in GPS, connected via USB
- **SIM card** with data plan (tested with Vodafone/LIDL Connect)
- **2.5A+ power supply** — the SIM7600 draws significant current; underpowered supplies cause USB resets

## Architecture

```
┌─────────────┐      ┌───────────┐      ┌───────────────┐
│  OBD Reader │─────>│ can_queue │──┐   │               │
│  (CAN/OBD2) │      └───────────┘  ├──>│   Uploader    │──> Supabase
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

- **OBD Reader** actively polls 24 OBD-II PIDs over CAN bus via python-can (socketcan). On startup, sends a wake-up sequence to activate the vehicle's diagnostic gateway (required on VW vehicles where the OBD CAN lines are behind a gateway that only responds after receiving a diagnostic request). Polls engine data (RPM, load, coolant/intake/ambient/catalyst temps), fuel trims, throttle/pedal positions, timing, pressures, voltages, and counters.
- **GPS Reader** parses NMEA sentences (`$GPRMC`, `$GPGGA`) streamed from the SIM7600's dedicated NMEA serial port (`/dev/sim7600-nmea`). Extracts latitude, longitude, altitude, speed, and course at a configurable interval (default 1 Hz).
- **Uploader** drains both queues and inserts each record into Supabase via REST API. On failure, records are buffered to a local SQLite database (WAL mode, FIFO, max 100k records) and flushed automatically when connectivity returns.

All threads use exponential backoff on crashes and recover independently — a GPS outage doesn't block CAN uploads, and vice versa.

## Project Structure

```
rpi-datalogger/
├── src/datalogger/
│   ├── __main__.py       # Entry point, thread orchestration, signal handling
│   ├── config.py         # Dataclass config loaded from .env
│   ├── can_reader.py     # OBD-II PID poller with gateway wake-up
│   ├── gps_reader.py     # GPS NMEA parser thread
│   ├── uploader.py       # Supabase uploader with offline fallback
│   ├── buffer.py         # SQLite FIFO buffer for offline resilience
│   └── logger.py         # Logging config (stdout → journald)
├── tests/                # 114 unit tests (pytest)
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
| `CAN_ENABLED` | `true` | Enable/disable OBD-II polling |
| `GPS_SERIAL_PORT` | `/dev/sim7600-nmea` | NMEA serial port for GPS data |
| `GPS_SERIAL_BAUD` | `115200` | Serial baud rate |
| `GPS_POLL_INTERVAL` | `1.0` | GPS emit rate in seconds |
| `BUFFER_DB_PATH` | `/var/lib/rpi-datalogger/buffer.db` | SQLite buffer location |
| `UPLOAD_QUEUE_MAXSIZE` | `1000` | Max in-memory queue size before dropping |

### 4. Wire OBD-II to PiCAN2

Connect the PiCAN2's CAN_H and CAN_L to the vehicle's OBD-II port. The easiest method is an OBD-II breakout box (e.g. DUOYI DY29) which exposes all 16 pins as banana sockets with activity LEDs.

| OBD-II Pin | Signal | PiCAN2 Terminal |
|------------|--------|-----------------|
| Pin 6      | CAN_H  | CAN_H           |
| Pin 14     | CAN_L  | CAN_L           |

**VW-specific note**: VW vehicles (Polo 6R, Golf, etc.) use a diagnostic gateway that only activates the OBD CAN lines after receiving a diagnostic request. The datalogger handles this automatically by sending a wake-up sequence on startup. The LEDs on the breakout box will only flicker once the datalogger is running — they won't show activity with just ignition on.

**Verifying the connection**:

```bash
# With ignition on, measure with a multimeter:
# Pin 6 to Pin 4 (CAN_H to GND): ~2.5V idle, fluctuating when active
# Pin 14 to Pin 4 (CAN_L to GND): ~2.5V idle, fluctuating when active
# Pin 16 to Pin 4 (BAT+ to GND): ~12V (confirms OBD port has power)

# With PiCAN2 connected, test OBD communication:
cansend can0 7DF#0201000000000000  # Request supported PIDs
candump can0                        # Should see 7E8 response
```

### 5. Create Supabase tables

Run the SQL migration files in order via the [Supabase SQL Editor](https://supabase.com/dashboard/project/_/sql):

1. `migrations/001_create_can_frames.sql` — CAN frames table (legacy)
2. `migrations/002_create_gps_readings.sql` — GPS readings table
3. `migrations/003_create_device_logs.sql` — Device logs
4. Create the OBD readings table:

```sql
CREATE TABLE obd_readings (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  timestamp timestamptz NOT NULL DEFAULT now(),
  device_id text NOT NULL,
  rpm real, speed_kmh real, engine_load real,
  coolant_temp real, throttle_pos real, intake_temp real,
  intake_pressure real, timing_advance real,
  fuel_trim_short real, fuel_trim_long real,
  air_fuel_ratio real, evap_purge real,
  rel_throttle_pos real, abs_throttle_b real,
  accel_pedal_d real, accel_pedal_e real, cmd_throttle real,
  ambient_temp real, catalyst_temp real,
  ctrl_module_volt real, baro_pressure real, abs_load real,
  runtime integer, dist_since_clear integer
);

ALTER TABLE obd_readings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow inserts" ON obd_readings FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow reads" ON obd_readings FOR SELECT USING (true);
```

### 6. Install udev rules for SIM7600

The SIM7600 exposes multiple USB serial ports. Udev rules create stable symlinks (`/dev/sim7600-at`, `/dev/sim7600-nmea`, etc.) regardless of enumeration order. Rules support both standard mode (PID `9001`) and ECM mode (PID `9011`).

```bash
sudo cp systemd/99-sim7600.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 7. Install systemd services

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

### 8. Test manually

```bash
source .venv/bin/activate
PYTHONPATH=src python3 -m datalogger
```

Check the Supabase dashboard for incoming rows in `obd_readings` and `gps_readings`.

### 9. Start services

```bash
sudo systemctl start sim7600-gps sim7600-lte rpi-datalogger
```

### Verifying the Setup

After completing all setup steps, run through these checks to confirm everything is working:

```bash
# 1. Check all three services are active
systemctl status sim7600-gps sim7600-lte rpi-datalogger

# 2. Verify udev symlinks exist for the SIM7600
ls -la /dev/sim7600-*
# Expected: /dev/sim7600-at, /dev/sim7600-nmea, /dev/sim7600-diag, /dev/sim7600-audio

# 3. Verify CAN interface is up
ip link show can0
# Expected: state UP, qlen 10, type can, bitrate 500000

# 4. Verify GPS serial port is readable
cat /dev/sim7600-nmea | head -5
# Expected: NMEA sentences like $GPRMC,... $GPGGA,...

# 5. Verify LTE connectivity
ip addr show usb0
ping -c 3 -I usb0 8.8.8.8

# 6. Check datalogger logs for successful uploads
journalctl -u rpi-datalogger --since "5 min ago" --no-pager | tail -20
# Expected: "uploaded" messages for CAN and/or GPS records

# 7. Check Supabase for data (from any machine with curl)
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/gps_readings?order=id.desc&limit=1" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool
# Expected: JSON array with your latest GPS reading
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

### Verifying Monitoring

```bash
# Confirm journald is capturing datalogger output
journalctl -u rpi-datalogger --since "1 hour ago" | grep -c "uploaded"
# Expected: non-zero count (one per successful upload batch)

# Verify startup logs reached Supabase
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/device_logs?level=eq.INFO&order=id.desc&limit=2" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool
# Expected: two INFO records — "Datalogger started" and "System status at startup"

# Verify error logs are forwarded (trigger a test by temporarily stopping GPS)
sudo systemctl stop sim7600-gps
sleep 10
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/device_logs?level=eq.ERROR&order=id.desc&limit=3" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool
sudo systemctl start sim7600-gps
# Expected: ERROR record from gps_reader about serial port failure
```

## Supabase Tables

### `obd_readings`

| Column | Type | PID | Description |
|---|---|---|---|
| `id` | `BIGINT` | — | Auto-incrementing primary key |
| `timestamp` | `TIMESTAMPTZ` | — | Capture time (UTC) |
| `device_id` | `TEXT` | — | Device identifier |
| `rpm` | `REAL` | 0C | Engine RPM |
| `speed_kmh` | `REAL` | 0D | Vehicle speed in km/h |
| `engine_load` | `REAL` | 04 | Calculated engine load (%) |
| `coolant_temp` | `REAL` | 05 | Coolant temperature (°C) |
| `throttle_pos` | `REAL` | 11 | Throttle position (%) |
| `intake_temp` | `REAL` | 0F | Intake air temperature (°C) |
| `intake_pressure` | `REAL` | 0B | Intake manifold pressure (kPa) |
| `timing_advance` | `REAL` | 0E | Timing advance (° before TDC) |
| `fuel_trim_short` | `REAL` | 06 | Short term fuel trim bank 1 (%) |
| `fuel_trim_long` | `REAL` | 07 | Long term fuel trim bank 1 (%) |
| `air_fuel_ratio` | `REAL` | 44 | Commanded air-fuel equivalence ratio |
| `evap_purge` | `REAL` | 2E | Commanded evaporative purge (%) |
| `rel_throttle_pos` | `REAL` | 45 | Relative throttle position (%) |
| `abs_throttle_b` | `REAL` | 47 | Absolute throttle position B (%) |
| `accel_pedal_d` | `REAL` | 49 | Accelerator pedal position D (%) |
| `accel_pedal_e` | `REAL` | 4A | Accelerator pedal position E (%) |
| `cmd_throttle` | `REAL` | 4C | Commanded throttle actuator (%) |
| `ambient_temp` | `REAL` | 46 | Ambient air temperature (°C) |
| `catalyst_temp` | `REAL` | 3C | Catalyst temperature bank 1 (°C) |
| `ctrl_module_volt` | `REAL` | 42 | Control module voltage (V) |
| `baro_pressure` | `REAL` | 33 | Barometric pressure (kPa) |
| `abs_load` | `REAL` | 43 | Absolute load value (%) |
| `runtime` | `INTEGER` | 1F | Run time since engine start (seconds) |
| `dist_since_clear` | `INTEGER` | 31 | Distance since codes cleared (km) |

24 PIDs polled via standard OBD-II Service 01 over CAN bus (500 kbps). The reader sends a gateway wake-up sequence on startup and re-wakes automatically if responses stop.

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
| `level` | `TEXT` | Log level (`ERROR`, `WARNING`, `INFO` for startup) |
| `component` | `TEXT` | Source module (`can_reader`, `gps_reader`, `uploader`, etc.) |
| `message` | `TEXT` | Human-readable error message |
| `detail` | `TEXT` | Exception traceback (if applicable) |

WARNING and ERROR log messages are automatically forwarded to this table for remote monitoring. Duplicate messages are rate-limited (1 per component+message per 60 seconds). Log upload failures are silently discarded to prevent infinite loops.

On every startup, two INFO-level records are pushed directly to this table:
- **Startup config** — device ID, CAN interface, GPS port, Supabase connectivity
- **System status** — CPU temperature, throttle state, memory, disk, uptime, kernel version

### Verifying Supabase Tables

```bash
# Check row counts for each table
for TABLE in obd_readings gps_readings device_logs; do
  echo -n "$TABLE: "
  curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/$TABLE?select=count" \
    -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" \
    -H "Prefer: count=exact" -o /dev/null -w "%{http_code}" && echo " OK"
done

# Verify OBD readings are arriving (requires ignition on + CAN connected)
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/obd_readings?order=id.desc&limit=1" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool

# Verify GPS readings are arriving
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/gps_readings?order=id.desc&limit=1" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool

# Verify device_logs has startup records
curl -s "https://YOUR_PROJECT.supabase.co/rest/v1/device_logs?component=eq.system&order=id.desc&limit=2" \
  -H "apikey: YOUR_KEY" -H "Authorization: Bearer YOUR_KEY" | python3 -m json.tool
# Expected: "Datalogger started" and "System status at startup" entries
```

## Fault Tolerance

The system is designed to survive unattended operation:

- **Network outages**: Records buffer to SQLite (up to 100k entries). When connectivity returns, the buffer flushes in FIFO order before processing new data.
- **Power cycles**: All three systemd services are enabled at boot. GPS enable script retries up to 5 times. LTE monitor restores the default route if lost.
- **CAN bus disconnect**: When the OBD gateway stops responding, the reader automatically re-sends the wake-up sequence. When the bus is reconnected, polling resumes immediately.
- **GPS cold start**: GPS data uploads begin as soon as a satellite fix is acquired. CAN data uploads independently without waiting for GPS.
- **USB resets**: Udev rules ensure stable device symlinks across USB re-enumeration. Services restart automatically via systemd.
- **Crash recovery**: All threads use exponential backoff (CAN: 2-60s, GPS: 2-60s, Uploader: 5-120s) to avoid tight crash loops.

### Verifying Fault Tolerance

```bash
# Test offline buffering — disconnect LTE, wait, reconnect
sudo ifconfig usb0 down
sleep 30
# Datalogger should keep running, buffering to SQLite:
journalctl -u rpi-datalogger --since "1 min ago" | grep -i "buffer"
# Check buffer file exists and has records:
ls -la /var/lib/rpi-datalogger/buffer.db
sudo ifconfig usb0 up
sleep 15
# Buffer should flush — look for "flushed" or "drained" messages:
journalctl -u rpi-datalogger --since "1 min ago" | grep -i "flush\|drain"

# Test CAN bus disconnect recovery — unplug OBD cable, wait, replug
# Logs should show noise filtering kicking in (< 5 frames/sec = ignored)
journalctl -u rpi-datalogger -f | grep -i "can\|noise"

# Test GPS cold start — restart the GPS service
sudo systemctl restart sim7600-gps
sleep 30
# GPS uploads should resume once a satellite fix is acquired:
journalctl -u rpi-datalogger --since "1 min ago" | grep -i "gps"

# Test crash recovery — kill the datalogger process
sudo systemctl kill -s KILL rpi-datalogger
sleep 5
# systemd should restart it automatically:
systemctl status rpi-datalogger
# Expected: active (running), recent start time

# Test power cycle — just reboot
sudo reboot
# After boot, all three services should be running:
systemctl status sim7600-gps sim7600-lte rpi-datalogger
```

## Running Tests

```bash
source .venv/bin/activate
PYTHONPATH=src python -m pytest tests/ -v
```

121 tests covering CAN reader (noise filtering, ID filtering, backoff), GPS reader (NMEA parsing, throttling), uploader (online/offline transitions, buffering, log draining), SQLite buffer (FIFO ordering, pruning), log handler (rate limiting, component extraction), and startup logging (system status collection).

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

# 2. Disable GPU/KMS driver (not needed headless, also enables vcgencmd display_power)
sudo sed -i 's/^dtoverlay=vc4-kms-v3d/# dtoverlay=vc4-kms-v3d/' /boot/firmware/config.txt
sudo sed -i 's/^max_framebuffers=2/# max_framebuffers=2/' /boot/firmware/config.txt
sudo sed -i 's/^disable_fw_kms_setup=1/# disable_fw_kms_setup=1/' /boot/firmware/config.txt

# 3. Comment out conflicting defaults (if present)
sudo sed -i 's/^dtparam=audio=on/# dtparam=audio=on/' /boot/firmware/config.txt
sudo sed -i 's/^camera_auto_detect=1/# camera_auto_detect=1/' /boot/firmware/config.txt
sudo sed -i 's/^display_auto_detect=1/# display_auto_detect=1/' /boot/firmware/config.txt
sudo sed -i 's/^arm_boost=1/# arm_boost=1/' /boot/firmware/config.txt

# 4. Limit to 2 CPU cores (saves ~20-40mA, sufficient for 1 Hz data collection)
CMDLINE=$(cat /boot/firmware/cmdline.txt)
echo "${CMDLINE} maxcpus=2" | sudo tee /boot/firmware/cmdline.txt

# 5. Set CPU governor to powersave (persistent via systemd)
sudo cp systemd/cpu-powersave.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cpu-powersave

# 6. Disable Bluetooth services
sudo systemctl disable --now hciuart bluetooth 2>/dev/null

# 7. Blacklist unused USB WiFi drivers (if using onboard WiFi)
echo 'blacklist rtl8192cu' | sudo tee /etc/modprobe.d/blacklist-edimax.conf

# 8. Reboot to apply
sudo reboot
```

### What Each Change Saves

| Change | Savings | Notes |
|---|---|---|
| Disable HDMI + KMS driver | ~30mA | Removes GPU driver entirely, HDMI PHY powers down |
| Disable Bluetooth | ~20mA | `dtoverlay=disable-bt` + disable services |
| Remove USB WiFi dongle | ~50-80mA | Use onboard WiFi instead |
| CPU powersave (600MHz) | ~20-50mA | Sufficient for 1 Hz data collection |
| Disable CPU cores 2-3 | ~20-40mA | `maxcpus=2` in cmdline.txt, 2 cores is plenty |
| Disable audio subsystem | ~5mA | `dtparam=audio=off` |
| GPU memory 16MB | ~5-10mA | Headless operation, no GPU needed |
| Disable arm_boost | reduces spikes | Prevents transient current draw |

### Verifying Power Optimizations

After rebooting with the power optimizations applied:

```bash
# 1. Check CPU cores — should show only 2 online
nproc
# Expected: 2

cat /proc/cpuinfo | grep "^processor"
# Expected: processor 0 and processor 1 only

# 2. Check CPU governor — should be powersave
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
# Expected: powersave

cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
# Expected: 600000 (600 MHz)

# 3. Check HDMI is off
vcgencmd display_power
# Expected: display_power=0

# 4. Check no GPU/DRM driver loaded
ls /dev/dri 2>/dev/null
# Expected: "No such file or directory"

# 5. Check Bluetooth is disabled
systemctl is-enabled hciuart bluetooth 2>/dev/null
# Expected: disabled (or "not found")

hciconfig 2>/dev/null
# Expected: no output or command not found

# 6. Check throttle state (most important!)
vcgencmd get_throttled
# Expected: throttled=0x0 (clean, no undervoltage)
# If 0x1 or 0x50005: power supply is too weak
```

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

### Verifying LTE Connectivity

```bash
# 1. Check usb0 interface has an IP
ip addr show usb0
# Expected: inet 192.168.225.x/24

# 2. Verify internet access over LTE
ping -c 3 -I usb0 8.8.8.8
# Expected: 3 packets received, ~50-200ms latency

# 3. Verify the default route goes through usb0
ip route show default
# Expected: default via 192.168.225.1 dev usb0

# 4. Verify DNS resolution works
nslookup supabase.co
# Expected: resolves to an IP address

# 5. Check modem status via AT commands
echo -e "AT+CSQ\r" > /dev/sim7600-at && sleep 1 && cat /dev/sim7600-at
# Expected: +CSQ: X,Y where X > 10 means decent signal

# 6. Check LTE monitor is watching the connection
systemctl status sim7600-lte
journalctl -u sim7600-lte --since "10 min ago" --no-pager | tail -5
# Expected: active, periodic route checks
```

## Remote Access (Tailscale)

The Pi's LTE connection is behind carrier-grade NAT (CGNAT), so it can't be reached directly. [Tailscale](https://tailscale.com/) creates a mesh VPN that works through NAT, giving the Pi a stable IP reachable from anywhere.

### Setup

```bash
# Install on the Pi
curl -fsSL https://tailscale.com/install.sh | sudo sh

# Start with built-in SSH server
sudo tailscale up --ssh

# Follow the auth URL printed to the terminal
```

Install Tailscale on your laptop too ([download](https://tailscale.com/download)), then connect:

```bash
# SSH via Tailscale IP
ssh daniel@100.113.231.61

# Or via MagicDNS hostname
ssh daniel@raspberrypi.tail9ba309.ts.net
```

Tailscale survives reboots (`tailscaled.service` is enabled by default). Works over LTE, WiFi, or any network — no port forwarding needed.

### Verifying Tailscale

```bash
# On the Pi:

# 1. Check Tailscale is running and authenticated
tailscale status
# Expected: shows this machine and any other devices on your tailnet

# 2. Check the Pi's Tailscale IP
tailscale ip -4
# Expected: 100.x.y.z

# 3. Verify Tailscale service survives reboot
systemctl is-enabled tailscaled
# Expected: enabled

# From your laptop:

# 4. SSH via Tailscale IP (use your Pi's Tailscale IP from step 2)
ssh daniel@100.x.y.z

# 5. SSH via MagicDNS hostname
ssh daniel@raspberrypi.tail9ba309.ts.net

# 6. Verify connection works over LTE (disconnect Pi from WiFi first)
# On the Pi:
sudo ifconfig wlan0 down
# From your laptop — SSH should still work via Tailscale:
ssh daniel@100.x.y.z
# Reconnect WiFi when done:
# On the Pi: sudo ifconfig wlan0 up
```
