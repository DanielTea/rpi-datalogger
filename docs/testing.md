# Testing Guide

## Running Tests

```bash
cd ~/rpi-datalogger
source .venv/bin/activate
PYTHONPATH=src python3 -m pytest tests/ -v
```

## Test Categories

### Unit Tests (no hardware needed)
Tests that mock hardware and can run anywhere:

```bash
PYTHONPATH=src python3 -m pytest tests/test_gps_reader.py tests/test_buffer.py tests/test_config.py tests/test_uploader.py -v
```

### Hardware Tests (run on Pi only)
Tests that require actual hardware:

```bash
PYTHONPATH=src python3 -m pytest tests/test_hardware.py -v
```

## Manual Hardware Tests

### Test CAN Bus

```bash
# Terminal 1: Listen for frames
candump can0

# Terminal 2: Send a test frame
cansend can0 123#DEADBEEF
```

You should see `123   [4]  DE AD BE EF` in terminal 1.

### Test GPS

```bash
# Check GPS is enabled
echo -e "AT+CGPS?\r" > /dev/ttyUSB2; sleep 1; cat /dev/ttyUSB2
# Expected: +CGPS: 1,1

# Get GPS position
echo -e "AT+CGPSINFO\r" > /dev/ttyUSB2; sleep 1; cat /dev/ttyUSB2
# Expected: +CGPSINFO: 5232.352790,N,01324.503530,E,...
```

### Test SIM/Network

```python
import serial, time
ser = serial.Serial('/dev/ttyUSB3', 115200, timeout=3)

def at(cmd, wait=2):
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode())
    time.sleep(wait)
    return ser.read(4096).decode('ascii', errors='replace').strip()

print(at("AT+CPIN?"))    # READY
print(at("AT+CSQ"))       # Signal strength
print(at("AT+CREG?"))     # 0,1 = registered
print(at("AT+COPS?"))     # Operator name
```

### Test Supabase Connection

```python
from supabase import create_client
client = create_client("YOUR_URL", "YOUR_KEY")
result = client.table("can_frames").select("*").limit(1).execute()
print(result)
```

### Test SQLite Buffer

```python
from datalogger.buffer import LocalBuffer
buf = LocalBuffer("/tmp/test_buffer.db")
buf.push("can_frames", {"arb_id": 123, "data": "deadbeef"})
print(f"Count: {buf.count()}")
print(f"Peek: {buf.peek()}")
buf.close()
```

### Test Full Datalogger

```bash
# Start with verbose logging
PYTHONPATH=src LOG_LEVEL=DEBUG .venv/bin/python3 -m datalogger
```

Check Supabase dashboard for incoming rows in `can_frames` and `gps_readings` tables.
