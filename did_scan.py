"""Read-only scan of seat-module data identifiers (UDS 0x22) with and without the 0x088 ignition frame.

Shows which DIDs exist and which change with the ignition message (e.g. the module's view of power mode).
"""
import re, time, serial, datetime, pathlib

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
RANGES = [(0xD100, 0xD1FF), (0xDD00, 0xDDFF), (0xDE00, 0xDE0F), (0xF100, 0xF1FF), (0x4000, 0x40FF)]
log_path = pathlib.Path(__file__).parent / "logs" / f"did_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
log = open(log_path, "w")
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.02)


def out(s):
    print(s, flush=True)
    log.write(s + "\n")


def send(line):
    ser.write((line + "\n").encode())


def frames(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        s = raw.decode(errors="replace").rstrip()
        m = rx_re.match(s)
        if m and int(m.group(1), 16) == RESP:
            yield bytes(int(x, 16) for x in m.group(2).split())


def read_did(did, wait=0.25):
    send(f"t{REQ:03X} 0322{did:04X}00000000")
    buf, total = b"", None
    for d in frames(wait):
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


def scan(label):
    found = {}
    for lo, hi in RANGES:
        for did in range(lo, hi + 1):
            r = read_did(did)
            if r and r[0] == 0x62:
                found[did] = r[3:]
    out(f"### {label}: {len(found)} DIDs")
    return found


send("x"); time.sleep(0.5); ser.reset_input_buffer()
out("### wake: ignition + NM partner")
send("i"); send("n")
for _ in frames(15):
    pass
a = scan("IGNITION ON (0x088 mode 6)")
send("i")  # ignition frame off, NM partner keeps module awake
for _ in frames(5):
    pass
b = scan("IGNITION FRAME OFF")
send("x")
out("### DID values (* = changed with ignition frame)")
for did in sorted(set(a) | set(b)):
    va, vb = a.get(did), b.get(did)
    mark = "*" if va != vb else " "
    asc = bytes(c if 32 <= c < 127 else 46 for c in (va or b"")).decode()
    out(f" {mark} {did:04X}  on={va.hex(' ').upper() if va else '-':<30} off={vb.hex(' ').upper() if vb else '-':<30} {asc}")
out(f"log saved: {log_path}")
