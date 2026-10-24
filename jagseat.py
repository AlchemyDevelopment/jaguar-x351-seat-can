"""JagSeat PC app: control the seat from the command line (for macros / Steam Deck) or a small window.

Talks to the ESP32 over Wi-Fi (its /api endpoints) by default, or over USB with --usb COM5.

  python jagseat.py                      open the button window
  python jagseat.py status               print seat status
  python jagseat.py massage on|off|30    massage on / off / any variant byte
  python jagseat.py cycle 30|off         cycle through all programs every 30 s (runs on the board) / stop
  python jagseat.py enable on|off        seat-enable 0x0B8 on / off

  --host jagseat.local   board address on Wi-Fi (or set JAGSEAT_HOST, e.g. 192.168.12.241)
  --usb COM5             use the USB serial port instead (needs pyserial)

Exit code is 0 on success, 1 if the board couldn't be reached, so macros can tell.
"""
import argparse, json, os, queue, socket, sys, threading, time, urllib.request

VARIANTS = ["10", "30", "50", "90", "11", "12", "14", "18"]


class WifiLink:
    def __init__(self, host):
        try:
            host = socket.gethostbyname(host)  # resolve jagseat.local once; Windows mDNS lookups take ~3 s each
        except OSError:
            pass
        self.base = f"http://{host}"

    def call(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as r:
            return json.loads(r.read())

    def status(self):
        return self.call("/api/status")

    def massage(self, v):
        return self.call(f"/api/massage?v={v}")

    def cycle(self, secs):
        return self.call(f"/api/cycle?s={secs}")

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

    def cycle(self, secs):
        self.ser.write(f"C{secs}\n".encode())
        return self.status()

    def enable(self, on):
        if self.status().get("tx_enable") != on:
            self.ser.write(b"e\n")  # 'e' toggles, so only send it when the state differs
        return self.status()


def describe(d):
    if not d.get("seat"):
        return "seat: no data (is the seat powered and the CAN wired?)"
    prog = d.get("variant", "00")
    massage = (f"running, program {prog_name(prog)}" if prog != "00" else "running, program unknown") if d["massage"] else "off"
    if d["massage"] and d.get("cycle"):
        massage += f" (cycling every {d['cycle']} s, next in {d.get('cycle_next', '?')} s)"
    return (f"seat: connected | continuous movement: {'yes' if d['enabled'] else 'inch mode'} | "
            f"massage: {massage} | motor: {'running' if d['motor'] else 'idle'} | "
            f"firmware {d.get('fw', '?')}")


def prog_name(v):
    return f"P{VARIANTS.index(v) + 1} ({v})" if v in VARIANTS else v


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
    elif args.cmd == "cycle":
        v = (args.value or "30").lower()
        d = link.cycle(0 if v in ("off", "0", "stop") else int(v))
    elif args.cmd == "enable":
        d = link.enable(on_off(args.value or "on"))
    else:
        raise SystemExit(f"unknown command {args.cmd!r}")
    print(describe(d))


def run_gui(link):
    import tkinter as tk

    BG, CARD, LINE, TX, MUTE = "#0b0c0d", "#17191c", "#2a2e33", "#ece8e1", "#8d8a85"
    GOLD, GOLD2, GREEN, AMBER, ACT = "#c9a45c", "#e6c88a", "#3fb97a", "#e0a43a", "#2a2418"
    UI, SERIF = ("Segoe UI", 11), ("Georgia", 20)
    root = tk.Tk()
    root.title("JagSeat")
    root.configure(bg=BG, padx=16, pady=14)
    root.resizable(False, False)
    state = {"d": {}}
    results = queue.Queue()

    def work(fn):  # board calls run off the UI thread so the window never freezes
        def go():
            try:
                results.put(fn())
            except Exception as e:
                results.put(e)
        threading.Thread(target=go, daemon=True).start()

    def button(parent, text, fn, **kw):
        return tk.Button(parent, text=text, command=lambda: work(fn), bg=CARD, fg=TX, activebackground=LINE,
                         activeforeground=TX, relief="flat", bd=0, highlightthickness=1, highlightbackground=LINE,
                         font=UI, padx=10, pady=9, cursor="hand2", **kw)

    def card(title):
        f = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=12, pady=10)
        f.pack(fill="x", pady=6)
        tk.Label(f, text=title.upper(), bg=CARD, fg=MUTE, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))
        return f

    def toggle_massage():
        d = state["d"]
        v = d.get("variant", "00")
        return link.massage("00" if d.get("massage") else (v if v != "00" else "10"))

    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", pady=(0, 6))
    tk.Label(head, text="Jaguar", bg=BG, fg=TX, font=SERIF).pack(side="left")
    tk.Label(head, text=" Seat", bg=BG, fg=GOLD, font=SERIF).pack(side="left")
    link_lbl = tk.Label(head, text="connecting", bg=BG, fg=MUTE, font=UI)
    link_lbl.pack(side="right")

    f = card("Massage")
    top = tk.Frame(f, bg=CARD)
    top.pack(fill="x")
    pwr = button(top, "Start", toggle_massage, width=8)
    pwr.pack(side="left")
    now = tk.Label(top, text="-", bg=CARD, fg=TX, font=("Georgia", 15), anchor="w")
    now.pack(side="left", padx=12, fill="x")
    sub = tk.Label(f, text="", bg=CARD, fg=MUTE, font=("Segoe UI", 9), anchor="w")
    sub.pack(fill="x", pady=(6, 0))

    f = card("Programs")
    grid = tk.Frame(f, bg=CARD)
    grid.pack(fill="x")
    progs = {}
    for i, v in enumerate(VARIANTS):
        b = button(grid, f"P{i + 1}\n{v}", lambda v=v: link.massage(v), width=6)
        b.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="ew")
        grid.columnconfigure(i % 4, weight=1)
        progs[v] = b

    f = card("Cycle all programs")
    crow = tk.Frame(f, bg=CARD)
    crow.pack(fill="x")
    pick = {"s": 30}
    segs = {}

    def interval(v):  # tapping a new interval while cycling applies it now
        pick["s"] = v
        paint_segs()
        if state["d"].get("cycle"):
            work(lambda: link.cycle(v))

    def paint_segs():
        live = bool(state["d"].get("cycle"))
        for v, b in segs.items():
            sel = v == pick["s"]
            b.configure(bg=(ACT if live else LINE) if sel else CARD, fg=(GOLD2 if live else TX) if sel else MUTE)

    for i, (v, label) in enumerate([(15, "15s"), (30, "30s"), (60, "1m"), (120, "2m"), (300, "5m")]):
        b = tk.Button(crow, text=label, command=lambda v=v: interval(v), relief="flat", bd=0, highlightthickness=0,
                      activebackground=LINE, activeforeground=TX, font=UI, pady=7, cursor="hand2")
        b.grid(row=0, column=i, sticky="ew", padx=1)
        crow.columnconfigure(i, weight=1)
        segs[v] = b
    cyc = button(f, "Start cycle", lambda: link.cycle(0 if state["d"].get("cycle") else pick["s"]))
    cyc.pack(fill="x", pady=(8, 0))

    f = card("Seat")
    srow = tk.Frame(f, bg=CARD)
    srow.pack(fill="x")
    seat = tk.Label(srow, text="-", bg=CARD, fg=MUTE, font=UI, anchor="w")
    seat.pack(side="left", fill="x", expand=True)
    fw = tk.Label(root, text="", bg=BG, fg=MUTE, font=("Segoe UI", 8))
    fw.pack(anchor="w", pady=(6, 0))

    def show(d):
        if isinstance(d, Exception):
            link_lbl.configure(text="board offline", fg=AMBER)
            return
        state["d"] = d
        ok = d.get("seat")
        run = ok and d.get("massage")
        v = d.get("variant", "00")
        link_lbl.configure(text="seat connected" if ok else "no seat data", fg=GREEN if ok else AMBER)
        pwr.configure(text="Stop" if run else "Start", fg=GOLD2 if run else TX, highlightbackground=GOLD if run else LINE)
        now.configure(text="-" if not ok else "Off" if not run else
                      f"Program {VARIANTS.index(v) + 1}" if v in VARIANTS else "Running")
        sub.configure(text=f"Cycling every {d['cycle']} s, next in {d.get('cycle_next', '?')} s" if run and d.get("cycle")
                      else f"0x4CB byte7 = {v}" if run and v != "00"
                      else "Program unknown (started before the ESP32 restarted)" if run
                      else "Pick a program or start the cycle")
        for pv, b in progs.items():
            on = bool(run and pv == v)
            b.configure(bg=ACT if on else CARD, fg=GOLD2 if on else TX, highlightbackground=GOLD if on else LINE)
        cyc.configure(text="Stop cycle" if d.get("cycle") else "Start cycle", fg=GOLD2 if d.get("cycle") else TX,
                      highlightbackground=GOLD if d.get("cycle") else LINE)
        if d.get("cycle"):
            pick["s"] = d["cycle"]
        paint_segs()
        seat.configure(text="-" if not ok else "continuous movement" if d.get("enabled") else "inch mode",
                       fg=GREEN if ok and d.get("enabled") else AMBER if ok else MUTE)
        fw.configure(text=f"firmware {d.get('fw', '?')}" + (f"   |   {d['ip']}" if d.get("ip") else ""))

    def pump():
        while not results.empty():
            show(results.get())
        root.after(100, pump)

    def poll():
        work(link.status)
        root.after(1000, poll)

    pump()
    poll()
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description="Control the Jaguar seat ESP32 from the PC.")
    p.add_argument("cmd", nargs="?", help="status | massage | cycle | enable (leave out for the window)")
    p.add_argument("value", nargs="?", help="on | off | variant byte like 30 | cycle seconds")
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
