#!/usr/bin/python3
"""a502 Bluetooth Wi-Fi rescue (runs as root: a502-bt-wifi.service).

The Pi stays visible over Bluetooth as "a502-wifi". Pair with PIN 12345678, then open the
Bluetooth serial port (SPP, channel 1) and you get a text menu:
  - which Wi-Fi the Pi is on, its password, IP, Tailscale address, internet OK or not
  - list / scan / add / connect / forget Wi-Fi networks (same helper as the admin page,
    with the same automatic roll-back if a new network fails)
Works from the laptop (`a502-bt` command) and Android ("Serial Bluetooth Terminal" app).
iPhones cannot use Bluetooth serial ports (Apple restriction).
"""
import json, os, socket, subprocess, threading, time
import dbus, dbus.service, dbus.mainloop.glib
from gi.repository import GLib

PIN = os.environ.get("BT_PIN", "12345678")
ALIAS = os.environ.get("BT_NAME", "a502-wifi")
CHANNEL = 1
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
WIFI = "/usr/local/sbin/a502-wifi"
RESULT = "/var/lib/a502/wifi.json"
BUS = "org.bluez"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def sh(cmd, inp=None, timeout=60):
    try:
        p = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


# ---------------- pairing agent: answers every pairing with the fixed PIN ----------------
class Agent(dbus.service.Object):
    I = "org.bluez.Agent1"

    @dbus.service.method(I, in_signature="", out_signature="")
    def Release(self): pass

    @dbus.service.method(I, in_signature="o", out_signature="s")
    def RequestPinCode(self, dev):
        log("pairing (PIN) with", dev); return PIN

    @dbus.service.method(I, in_signature="o", out_signature="u")
    def RequestPasskey(self, dev):
        log("pairing (passkey) with", dev); return dbus.UInt32(int(PIN))

    @dbus.service.method(I, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, dev, passkey, entered): pass

    @dbus.service.method(I, in_signature="os", out_signature="")
    def DisplayPinCode(self, dev, pin): pass

    @dbus.service.method(I, in_signature="ou", out_signature="")
    def RequestConfirmation(self, dev, passkey):
        log("pairing confirm", dev, passkey)

    @dbus.service.method(I, in_signature="o", out_signature="")
    def RequestAuthorization(self, dev): pass

    @dbus.service.method(I, in_signature="os", out_signature="")
    def AuthorizeService(self, dev, uuid): pass

    @dbus.service.method(I, in_signature="", out_signature="")
    def Cancel(self): pass


# ---------------- serial-port profile: BlueZ hands us each new connection ----------------
class Profile(dbus.service.Object):
    I = "org.bluez.Profile1"

    @dbus.service.method(I, in_signature="", out_signature="")
    def Release(self): pass

    @dbus.service.method(I, in_signature="oha{sv}", out_signature="")
    def NewConnection(self, dev, fd, props):
        fd = fd.take()
        s = socket.socket(fileno=fd)
        s.setblocking(True)          # BlueZ hands the connection over in non-blocking mode
        log("menu opened by", dev)
        threading.Thread(target=Session(s, str(dev)).run, daemon=True).start()

    @dbus.service.method(I, in_signature="o", out_signature="")
    def RequestDisconnection(self, dev): pass


# ---------------- the text menu ----------------
def wifi_list():
    rc, out, err = sh([WIFI, "list"])
    nets = []
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) >= 5:
            nets.append({"uuid": f[0], "name": f[1], "ssid": f[2], "prio": int(f[3] or 0),
                         "active": f[4] == "yes", "pw": f[5] if len(f) > 5 else "?"})
    nets.sort(key=lambda n: (not n["active"], -n["prio"], n["ssid"].lower()))
    return nets


def password_of(uuid):
    rc, out, err = sh(["nmcli", "-s", "-g", "802-11-wireless-security.psk", "con", "show", "uuid", uuid])
    return out or "(open network / not stored)"


