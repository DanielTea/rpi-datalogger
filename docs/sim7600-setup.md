# SIM7600E-H Setup

## USB Ports

When connected via USB, the SIM7600E-H exposes 6 interfaces:

| Port | Purpose | Notes |
|---|---|---|
| `/dev/ttyUSB0` | Diagnostic | May disconnect intermittently |
| `/dev/ttyUSB1` | NMEA GPS output | Raw NMEA stream |
| `/dev/ttyUSB2` | AT commands | Shared with GPS CGPSINFO output |
| `/dev/ttyUSB3` | AT commands (modem) | **Clean AT port** — use for modem control |
| `/dev/ttyUSB4` | Audio | Not used |
| `wwan0` | QMI WWAN | Data interface |

> **Important**: When GPS is active, `/dev/ttyUSB2` will have CGPSINFO output mixed in.
> Use `/dev/ttyUSB3` for clean AT command interaction.

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

### Enable GPS on Boot (systemd service)

```bash
sudo tee /etc/systemd/system/sim7600-gps.service << EOF
[Unit]
Description=Enable SIM7600 GPS
After=multi-user.target

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 5
ExecStart=/bin/bash -c 'echo -e "AT+CGPS=1\r" > /dev/ttyUSB2'
RemainAfterExit=yes
ExecStop=/bin/bash -c 'echo -e "AT+CGPS=0\r" > /dev/ttyUSB2'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sim7600-gps.service
```

### Test GPS

```bash
echo -e "AT+CGPSINFO\r" > /dev/ttyUSB2; sleep 1; cat /dev/ttyUSB2
```

Expected (with fix):
```
+CGPSINFO: 5232.352790,N,01324.503530,E,040326,123725.0,83.4,0.0,
```

Format: `lat,N/S,lon,E/W,date(ddmmyy),time(hhmmss),alt(m),speed(km/h),course`

> **Note**: GPS cold start can take 1-3 minutes. Works best near a window or outdoors.

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
