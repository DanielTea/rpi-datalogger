#!/usr/bin/env python3
"""Monitor LTE ECM connection via usb0."""
import os
import subprocess
import sys
import time

IFACE = "usb0"
SYSFS = f"/sys/class/net/{IFACE}"

# Wait for usb0 to appear
print("LTE: waiting for usb0...")
for _ in range(60):
    if os.path.exists(SYSFS):
        break
    time.sleep(2)
else:
    print("LTE: usb0 not found after 120s")
    sys.exit(1)

# Bring up interface
subprocess.run(["ip", "link", "set", IFACE, "up"], check=False)
print("LTE: usb0 is up")

# Wait for DHCP
time.sleep(5)

# Add default route
subprocess.run(
    ["ip", "route", "add", "default", "dev", IFACE, "metric", "100"],
    check=False, capture_output=True,
)
print("LTE: default route set")

# Monitor loop
while True:
    time.sleep(30)
    if not os.path.exists(SYSFS):
        print("LTE: usb0 disappeared")
        sys.exit(1)
    # Check route exists
    result = subprocess.run(
        ["ip", "route", "show"], capture_output=True, text=True, check=False,
    )
    if f"dev {IFACE}" not in result.stdout:
        subprocess.run(["ip", "link", "set", IFACE, "up"], check=False)
        time.sleep(3)
        subprocess.run(
            ["ip", "route", "add", "default", "dev", IFACE, "metric", "100"],
            check=False, capture_output=True,
        )
        print("LTE: route restored")