def status_data():
    nets = wifi_list()
    act = next((n for n in nets if n["active"]), None)
    rc, ip, _ = sh(["sh", "-c", "ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1"])
    rc, ts, _ = sh(["tailscale", "ip", "-4"], timeout=5)
    rc, sig, _ = sh(["sh", "-c", "nmcli -t -f IN-USE,SIGNAL dev wifi | awk -F: '$1==\"*\"{print $2}'"])
    net = sh(["curl", "-s", "-m", "4", "-o", "/dev/null", "https://controlplane.tailscale.com"], timeout=6)[0] == 0
    rc, dns, _ = sh(["sh", "-c", "tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)[\"Self\"][\"DNSName\"].rstrip(\".\"))'"], timeout=6)
    return {"ssid": act["ssid"] if act else None, "password": password_of(act["uuid"]) if act else None,
            "signal": sig or None, "ip": ip or None, "tailscale": ts or None, "internet": net,
            "user_link": f"https://{dns}/" if dns else None, "admin_link": f"https://{dns}:8443/" if dns else None}


def status_text():
    d = status_data()
    lines = ["", "===== a502 status ====="]
    if d["ssid"]:
        lines += [f"Wi-Fi     : {d['ssid']}  (signal {d['signal'] or '?'}%)",
                  f"Password  : {d['password']}"]
    else:
        lines += ["Wi-Fi     : NOT CONNECTED"]
    lines += [f"IP (Wi-Fi): {d['ip'] or '-'}",
              f"Tailscale : {d['tailscale'] or '-'}",
              f"Internet  : {'OK' if d['internet'] else 'NOT reachable'}"]
    if d["user_link"]:
        lines += [f"User link : {d['user_link']}", f"Admin link: {d['admin_link']}"]
    return "\n".join(lines)


def scan_data():
    rc, out, err = sh([WIFI, "scan", "rescan"], timeout=30)
    seen = {}
    for line in out.splitlines():
        f = line.replace("\\:", "\x00").split(":")
        if len(f) >= 4 and f[1]:
            ss = f[1].replace("\x00", ":")
            if ss not in seen or int(f[2] or 0) > seen[ss]["signal"]:
                seen[ss] = {"ssid": ss, "signal": int(f[2] or 0), "security": f[3] or "open", "connected": f[0] == "*"}
    return sorted(seen.values(), key=lambda n: -n["signal"])


MENU = """
===== a502 Wi-Fi menu (Bluetooth) =====
 1  status (Wi-Fi, password, IP, links)
 2  saved networks
 3  scan networks in range
 4  add / update a network
 5  connect to a saved network
 6  forget a saved network
 7  show password of a saved network
 q  quit
Type a number and press Enter."""


def handle_request(req, send):
    """One JSON request -> JSON answers via send(dict). Shared by the serial port and BLE.
    Returns "quit" when the client asked to stop."""
    cmd, rid = req.get("cmd"), req.get("id")
    def reply(**kw): send(dict(id=rid, **kw))
    if cmd == "status":
        reply(ok=True, status=status_data())
    elif cmd == "list":
        reply(ok=True, saved=[{k: n[k] for k in ("uuid", "ssid", "prio", "active", "pw")} for n in wifi_list()])
    elif cmd == "scan":
        reply(ok=True, visible=scan_data())
    elif cmd == "password":
        reply(ok=True, password=password_of(str(req.get("uuid", ""))))
    elif cmd == "delete":
        rc, o, err = sh([WIFI, "delete", str(req.get("uuid", ""))])
        reply(ok=rc == 0, error=err.replace("error: ", "") or None)
    elif cmd in ("add", "connect"):
        uuid = str(req.get("uuid", ""))
        if cmd == "add":
            rc, out, err = sh([WIFI, "add"], inp=str(req.get("ssid", "")) + "\n" + str(req.get("password", "")) + "\n")
            if rc:
                return reply(ok=False, error=err.replace("error: ", ""))
            uuid = out.strip()
            if not req.get("connect"):
                return reply(ok=True, done=True, message=f"Saved {req.get('ssid')}.")
        rc, o, err = sh([WIFI, "connect", uuid])
        if rc:
            return reply(ok=False, error=err.replace("error: ", ""))
        seen, t0, last = "", time.time(), {}
        while time.time() - t0 < 180:            # stream each step of the switch
            try:
                last = json.load(open(RESULT))
            except (OSError, ValueError):
                last = {}
            msg = last.get("message", "")
            if msg and msg != seen:
                reply(ok=True, progress=msg); seen = msg
            if last.get("state") in ("ok", "failed") and time.time() - t0 > 2:
                break
            time.sleep(1)
        reply(ok=last.get("state") == "ok", done=True, message=seen, status=status_data())
    elif cmd == "quit":
        return "quit"
    else:
        reply(ok=False, error="unknown command")


