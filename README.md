# Bluetooth Wi-Fi Controller

See and change a Raspberry Pi's Wi-Fi **over Bluetooth** — no SSH, no screen, no network needed.

**Easiest: the web page.** Open **https://vikas-meu.github.io/bluetooth-controller/web/** in Chrome or Edge,
type the Pi's PIN, press *Connect to the Pi* and pick **a502-wifi**. Nothing to install.
Useful when the Pi is on a Wi-Fi you can't reach: pair over Bluetooth and you get the Pi's current
network, its password and IP, and you can scan, add, connect to or forget networks. If a new network
fails (wrong password, no internet), the Pi goes back to the previous one by itself.

## What's in here

| File | Runs on | What it is |
|---|---|---|
| `pi/a502-bt-wifi.py` | the Pi (root service) | makes the Pi visible over Bluetooth and serves the Wi-Fi menu |
| `pi/a502-wifi.sh` | the Pi | the Wi-Fi helper it calls (list / scan / add / connect / forget, with roll-back) |
| `pi/install.sh` | the Pi | installs both as a service |
| `laptop/a502-bt-gui.py` | your computer | the window app |
| `laptop/a502-bt-client.py` | your computer | a text version for the terminal |
| `laptop/install.sh` | your computer | installs the app + a `btwifi` command |
| `web/index.html` | any browser with Web Bluetooth | the same controls as a web page (Bluetooth Low Energy) |

## Requirements

- **Pi:** Raspberry Pi OS / Debian with **NetworkManager** (`nmcli`) and Bluetooth.
- **Computer:** Linux with Bluetooth, Python 3, `python3-tk` for the window (`sudo apt-get install -y python3-tk`).
- **Phones:** Android works with the free **Serial Bluetooth Terminal** app. **iPhone cannot** (Apple blocks this kind of Bluetooth serial link).

## Install

**On the Pi:**
```bash
git clone https://github.com/<you>/bluetooth-controller
cd bluetooth-controller
bash pi/install.sh          # prints the Pi's Bluetooth address
```
The Pi is now always visible over Bluetooth as **a502-wifi**, pairing **PIN 12345678**.

**On your computer:**
```bash
bash laptop/install.sh
```
Then pair **a502-wifi** (PIN **12345678**) in Settings → Bluetooth, and run:
```bash
btwifi            # window app (also in your apps menu as "Bluetooth Wi-Fi Controller")
btwifi-term       # same thing as text, in the terminal
```
If you have several Pis, pick one: `A502_BT=XX:XX:XX:XX:XX:XX btwifi`.

## Web page (Web Bluetooth)

Browsers can only use **Bluetooth Low Energy**, so the Pi also offers the same commands over BLE (service
`6e0b1a50-a502-4b7e-9c1f-a502b1e00001`), next to the classic serial port used by the laptop app.

| Browser | Works? |
|---|---|
| Chrome / Edge on Windows, macOS, Android | yes |
| Chrome on Linux | turn on `chrome://flags/#enable-experimental-web-platform-features`, restart Chrome |
| iPhone / iPad | use the free **Bluefy** browser (Safari has no Web Bluetooth) |
| Firefox | no |

The page must be opened over **https** (GitHub Pages is fine) or from `localhost`.
Every BLE session must send the PIN before anything else; wrong PINs are slowed down.

**Over-the-air security:** by default (`BT_BLE_ENCRYPT=0`) the PIN guards access but the BLE link itself is
not encrypted, so someone nearby with Bluetooth sniffing gear could read what is sent, including the Wi-Fi
password. Set `BT_BLE_ENCRYPT=1` in `/etc/systemd/system/a502-bt-wifi.service` to require an encrypted
(paired) link; each visitor's device then has to accept a Bluetooth pairing first.

## Android (text menu)

Install **Serial Bluetooth Terminal**, pair **a502-wifi** (PIN 12345678), connect, then type a number:
`1` status · `2` saved · `3` scan · `4` add · `5` connect · `6` forget · `7` show password.

## Security note

The pairing PIN is a fixed **12345678**. Anyone within Bluetooth range (~10 m) who pairs can read the
Wi-Fi password and change the network. Change `BT_PIN` in `/etc/systemd/system/a502-bt-wifi.service`
(and re-pair) if that matters to you.

## Change the PIN or the Bluetooth name
Edit `/etc/systemd/system/a502-bt-wifi.service` on the Pi (`BT_PIN`, `BT_NAME`), then
`sudo systemctl daemon-reload && sudo systemctl restart a502-bt-wifi` and pair again.
