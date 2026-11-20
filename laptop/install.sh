#!/bin/bash
# Install the laptop app (Linux). Opens a window to see/change the Pi's Wi-Fi over Bluetooth.
# Also works from the VS Code terminal. Run:   bash laptop/install.sh
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
python3 -c "import tkinter" 2>/dev/null || { echo "Install python3-tk:  sudo apt-get install -y python3-tk"; }
mkdir -p ~/.local/opt/bt-controller ~/.local/bin ~/.local/share/applications
install -m 755 "$DIR/a502-bt-gui.py"    ~/.local/opt/bt-controller/gui.py
install -m 755 "$DIR/a502-bt-client.py" ~/.local/bin/btwifi-term
cat > ~/.local/bin/btwifi <<'EOF'
#!/bin/bash
# Launch the Bluetooth Wi-Fi window with the desktop's own environment (so it works from any terminal,
# incl. the VS Code snap terminal, and on Wayland). Finds the Pi by its Bluetooth name if you paired it.
export A502_BT="${A502_BT:-$(bluetoothctl devices 2>/dev/null | awk '/a502-wifi/{print $2; exit}')}"
P=$(pgrep -u "$USER" -x gsd-xsettings | head -1); [ -z "$P" ] && P=$(pgrep -u "$USER" -x gnome-shell | head -1)
if [ -n "$P" ] && [ -r /proc/$P/environ ]; then
  mapfile -d '' E < /proc/$P/environ
  exec env -i "${E[@]}" A502_BT="$A502_BT" /usr/bin/python3 "$HOME/.local/opt/bt-controller/gui.py" "$@"
fi
exec /usr/bin/python3 "$HOME/.local/opt/bt-controller/gui.py" "$@"
EOF
chmod +x ~/.local/bin/btwifi
cat > ~/.local/share/applications/btwifi.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Bluetooth Wi-Fi Controller
Comment=See and change a Raspberry Pi's Wi-Fi over Bluetooth
Exec=$HOME/.local/bin/btwifi
Icon=bluetooth
Terminal=false
Categories=Network;
EOF
update-desktop-database ~/.local/share/applications 2>/dev/null || true
echo "Installed. Pair 'a502-wifi' (PIN 12345678) in Settings -> Bluetooth, then run:  btwifi"
echo "(terminal version:  btwifi-term  |  specify a Pi:  A502_BT=XX:XX:XX:XX:XX:XX btwifi)"
