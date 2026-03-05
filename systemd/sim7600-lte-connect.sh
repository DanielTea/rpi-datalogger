#!/bin/bash
# Wait for SIM7600 modem QMI device to appear
MAX_WAIT=60
WAITED=0

echo "Waiting for SIM7600 modem (QMI)..."
while [ ! -e /dev/cdc-wdm0 ] && [ $WAITED -lt $MAX_WAIT ]; do
    sleep 2
    WAITED=$((WAITED + 2))
done

if [ ! -e /dev/cdc-wdm0 ]; then
    echo "ERROR: QMI device not found after ${MAX_WAIT}s"
    # Try to recover by resetting USB
    for port in /sys/bus/usb/devices/*/idVendor; do
        if [ -f "$port" ] && grep -q '1e0e' "$port" 2>/dev/null; then
            DEV=$(dirname "$port")
            echo "Resetting modem at $DEV"
            echo 0 > "$DEV/authorized"
            sleep 2
            echo 1 > "$DEV/authorized"
            sleep 5
        fi
    done
    # Check again
    if [ ! -e /dev/cdc-wdm0 ]; then
        echo "ERROR: Modem recovery failed"
        exit 1
    fi
fi

echo "Modem found at /dev/cdc-wdm0"
sleep 2

# Set modem to online mode
qmicli -d /dev/cdc-wdm0 --dms-set-operating-mode=online 2>/dev/null || true

# Configure raw-ip mode for QMI
echo Y > /sys/class/net/wwan0/qmi/raw_ip 2>/dev/null || true

# Start network connection (Lidl Connect / Vodafone)
echo "Starting LTE connection..."
qmicli -d /dev/cdc-wdm0 --wds-start-network="apn=web.vodafone.de,ip-type=4" --client-no-release-cid

# Bring up the interface and get IP
ip link set wwan0 up
udhcpc -i wwan0 -q -n

echo "LTE connection established"
ip addr show wwan0
