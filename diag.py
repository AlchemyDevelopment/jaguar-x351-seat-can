"""Find the seat module's UDS diagnostic address on MS-CAN and read its DTCs (read-only).

Keeps ignition (0x088) + NM partner running so the module stays awake.
"""
import re, sys, time, serial, datetime, pathlib

PORT = "COM5"
log_path = pathlib.Path(__file__).parent / "logs" / f"diag_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
log_path.parent.mkdir(exist_ok=True)
log = open(log_path, "w")
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")

ser = serial.Serial(PORT, 115200, timeout=0.05)


def out(s):
    print(s, flush=True)
    log.write(s + "\n")


def send(line):
    ser.write((line + "\n").encode())


def frames(seconds, want=None):
    """Yield (id, bytes) of received diagnostic frames (ID >= 0x700) for `seconds`."""
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        s = raw.decode(errors="replace").rstrip()
        log.write(s + "\n")
        m = rx_re.match(s)
        if m:
            cid = int(m.group(1), 16)
            data = bytes(int(x, 16) for x in m.group(2).split())
            if cid >= 0x700 and (want is None or cid == want):
                yield cid, data


def request(req_id, payload, resp_id, wait=1.5):
    """Single ISO-TP request; handles multi-frame responses. Returns UDS payload bytes or None."""
    assert len(payload) <= 7
    send(f"t{req_id:03X} {bytes([len(payload)] + list(payload) + [0] * (7 - len(payload))).hex().upper()}")
    buf, total = b"", None
    for cid, d in frames(wait, resp_id):
        pci = d[0] >> 4
        if pci == 0:  # single frame
            p = d[1:1 + (d[0] & 0x0F)]
            if len(p) >= 3 and p[0] == 0x7F and p[2] == 0x78:  # response pending
                continue
            return p
        if pci == 1:  # first frame -> send flow control
            total = ((d[0] & 0x0F) << 8) | d[1]
            buf = d[2:]
            send(f"t{req_id:03X} 3000000000000000")
        elif pci == 2 and total is not None:
            buf += d[1:]
            if len(buf) >= total:
                return buf[:total]
    return None


send("x")
time.sleep(0.5)
ser.reset_input_buffer()
out("### waking seat: ignition + NM partner, 15 s")
send("i"); send("n")
for _ in frames(15):
    pass

out("### functional TesterPresent / DiagnosticSessionControl on 0x7DF")
responders = set()
for req in ("t7DF 023E000000000000", "t7DF 0210010000000000"):
    send(req)
    for cid, d in frames(1.0):
        out(f"   response from {cid:03X}: {d.hex(' ').upper()}")
        responders.add(cid)

if not responders:
    out("### no functional response; scanning physical IDs 0x700-0x7EF")
    for rid in range(0x700, 0x7F0):
        send(f"t{rid:03X} 0210010000000000")
        for cid, d in frames(0.06):
            out(f"   request {rid:03X} -> response from {cid:03X}: {d.hex(' ').upper()}")
            responders.add(cid)

out(f"### responders: {[hex(r) for r in sorted(responders)]}")
for resp in sorted(responders):
    req = resp - 8
    out(f"### module resp {resp:03X} (req {req:03X})")
    for name, pl in (("ReadDTC 19 02 FF", [0x19, 0x02, 0xFF]),
                     ("ReadDTC 19 0A (all supported)", [0x19, 0x0A]),
                     ("PartNumber F187", [0x22, 0xF1, 0x87]),
                     ("PartNumber F188", [0x22, 0xF1, 0x88]),
                     ("F113", [0x22, 0xF1, 0x13]),
                     ("ECU name F190", [0x22, 0xF1, 0x90])):
        r = request(req, pl, resp)
        if r is None:
            out(f"   {name}: no response")
            continue
        out(f"   {name}: {r.hex(' ').upper()}")
        if pl[0] == 0x22 and r[0] == 0x62:
            out(f"      ascii: {bytes(c if 32 <= c < 127 else 46 for c in r[3:]).decode()}")
        if pl[:2] == [0x19, 0x02] and r[0] == 0x59:
            recs = r[3:]
            for i in range(0, len(recs) - 3, 4):
                dtc = recs[i:i + 3]
                st = recs[i + 3]
                letter = "PCBU"[dtc[0] >> 6]
                code = f"{letter}{(dtc[0] >> 4) & 3}{dtc[0] & 0xF:X}{dtc[1]:02X}-{dtc[2]:02X}"
                out(f"      DTC {code}  status {st:02X}")

send("x")
time.sleep(0.5)
out(f"log saved: {log_path}")
