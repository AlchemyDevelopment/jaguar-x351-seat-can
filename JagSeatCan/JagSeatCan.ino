// Jaguar XJ (X351) passenger seat - MS-CAN bench emulator
// Board: Waveshare ESP32-S3-RS485-CAN  (CAN TX = GPIO15, RX = GPIO16)
// Bus:   Jaguar MS-CAN, 125 kbit/s  (C33-C pin 10 = CAN-H, pin 11 = CAN-L)
//
// AUTO_RUN: on power-up the board joins the seat's NM ring and sends 0x0B8 = 70 FF 00.. every 50 ms.
// That frame takes the seat module out of inch mode (bench-confirmed 2026-10-18), so the factory
// switches give continuous movement with no PC attached. Set AUTO_RUN to false to boot listen-only.
// Serial 115200, commands (letter + Enter):
//   e  toggle seat-enable frame      0x0B8  every 50 ms
//   k  massage ON  (0x4CB byte7=0x10 for 1 s)   K  massage OFF (0x4CB byte7=0x00 for 1 s)
//   M<hex>  massage with any 0x4CB byte7 value, e.g. M30 / M90 / M1F (each value with bit 0x10 starts massage)
//   i  toggle ignition-on frame      0x088  every 20 ms
//   d  toggle passenger door OPEN    0x0A8  every 100 ms (otherwise 0x0A8 reports all closed)
//   a  toggle sending 0x0A8 at all (door frame on/off)
//   m  cycle 0x088 power mode 6 -> 7 -> 9 -> 4 -> 6
//   n  toggle network-management partner (joins seat's OSEK NM ring)
//   x  stop all transmit, return to listen-only
//   l  listen-only (same as x)
//   s  print status / error counters
//   p  print table of every CAN ID seen so far
//   v  toggle verbose (print every received frame)
//
// Frame contents come from the XF X250 MS-bus DBC (fsfarmscaper/jaguar-xf-x250-can).
// They are NOT verified on X351 hardware.

#include "driver/twai.h"
#include <WiFi.h>
#include <WebServer.h>

// Web control: the board runs its own Wi-Fi network; open http://192.168.4.1 on a phone joined to it
#define WIFI_SSID "JagSeat"
#define WIFI_PASS "jaguarseat"  // at least 8 characters; change before sharing your rig
static WebServer web(80);
static uint8_t last4FB6 = 0, last4FB7 = 0;
static uint32_t last4FBMs = 0;

#define CAN_TX_PIN GPIO_NUM_15
#define CAN_RX_PIN GPIO_NUM_16

#define AUTO_RUN true

