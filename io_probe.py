"""Probe which DIDs the seat module accepts for IO control (UDS 0x2F) -- uses only 'returnControlToECU' (00), nothing moves.

Also reports session / security requirements.
"""
import re, sys, time, threading, serial, datetime, pathlib

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
RANGES = [(0xD000, 0xDFFF), (0x4000, 0x4FFF), (0x0000, 0x0FFF), (0xE000, 0xEFFF), (0xF000, 0xF0FF), (0xFD00, 0xFEFF)]
out_path = pathlib.Path(__file__).parent / "logs" / f"io_probe_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.005)
NRC = {0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported", 0x13: "badLength", 0x22: "conditionsNotCorrect",
       0x31: "requestOutOfRange", 0x33: "securityAccessDenied", 0x7E: "subFnNotSupportedInSession",
       0x7F: "serviceNotSupportedInSession", 0x24: "requestSequenceError", 0x35: "invalidKey"}


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")


def send(line):
    ser.write((line + "\n").encode())


def rx(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if raw:
            m = rx_re.match(raw.decode(errors="replace").rstrip())
            if m and int(m.group(1), 16) == RESP:
                yield bytes(int(x, 16) for x in m.group(2).split())


def request(hexpayload, wait=0.3):
    n = len(hexpayload) // 2
    send(f"t{REQ:03X} {n:02X}{hexpayload}{'00' * (7 - n)}")
    for d in rx(wait):
        if d[0] >> 4 == 0:
            p = d[1:1 + (d[0] & 0x0F)]
            if len(p) >= 3 and p[0] == 0x7F and p[2] == 0x78:
                continue
            return p
        if d[0] >> 4 == 1:
            send(f"t{REQ:03X} 3000000000000000")
            return d[2:]
    return None


def desc(p):
    if p is None:
        return "no response"
    if p[0] == 0x7F:
        return f"NEG {NRC.get(p[2], hex(p[2]))}"
    return "POS " + p.hex(" ").upper()


keep = threading.Event()


def tester_present():
    while not keep.is_set():
        send(f"t{REQ:03X} 023E800000000000")  # suppress-positive-response tester present
        time.sleep(1.5)


send("x"); time.sleep(0.5); ser.reset_input_buffer()
send("n")
for _ in rx(12):
    pass
out(f"### default session, 2F D000 00 : {desc(request('2FD00000'))}")
out(f"### extended session 10 03      : {desc(request('1003'))}")
threading.Thread(target=tester_present, daemon=True).start()
out(f"### security seed 27 01         : {desc(request('2701'))}")
out(f"### security seed 27 03         : {desc(request('2703'))}")
out(f"### routine probe 31 01 0202    : (skipped, could start a routine)")
found = []
nrc_count = {}
for lo, hi in RANGES:
    out(f"### scanning 2F {lo:04X}-{hi:04X} (returnControlToECU only)")
    for did in range(lo, hi + 1):
        p = request(f"2F{did:04X}00", wait=0.12)
        if p is None:
            continue
        if p[0] == 0x7F:
            nrc_count[p[2]] = nrc_count.get(p[2], 0) + 1
            if p[2] != 0x31:
                out(f"   {did:04X}: {desc(p)}")
                found.append((did, desc(p)))
        else:
            out(f"   {did:04X}: {desc(p)}")
            found.append((did, desc(p)))
keep.set()
out(f"### NRC summary: { {NRC.get(k, hex(k)): v for k, v in nrc_count.items()} }")
out(f"### candidates: {found}")
request("1001")
send("x")
out(f"log saved: {out_path}")
