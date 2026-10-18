"""Poll candidate seat position DIDs while the user presses switches (read-only)."""
import re, sys, time, serial, datetime, pathlib

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
DIDS = [0x40A3, 0x40D8, 0xD900, 0xD901, 0xD110]
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
out_path = pathlib.Path(__file__).parent / "logs" / f"pos_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
rx_re = re.compile(r"^\s*(\d+) RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.005)


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")


def send(line):
    ser.write((line + "\n").encode())


def rx(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        s = raw.decode(errors="replace").rstrip()
        if "MOTOR" in s:
            out(s)
        m = rx_re.match(s)
        if m and int(m.group(2), 16) == RESP:
            yield bytes(int(x, 16) for x in m.group(3).split())


def read_did(did):
    send(f"t{REQ:03X} 0322{did:04X}00000000")
    for d in rx(0.25):
        if d[0] >> 4 == 0:
            p = d[1:1 + (d[0] & 0x0F)]
            if p[:1] == b"\x62":
                return p[3:].hex(" ").upper()
            if p[:1] == b"\x7f" and p[2] != 0x78:
                return f"NRC{p[2]:02X}"
        elif d[0] >> 4 == 1:
            send(f"t{REQ:03X} 3000000000000000")
            buf, total = d[2:], ((d[0] & 0xF) << 8) | d[1]
            for c in rx(0.3):
                buf += c[1:]
                if len(buf) >= total:
                    return buf[3:total].hex(" ").upper()
    return None


send("x"); time.sleep(0.5); ser.reset_input_buffer()
send("i"); send("n")
for _ in rx(12):
    pass
prev = None
t0 = time.time()
while time.time() - t0 < secs:
    cur = {f"{d:04X}": read_did(d) for d in DIDS}
    if cur != prev:
        out(f"t+{time.time() - t0:5.1f}s " + "  ".join(f"{k}={v}" for k, v in cur.items()))
        prev = cur
send("x")
out(f"log saved: {out_path}")
