/*
 DeepEcoHAB standalone lickometer v8.0 / Acquisition_GUI_v2.0 Patch 5.
 UNO R4 WiFi + Adafruit MPR121: E1/E3/E5/E7 -> D8/D9/D10/D11.
 BNC HIGH during a confirmed contact, LOW after release. RFID DAQ records ONSETS.
 No Serial.begin, USB handshake, CSV recording, RTC or PC control.

 Connect filled bottles before power. Leave spouts untouched during the initial
 ~4 s calibration. Per-channel automatic thresholds are commissioning defaults,
 not a guarantee of mouse sensitivity; validate actual contacts against TTLs.
 Read PATCH_5.md before unattended use. Power faults still require hardware checks.
*/
#if !defined(ARDUINO_UNOR4_WIFI) && !defined(LICKOMETER_HOST_TEST)
#error "Select Arduino UNO R4 WiFi (Arduino UNO R4 Boards package)."
#endif
#include <Arduino.h>
#include <Arduino_LED_Matrix.h>
#include <WDT.h>
#include "LickometerCore.h"
#include "Mpr121Driver.h"

ArduinoLEDMatrix matrix;
Mpr121Driver sensor;
Lickometer::Channel channels[4];
uint8_t frame[8][12] = {};
enum class LinkMode : uint8_t { Boot, Retry, Settling, Running };
LinkMode linkMode = LinkMode::Boot;
uint32_t stateAt = 0, retryDelay = 1000, retryAt = 0, sampledAt = 0, displayAt = 0;
bool watchdogOK = false;
uint8_t initialRangeRetries = 0;

void outputsOff() {
  for (uint8_t pin : Settings::outputs) digitalWrite(pin, LOW);
}
void linkFault(uint32_t now) {
  outputsOff();
  for (auto &channel : channels) channel.unavailable(now);
  linkMode = LinkMode::Retry;
  retryAt = now;
  // 1,2,4,8,16,30 s backoff, continuing indefinitely without rapid MCU resets.
}
void attemptStart(uint32_t now) {
  outputsOff();
  if (sensor.configure()) {
    linkMode = LinkMode::Settling;
    stateAt = millis();
  } else {
    linkFault(now);
    retryDelay = retryDelay < 15000 ? retryDelay * 2 : 30000;
  }
}
void tickSensor(uint32_t now) {
  if (linkMode == LinkMode::Boot) {
    if (Lickometer::elapsed(now, stateAt) >= Settings::powerSettleMs) attemptStart(now);
    return;
  }
  if (linkMode == LinkMode::Retry) {
    if (Lickometer::elapsed(now, retryAt) >= retryDelay) attemptStart(now);
    return;
  }
  if (linkMode == LinkMode::Settling) {
    if (Lickometer::elapsed(now, stateAt) < Settings::sensorSettleMs) return;
    linkMode = LinkMode::Running;
  }
  if (Lickometer::elapsed(now, sampledAt) < Settings::sampleMs) return;
  sampledAt = now; // start-to-start interval includes I2C transfer time
  uint16_t values[4]; uint8_t rangeFaults;
  if (!sensor.snapshot(values, rangeFaults)) { linkFault(millis()); return; }
  now = millis(); // receipt time after the bounded I2C read, not stale loop time
  // Range selection may fail while the sensor supply is still settling. Retry
  // before any channel has a learned reference. After two partial retries,
  // allow healthy channels to start and isolate the failed ones. If every
  // channel failed range selection, retry with backoff until it can measure.
  bool learnedReference = false;
  for (const auto &channel : channels) learnedReference |= channel.calibrated;
  if (rangeFaults && !learnedReference &&
      (initialRangeRetries < 2 || rangeFaults == Mpr121Driver::usedMask)) {
    if (initialRangeRetries < 255) ++initialRangeRetries;
    sensor.haveRange = false;
    sensor.error = 4;
    linkFault(now);
    retryDelay = retryDelay < 15000 ? retryDelay * 2 : 30000;
    return;
  }
  retryDelay = 1000;
  for (unsigned i = 0; i < 4; ++i) {
    channels[i].sample(values[i], rangeFaults & (1 << Settings::electrodes[i]), now);
    digitalWrite(Settings::outputs[i], channels[i].active ? HIGH : LOW);
  }
}
void updateDisplay(uint32_t now, bool force = false) {
  if (!force && Lickometer::elapsed(now, displayAt) < 50) return;
  displayAt = now;
  for (auto &row : frame) for (auto &pixel : row) pixel = 0;
  const bool blink = (now / 250) % 2;
  // Top-left heartbeat proves that loop executes, independently of sensor state.
  frame[0][0] = (now % 1000) < 100;
  frame[0][2] = linkMode == LinkMode::Boot || linkMode == LinkMode::Settling;
  frame[0][4] = linkMode == LinkMode::Running;
  frame[0][6] = linkMode == LinkMode::Retry && blink;
  frame[0][8] = !watchdogOK && blink;
  if (linkMode == LinkMode::Retry) {
    // Bottom-right error bars: I2C=1, overcurrent/STOP=2, configuration=3+.
    for (unsigned p = 0; p < sensor.error && p < 3; ++p) frame[7][9 + p] = blink;
  }
  for (unsigned i = 0; i < 4; ++i) {
    const auto &ch = channels[i];
    const unsigned x = 3 * i;
    if (ch.mode == Lickometer::Mode::Ready) {
      frame[6][x] = 1; // small, steady ready mark, also present during contacts
      if (ch.active)
        for (unsigned y = 2; y < 6; ++y) frame[y][x] = frame[y][x + 1] = 1;
    } else if (ch.fault != Lickometer::Fault::None) {
      // Four different heights: link=1, range=2, noise=3, stuck=4.
      for (unsigned y = 0; y < unsigned(ch.fault); ++y)
        frame[2 + y][x] = frame[2 + y][x + 1] = blink;
    } else {
      frame[2 + (now / 200) % 4][x] = 1; // calibrating/release qualification
    }
  }
  digitalWrite(LED_BUILTIN, (now % 1000) < 100 ? HIGH : LOW);
  matrix.renderBitmap(frame, 8, 12);
}
void setup() {
  // Make execution visible BEFORE Wire, MPR121 or matrix initialization.
  pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, HIGH);
  for (uint8_t pin : Settings::outputs) {
    digitalWrite(pin, LOW); pinMode(pin, OUTPUT);
  }
  watchdogOK = WDT.begin(Settings::watchdogMs) != 0;
  matrix.begin();
  stateAt = millis();
  updateDisplay(stateAt, true);
}
void loop() {
  tickSensor(millis());
  updateDisplay(millis());
  // Feed only after the complete iteration. Controlled fault backoff is healthy
  // execution; a blocked I2C operation or stalled loop must NOT feed the WDT.
  if (watchdogOK) WDT.refresh();
}
