"""Replay a candump log to the seat (looped) while polling the seat module's DIDs/DTC status for a reaction.

usage: python replay.py <candump.log> [seconds] [exclude-ranges e.g. 500-57F,088]
"""
import re, sys, time, threading, serial, datetime, pathlib

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
logfile = sys.argv[1]
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 90
excl = []
for part in (sys.argv[3] if len(sys.argv) > 3 else "500-57F").split(","):
    a, _, b = part.partition("-")
    excl.append((int(a, 16), int(b or a, 16)))
WATCH_DIDS = [0xDD00, 0xDD01, 0xDD02, 0xDD04, 0xDD06]

out_path = pathlib.Path(__file__).parent / "logs" / f"replay_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.02)
wlock = threading.Lock()
stop = threading.Event()


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")


def send(line):
    with wlock:
        ser.write((line + "\n").encode())


frames = []
for ln in open(logfile):
    m = re.match(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-F]{3})#([0-9A-F]*)", ln.strip())
    if not m:
        continue
    cid = int(m.group(2), 16)
    if any(lo <= cid <= hi for lo, hi in excl) or cid >= 0x700:
        continue
    frames.append((float(m.group(1)), cid, m.group(3).ljust(16, "0")[:16]))
ids = sorted({f[1] for f in frames})
out(f"### {len(frames)} frames, {len(ids)} IDs: {' '.join(f'{i:03X}' for i in ids)}")


def player():
    while not stop.is_set():
        t0, w0 = frames[0][0], time.time()
        for ts, cid, data in frames:
            if stop.is_set():
                return
            delay = (ts - t0) - (time.time() - w0)
            if delay > 0:
                time.sleep(delay)
            send(f"t{cid:03X} {data}")


def rx(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        s = raw.decode(errors="replace").rstrip()
        if "MOTOR" in s:
            out("   " + s)
        m = rx_re.match(s)
        if m and int(m.group(1), 16) == RESP:
            yield bytes(int(x, 16) for x in m.group(2).split())


def request(hexpayload, wait=0.6):
    n = len(hexpayload) // 2
    send(f"t{REQ:03X} {n:02X}{hexpayload}{'00' * (7 - n)}")
    buf, total = b"", None
    for d in rx(wait):
        pci = d[0] >> 4
        if pci == 0:
            p = d[1:1 + (d[0] & 0x0F)]
            if len(p) >= 3 and p[0] == 0x7F and p[2] == 0x78:
                continue
            return p
        if pci == 1:
            total = ((d[0] & 0x0F) << 8) | d[1]
            buf = d[2:]
            send(f"t{REQ:03X} 3000000000000000")
        elif pci == 2 and total is not None:
            buf += d[1:]
            if len(buf) >= total:
                return buf[:total]
    return None


def snapshot():
    snap = {}
    for did in WATCH_DIDS:
        r = request(f"22{did:04X}")
        snap[f"{did:04X}"] = r[3:].hex() if r and r[0] == 0x62 else None
    r = request("1902FF", wait=1.0)
    if r and r[0] == 0x59:
        recs = r[3:]
        snap["dtc"] = " ".join(f"{recs[i:i+3].hex()}:{recs[i+3]:02x}" for i in range(0, len(recs) - 3, 4))
    else:
        snap["dtc"] = None
    return snap


send("x"); time.sleep(0.5); ser.reset_input_buffer()
send(sys.argv[4] if len(sys.argv) > 4 else "n")
for _ in rx(12):
    pass
base = snapshot()
out(f"### baseline: {base}")
th = threading.Thread(target=player, daemon=True)
th.start()
t_start = time.time()
while time.time() - t_start < duration:
    for _ in rx(8):
        pass
    s = snapshot()
    diff = {k: v for k, v in s.items() if v != base.get(k)}
    out(f"### t+{time.time() - t_start:4.0f}s {'CHANGED ' + str(diff) if diff else 'no change'}")
stop.set()
th.join(2)
send("x")
out(f"log saved: {out_path}")
