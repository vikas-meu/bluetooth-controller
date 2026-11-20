#!/usr/bin/python3
"""Open the a502 Pi's Bluetooth Wi-Fi menu from this computer.
First time: pair the Pi in Settings -> Bluetooth ("a502-wifi", PIN 12345678), then run:  a502-bt
"""
import os, select, socket, sys, termios
PI = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("A502_BT", "88:A2:9E:2B:B5:B6")
s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
s.settimeout(15)
try:
    s.connect((PI, 1))
except OSError as e:
    print(f"Could not reach the Pi over Bluetooth ({PI}): {e}")
    print("Is it paired? Settings -> Bluetooth -> a502-wifi -> PIN 12345678. Is the Pi within ~10 m?")
    sys.exit(1)
s.settimeout(None)
print(f"Connected to {PI} over Bluetooth. Type q + Enter to quit.\n")
try:
    while True:
        r, _, _ = select.select([s, sys.stdin], [], [])
        if s in r:
            d = s.recv(4096)
            if not d:
                print("\n[Pi closed the connection]"); break
            sys.stdout.write(d.decode(errors="replace").replace("\r\n", "\n")); sys.stdout.flush()
        if sys.stdin in r:
            line = sys.stdin.readline()
            if not line:
                break
            s.sendall(line.rstrip("\n").encode() + b"\r\n")
except KeyboardInterrupt:
    pass
finally:
    s.close()
