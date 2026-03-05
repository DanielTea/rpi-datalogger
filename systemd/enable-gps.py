#!/usr/bin/env python3
"""Enable GPS on SIM7600 modem via AT command."""
import os
import sys
import time

AT_PORT = "/dev/sim7600-at"

# Wait for AT port to appear
print("GPS: waiting for AT port...")
for _ in range(30):
    if os.path.exists(AT_PORT):
        break
    time.sleep(2)
else:
    print("GPS: AT port not found after 60s")
    sys.exit(1)

import serial


def at_cmd(ser, cmd):
    """Send AT command and return response."""
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(2)
    return ser.read(ser.in_waiting).decode(errors="replace")


for attempt in range(1, 6):
    try:
        ser = serial.Serial(AT_PORT, 115200, timeout=3)

        # Check if GPS is already enabled
        resp = at_cmd(ser, "AT+CGPS?")
        if "+CGPS: 1" in resp:
            print(f"GPS already enabled (attempt {attempt})")
            ser.close()
            sys.exit(0)

        # Try to enable
        resp = at_cmd(ser, "AT+CGPS=1")
        ser.close()

        if "OK" in resp:
            print(f"GPS enabled on attempt {attempt}")
            sys.exit(0)

        print(f"GPS attempt {attempt}: {resp.strip()}")
    except Exception as e:
        print(f"GPS attempt {attempt} error: {e}")
    time.sleep(3)

print("GPS: failed after 5 attempts")
sys.exit(1)
