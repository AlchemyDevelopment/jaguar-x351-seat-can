"""Drive JagSeatCan over serial: send a scripted list of commands with waits, log everything.

usage: python cantest.py "s:3" "i:5" "d:20" "x:2"
   each arg = <command letters>:<seconds to wait/log afterwards>
"""
import sys, time, serial, datetime, pathlib

PORT = "COM5"
log_dir = pathlib.Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
log_path = log_dir / f"can_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"

def pump(ser, seconds, log):
    end = time.time() + seconds
    while time.time() < end:
        line = ser.readline()
        if line:
            s = line.decode(errors="replace").rstrip()
            print(s, flush=True)
            log.write(s + "\n")

with serial.Serial(PORT, 115200, timeout=0.2) as ser, open(log_path, "w") as log:
    pump(ser, 2, log)
    for step in sys.argv[1:]:
        cmd, _, wait = step.partition(":")
        msg = f"### sending '{cmd}' then logging {wait or 1}s"
        print(msg, flush=True); log.write(msg + "\n")
        for c in cmd:
            ser.write((c + "\n").encode())
            time.sleep(0.05)
        pump(ser, float(wait or 1), log)
    ser.write(b"x\n")  # always leave the bus quiet
    pump(ser, 1, log)
print(f"log saved: {log_path}")