class Session:
    def __init__(self, s, dev):
        self.s, self.dev, self.buf = s, dev, b""

    def out(self, text=""):
        self.s.sendall((text.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n").encode())

    def ask(self, prompt):
        if prompt:
            self.s.sendall((prompt + " ").encode())
        while b"\n" not in self.buf and b"\r" not in self.buf:
            d = self.s.recv(1024)
            if not d:
                raise EOFError
            self.buf += d
        i = min(x for x in (self.buf.find(b"\n"), self.buf.find(b"\r")) if x >= 0)
        line, self.buf = self.buf[:i], self.buf[i + 1:].lstrip(b"\r\n")
        return line.decode(errors="replace").strip()

    def pick(self, nets, what):
        for i, n in enumerate(nets, 1):
            flag = " (CONNECTED)" if n["active"] else (" (NO PASSWORD STORED)" if n["pw"] == "missing" else "")
            self.out(f" {i}. {n['ssid']}{flag}")
        a = self.ask(f"Number to {what} (Enter = cancel):")
        if a.isdigit() and 1 <= int(a) <= len(nets):
            return nets[int(a) - 1]
        self.out("cancelled"); return None

    def follow_switch(self, ssid):
        """Print each step of the switch (the Bluetooth link stays up while Wi-Fi changes)."""
        seen, t0 = "", time.time()
        while time.time() - t0 < 180:
            try:
                r = json.load(open(RESULT))
            except (OSError, ValueError):
                r = {}
            msg = r.get("message", "")
            if msg and msg != seen:
                self.out(f"  [{int(time.time() - t0):3d}s] {msg}"); seen = msg
            if r.get("state") in ("ok", "failed") and time.time() - t0 > 2:
                break
            time.sleep(1)
        self.out(status_text())

    def jout(self, obj):
        self.s.sendall((json.dumps(obj) + "\n").encode())

    def json_mode(self):
        """Machine mode for the laptop window app: one JSON request per line, JSON answers."""
        self.jout({"hello": ALIAS})
        while True:
            line = self.ask("")
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                self.jout({"ok": False, "error": "bad request"}); continue
            if handle_request(req, self.jout) == "quit":
                return

    def run(self):
        try:
            self.out(f"\nConnected to {ALIAS}."); self.out(status_text()); self.out(MENU)
            while True:
                c = self.ask("\n>").lower()
                if c in ("q", "quit", "exit"):
                    self.out("bye"); break
                elif c in ("1", "s", "status"):
                    self.out(status_text())
                elif c == "2":
                    for n in wifi_list():
                        flag = "CONNECTED" if n["active"] else ""
                        self.out(f" - {n['ssid']:<28} priority {n['prio']:<4} password {n['pw']:<8} {flag}")
                elif c == "3":
                    self.out("scanning (about 5 s)…")
                    for n in scan_data():
                        self.out(f" {n['signal']:3d}%  {n['ssid']:<28} {n['security']}{'  (connected)' if n['connected'] else ''}")
                elif c == "4":
                    ssid = self.ask("Network name (SSID):")
                    if not ssid:
                        self.out("cancelled"); continue
                    pw = self.ask("Password (Enter = open network):")
                    rc, out, err = sh([WIFI, "add"], inp=ssid + "\n" + pw + "\n")
                    if rc:
                        self.out("NOT saved: " + err.replace("error: ", "")); continue
                    self.out(f"Saved {ssid}.")
                    if self.ask("Connect to it now? (y/n):").lower().startswith("y"):
                        rc, o, err = sh([WIFI, "connect", out.strip()])
                        self.out("NOT started: " + err) if rc else self.follow_switch(ssid)
                elif c == "5":
                    n = self.pick(wifi_list(), "connect to")
                    if n:
                        rc, o, err = sh([WIFI, "connect", n["uuid"]])
                        self.out("NOT started: " + err.replace("error: ", "")) if rc else self.follow_switch(n["ssid"])
                elif c == "6":
                    n = self.pick(wifi_list(), "forget")
                    if n and self.ask(f"Forget {n['ssid']}? (y/n):").lower().startswith("y"):
                        rc, o, err = sh([WIFI, "delete", n["uuid"]])
                        self.out(err.replace("error: ", "") if rc else f"Forgot {n['ssid']}.")
                elif c == "7":
                    n = self.pick(wifi_list(), "show the password of")
                    if n:
                        self.out(f"{n['ssid']}: {password_of(n['uuid'])}")
                elif c == "json":
                    return self.json_mode()
                elif c in ("", "m", "menu", "?", "h", "help"):
                    self.out(MENU)
                else:
                    self.out("unknown choice"); self.out(MENU)
        except (EOFError, ConnectionError):
            pass
        except Exception:
            import traceback
            log("menu error:", traceback.format_exc())
            try: self.out("Sorry, something went wrong on the Pi - please open the menu again.")
            except OSError: pass
        finally:
            log("menu closed by", self.dev)
            try: self.s.close()
            except OSError: pass


# ---------------- adapter setup ----------------
def adapter_setup(bus):
    # PIN pairing (turn off "Secure Simple Pairing" so devices ask for the PIN)
    for a in (["power", "off"], ["ssp", "off"], ["bondable", "on"], ["power", "on"]):
        sh(["btmgmt", "-i", "hci0"] + a, timeout=10)
    props = dbus.Interface(bus.get_object(BUS, "/org/bluez/hci0"), "org.freedesktop.DBus.Properties")
    for k, v in (("Powered", True), ("Alias", ALIAS), ("DiscoverableTimeout", dbus.UInt32(0)),
                 ("PairableTimeout", dbus.UInt32(0)), ("Pairable", True), ("Discoverable", True)):
        try:
            props.Set("org.bluez.Adapter1", k, v)
        except dbus.DBusException as e:
            log("adapter", k, e)


def keep_visible(bus, agent_mgr):
    """Every minute: stay powered, visible and our agent the default (the desktop may change these)."""
    try:
        props = dbus.Interface(bus.get_object(BUS, "/org/bluez/hci0"), "org.freedesktop.DBus.Properties")
        if not props.Get("org.bluez.Adapter1", "Powered"):
            adapter_setup(bus)
        for k in ("Discoverable", "Pairable"):
            if not props.Get("org.bluez.Adapter1", k):
                props.Set("org.bluez.Adapter1", k, True)
        agent_mgr.RequestDefaultAgent("/a502/agent")
    except dbus.DBusException as e:
        log("keep_visible:", e)
    return True


def on_props(iface, changed, invalidated, path=None):
    """Trust every device right after it paired, so it can reconnect without questions.
    Forget a device's BLE PIN login when it disconnects."""
    if iface == "org.bluez.Device1" and "Connected" in changed and not changed["Connected"]:
        ble_auth.discard(str(path)); ble_buf.pop(str(path), None)
    if iface == "org.bluez.Device1" and changed.get("Paired"):
        try:
            p = dbus.Interface(dbus.SystemBus().get_object(BUS, path), "org.freedesktop.DBus.Properties")
            p.Set("org.bluez.Device1", "Trusted", True)
            log("paired + trusted", path)
        except dbus.DBusException as e:
            log("trust:", e)


# ---------------- BLE (for the web page: browsers only speak Bluetooth Low Energy) ----------------
# One GATT service with two characteristics, carrying the same JSON lines as the serial JSON mode:
#   RX  (browser writes, encrypted link required)  -> requests, '\n'-terminated, may arrive in pieces
#   TX  (notify)                                    -> answers, '\n'-terminated, sent in MTU-size pieces
# A device must send {"cmd":"auth","pin":"..."} first; until then every other command is refused.
BLE_SVC = "6e0b1a50-a502-4b7e-9c1f-a502b1e00001"
BLE_RX = "6e0b1a50-a502-4b7e-9c1f-a502b1e00002"
BLE_TX = "6e0b1a50-a502-4b7e-9c1f-a502b1e00003"
GATT_CHRC = "org.bluez.GattCharacteristic1"
# BT_BLE_ENCRYPT=1: browsers must pair (encrypted link) before writing - protects the Wi-Fi password over the
# air, but every visitor's device has to complete Bluetooth pairing. 0: the PIN alone guards access.
BLE_ENCRYPT = os.environ.get("BT_BLE_ENCRYPT", "0") == "1"
ble_auth, ble_buf, ble_mtu, ble_fails = set(), {}, {}, {}


class GattObj(dbus.service.Object):
    def __init__(self, bus, path, iface, props):
        self.path, self.iface, self._props = path, iface, props
        super().__init__(bus, path)

    def managed(self):
        return {self.iface: self._props}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface):
        return self._props if iface == self.iface else {}

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, iface, name):
        return self._props[name]

    @dbus.service.signal("org.freedesktop.DBus.Properties", signature="sa{sv}as")
    def PropertiesChanged(self, iface, changed, invalidated):
        pass


