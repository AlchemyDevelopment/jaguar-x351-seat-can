"""Clear all DTCs in the passenger seat module (UDS 0x14 FFFFFF), then re-read them."""
import re, time, serial

PORT, REQ, RESP = "COM5", 0x7A3, 0x7AB
rx_re = re.compile(r"^\s*\d+ RX. ([0-9A-F]{3}) \[\d\]((?: [0-9A-F]{2})*)")
ser = serial.Serial(PORT, 115200, timeout=0.02)


def send(line):
    ser.write((line + "\n").encode())


def frames(seconds):
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if raw:
            m = rx_re.match(raw.decode(errors="replace").rstrip())
            if m and int(m.group(1), 16) == RESP:
                yield bytes(int(x, 16) for x in m.group(2).split())


def request(hexpayload, wait=2.0):
    n = len(hexpayload) // 2
    send(f"t{REQ:03X} {n:02X}{hexpayload}{'00' * (7 - n)}")
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


def show_dtcs(label):
    r = request("1902FF")
    print(f"### {label}: {r.hex(' ').upper() if r else 'no response'}")
    if r and r[0] == 0x59:
        recs = r[3:]
        if not recs:
            print("   (no DTCs)")
        for i in range(0, len(recs) - 3, 4):
            d = recs[i:i + 3]
            print(f"   DTC {'PCBU'[d[0] >> 6]}{(d[0] >> 4) & 3}{d[0] & 0xF:X}{d[1]:02X}-{d[2]:02X}  status {recs[i + 3]:02X}")


send("x"); time.sleep(0.5); ser.reset_input_buffer()
print("### wake: ignition + NM partner, 15 s")
send("i"); send("n")
for _ in frames(15):
    pass
show_dtcs("before")
r = request("14FFFFFF", wait=3.0)
print(f"### clear DTCs: {r.hex(' ').upper() if r else 'no response'}  ({'OK' if r and r[0] == 0x54 else 'NOT accepted'})")
time.sleep(1)
show_dtcs("after clear")
print("### waiting 20 s for the module to re-run its checks")
for _ in frames(20):
    pass
show_dtcs("after 20 s")
send("x")
