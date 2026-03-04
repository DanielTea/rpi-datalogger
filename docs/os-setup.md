# Raspberry Pi OS Setup

## Flashing the SD Card

### 1. Download Raspberry Pi OS Lite (64-bit)

```bash
curl -L -o /tmp/raspios-lite.img.xz \
  "https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-12-04/2025-12-04-raspios-trixie-arm64-lite.img.xz"
```

### 2. Flash to SD Card

```bash
# Find your SD card device (e.g., /dev/rdisk7 on macOS, /dev/sdb on Linux)
diskutil list            # macOS
lsblk                   # Linux

# Unmount
diskutil unmountDisk /dev/disk7   # macOS
sudo umount /dev/sdb*             # Linux

# Flash
xzcat /tmp/raspios-lite.img.xz | sudo dd of=/dev/rdisk7 bs=4m status=progress
```

### 3. Configure WiFi and User (cloud-init)

After flashing, the `bootfs` partition will mount automatically.

**Edit `bootfs/network-config`:**
```yaml
network:
  version: 2

  wifis:
    wlan0:
      dhcp4: true
      optional: false
      access-points:
        "YOUR_WIFI_SSID":
          password: "YOUR_WIFI_PASSWORD"

      regulatory-domain: DE
```

**Edit `bootfs/user-data`:**
```yaml
#cloud-config

hostname: raspberrypi

users:
- name: daniel
  groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
  shell: /bin/bash
  lock_passwd: false
  passwd: YOUR_HASHED_PASSWORD
  sudo: ALL=(ALL) NOPASSWD:ALL

ssh_pwauth: true

packages:
- avahi-daemon
```

Generate a password hash with:
```bash
openssl passwd -5 'YourPassword'
```

### 4. Enable SSH

```bash
touch /Volumes/bootfs/ssh    # macOS
touch /mnt/bootfs/ssh        # Linux
```

### 5. Eject and Boot

```bash
sync && diskutil eject /dev/disk7
```

Insert SD card into Pi and power on. Wait ~2 minutes for first boot.

### 6. Connect via SSH

```bash
ssh daniel@raspberrypi.local
```

> **Tip**: If password auth is unreliable, copy your SSH key:
> ```bash
> ssh-copy-id daniel@raspberrypi.local
> ```

## Enable PiCAN 2 (SPI + MCP2515)

Edit `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
```

Uncomment/add:
```
dtparam=spi=on
```

Add at the end (before `[cm4]` section):
```
# PiCAN 2 - MCP2515 CAN controller
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
dtoverlay=spi-bcm2835-overlay
```

Reboot:
```bash
sudo reboot
```

## Configure CAN Interface

```bash
# Bring up CAN0 at 500kbps
sudo ip link set can0 up type can bitrate 500000

# Verify
ip -details link show can0
```

### Make CAN Persistent Across Reboots

```bash
sudo tee /etc/systemd/network/80-can.network << EOF
[Match]
Name=can0

[CAN]
BitRate=500000
RestartSec=100ms
EOF

sudo systemctl enable systemd-networkd
```

## Install CAN Utilities

```bash
sudo apt-get install -y can-utils
```

Test with:
```bash
candump can0           # Listen for frames
cansend can0 123#DEADBEEF   # Send a test frame
```
