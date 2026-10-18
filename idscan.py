"""Replay one CAN ID at a time from a candump and see which seat DIDs it fills in (attribution of DD00..DD06)."""
import re, sys, time, serial, datetime, pathlib

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
WATCH = [0xDD00, 0xDD01, 0xDD02, 0xDD04, 0xDD06]
logfile = sys.argv[1]
only = [int(x, 16) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else None
out_path = pathlib.Path(__file__).parent / "logs" / f"idscan_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.01)


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")


def send(line):
    ser.write((line + "\n").encode())


byid = {}
for ln in open(logfile):
    m = re.match(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-F]{3})#([0-9A-F]*)", ln.strip())
    if m:
        cid = int(m.group(2), 16)
        if not (0x500 <= cid <= 0x57F) and cid < 0x700:
            byid.setdefault(cid, []).append(m.group(3).ljust(16, "0")[:16])


def rx(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if raw:
            m = rx_re.match(raw.decode(errors="replace").rstrip())
            if m and int(m.group(1), 16) == RESP:
                yield bytes(int(x, 16) for x in m.group(2).split())


def read_did(did):
    send(f"t{REQ:03X} 0322{did:04X}00000000")
    for d in rx(0.25):
        if d[0] >> 4 == 0 and d[1] == 0x62:
            return d[4:1 + (d[0] & 0x0F)].hex()
    return None


def snap():
    return {f"{d:04X}": read_did(d) for d in WATCH}


send("x"); time.sleep(0.5); ser.reset_input_buffer()
send("n")
for _ in rx(12):
    pass
prev = snap()
out(f"### baseline {prev}")
for cid in sorted(byid):
    if only and cid not in only:
        continue
    data = byid[cid]
    t_end, i = time.time() + 3.0, 0
    while time.time() < t_end:
        send(f"t{cid:03X} {data[i % len(data)]}")
        i += 1
        time.sleep(0.03)
    s = snap()
    diff = {k: f"{prev[k]}->{v}" for k, v in s.items() if v != prev[k]}
    out(f"   {cid:03X} ({len(data)} frames, e.g. {data[0]}): {diff if diff else '-'}")
    prev = s
send("x")
out(f"log saved: {out_path}")
