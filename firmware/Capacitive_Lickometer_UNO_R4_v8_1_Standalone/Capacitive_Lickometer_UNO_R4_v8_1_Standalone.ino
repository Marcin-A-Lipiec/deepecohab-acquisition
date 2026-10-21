/*
 DeepEcoHAB standalone lickometer v8.1 / electrode range hotfix.
 UNO R4 WiFi + Adafruit MPR121: E1/E3/E5/E7 -> D8/D9/D10/D11.
 BNC HIGH during a confirmed contact, LOW after release. RFID DAQ records ONSETS.
 No Serial.begin, USB handshake, CSV recording, RTC or PC control.

 Connect filled bottles before power. Leave spouts untouched during the initial
 ~4 s calibration, or up to ~20 s if charge-range search is needed.
 Per-channel automatic thresholds are commissioning defaults,
 not a guarantee of mouse sensitivity; validate actual contacts against TTLs.
 Read INSTALLATION.md before use. Power faults still require hardware checks.
*/
#if !defined(ARDUINO_UNOR4_WIFI) && !defined(LICKOMETER_HOST_TEST)
#error "Select Arduino UNO R4 WiFi (Arduino UNO R4 Boards package)."
#endif
#include <Arduino.h>
#include <Arduino_LED_Matrix.h>
#include <WDT.h>
#include "LickometerCore.h"
#include "Mpr121Driver.h"
#include "RangeSearch.h"

ArduinoLEDMatrix matrix;
Mpr121Driver sensor;
RangeSearch rangeSearch;
Lickometer::Channel channels[4];
uint8_t frame[8][12] = {};
enum class LinkMode : uint8_t { Boot, Retry, Settling, Tuning, Running };
LinkMode linkMode = LinkMode::Boot;
uint32_t stateAt = 0, retryDelay = 1000, retryAt = 0, sampledAt = 0, displayAt = 0;
bool watchdogOK = false;
bool rangeSelected = false;
uint8_t fallbackMask = 0, failedRangeMask = 0, rangeCount = 0;
uint16_t rangeReadings[4][Settings::rangeSamples] = {};
uint32_t rangeAt = 0;

