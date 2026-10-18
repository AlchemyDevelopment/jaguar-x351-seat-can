"""Narrow the massage-on frame down to one CAN ID inside a range, switching it off (all-zeros) after every hit."""
import subprocess, sys, pathlib

HUNT = str(pathlib.Path(__file__).parent / "trigger_hunt.py")
ON, OFF = "FFFFFFFFFFFFFFFF", "0000000000000000"
lo = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x480
hi = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x4FF


def run(payload, a, b):
    r = subprocess.run([sys.executable, HUNT, payload, f"{a:03X}-{b:03X}", "128", "8"],
                       capture_output=True, text=True, timeout=120)
    hit = "### result: HIT" in r.stdout
    print(f"   {payload[:2]} {a:03X}-{b:03X}: {'HIT' if hit else 'no change'}", flush=True)
    return hit


while hi > lo:
    mid = (lo + hi) // 2
    if run(ON, lo, mid):
        run(OFF, lo, mid)  # massage off again before the next round
        hi = mid
    else:
        lo = mid + 1
print(f"### candidate: {lo:03X}")
on = run(ON, lo, lo)
off = run(OFF, lo, lo) if on else False
print(f"### {lo:03X}: all-ones starts massage = {on}, all-zeros stops it = {off}")
