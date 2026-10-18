"""Keep sending one CAN frame (plus NM partner) and log seat motor run times while the user presses switches.

usage: python hold_frame.py <id_hex> <payload_hex> [seconds] [period_ms]
"""
import sys, time, threading, serial, datetime, pathlib

cid, payload = sys.argv[1], sys.argv[2]
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 75
period = float(sys.argv[4]) / 1000 if len(sys.argv) > 4 else 0.05
out_path = pathlib.Path(__file__).parent / "logs" / f"hold_{cid}_{payload}_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
olog = open(out_path, "w")
ser = serial.Serial("COM5", 115200, timeout=0.01)
lock = threading.Lock()
stop = threading.Event()


def out(s):
    print(s, flush=True)
    olog.write(s + "\n")
    olog.flush()


def send(line):
    with lock:
        ser.write((line + "\n").encode())


def reader():
    while not stop.is_set():
        raw = ser.readline()
        if raw:
            s = raw.decode(errors="replace").rstrip()
            if "MOTOR" in s or " 4FB " in s or "BUS-OFF" in s:
                out(s)


threading.Thread(target=reader, daemon=True).start()
send("x"); time.sleep(0.5)
send("n")
out(f"### sending {cid}#{payload} every {period * 1000:.0f} ms for {secs:.0f} s; press switches after ~25 s")
t_end = time.time() + secs
while time.time() < t_end:
    send(f"t{cid} {payload}")
    time.sleep(period)
send("x")
time.sleep(0.5)
stop.set()
out(f"log saved: {out_path}")
