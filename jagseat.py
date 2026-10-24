"""JagSeat PC app: control the seat from the command line (for macros / Steam Deck) or a small window.

Talks to the ESP32 over Wi-Fi (its /api endpoints) by default, or over USB with --usb COM5.

  python jagseat.py                      open the button window
  python jagseat.py status               print seat status
  python jagseat.py massage on|off|30    massage on / off / any variant byte
  python jagseat.py enable on|off        seat-enable 0x0B8 on / off

  --host jagseat.local   board address on Wi-Fi (or set JAGSEAT_HOST, e.g. 192.168.12.241)
  --usb COM5             use the USB serial port instead (needs pyserial)

Exit code is 0 on success, 1 if the board couldn't be reached, so macros can tell.
"""
import argparse, json, os, sys, time, urllib.request

VARIANTS = ["10", "30", "50", "90", "11", "12", "14", "18"]


class WifiLink:
    def __init__(self, host):
        self.base = f"http://{host}"

    def call(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as r:
            return json.loads(r.read())

    def status(self):
        return self.call("/api/status")

    def massage(self, v):
        return self.call(f"/api/massage?v={v}")

    def enable(self, on):
        return self.call(f"/api/enable?on={1 if on else 0}")


class UsbLink:
    def __init__(self, port):
        import serial  # pip install pyserial
        self.ser = serial.Serial(port, 115200, timeout=0.1)

    def status(self):
        self.ser.reset_input_buffer()
        self.ser.write(b"j\n")
        t = time.time()
        while time.time() - t < 2:
            line = self.ser.readline().decode(errors="replace").strip()
            if line.startswith("J {"):
                return json.loads(line[2:])
        raise OSError("no status reply on USB")

    def massage(self, v):
        self.ser.write(f"M{v}\n".encode())
        return self.status()

    def enable(self, on):
        if self.status().get("tx_enable") != on:
            self.ser.write(b"e\n")  # 'e' toggles, so only send it when the state differs
        return self.status()


def describe(d):
    if not d.get("seat"):
        return "seat: no data (is the seat powered and the CAN wired?)"
    return (f"seat: connected | continuous movement: {'yes' if d['enabled'] else 'inch mode'} | "
            f"massage: {'running' if d['massage'] else 'off'} | motor: {'running' if d['motor'] else 'idle'} | "
            f"firmware {d.get('fw', '?')}")


def on_off(word):
    if word.lower() in ("on", "1", "true"):
        return True
    if word.lower() in ("off", "0", "false"):
        return False
    raise SystemExit(f"expected on/off, got {word!r}")


def run_cli(link, args):
    if args.cmd == "status":
        d = link.status()
    elif args.cmd == "massage":
        v = args.value or "on"
        v = {"on": "10", "off": "00"}.get(v.lower(), v)
        d = link.massage(f"{int(v, 16):02X}")
    elif args.cmd == "enable":
        d = link.enable(on_off(args.value or "on"))
    else:
        raise SystemExit(f"unknown command {args.cmd!r}")
    print(describe(d))


def run_gui(link):
    import tkinter as tk

    root = tk.Tk()
    root.title("JagSeat")
    root.configure(bg="#111")
    st = tk.StringVar(value="connecting...")
    tk.Label(root, textvariable=st, bg="#111", fg="#eee", justify="left", wraplength=360,
             font=("Segoe UI", 11)).pack(padx=12, pady=10, anchor="w")

    def act(fn):
        try:
            st.set(describe(fn()))
        except Exception as e:
            st.set(f"board not reachable: {e}")

    def row(buttons):
        f = tk.Frame(root, bg="#111")
        f.pack(padx=12, pady=4, fill="x")
        for text, fn, color in buttons:
            tk.Button(f, text=text, command=lambda fn=fn: act(fn), bg=color, fg="#eee", relief="flat",
                      font=("Segoe UI", 11), padx=8, pady=8).pack(side="left", expand=True, fill="x", padx=3)

    row([("Massage on", lambda: link.massage("10"), "#14532d"), ("Massage off", lambda: link.massage("00"), "#7f1d1d")])
    row([(v, lambda v=v: link.massage(v), "#2a2a2a") for v in VARIANTS[:4]])
    row([(v, lambda v=v: link.massage(v), "#2a2a2a") for v in VARIANTS[4:]])
    row([("Enable", lambda: link.enable(True), "#2a2a2a"), ("Disable", lambda: link.enable(False), "#2a2a2a")])

    def poll():
        act(link.status)
        root.after(1500, poll)

    poll()
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description="Control the Jaguar seat ESP32 from the PC.")
    p.add_argument("cmd", nargs="?", help="status | massage | enable (leave out for the window)")
    p.add_argument("value", nargs="?", help="on | off | variant byte like 30")
    p.add_argument("--host", default=os.environ.get("JAGSEAT_HOST", "jagseat.local"))
    p.add_argument("--usb", metavar="PORT", help="use USB serial (e.g. COM5) instead of Wi-Fi")
    args = p.parse_args()
    link = UsbLink(args.usb) if args.usb else WifiLink(args.host)
    if not args.cmd:
        run_gui(link)
        return
    try:
        run_cli(link, args)
    except (OSError, ValueError) as e:
        print(f"board not reachable: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
