# Hardware Setup

## Components

| Component | Model | Interface | Purpose |
|---|---|---|---|
| Raspberry Pi | Pi 3B / 3B+ / 4B | — | Main controller |
| CAN Controller | PiCAN 2 (MCP2515) | SPI (GPIO header) | CAN bus interface |
| 4G/GPS Module | SIM7600E-H | USB (5× ttyUSB + usb0 ECM) | Mobile data + GPS |
| SIM Card | Lidl Connect (Vodafone DE) | SIM slot on SIM7600 | Mobile connectivity |
| SD Card | 128 GB (or larger) | SD slot | OS + storage |

## Power Supply

The SIM7600 modem draws 200-400mA depending on cellular activity, on top of the Pi's ~400mA base draw. An underpowered supply causes undervoltage events that crash the USB bus and disconnect the modem.

| Supply | Result |
|---|---|
| 5V / 1.5A | Frequent USB resets, modem drops off bus |
| 5V / 2.0A | Marginal — works at idle, fails during LTE transmit |
| **5V / 2.5A** | **Minimum recommended** — stable with power optimizations applied |
| **5V / 3.0A** | **Recommended** — stable without power optimizations |

**Do not use USB WiFi dongles** — the Pi 3B has onboard WiFi. An external USB WiFi adapter adds ~50-80mA of unnecessary draw. Use the onboard WiFi (`brcmfmac`) for SSH and management; use 4G LTE for data uploads.

Check for undervoltage with:
```bash
vcgencmd get_throttled
# 0x0 = clean, 0x50005 = undervoltage active
```

See the [Power Optimization](../README.md#power-optimization) section in the README for config.txt settings that reduce draw by ~130-200mA.

## Physical Assembly

1. **PiCAN 2**: Mount directly onto the Pi's 40-pin GPIO header
2. **SIM7600E-H**: Connect via USB cable to the Pi
3. **SIM Card**: Insert into the SIM7600E-H's SIM slot
4. **SD Card**: Flash Raspberry Pi OS Lite and insert into Pi

## Wiring

### PiCAN 2
The PiCAN 2 sits on the GPIO header and uses:
- SPI0 (MOSI, MISO, SCLK, CE0)
- GPIO25 for interrupt
- 3.3V and GND

No additional wiring needed — it's a HAT that plugs directly onto the Pi.

### SIM7600E-H
Connected via USB. Provides 6 USB interfaces:
- `/dev/ttyUSB0` — Diagnostic port
- `/dev/ttyUSB1` — NMEA GPS output
- `/dev/ttyUSB2` — AT commands (used for GPS polling)
- `/dev/ttyUSB3` — AT commands (clean, used for modem control)
- `/dev/ttyUSB4` — Audio
- `wwan0` / `cdc-wdm0` — QMI WWAN data interface

### CAN Bus Connection
Connect your CAN bus to the PiCAN 2's screw terminal:
- **CAN_H** — CAN High
- **CAN_L** — CAN Low
- **GND** — Ground (optional, for shielding)

> **Note**: Ensure proper 120Ω termination at both ends of the CAN bus.