class TxChar(GattObj):
    def __init__(self, bus, path, svc):
        super().__init__(bus, path, GATT_CHRC, {"UUID": BLE_TX, "Service": dbus.ObjectPath(svc),
                                                "Flags": dbus.Array(["notify"], signature="s"),
                                                "Descriptors": dbus.Array([], signature="o")})
        self.notifying = False

    @dbus.service.method(GATT_CHRC, in_signature="", out_signature="")
    def StartNotify(self):
        self.notifying = True

    @dbus.service.method(GATT_CHRC, in_signature="", out_signature="")
    def StopNotify(self):
        self.notifying = False

    def send(self, dev, obj):
        """Called from worker threads: hand each piece to the main loop, in order."""
        data = (json.dumps(obj) + "\n").encode()
        n = max(20, ble_mtu.get(dev, 23) - 3)
        for i in range(0, len(data), n):
            GLib.idle_add(self._emit, data[i:i + n])

    def _emit(self, chunk):
        if self.notifying:
            self.PropertiesChanged(GATT_CHRC, {"Value": dbus.Array(chunk, signature="y")}, [])
        return False


class RxChar(GattObj):
    def __init__(self, bus, path, svc, tx):
        super().__init__(bus, path, GATT_CHRC, {"UUID": BLE_RX, "Service": dbus.ObjectPath(svc),
                                                "Flags": dbus.Array(["encrypt-write" if BLE_ENCRYPT else "write"], signature="s"),
                                                "Descriptors": dbus.Array([], signature="o")})
        self.tx = tx

    @dbus.service.method(GATT_CHRC, in_signature="aya{sv}", out_signature="")
    def WriteValue(self, value, options):
        dev = str(options.get("device", "?"))
        if "mtu" in options:
            ble_mtu[dev] = int(options["mtu"])
        buf = ble_buf.get(dev, b"") + bytes(value)
        if len(buf) > 8192:
            buf = b""                                   # garbage / too long: drop it
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                threading.Thread(target=self.handle, args=(dev, line.decode(errors="replace")), daemon=True).start()
        ble_buf[dev] = buf

    def handle(self, dev, line):
        send = lambda obj: self.tx.send(dev, obj)
        try:
            req = json.loads(line)
        except ValueError:
            return send({"ok": False, "error": "bad request"})
        if req.get("cmd") == "auth":
            if str(req.get("pin", "")) == PIN:
                ble_auth.add(dev); ble_fails.pop(dev, None)
                log("BLE: PIN ok from", dev)
                return send({"id": req.get("id"), "ok": True, "hello": ALIAS})
            ble_fails[dev] = ble_fails.get(dev, 0) + 1
            time.sleep(min(2 * ble_fails[dev], 10))         # slow down guessing
            log("BLE: wrong PIN from", dev)
            return send({"id": req.get("id"), "ok": False, "error": "wrong PIN"})
        if dev not in ble_auth:
            return send({"id": req.get("id"), "ok": False, "error": "send the PIN first"})
        handle_request(req, send)