// 0x0B8: unknown vehicle signal; byte0 = 0x70 + byte1 = 0xFF stops inch mode (neither byte alone does)
static const uint8_t SEAT_ENABLE[8] = {0x70, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
static bool txSeatEnable = false;
static uint32_t lastSeatEnable = 0, busOffAt = 0;
static bool busOffRetry = false;

// 0x4CB byte7 bit4 (0x10) = massage request: 0x10 starts it (it keeps running), 0x00 stops it (bench-confirmed)
static int massageBurst = 0;  // frames left to send
static uint8_t massageByte7 = 0;
static uint32_t lastMassage = 0;

static bool txIgnition = false;
static bool txDoorFrame = false;
static bool doorOpen = false;
static bool verbose = false;
static bool listenOnly = true;

// OSEK-style network management. Seat module was observed sending ID 0x558
// (0x500 + node 0x58): byte0 = destination node, byte1 = opcode
// (bit0 ring, bit1 alive, bit2 limp-home, bit4 sleep-ind, bit5 sleep-ack).
// We pretend to be one more node so the seat is not alone in the ring.
#define NM_BASE 0x500
#define NM_ME 0x11
static bool txNm = false;
static bool nmTokenPending = false;
static uint8_t nmPeer = 0x58;
static uint32_t nmTokenAt = 0, lastNmHeard = 0, lastNmAlive = 0;

static uint8_t ignCounter = 0;
static uint8_t powerMode = 0x06;  // 0 off, 4 accessory, 6 ign on, 7 running, 9 cranking
static bool motorOn = false;
static uint32_t motorOnAt = 0;
static uint32_t lastIgn = 0, lastDoor = 0, lastStatus = 0;
static uint32_t txOk = 0, txFail = 0;

struct SeenId {
  uint32_t id;
  uint8_t dlc;
  uint8_t data[8];
  uint32_t count;
  uint32_t lastMs;
  bool ext;
};
static SeenId seen[64];
static int seenCount = 0;

static void printFrame(const char *tag, const twai_message_t &m) {
  Serial.printf("%8lu %s %s%03lX [%d]", (unsigned long)millis(), tag,
                m.extd ? "x" : "", (unsigned long)m.identifier, m.data_length_code);
  for (int i = 0; i < m.data_length_code; i++) Serial.printf(" %02X", m.data[i]);
  if (m.rtr) Serial.print(" RTR");
  Serial.println();
}

static bool startDriver(bool listen) {
  twai_stop();
  twai_driver_uninstall();
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      CAN_TX_PIN, CAN_RX_PIN, listen ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
  g.rx_queue_len = 64;
  g.tx_queue_len = 16;
  g.alerts_enabled = TWAI_ALERT_BUS_OFF | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_ERROR |
                     TWAI_ALERT_TX_FAILED | TWAI_ALERT_BUS_RECOVERED | TWAI_ALERT_ERR_ACTIVE;
  twai_timing_config_t t = TWAI_TIMING_CONFIG_125KBITS();
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  if (twai_driver_install(&g, &t, &f) != ESP_OK) {
    Serial.println("!! twai_driver_install failed");
    return false;
  }
  if (twai_start() != ESP_OK) {
    Serial.println("!! twai_start failed");
    return false;
  }
  listenOnly = listen;
  Serial.printf(">> CAN started, %s mode\n", listen ? "LISTEN-ONLY" : "NORMAL (transmit enabled)");
  return true;
}

static void stopAll() {
  txIgnition = txDoorFrame = doorOpen = txNm = txSeatEnable = false;
  busOffRetry = false;
  startDriver(true);
}

static void ensureNormal() {
  if (listenOnly) startDriver(false);
}

static void sendFrame(uint32_t id, const uint8_t *d) {
  twai_message_t m = {};
  m.identifier = id;
  m.data_length_code = 8;
  memcpy(m.data, d, 8);
  esp_err_t r = twai_transmit(&m, 0);
  if (r == ESP_OK) txOk++;
  else txFail++;
  if (verbose) printFrame(r == ESP_OK ? "TX" : "TX!", m);
}

static void sendIgnition() {
  // byte1 rolling counter (not required per DBC), byte2 power mode 6 = IgnitionOn,
  // byte3 complement = 0x10 | (0x0F - mode) = 0x19
  uint8_t d[8] = {0x00, ignCounter++, powerMode, (uint8_t)(0x10 | (0x0F - (powerMode & 0x0F))), 0x00, 0x00, 0x00, 0x00};
  sendFrame(0x088, d);
}

static void sendDoor() {
  // byte0 0x80 validity gate, byte2 0x10 bong disable, byte5 door-closed mask
  // bit0 FL, bit1 FR, bit2 RL, bit3 RR ; 0x3F = everything closed
  uint8_t mask = doorOpen ? (0x3F & ~0x02) : 0x3F;  // front-right (passenger, LHD) open
  uint8_t d[8] = {0x80, 0x00, 0x10, 0x00, 0x00, mask, 0x00, 0x00};
  sendFrame(0x0A8, d);
}

// JLR NM layout (from real Freelander 2 / X250-family captures):
// byte0 = logical successor node, byte1 = opcode (0x01 alive, 0x02 ring, 0x04 limp-home,
// 0x10 sleep-indication), byte4 = sender's own node address.
static void sendNm(uint8_t dest, uint8_t op) {
  uint8_t d[8] = {dest, op, 0, 0, NM_ME, 0, 0, 0};
  sendFrame(NM_BASE + NM_ME, d);
}

static void nmRx(const twai_message_t &m) {
  if (!txNm || m.extd || m.identifier < NM_BASE || m.identifier > NM_BASE + 0x7F) return;
  uint8_t src = m.identifier - NM_BASE;
  if (src == NM_ME || m.data_length_code < 2) return;
  nmPeer = src;
  lastNmHeard = millis();
  uint8_t dest = m.data[0], op = m.data[1];
  if ((op & 0x02) && dest == NM_ME) {  // ring token handed to us -> pass it on after ~100 ms
    nmTokenPending = true;
    nmTokenAt = millis() + 100;
  } else if ((op & 0x01) && !(op & 0x02)) {  // peer (re)joining: announce ourselves
    sendNm(NM_ME, 0x01);
  }
}
static void recordRx(const twai_message_t &m) {
  nmRx(m);
  // 0x4FB byte7 bit0 observed = 1 while the seat motor is running
  if (m.identifier == 0x4FB && !m.extd && m.data_length_code >= 8) {
    last4FB6 = m.data[6];
    last4FB7 = m.data[7];
    last4FBMs = millis();
    bool on = m.data[7] & 0x01;
    if (on && !motorOn) { motorOnAt = millis(); Serial.printf("%8lu MOTOR ON\n", (unsigned long)millis()); }
    if (!on && motorOn)
      Serial.printf("%8lu MOTOR OFF after %lu ms\n", (unsigned long)millis(), (unsigned long)(millis() - motorOnAt));
    motorOn = on;
  }
  for (int i = 0; i < seenCount; i++) {
    SeenId &s = seen[i];
    if (s.id == m.identifier && s.ext == (bool)m.extd) {
      bool changed = s.dlc != m.data_length_code || memcmp(s.data, m.data, m.data_length_code);
      s.count++;
      s.lastMs = millis();
      s.dlc = m.data_length_code;
      memcpy(s.data, m.data, 8);
      // 0x4FB / 0x558 change constantly; printing them floods USB serial and can stall the TX loop
      bool chatter = m.identifier == 0x4FB || m.identifier == 0x558;
      if (verbose || (changed && !chatter) || m.identifier >= 0x700) printFrame(changed ? "RX*" : "RX ", m);
      return;
    }
  }
  if (seenCount < 64) {
    SeenId &s = seen[seenCount++];
    s.id = m.identifier;
    s.ext = m.extd;
    s.dlc = m.data_length_code;
    memcpy(s.data, m.data, 8);
    s.count = 1;
    s.lastMs = millis();
  }
  printFrame("RX+", m);  // first time this ID is seen
}

static void printStatus() {
  twai_status_info_t st;
  twai_get_status_info(&st);
  const char *state[] = {"STOPPED", "RUNNING", "BUS_OFF", "RECOVERING"};
  Serial.printf("== state=%s mode=%s pm=%d ign=%d doorFrame=%d doorOpen=%d | rx_total_ids=%d "
                "TEC=%lu REC=%lu bus_err=%lu arb_lost=%lu tx_failed=%lu rx_missed=%lu "
                "tx_queued=%lu txOk=%lu txFail=%lu\n",
                state[st.state], listenOnly ? "listen" : "normal", powerMode, txIgnition, txDoorFrame, doorOpen,
                seenCount, (unsigned long)st.tx_error_counter, (unsigned long)st.rx_error_counter,
                (unsigned long)st.bus_error_count, (unsigned long)st.arb_lost_count,
                (unsigned long)st.tx_failed_count, (unsigned long)st.rx_missed_count,
                (unsigned long)st.msgs_to_tx, (unsigned long)txOk, (unsigned long)txFail);
}

static void printTable() {
  Serial.println("== IDs seen:");
  for (int i = 0; i < seenCount; i++) {
    SeenId &s = seen[i];
    Serial.printf("   %s%03lX count=%lu last=%lums ago :", s.ext ? "x" : "", (unsigned long)s.id,
                  (unsigned long)s.count, (unsigned long)(millis() - s.lastMs));
    for (int j = 0; j < s.dlc; j++) Serial.printf(" %02X", s.data[j]);
    Serial.println();
  }
}

static void startAuto() {
  ensureNormal();
  txNm = true;
  sendNm(NM_ME, 0x01);
  lastNmAlive = millis();
  txSeatEnable = true;
  Serial.println(">> AUTO: NM partner + seat-enable 0x0B8 running");
}

static void handleCmd(char c) {
  switch (c) {
    case 'k':
    case 'K':
      ensureNormal();
      massageByte7 = (c == 'k') ? 0x10 : 0x00;
      massageBurst = 20;  // 20 x 50 ms = 1 s
      Serial.printf(">> massage %s (0x4CB byte7=0x%02X for 1 s)\n", c == 'k' ? "ON" : "OFF", massageByte7);
      break;
    case 'e':
      txSeatEnable = !txSeatEnable;
      if (txSeatEnable) ensureNormal();
      Serial.printf(">> seat-enable 0x0B8 %s\n", txSeatEnable ? "ON" : "OFF");
      break;
    case 'i':
      txIgnition = !txIgnition;
      if (txIgnition) ensureNormal();
      Serial.printf(">> ignition 0x088 %s\n", txIgnition ? "ON" : "OFF");
      break;
    case 'a':
      txDoorFrame = !txDoorFrame;
      if (txDoorFrame) ensureNormal();
      Serial.printf(">> door frame 0x0A8 %s\n", txDoorFrame ? "ON" : "OFF");
      break;
    case 'd':
      doorOpen = !doorOpen;
      if (doorOpen && !txDoorFrame) { txDoorFrame = true; ensureNormal(); }
      Serial.printf(">> passenger door %s (0x0A8 sending=%d)\n", doorOpen ? "OPEN" : "CLOSED", txDoorFrame);
      break;
    case 'm':
      powerMode = powerMode == 0x06 ? 0x07 : powerMode == 0x07 ? 0x09 : powerMode == 0x09 ? 0x04 : 0x06;
      Serial.printf(">> power mode now %d\n", powerMode);
      break;
    case 'n':
      txNm = !txNm;
      if (txNm) { ensureNormal(); sendNm(NM_ME, 0x01); lastNmAlive = millis(); }
      Serial.printf(">> network-management partner (node 0x%02X) %s\n", NM_ME, txNm ? "ON" : "OFF");
      break;
    case 'x':
    case 'l':
      stopAll();
      Serial.println(">> all transmit stopped");
      break;
    case 's': printStatus(); break;
    case 'p': printTable(); break;
    case 'v':
      verbose = !verbose;
      Serial.printf(">> verbose %s\n", verbose ? "ON" : "OFF");
      break;
    case '\r': case '\n': case ' ': break;
    default: Serial.printf("?? unknown command '%c'\n", c);
  }
}

static const char PAGE[] PROGMEM = R"HTML(<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>JagSeat</title>
<style>
body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px;max-width:520px}
h1{font-size:20px;margin:0 0 12px}h2{font-size:15px;margin:20px 0 8px;color:#aaa;text-transform:uppercase;letter-spacing:.05em}
.st{display:grid;grid-template-columns:1fr auto;gap:6px 12px;background:#1c1c1c;padding:12px;border-radius:10px}
.on{color:#4ade80}.off{color:#888}.warn{color:#fbbf24}
button{font-size:16px;padding:14px 10px;border:0;border-radius:10px;background:#2a2a2a;color:#eee;width:100%}
button:active{background:#3a3a3a}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.go{background:#14532d}.stop{background:#7f1d1d}
small{color:#888}
</style></head><body>
<h1>Jaguar seat</h1>
<div class="st">
<span>Seat link</span><b id="link">-</b>
<span>Continuous movement</span><b id="en">-</b>
<span>Massage</span><b id="ms">-</b>
<span>Motor</span><b id="mo">-</b>
</div>
<h2>Massage</h2>
<div class="row"><button class="go" onclick="m('10')">On</button><button class="stop" onclick="m('00')">Off</button></div>
<h2>Massage variants</h2>
<div class="grid">
<button onclick="m('10')">10</button><button onclick="m('30')">30</button><button onclick="m('50')">50</button><button onclick="m('90')">90</button>
<button onclick="m('11')">11</button><button onclick="m('12')">12</button><button onclick="m('14')">14</button><button onclick="m('18')">18</button>
</div>
<p><small>Variant effects aren't identified yet. Note what each one feels like.</small></p>
<h2>Seat enable</h2>
<div class="row"><button onclick="e(1)">Enable</button><button onclick="e(0)">Disable</button></div>
<p><small>Disable stops the 0x0B8 frame. The seat stays enabled until it loses power.</small></p>
<script>
function q(u){return fetch(u).then(r=>r.json()).then(show).catch(()=>{})}
function m(v){q('/api/massage?v='+v)}
function e(v){q('/api/enable?on='+v)}
function s(id,t,c){var x=document.getElementById(id);x.textContent=t;x.className=c}
function show(d){
 s('link',d.seat?'connected':'no data','' + (d.seat?'on':'warn'));
 s('en',d.seat?(d.enabled?'yes':'inch mode'):'-',d.enabled?'on':'off');
 s('ms',d.seat?(d.massage?'running':'off'):'-',d.massage?'on':'off');
 s('mo',d.seat?(d.motor?'running':'idle'):'-',d.motor?'on':'off');
}
q('/api/status');setInterval(()=>q('/api/status'),1000);
</script></body></html>)HTML";

static void sendStatus() {
  bool seat = last4FBMs && millis() - last4FBMs < 2000;
  char buf[160];
  snprintf(buf, sizeof(buf),
           "{\"seat\":%s,\"enabled\":%s,\"massage\":%s,\"motor\":%s,\"tx_enable\":%s}",
           seat ? "true" : "false", (seat && !(last4FB6 & 0x04)) ? "true" : "false",
           (seat && (last4FB6 & 0x01)) ? "true" : "false", (seat && (last4FB7 & 0x01)) ? "true" : "false",
           txSeatEnable ? "true" : "false");
  web.send(200, "application/json", buf);
}

static void startWeb() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_SSID, WIFI_PASS);
  web.on("/", [] { web.send_P(200, "text/html", PAGE); });
  web.on("/api/status", sendStatus);
  web.on("/api/massage", [] {
    ensureNormal();
    massageByte7 = (uint8_t)strtoul(web.arg("v").c_str(), nullptr, 16);
    massageBurst = 20;
    sendStatus();
  });
  web.on("/api/enable", [] {
    if (web.arg("on") == "1") startAuto();
    else txSeatEnable = false;
    sendStatus();
  });
  web.begin();
  Serial.printf(">> web: join Wi-Fi \"%s\" and open http://%s\n", WIFI_SSID, WiFi.softAPIP().toString().c_str());
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // never block the CAN loop when no PC is reading the USB port
  delay(1500);
  Serial.println("\nJagSeatCan - X351 seat MS-CAN bench emulator (125k, TX15/RX16)");
  Serial.println("cmds: e=seat-enable k/K=massage on/off i=ignition d=door-open a=door-frame n=netmgmt x=stop s=status p=ids v=verbose");
  startDriver(true);
  if (AUTO_RUN) startAuto();
  startWeb();
}

void loop() {
  web.handleClient();
  // "t<ID> <hex bytes>" sends one raw frame (e.g. "t7DF 0219028F00000000"); anything else is single-letter commands
  static char line[48];
  static int lineLen = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (lineLen == 0 && c != 't' && c != 'M') { handleCmd(c); continue; }
    if (c == '\n' || c == '\r') {
      line[lineLen] = 0;
      lineLen = 0;
      if (line[0] == 'M') {  // "M<hex>": massage with any byte7 value, e.g. M30 (0x10 = plain on, 00 = off)
        ensureNormal();
        massageByte7 = (uint8_t)strtoul(line + 1, nullptr, 16);
        massageBurst = 20;
        Serial.printf(">> massage byte7=0x%02X for 1 s\n", massageByte7);
        continue;
      }
      char *p = line + 1;
      uint32_t id = strtoul(p, &p, 16);
      uint8_t d[8] = {0};
      int n = 0;
      while (*p == ' ') p++;
      while (n < 8 && p[0] && p[1]) {
        char hx[3] = {p[0], p[1], 0};
        d[n++] = (uint8_t)strtoul(hx, nullptr, 16);
        p += 2;
      }
      ensureNormal();
      twai_message_t m = {};
      m.identifier = id;
      m.data_length_code = 8;  // ISO-TP frames padded to 8
      memcpy(m.data, d, 8);
      esp_err_t r = twai_transmit(&m, pdMS_TO_TICKS(20));
      printFrame(r == ESP_OK ? "TX " : "TX!", m);
    } else if (lineLen < (int)sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }

  twai_message_t m;
  while (twai_receive(&m, 0) == ESP_OK) recordRx(m);

  uint32_t alerts;
  if (twai_read_alerts(&alerts, 0) == ESP_OK && alerts) {
    if (alerts & TWAI_ALERT_ERR_PASS) Serial.println("!! ALERT error-passive");
    if (alerts & TWAI_ALERT_BUS_ERROR) Serial.println("!! ALERT bus error");
    if (alerts & TWAI_ALERT_TX_FAILED) Serial.println("!! ALERT tx failed (no ACK?)");
    if (alerts & TWAI_ALERT_BUS_OFF) {
      Serial.println("!! ALERT BUS-OFF -> stopping transmit, back to listen-only");
      bool wasAuto = txSeatEnable;
      txIgnition = txDoorFrame = doorOpen = txNm = txSeatEnable = false;
      startDriver(true);
      if (wasAuto) { busOffRetry = true; busOffAt = millis(); Serial.println(">> will retry AUTO in 3 s"); }
    }
    if (alerts & TWAI_ALERT_ERR_ACTIVE) Serial.println("-- error-active again");
  }

  uint32_t now = millis();
  if (busOffRetry && now - busOffAt >= 3000) { busOffRetry = false; startAuto(); }
  if (!listenOnly) {
    if (txSeatEnable && now - lastSeatEnable >= 50) { lastSeatEnable = now; sendFrame(0x0B8, SEAT_ENABLE); }
    if (massageBurst > 0 && now - lastMassage >= 50) {
      lastMassage = now;
      massageBurst--;
      uint8_t d[8] = {0, 0, 0, 0, 0, 0, 0, massageByte7};
      sendFrame(0x4CB, d);
    }
    if (txIgnition && now - lastIgn >= 20) { lastIgn = now; sendIgnition(); }
    if (txDoorFrame && now - lastDoor >= 100) { lastDoor = now; sendDoor(); }
    if (txNm) {
      if (nmTokenPending && (int32_t)(now - nmTokenAt) >= 0) {
        nmTokenPending = false;
        sendNm(nmPeer, 0x02);  // pass ring token back to the seat
      }
      // no ring traffic for a while -> re-announce
      if (now - lastNmHeard > 1000 && now - lastNmAlive > 1000) { lastNmAlive = now; sendNm(NM_ME, 0x01); }
    }
  }
  if (now - lastStatus >= 5000) { lastStatus = now; printStatus(); }
}
