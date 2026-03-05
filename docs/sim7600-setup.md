# SIM7600E-H Setup

## USB Modes

The SIM7600 supports two USB modes. **ECM mode is recommended** — it provides a simple USB Ethernet interface (`usb0`) that is far more reliable than PPP or QMI on this hardware.

### ECM Mode (PID 9011) — Recommended

Switch to ECM mode (one-time, persists across reboots):

```bash
# Connect to AT port and send:
echo -e "AT+CUSBPIDSWITCH=9011,1,1\r" > /dev/ttyUSB2
```

> **Warning**: This reboots the modem. Use a stable 2.5A+ power supply to avoid USB bus crashes.

In ECM mode, USB interfaces 00-01 are used by the ECM Ethernet adapter (`usb0`), and serial ports shift to interfaces 02-06:

| Symlink | Interface (ECM) | Purpose |
|---|---|---|
| `/dev/sim7600-diag` | 02 | Diagnostic port |
| `/dev/sim7600-nmea` | 03 | NMEA GPS output (raw stream) |
| `/dev/sim7600-at` | 04 | AT commands (GPS enable, modem control) |
| `/dev/sim7600-at2` | 05 | AT commands (secondary) |
| `/dev/sim7600-audio` | 06 | Audio (not used) |
| `usb0` | 00-01 | **LTE data** (USB Ethernet / CDC ECM) |

### Standard Mode (PID 9001)

In standard mode, serial ports are on interfaces 00-04:

| Symlink | Interface (Std) | Purpose |
|---|---|---|
| `/dev/sim7600-diag` | 00 | Diagnostic port |
| `/dev/sim7600-nmea` | 01 | NMEA GPS output |
| `/dev/sim7600-at` | 02 | AT commands |
| `/dev/sim7600-at2` | 03 | AT commands (secondary) |
| `/dev/sim7600-audio` | 04 | Audio (not used) |

To switch back to standard mode: `AT+CUSBPIDSWITCH=9001,1,1`

## Udev Rules (Stable Symlinks)

The `/dev/ttyUSB*` numbers can change across reboots. Udev rules in `systemd/99-sim7600.rules` create stable symlinks that work in both USB modes:

```bash
sudo cp systemd/99-sim7600.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

After this, always use `/dev/sim7600-at`, `/dev/sim7600-nmea`, etc. instead of `/dev/ttyUSB*`.

## SIM Card Setup

### 1. Insert SIM Card

Insert your SIM card into the SIM7600E-H's SIM slot.

### 2. Unlock SIM PIN

```bash
# Send AT command via serial
echo -e "AT+CPIN=YOUR_PIN\r" > /dev/ttyUSB3
```

### 3. Disable PIN for Unattended Boot

This is critical for headless Raspberry Pi operation:

```bash
echo -e 'AT+CLCK="SC",0,"YOUR_PIN"\r' > /dev/ttyUSB3
```

### 4. Verify SIM Status

Using Python:
```python
import serial, time

ser = serial.Serial('/dev/ttyUSB3', 115200, timeout=3)
ser.reset_input_buffer()

def at(cmd, wait=2):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode())
    time.sleep(wait)
    return ser.read(4096).decode('ascii', errors='replace').strip()

print(at("AT+CPIN?"))      # Should show: +CPIN: READY
print(at("AT+CSQ"))         # Signal quality (0-31, higher=better)
print(at("AT+CREG?"))       # Network reg (0,1=home, 0,5=roaming)
print(at("AT+COPS?"))       # Operator name
print(at("AT+CPSI?"))       # Network type (LTE/WCDMA/GSM)

ser.close()
```

Expected output:
```
+CPIN: READY
+CSQ: 22,99
+CREG: 0,1
+COPS: 1,0,"LIDL Connect",7
+CPSI: LTE,Online,262-02,...,EUTRAN-BAND20,...
```

## GPS Setup

### How GPS Works

The SIM7600's GPS outputs NMEA sentences on a dedicated serial port (`/dev/sim7600-nmea`). The datalogger reads two sentence types:

- **$GPRMC / $GNRMC** — latitude, longitude, speed (km/h), course (heading)
- **$GPGGA / $GNGGA** — altitude (meters above sea level)

This dedicated NMEA port avoids conflicts with the AT command port used for modem control.

### Enable GPS on Boot (systemd service)

The GPS enable script (`systemd/enable-gps.py`) waits for the AT port, checks if GPS is already running, and enables it with retries:

```bash
sudo mkdir -p /opt/sim7600
sudo cp systemd/enable-gps.py /opt/sim7600/
sudo cp systemd/sim7600-gps.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sim7600-gps.service
```

### Test GPS

```bash
# Check GPS is enabled
echo -e "AT+CGPS?\r" > /dev/sim7600-at; sleep 1; cat /dev/sim7600-at
# Expected: +CGPS: 1,1

# Read raw NMEA stream
cat /dev/sim7600-nmea
# Expected: $GPRMC,123725.00,A,5232.352790,N,01324.503530,E,...
```

> **Note**: GPS cold start can take 1-3 minutes. Works best near a window or outdoors. The datalogger will automatically start uploading GPS data as soon as a fix is acquired.

## Troubleshooting

### SIM not detected
- Check SIM is inserted correctly (gold contacts facing down)
- Verify USB connection: `lsusb | grep SimTech`
- Check dmesg: `dmesg | grep -i sim`

### Network not registering
- Verify SIM is activated (call carrier)
- Check signal: `AT+CSQ` (0 = no signal, 31 = max)
- Try manual operator: `AT+COPS=1,2,"26202"` (Vodafone DE)
- Check supported bands: `AT+CBANDCFG?`

### GPS no fix
- Ensure GPS is enabled: `AT+CGPS?` should return `+CGPS: 1,1`
- Move near a window or outdoors
- Cold start takes 1-3 minutes, warm start ~30 seconds
- Check satellite count via NMEA: `AT+CGPSINFOCFG=1,31`