class BleApp(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/a502/gatt"
        super().__init__(bus, self.path)
        self.svc = GattObj(bus, self.path + "/s0", "org.bluez.GattService1", {})
        self.tx = TxChar(bus, self.svc.path + "/tx", self.svc.path)
        self.rx = RxChar(bus, self.svc.path + "/rx", self.svc.path, self.tx)
        self.svc._props.update({"UUID": BLE_SVC, "Primary": True,
                                "Characteristics": dbus.Array([self.rx.path, self.tx.path], signature="o")})

    @dbus.service.method("org.freedesktop.DBus.ObjectManager", out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {o.path: o.managed() for o in (self.svc, self.rx, self.tx)}


class BleAdvert(GattObj):
    def __init__(self, bus):
        super().__init__(bus, "/a502/adv", "org.bluez.LEAdvertisement1", {
            "Type": "peripheral", "ServiceUUIDs": dbus.Array([BLE_SVC], signature="s"),
            "LocalName": ALIAS, "Discoverable": True})

    @dbus.service.method("org.bluez.LEAdvertisement1", in_signature="", out_signature="")
    def Release(self):
        pass


def ble_start(bus):
    app, adv = BleApp(bus), BleAdvert(bus)
    adapter = bus.get_object(BUS, "/org/bluez/hci0")
    ok = lambda what: (lambda *a: log("BLE:", what, "registered"))
    bad = lambda what: (lambda e: log("BLE:", what, "failed:", e))
    dbus.Interface(adapter, "org.bluez.GattManager1").RegisterApplication(
        app.path, {}, reply_handler=ok("service"), error_handler=bad("service"))
    dbus.Interface(adapter, "org.bluez.LEAdvertisingManager1").RegisterAdvertisement(
        adv.path, {}, reply_handler=ok("advertising"), error_handler=bad("advertising"))
    return app, adv


if __name__ == "__main__":
    dbus.mainloop.glib.threads_init()                      # BLE answers come from worker threads
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter_setup(bus)
    Agent(bus, "/a502/agent")
    agent_mgr = dbus.Interface(bus.get_object(BUS, "/org/bluez"), "org.bluez.AgentManager1")
    agent_mgr.RegisterAgent("/a502/agent", "KeyboardDisplay")
    agent_mgr.RequestDefaultAgent("/a502/agent")
    Profile(bus, "/a502/profile")
    dbus.Interface(bus.get_object(BUS, "/org/bluez"), "org.bluez.ProfileManager1").RegisterProfile(
        "/a502/profile", SPP_UUID,
        {"Name": "a502 Wi-Fi rescue", "Role": "server", "Channel": dbus.UInt16(CHANNEL),
         "RequireAuthentication": True, "RequireAuthorization": False, "AutoConnect": False})
    bus.add_signal_receiver(on_props, dbus_interface="org.freedesktop.DBus.Properties",
                            signal_name="PropertiesChanged", path_keyword="path")
    GLib.timeout_add_seconds(60, keep_visible, bus, agent_mgr)
    ble = ble_start(bus)                                    # keep references alive
    log(f"Bluetooth Wi-Fi rescue ready: name {ALIAS}, PIN {PIN}, serial channel {CHANNEL}")
    GLib.MainLoop().run()
