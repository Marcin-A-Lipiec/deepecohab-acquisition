/*
 DeepEcoHAB v8.1-DIAG1 / temporary bench measurement build.
 UNO R4 WiFi + Adafruit MPR121: E1/E3/E5/E7 -> D8/D9/D10/D11.
 BNC HIGH during a confirmed contact, LOW after release. RFID DAQ records ONSETS.
 Passive Serial Monitor observations; no commands, handshake, CSV, RTC or PC control.

 Calibration/detection decisions intentionally MATCH v8.1 to identify the
 reported fault. This build does not claim to fix it. See READ_ME_FIRST.md.
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
#include "BenchDiagnostics.h"

ArduinoLEDMatrix matrix;
Mpr121Driver sensor;
RangeSearch rangeSearch;
BenchDiagnostics diagnostics;
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
  diagnostics.observe(values, millis());
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
  diagnostics.observe(values, now);
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
char diagnosticCode(unsigned i) {
  if (linkMode == LinkMode::Retry) return 'I';
  if (linkMode == LinkMode::Tuning) return 'T';
  if (linkMode != LinkMode::Running) return 'C';
  const auto &ch = channels[i];
  if (ch.mode == Lickometer::Mode::Calibrating) return 'C';
  if (ch.mode == Lickometer::Mode::WaitRelease)
    return ch.fault == Lickometer::Fault::Stuck ? 'S' : 'W';
  if (ch.fault == Lickometer::Fault::Range) {
    if (ch.rangeReason == Lickometer::RangeReason::Low) return 'L';
    if (ch.rangeReason == Lickometer::RangeReason::High) return 'H';
    return 'R';
  }
  if (ch.fault == Lickometer::Fault::Noise) return 'N';
  if (ch.fault == Lickometer::Fault::Stuck) return 'S';
  if (ch.fault == Lickometer::Fault::Link) return 'I';
  return ch.active ? 'A' : 'O';
}
const char *diagnosticState(char code) {
  switch (code) {
    case 'I': return "LINK"; case 'T': return "RANGE_SEARCH";
    case 'C': return "CALIBRATING"; case 'W': return "WAIT_RELEASE";
    case 'S': return "STUCK"; case 'L': return "LOW_READING";
    case 'H': return "HIGH_READING"; case 'R': return "CHIP_RANGE";
    case 'N': return "NOISE"; case 'A': return "ACTIVE";
    default: return "READY";
  }
}
void reportDiagnostics(uint32_t now) {
  const bool connected = diagnostics.service();
  if (Lickometer::elapsed(now, diagnostics.reportAt) < 1000) return;
  diagnostics.reportAt = now;
  if (connected) {
    char line[420];
    snprintf(line, sizeof(line),
      "V8.1-DIAG1 uptime_ms=%lu link=%u error=%u config_attempts=%lu snapshots=%lu ECR=0x%02X OOR=0x%02X touch_status=0x%04X trials=%u failed_mask=0x%X dropped=%lu",
      (unsigned long)now, unsigned(linkMode), sensor.error,
      (unsigned long)sensor.configureAttempts, (unsigned long)sensor.successfulSnapshots,
      sensor.observedMode, sensor.observedOOR, sensor.observedTouch,
      rangeSearch.trials, failedRangeMask, (unsigned long)diagnostics.dropped);
    diagnostics.line(line);
    for (unsigned i = 0; i < 4; ++i) {
      const auto &ch = channels[i];
      const auto &w = diagnostics.window[i];
      const unsigned e = Settings::electrodes[i];
      // The MCU C library's printf does not support floating-point formats.
      // Explicit integer scaling also preserves half-count thresholds.
      snprintf(line, sizeof(line),
        "CH%u state=%s mode=%u fault=%u cal_ok=%u adc=%u lo=%u hi=%u n=%lu age_ms=%lu baseline_x10=%ld on_x10=%ld off_x10=%ld cdc=%u cdt_code=%u cal_attempt=%lu cal_progress=%u cal_min=%u cal_max=%u cal_median_x10=%ld cal_spread_x10=%ld proposed_on_x10=%ld",
        i + 1, diagnosticState(diagnosticCode(i)), unsigned(ch.mode), unsigned(ch.fault), ch.calibrated ? 1u : 0u,
        w.last, w.count ? w.low : 0, w.count ? w.high : 0, (unsigned long)w.count,
        (unsigned long)Lickometer::elapsed(now, w.lastAt),
        long(lroundf(ch.baseline * 10)), long(lroundf(ch.onThreshold * 10)), long(lroundf(ch.offThreshold * 10)),
        sensor.chargeCurrent[e], (sensor.chargeTime[e / 2] >> (4 * (e % 2))) & 7,
        (unsigned long)ch.calibrationAttempts, ch.calibrationProgress(), ch.calibrationMin, ch.calibrationMax,
        long(lroundf(ch.calibrationMedian * 10)), long(lroundf(ch.calibrationSpread * 10)),
        long(lroundf(ch.proposedThreshold * 10)));
      diagnostics.line(line);
    }
  }
  for (auto &w : diagnostics.window) w.clear();
}
void updateDisplay(uint32_t now, bool force = false) {
  if (!force && Lickometer::elapsed(now, displayAt) < 50) return;
  displayAt = now;
  for (auto &row : frame) for (auto &pixel : row) pixel = 0;
  // Whole matrix shows ONE channel number + a state letter for two seconds.
  // Repeat CH1..CH4; this replaces ambiguous block-height fault indicators.
  const unsigned i = (now / 2000) % 4;
  const uint8_t digits[4][5] = {{2,6,2,2,7},{7,1,7,4,7},{7,1,7,1,7},{5,5,7,1,1}};
  const char letters[] = "OACTWNLHRSI";
  const uint8_t glyphs[][5] = {
    {14,17,17,17,14}, {14,17,31,17,17}, {15,16,16,16,15},
    {31,4,4,4,4}, {17,17,21,27,17}, {17,25,21,19,17},
    {16,16,16,16,31}, {17,17,31,17,17}, {30,17,30,20,18},
    {15,16,14,1,30}, {31,4,4,4,31}
  };
  unsigned g = 0;
  while (letters[g] && letters[g] != diagnosticCode(i)) ++g;
  if (!letters[g]) g = 0;
  for (unsigned y = 0; y < 5; ++y) {
    for (unsigned x = 0; x < 3; ++x) frame[2 + y][1 + x] = !!(digits[i][y] & (1 << (2 - x)));
    for (unsigned x = 0; x < 5; ++x) frame[2 + y][6 + x] = !!(glyphs[g][y] & (1 << (4 - x)));
  }
  frame[0][0] = (now % 1000) < 100;
  frame[0][11] = 1; // diagnostic build marker
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
  Serial.begin(115200); // Optional observation only. Never wait for connection.
  stateAt = millis();
  updateDisplay(stateAt, true);
}
void loop() {
  tickSensor(millis());
  updateDisplay(millis());
  reportDiagnostics(millis());
  // Feed only after the complete iteration. Controlled fault backoff is healthy
  // execution; a blocked I2C operation or stalled loop must NOT feed the WDT.
  if (watchdogOK) WDT.refresh();
}
