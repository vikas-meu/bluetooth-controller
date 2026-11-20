#!/usr/bin/python3
"""a502 Wi-Fi over Bluetooth - window app for the laptop (no terminal, no SSH, no network needed).

Talks to the Pi's Bluetooth Wi-Fi rescue service (a502-bt-wifi) over the Bluetooth serial port.
First time only: pair "a502-wifi" in Settings -> Bluetooth with PIN 12345678.
"""
import json, os, queue, socket, threading
import tkinter as tk
from tkinter import ttk, messagebox

PI = os.environ.get("A502_BT", "88:A2:9E:2B:B5:B6")


class Link:
    """Bluetooth connection to the Pi; requests run in a worker thread, answers go to the UI queue."""
    def __init__(self, ui_q):
        self.q, self.s, self.buf, self.lock, self.n = ui_q, None, b"", threading.Lock(), 0

    def _line(self):
        while b"\n" not in self.buf:
            d = self.s.recv(4096)
            if not d:
                raise ConnectionError("the Pi closed the Bluetooth connection")
            self.buf += d
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def connect(self):
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(20)
        s.connect((PI, 1))
        self.s, self.buf = s, b""
        s.sendall(b"json\r\n")
        s.settimeout(30)
        while True:                                # skip the text menu until the JSON hello
            line = self._line()
            if '{"hello"' in line:                 # may come right after the "> " prompt
                return

    def request(self, cmd, on_reply, **args):
        def work():
            with self.lock:
                try:
                    if self.s is None:
                        self.q.put(("info", "Connecting over Bluetooth…"))
                        self.connect()
                        self.q.put(("connected", None))
                    self.n += 1
                    rid = self.n
                    self.s.settimeout(200 if cmd in ("add", "connect") else 40)
                    self.s.sendall((json.dumps(dict(cmd=cmd, id=rid, **args)) + "\n").encode())
                    while True:
                        msg = json.loads(self._line())
                        if msg.get("id") != rid:
                            continue
                        if "progress" in msg:
                            self.q.put(("progress", msg["progress"])); continue
                        self.q.put(("reply", (on_reply, msg))); break
                except Exception as e:
                    try:
                        if self.s: self.s.close()
                    except OSError:
                        pass
                    self.s = None
                    self.q.put(("error", f"Bluetooth: {e}. Is the Pi within ~10 m and paired (PIN 12345678)?"))
        threading.Thread(target=work, daemon=True).start()


