#!/bin/bash
# Install the Bluetooth Wi-Fi controller on a Raspberry Pi (Raspberry Pi OS / Debian, NetworkManager).
# After this the Pi is always visible over Bluetooth as "a502-wifi", PIN 12345678, and a paired
# device can see and change the Pi's Wi-Fi. Run on the Pi:   bash pi/install.sh
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) is required."; exit 1; }
python3 -c "import dbus, gi" 2>/dev/null || sudo apt-get install -y python3-dbus python3-gi
sudo rfkill unblock bluetooth 2>/dev/null || true
sudo systemctl enable --now bluetooth
sudo mkdir -p /var/lib/a502
sudo install -m 755 -o root -g root "$DIR/a502-wifi.sh"   /usr/local/sbin/a502-wifi
sudo install -m 755 -o root -g root "$DIR/a502-bt-wifi.py" /usr/local/sbin/a502-bt-wifi
sudo tee /etc/systemd/system/a502-bt-wifi.service >/dev/null <<'UNIT'
[Unit]
Description=Bluetooth Wi-Fi controller (name a502-wifi, PIN 12345678)
After=bluetooth.service NetworkManager.service
Requires=bluetooth.service
StartLimitIntervalSec=0
[Service]
Environment=BT_PIN=12345678
Environment=BT_NAME=a502-wifi
# 1 = browsers must pair (encrypted link) before writing; 0 = the PIN alone guards access (works everywhere)
Environment=BT_BLE_ENCRYPT=0
ExecStartPre=/usr/sbin/rfkill unblock bluetooth
ExecStart=/usr/local/sbin/a502-bt-wifi
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now a502-bt-wifi
sleep 3
echo "service: $(systemctl is-active a502-bt-wifi)"
bluetoothctl show | grep -E "Alias|Powered|Discoverable" || true
echo "Pi Bluetooth address (give this to the laptop):"
bluetoothctl show | awk '/Controller/{print "  "$2}'