void outputsOff() {
  for (uint8_t pin : Settings::outputs) digitalWrite(pin, LOW);
}
void linkFault(uint32_t now) {
  outputsOff();
  for (auto &channel : channels) channel.unavailable(now);
  rangeSearch.active = false; // failed startup trials restart after bus recovery
  linkMode = LinkMode::Retry;
  retryAt = now;
  // 1,2,4,8,16,30 s backoff, continuing indefinitely without rapid MCU resets.
}
bool applyTrial() {
  if (!sensor.applyRange(rangeSearch.currents, rangeSearch.times)) {
    linkFault(millis()); return false;
  }
  rangeAt = millis();
  rangeCount = 0;
  return true;
}
void beginRangeSearch(uint8_t mask) {
  outputsOff();
  // Preserve all non-failed channels' charge settings; no detector has started.
  fallbackMask = mask;
  rangeSearch.begin(mask, sensor.chargeCurrent, sensor.chargeTime);
  if (!sensor.beginManualRange()) { linkFault(millis()); return; }
  if (applyTrial()) linkMode = LinkMode::Tuning;
}
void tickRangeSearch(uint32_t now) {
  if (Lickometer::elapsed(now, rangeAt) < Settings::rangeSettleMs ||
      Lickometer::elapsed(now, sampledAt) < Settings::sampleMs) return;
  sampledAt = now;
  uint16_t values[4]; uint8_t faults;
  if (!sensor.snapshot(values, faults)) { linkFault(millis()); return; }
  for (unsigned i = 0; i < 4; ++i) rangeReadings[i][rangeCount] = values[i];
  if (++rangeCount < Settings::rangeSamples) return;
  for (unsigned i = 0; i < 4; ++i) {
    qsort(rangeReadings[i], Settings::rangeSamples, sizeof(uint16_t),
          Lickometer::compareSamples);
    values[i] = rangeReadings[i][Settings::rangeSamples / 2];
  }
  rangeSearch.observe(values);
  if (!applyTrial()) return;
  if (!rangeSearch.active) {
    failedRangeMask = rangeSearch.unresolved;
    fallbackMask = 0;
    rangeSelected = true;
    linkMode = LinkMode::Settling;
    stateAt = millis();
    // Final profiles get the full settle + independent ~2 s noise/baseline
    // calibration. Tuning measurements can never directly declare READY.
  }
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
  if (linkMode == LinkMode::Tuning) { tickRangeSearch(now); return; }
  if (linkMode == LinkMode::Settling) {
    if (Lickometer::elapsed(now, stateAt) < Settings::sensorSettleMs) return;
    linkMode = LinkMode::Running;
  }
  if (Lickometer::elapsed(now, sampledAt) < Settings::sampleMs) return;
  sampledAt = now; // start-to-start interval includes I2C transfer time
  uint16_t values[4]; uint8_t rangeFaults;
  if (!sensor.snapshot(values, rangeFaults)) { linkFault(millis()); return; }
  now = millis(); // receipt time after the bounded I2C read, not stale loop time
  if (!rangeSelected) {
    uint8_t mask = fallbackMask;
    for (unsigned i = 0; i < 4; ++i) {
      if ((rangeFaults & (1 << Settings::electrodes[i])) ||
          values[i] < Settings::baselineMinADC ||
          values[i] > Settings::baselineMaxADC) mask |= 1 << i;
    }
    if (mask) { beginRangeSearch(mask); return; }
    rangeSelected = true;
  }
  retryDelay = 1000;
  for (unsigned i = 0; i < 4; ++i) {
    const bool hardwareFault = rangeFaults & (1 << Settings::electrodes[i]);
    const bool searchFailed = failedRangeMask & (1 << i);
    auto reason = Lickometer::RangeReason::Hardware;
    if (!hardwareFault && searchFailed) {
      reason = rangeSearch.bestADC[i] < Settings::baselineMinADC
          ? Lickometer::RangeReason::Low : rangeSearch.bestADC[i] > Settings::baselineMaxADC
          ? Lickometer::RangeReason::High : Lickometer::RangeReason::Search;
    }
    channels[i].sample(values[i], hardwareFault || searchFailed, now, reason);
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
  frame[0][2] = linkMode == LinkMode::Boot || linkMode == LinkMode::Settling ||
      linkMode == LinkMode::Tuning;
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
    if (linkMode == LinkMode::Tuning) {
      frame[2 + (now / 200) % 4][x] = 1;
    } else if (ch.mode == Lickometer::Mode::Ready) {
      frame[6][x] = 1; // small, steady ready mark, also present during contacts
      if (ch.active)
        for (unsigned y = 2; y < 6; ++y) frame[y][x] = frame[y][x + 1] = 1;
    } else if (ch.fault != Lickometer::Fault::None) {
      // Four different heights: link=1, range=2, noise=3, stuck=4.
      for (unsigned y = 0; y < unsigned(ch.fault); ++y)
        frame[2 + y][x] = frame[2 + y][x + 1] = blink;
      if (ch.fault == Lickometer::Fault::Range) {
        // Steady marks below the blinking block identify the actual reason.
        // LEFT=too low, RIGHT=too high, BOTH=chip's range flag.
        frame[6][x] = ch.rangeReason == Lickometer::RangeReason::Low ||
            ch.rangeReason == Lickometer::RangeReason::Hardware;
        frame[6][x + 1] = ch.rangeReason == Lickometer::RangeReason::High ||
            ch.rangeReason == Lickometer::RangeReason::Hardware;
        if (ch.rangeReason == Lickometer::RangeReason::Search)
          frame[7][x] = frame[7][x + 1] = 1;
      }
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
