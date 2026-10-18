"""Controlled re-test of the door-sweep groups: stop the instant the seat's 0x4FB status changes or a new ID appears.

Logs every received frame change ("replies"). Baseline 0x4FB byte6 steady bits (byte6 & 0x05) = 0x04.

usage: python trigger_hunt.py [payloads] [ranges] [group] [secs]
   e.g. python trigger_hunt.py 0000000000000000 000-07F 128 8
"""
import re, sys, time, threading, serial, datetime, pathlib

PORT = "COM5"
payloads = (sys.argv[1] if len(sys.argv) > 1 else "0000000000000000,FFFFFFFFFFFFFFFF").split(",")
ranges = sys.argv[2] if len(sys.argv) > 2 else "000-4FF,580-6FF"
group = int(sys.argv[3]) if len(sys.argv) > 3 else 128
secs = float(sys.argv[4]) if len(sys.argv) > 4 else 8
ids = []
for r in ranges.split(","):
    a, _, b = r.partition("-")
    ids += list(range(int(a, 16), int(b or a, 16) + 1))
ids = [i for i in ids if not (0x500 <= i <= 0x57F) and i < 0x700]
KNOWN = {0x4FB, 0x558}
out_path = pathlib.Path(__file__).parent / "logs" / f"trigger_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
rx_re = re.compile(r"^\s*(\d+) RX(.) ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.005)
wlock = threading.Lock()
trig = threading.Event()
trig_reason = []
state = {"steady": None, "phase": "wake"}


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")
    olog.flush()


def send(line):
    with wlock:
        ser.write((line + "\n").encode())


def reader(stop):
    while not stop.is_set():
        raw = ser.readline()
        if not raw:
            continue
        s = raw.decode(errors="replace").rstrip()
        m = rx_re.match(s)
        if not m:
            if "MOTOR" in s or "BUS-OFF" in s or "error-passive" in s:
                out(f"   [{state['phase']}] {s.strip()}")
                if "MOTOR ON" in s and state["phase"] not in ("wake", "after") and not trig.is_set():
                    trig_reason.append("motor/actuator started"); trig.set()
            continue
        cid = int(m.group(3), 16)
        data = [int(x, 16) for x in m.group(4).split()]
        if cid == 0x4FB and len(data) >= 8:
            steady = data[6] & 0x05
            if state["steady"] is None:
                state["steady"] = steady
            elif steady != state["steady"]:
                out(f"   [{state['phase']}] 4FB status change: {s.strip()}")
                if state["phase"] != "wake" and not trig.is_set():
                    trig_reason.append(f"4FB byte6 steady {state['steady']:02X}->{steady:02X}")
                    trig.set()
                state["steady"] = steady
            continue
        if cid == 0x558 and m.group(2) != "+":
            continue  # NM ring chatter
        out(f"   [{state['phase']}] reply: {s.strip()}")
        if cid not in KNOWN and m.group(2) == "+" and state["phase"] != "wake" and not trig.is_set():
            trig_reason.append(f"new ID {cid:03X}")
            trig.set()


stop = threading.Event()
threading.Thread(target=reader, args=(stop,), daemon=True).start()
send("x"); time.sleep(0.5)
send("v")  # verbose: firmware no longer prints 0x4FB changes by default
send("n")
out("### waking (network partner only)")
t_w = time.time()
while time.time() - t_w < 25 and not (state["steady"] is not None and time.time() - t_w > 3):
    time.sleep(0.2)
out(f"### baseline 0x4FB steady bits = {state['steady']}")
phases = [(p, ids[k:k + group]) for p in payloads for k in range(0, len(ids), group)]
hit = None
for p, grp in phases:
    state["phase"] = f"{p[:2]}x8 {grp[0]:03X}-{grp[-1]:03X}"
    t_end = time.time() + secs
    while time.time() < t_end and not trig.is_set():
        for cid in grp:
            if trig.is_set():
                break
            send(f"t{cid:03X} {p}")
            time.sleep(0.0016)
    if trig.is_set():
        hit = (p, grp)
        out(f"*** TRIGGER during {state['phase']}: {trig_reason}")
        break
    out(f"    {state['phase']}: no change")
# stop blasting immediately; keep NM partner only and watch whether the state clears by itself
state["phase"] = "after"
out("### blast stopped; watching 10 s with network partner only")
time.sleep(10)
out(f"### 0x4FB steady bits now = {state['steady']}")
send("x")
time.sleep(0.5)
send("v"); send("n"); send("e")  # verbose off, restore normal operation: NM partner + seat-enable 0x0B8
stop.set()
out(f"### result: {'HIT ' + hit[0][:2] + ' ' + format(hit[1][0], '03X') + '-' + format(hit[1][-1], '03X') if hit else 'no trigger'}")
out(f"log saved: {out_path}")
