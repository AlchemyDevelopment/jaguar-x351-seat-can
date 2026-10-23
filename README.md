# Jaguar X351 power seat on the bench: ending "inch mode" with an ESP32 CAN emulator

A 2012 Jaguar XJL (X351) 12-way memory passenger seat moves continuously on the bench once a CAN board
sends **CAN ID 0x0B8 = `70 FF 00 00 00 00 00 00` every 50 ms** and joins the seat's network-management
ring as one extra node. Without that, the seat only moves in 200 ms steps per switch press ("inch mode").

Tested on one seat (module part CW93-14C548-AB) on 2026-10-18. Other years, seat types or module
versions may differ. Treat this as a starting point, not a guarantee.

## What you need

| Item | Notes | Approx. cost |
| --- | --- | --- |
| X351 seat with its seat module and switches | Keep the cut vehicle-side harness plugs | — |
| 12 V power supply | We used an HP PA-3401-2HA 400 W server PSU (multiple 12 V rails) | $20–40 used |
| Waveshare ESP32-S3-RS485-CAN board ([the one used here, Amazon](https://www.amazon.com/dp/B0FN7VZ8H7)) | Built-in CAN transceiver, screw terminals, 7–36 V input | ~$20 |
| USB-C cable + Windows PC | Only for flashing; the board runs standalone after that | — |
| Arduino CLI + Espressif ESP32 core (3.3.x) | Free | $0 |

## Wiring

Pin numbers below are from the factory diagram ("Passenger seat - 12 way memory (LHD)", page 166 of the 2012
XJ electrical guide). Check them against your own connector before connecting power.

| Seat connector pin | Wire colour | Function | Connect to |
| --- | --- | --- | --- |
| C33-C/1 | GY-RD | Power feed 1 (30 A in car) | +12 V |
| C33-C/7 | VT-RD | Power feed 2 (30 A in car) | +12 V |
| C33-K/7 | WH-RD | Module logic feed (10 A in car) | +12 V |
| C33-C/6, C33-C/12 | BK (thick) | Power ground | PSU ground |
| C33-C/5 | BK (thin) | Logic ground | PSU ground |
| C33-C/10 | GY-OG | MS-CAN high | ESP32 board CAN-H |
| C33-C/11 | VT-OG | MS-CAN low | ESP32 board CAN-L |

- Leave the yellow SRS / airbag connector unplugged and unpowered.
- Use the board's **CAN** terminals, not the RS485 ones.
- The board's CAN pins are TX = GPIO15 and RX = GPIO16, with no enable pin.
- Power the board from USB **or** its 7–36 V terminal, never both at once (Waveshare says this can damage it).
- The bench bus worked without adding termination. If you see constant bus errors, enable the board's 120 Ω jumper.

## Why the seat inches on the bench

The seat module is deliberately stopping the motors. It isn't a power or wiring fault.

- **Motor current is normal.** The module's own readings (UDS DIDs D900/D901) show a start-up spike, a steady
  running current, then a cut-off at 200 ms.
- **The seat moved smoothly in the car**, including with the door open and ignition off. All motor and Hall-sensor
  wiring stays inside the seat, so the hardware is fine.
- **The module talks normally on MS-CAN (125 kbit/s).** It answers UDS diagnostics at request 0x7A3, response 0x7AB.
- **Things that did not end inch mode:**
  - XF-style ignition (0x088) and door (0x0A8) frames
  - a replay of real JLR MS-CAN traffic, including with the seat's own VIN
  - clearing the fault codes
  - a working network-management ring on its own

## The fix: CAN frames that end inch mode

Send these two things, starting at power-up:

1. **Network-management partner, node 0x11** (ID 0x511), in the JLR/OSEK ring format:
   - byte0 = the next node in the ring
   - byte1 = opcode: 0x01 alive, 0x02 ring, plus 0x10 for sleep-indication
   - byte4 = the sender's own node address

   The seat is node 0x58 (ID 0x558). When it sends a ring message addressed to 0x11, answer about 100 ms later
   with `58 02 00 00 11 00 00 00`.
2. **The seat-enable frame, ID 0x0B8 = `70 FF 00 00 00 00 00 00`, every 50 ms.**

What we know about 0x0B8:

- **byte0 alone or byte1 alone does nothing.** Byte1 = 0xFF together with byte0 = 0x70 works.
- **These did not work:** byte0 = 0x0F, 0x80 or 0x00 with byte1 = 0xFF, and byte1 = 0x0F or 0xF0 with byte0 = 0xFF.
- **It is probably a multi-bit value** spanning byte0 bits 4–6 and byte1. Its real meaning is still unknown.
- **The seat reports the change itself:** its status message 0x4FB byte6 bit 2 goes from 1 to 0, and stays that way until the
  module loses power for about 15 s.

Seat status message 0x4FB, as far as we've decoded it:

| Field | Meaning |
| --- | --- |
| byte6 bit 0 | Massage running |
| byte6 bit 2 | 1 = inch mode, 0 = enabled by 0x0B8 |
| byte6 bits 1 and 3 | Heartbeat toggle |
| byte7 bit 0 | A seat motor (or massage actuator) is running |

**Massage: CAN ID 0x4CB, byte 7, bit 4.**
- `00 00 00 00 00 00 00 10` starts the massage, and it keeps running after the frames stop.
- `00 00 00 00 00 00 00 00` stops it.
- Send about 1 s of frames either way. The firmware's `k` / `K` serial commands do exactly this.
- The other bits of byte 7 did nothing on their own. Combined with 0x10, each one still starts the massage, and the user reports that they
  change something about it. Each value is noted below; what each one does is still to be identified by feel.

| byte 7 | Starts massage | Effect (to fill in) |
| --- | --- | --- |
| 0x10 | yes | reference program |
| 0x30 | yes | different, unidentified |
| 0x50 | yes | different, unidentified |
| 0x90 | yes | different, unidentified |
| 0x11 | yes | different, unidentified |
| 0x12 | yes | different, unidentified |
| 0x14 | yes | different, unidentified |
| 0x18 | yes | different, unidentified |

To try any value, send `M<hex>` over serial (e.g. `M30`). `M00` or `K` stops the massage.

## Step by step

1. Wire power, ground and CAN as in the wiring table. Leave the seat switched off.
2. Install Arduino CLI and the Espressif ESP32 board package (board URL
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`).
3. Open the sketch `JagSeatCan/JagSeatCan.ino`. `AUTO_RUN` is `true`, so the board starts the network partner and
   0x0B8 on boot.
4. Compile and upload. Board: `esp32:esp32:esp32s3:CDCOnBoot=cdc`.
5. Power the seat, wait about 30 s for the module to wake, then hold a seat switch. It should move continuously.
6. Optional: open the serial monitor at 115200 baud. Commands:
   - `s` status
   - `p` IDs seen
   - `v` verbose
   - `x` stop all transmitting
   - `e` toggle 0x0B8
   - `n` toggle the network partner
   - `k` / `K` massage on / off, `M<hex>` massage with any byte-7 value

## USB dashboard (no Wi-Fi)

Open **`dashboard.html`** in Chrome or Edge on the PC the ESP32 is plugged into. Click **Connect to ESP32** and pick
its COM port. The page talks to the board over USB (Web Serial), so you don't need to join any Wi-Fi network. It shows the
same live status and buttons as the Wi-Fi page, plus the serial log. Close the Arduino serial monitor and the Python
scripts first, because only one program can use the COM port at a time.

Under the hood it uses the firmware's serial commands: `j` returns one `J {...}` JSON status line, `M10` / `M00` turns the
massage on or off, and `e` toggles seat-enable.

## Web control (Wi-Fi)

**On your home Wi-Fi:** copy `JagSeatCan/wifi_secrets.example.h` to `JagSeatCan/wifi_secrets.h`, fill in your network
name and password, and flash again. The board joins your network, so your phone stays on it, and the page is at
**http://jagseat.local**. If that name doesn't resolve on your phone, use the IP the board prints on the serial port.
`wifi_secrets.h` is git-ignored, so your password never ends up in the repo.

**On its own network:** the firmware also always runs its own Wi-Fi network. Join **`JagSeat`** (password `jaguarseat`; change `WIFI_PASS` in the sketch) and
open **http://192.168.4.1**. The page shows live seat status (link, continuous movement, massage, motor), plus massage
on/off, the massage variants and seat-enable on/off. The same actions are available as a small API:

- `/api/status`
- `/api/massage?v=10` (or `v=00` for off)
- `/api/enable?on=1`

## Files in this repo

| File | What it does |
| --- | --- |
| `JagSeatCan/JagSeatCan.ino` | ESP32 firmware. It auto-runs the seat-enable frame and network partner, and has serial commands for testing |
| `cantest.py` | Sends single-letter firmware commands with timed waits and logs the output |
| `diag.py` | Finds the seat module's UDS address and reads its fault codes, part numbers and VIN (read-only) |
| `clear_dtc.py` | Clears the seat module's fault codes |
| `did_scan.py`, `pos_watch.py` | Read the module's data identifiers (read-only) |
| `io_probe.py` | Checks which outputs the module allows to be controlled. It only sends "return control to module", so nothing moves |
| `replay.py`, `idscan.py` | Replay a candump log to the seat and work out which ID fills which value |
| `trigger_hunt.py` | Sends test payloads to groups of IDs and **stops at the first change** in the seat's status |
| `massage_bisect.py` | Narrows a hit from `trigger_hunt.py` down to one ID, switching the massage off after each hit |
| `hold_frame.py` | Keeps sending one frame while you test the switches |
| `dashboard.html` | USB dashboard for massage and seat status. Open it in Chrome or Edge; no Wi-Fi needed |

All scripts expect the board on `COM5`. Edit `PORT` at the top of each script if yours differs.
Your own logs can contain your seat's VIN, so don't publish them.

## Safety notes and gotchas

- **Never block the board's main loop with serial output.** Use `Serial.setTxTimeoutMs(0)`, and don't print the seat's
  constant 0x4FB/0x558 chatter. If you do, the loop stalls when no PC is reading, 0x0B8 stops, and the seat drops back to
  inch mode.
- **Don't flood random CAN IDs.** Our search sent made-up frames on every ID, and that switched on the massage and pump.
  If you search, watch the seat's status and stop at the first change, and stay at the seat with the power within reach.
- **Don't tape a switch down and walk away.** The seat will keep stepping or straining against its end stop.
- **A full reset means at least 15 s with the power off.** A short unplug may not reset the module.
- **The seat's diagnostic outputs (motor/IO control) are locked behind security access.** We didn't try to bypass it.

## Still open

- **Heating, cooling and ventilation need a separate module.** The seat holds only the parts: Peltier (TED) pads, temperature
  sensors and fans. The **climate-controlled seat module** (connector C3HS03 A/B/C) is body-mounted near the front seats and
  stays with the car. It has its own MS-CAN pair and drives the seat through C33-K / C33-C:
  - backrest TED: K/21, K/20
  - backrest sensor: K/10, K/9
  - cushion TED: C/3, C/2
  - cushion sensor: K/13, K/12
  - fans: power K/4, return K/5, speed K/6 and K/19

  Get a used module and wire it in. Don't drive the TEDs directly: they need closed-loop temperature control.
- **Memory buttons** are wired switches on this seat (STATUS / MEMORY lines to the door switch), not CAN.
- **The exact meaning and the minimum working value of 0x0B8** are unknown.

## Sources

- [2012 XJ (X351) electrical wiring diagrams](http://jagrepair.com/images/Electrical/XJElectrical/2012%20XJ%20-%20X351.pdf) (passenger seat 12-way, page 166)
- [Jaguar Forums: 2012 XJL power seat adjusts in pulses](https://www.jaguarforums.com/forum/xj-x351-53/help-2012-xjl-power-seat-adjusts-pulses-not-smooth-199625/)
- [JLR seat operation and inch mode description (Land Rover Discovery manual)](https://www.lrdisc.com/seating_operation-572.html)
- [fsfarmscaper/jaguar-xf-x250-can MS-bus DBC](https://github.com/fsfarmscaper/jaguar-xf-x250-can)
- [rhysmorgan134/x250-can (real JLR MS-CAN captures; network-management format)](https://github.com/rhysmorgan134/x250-can)
- [Waveshare ESP32-S3-RS485-CAN wiki](https://www.waveshare.com/wiki/ESP32-S3-RS485-CAN)
- [Board pinout (CAN TX15/RX16)](https://github.com/Sleeper85/esphome-yambms/blob/main/documents/README/Board_Waveshare_ESP32-S3-RS485-CAN.md)