class App:
    def __init__(self, root):
        self.root, self.q = root, queue.Queue()
        self.link = Link(self.q)
        self.saved, self.visible, self.cur_pw = [], [], ""
        root.title("a502 Wi-Fi (Bluetooth)")
        root.geometry("760x640")
        pad = {"padx": 8, "pady": 4}

        top = ttk.LabelFrame(root, text="Pi is connected to")
        top.pack(fill="x", **pad)
        self.v = {k: tk.StringVar(value="…") for k in ("ssid", "password", "ip", "tailscale", "internet", "user", "admin")}
        rows = [("Wi-Fi", "ssid"), ("Password", "password"), ("IP (Wi-Fi)", "ip"), ("Tailscale", "tailscale"),
                ("Internet", "internet"), ("User link", "user"), ("Admin link", "admin")]
        for i, (lab, key) in enumerate(rows):
            ttk.Label(top, text=lab + ":").grid(row=i, column=0, sticky="w", padx=6)
            ttk.Entry(top, textvariable=self.v[key], width=60, state="readonly").grid(row=i, column=1, sticky="we", padx=6, pady=1)
        self.showpw = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="show", variable=self.showpw, command=self.paint_pw).grid(row=1, column=2)
        ttk.Button(top, text="Refresh", command=self.refresh).grid(row=0, column=2, padx=6)
        top.columnconfigure(1, weight=1)

        mid = ttk.Frame(root); mid.pack(fill="both", expand=True, **pad)
        lf = ttk.LabelFrame(mid, text="Saved networks"); lf.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.tv_saved = ttk.Treeview(lf, columns=("ssid", "state"), show="headings", height=8)
        self.tv_saved.heading("ssid", text="Network"); self.tv_saved.heading("state", text="")
        self.tv_saved.column("state", width=150)
        self.tv_saved.pack(fill="both", expand=True, padx=4, pady=4)
        b = ttk.Frame(lf); b.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(b, text="Connect", command=self.do_connect).pack(side="left")
        ttk.Button(b, text="Show password", command=self.do_password).pack(side="left", padx=4)
        ttk.Button(b, text="Forget", command=self.do_forget).pack(side="left")

        rf = ttk.LabelFrame(mid, text="In range (double-click = fill in below)"); rf.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.tv_vis = ttk.Treeview(rf, columns=("ssid", "sig", "sec"), show="headings", height=8)
        for c, t, w in (("ssid", "Network", 170), ("sig", "Signal", 60), ("sec", "Security", 90)):
            self.tv_vis.heading(c, text=t); self.tv_vis.column(c, width=w)
        self.tv_vis.pack(fill="both", expand=True, padx=4, pady=4)
        self.tv_vis.bind("<Double-1>", self.fill_from_scan)
        ttk.Button(rf, text="Scan", command=self.scan).pack(anchor="w", padx=4, pady=(0, 4))

        af = ttk.LabelFrame(root, text="Add / update a network"); af.pack(fill="x", **pad)
        ttk.Label(af, text="Name (SSID)").grid(row=0, column=0, padx=6, sticky="w")
        self.e_ssid = ttk.Entry(af, width=28); self.e_ssid.grid(row=0, column=1, padx=6, pady=3, sticky="we")
        ttk.Label(af, text="Password").grid(row=0, column=2, padx=6, sticky="w")
        self.e_pw = ttk.Entry(af, width=24, show="•"); self.e_pw.grid(row=0, column=3, padx=6, pady=3, sticky="we")
        ttk.Button(af, text="Save", command=lambda: self.do_add(False)).grid(row=0, column=4, padx=3)
        ttk.Button(af, text="Save & connect", command=lambda: self.do_add(True)).grid(row=0, column=5, padx=3)
        af.columnconfigure(1, weight=1); af.columnconfigure(3, weight=1)

        lg = ttk.LabelFrame(root, text="Messages"); lg.pack(fill="both", **pad)
        self.log = tk.Text(lg, height=7, wrap="word", state="disabled"); self.log.pack(fill="both", padx=4, pady=4)
        self.busy = False
        root.after(150, self.pump)
        self.say(f"Pi Bluetooth address {PI}. If this is the first time, pair 'a502-wifi' in Settings → Bluetooth (PIN 12345678).")
        self.refresh()

    # ---------- helpers ----------
    def say(self, text):
        self.log.configure(state="normal"); self.log.insert("end", text + "\n"); self.log.see("end")
        self.log.configure(state="disabled")

    def paint_pw(self):
        self.v["password"].set(self.cur_pw if self.showpw.get() else ("•" * 8 if self.cur_pw else "-"))

    def pump(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind in ("info", "progress", "error"):
                    self.say(("⚠ " if kind == "error" else "• ") + data)
                    if kind == "error": self.busy = False
                elif kind == "connected":
                    self.say("Connected to the Pi over Bluetooth.")
                elif kind == "reply":
                    fn, msg = data
                    fn(msg)
        except queue.Empty:
            pass
        self.root.after(150, self.pump)

    def selected_saved(self):
        sel = self.tv_saved.selection()
        if not sel:
            messagebox.showinfo("a502", "Click a network in 'Saved networks' first."); return None
        return next(n for n in self.saved if n["uuid"] == sel[0])

    # ---------- actions ----------
    def refresh(self):
        self.link.request("status", self.got_status)
        self.link.request("list", self.got_list)

    def got_status(self, m):
        st = m.get("status") or {}
        self.v["ssid"].set(f"{st.get('ssid') or 'NOT CONNECTED'}" + (f"   (signal {st['signal']}%)" if st.get("signal") else ""))
        self.cur_pw = st.get("password") or ""; self.paint_pw()
        self.v["ip"].set(st.get("ip") or "-"); self.v["tailscale"].set(st.get("tailscale") or "-")
        self.v["internet"].set("OK" if st.get("internet") else "NOT reachable")
        self.v["user"].set(st.get("user_link") or "-"); self.v["admin"].set(st.get("admin_link") or "-")

    def got_list(self, m):
        self.saved = m.get("saved", [])
        self.tv_saved.delete(*self.tv_saved.get_children())
        for n in self.saved:
            state = "CONNECTED" if n["active"] else ("NO PASSWORD – re-enter" if n["pw"] == "missing" else "")
            self.tv_saved.insert("", "end", iid=n["uuid"], values=(n["ssid"], state))

    def scan(self):
        self.say("• Scanning (about 5 s)…")
        self.link.request("scan", self.got_scan)

    def got_scan(self, m):
        self.visible = m.get("visible", [])
        self.tv_vis.delete(*self.tv_vis.get_children())
        for n in self.visible:
            self.tv_vis.insert("", "end", values=(n["ssid"] + ("  ✓" if n["connected"] else ""), f"{n['signal']}%", n["security"]))
        self.say(f"• {len(self.visible)} network(s) in range.")

    def fill_from_scan(self, _e):
        sel = self.tv_vis.selection()
        if sel:
            self.e_ssid.delete(0, "end"); self.e_ssid.insert(0, self.tv_vis.item(sel[0])["values"][0].replace("  ✓", ""))
            self.e_pw.delete(0, "end"); self.e_pw.focus()

    def switch_done(self, m):
        self.busy = False
        self.say(("✔ " if m.get("ok") else "✘ ") + (m.get("message") or m.get("error") or "done"))
        if m.get("status"): self.got_status(m)
        self.link.request("list", self.got_list)

    def do_connect(self):
        n = self.selected_saved()
        if not n or self.busy: return
        if not messagebox.askyesno("a502", f"Switch the Pi to '{n['ssid']}' now?\n\nThe screen sharing is away for a moment. "
                                           "If it fails, the Pi goes back to the current network by itself."):
            return
        self.busy = True; self.say(f"• Switching to {n['ssid']}…")
        self.link.request("connect", self.switch_done, uuid=n["uuid"])

    def do_password(self):
        n = self.selected_saved()
        if n: self.link.request("password", lambda m: messagebox.showinfo("a502", f"{n['ssid']}\n\nPassword: {m.get('password')}"), uuid=n["uuid"])

    def do_forget(self):
        n = self.selected_saved()
        if not n: return
        if messagebox.askyesno("a502", f"Forget '{n['ssid']}'? Its password is removed from the Pi."):
            self.link.request("delete", lambda m: (self.say(("✔ Forgot " + n["ssid"]) if m.get("ok") else "✘ " + (m.get("error") or "")),
                                                   self.link.request("list", self.got_list)), uuid=n["uuid"])

    def do_add(self, connect):
        ssid, pw = self.e_ssid.get().strip(), self.e_pw.get()
        if not ssid:
            messagebox.showinfo("a502", "Type the network name."); return
        if pw and not 8 <= len(pw) <= 63:
            messagebox.showinfo("a502", "Wi-Fi passwords are 8–63 characters."); return
        if self.busy: return
        if connect:
            if not messagebox.askyesno("a502", f"Save '{ssid}' and switch the Pi to it now?\n\nIf it fails, the Pi goes back by itself."):
                return
            self.busy = True; self.say(f"• Saving and switching to {ssid}…")
        self.link.request("add", self.switch_done, ssid=ssid, password=pw, connect=connect)
        self.e_pw.delete(0, "end")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
