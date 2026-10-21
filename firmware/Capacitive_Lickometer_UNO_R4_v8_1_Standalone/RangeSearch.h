#pragma once
#include "Settings.h"
#include <string.h>

// Bounded, per-electrode startup fallback when the chip's automatic search
// fails. Only charge current/time are optimized here, NOT lick thresholds.
// At most 7 charge times x 6 binary-search current trials, shared by channels.
// No I/O or delays: the sketch settles and measures each trial asynchronously.
class RangeSearch {
public:
  bool active = false;
  uint8_t unresolved = 0; // logical channels 0..3, not MPR121 electrode bits
  uint8_t currents[8] = {}, times[4] = {};
  uint16_t bestADC[4] = {};
  unsigned trials = 0;

  void begin(uint8_t mask, const uint8_t baseCurrent[8],
             const uint8_t baseTime[4]) {
    active = true;
    pending = requested = mask;
    unresolved = 0;
    trials = 0;
    step = 0;
    chargeTime = 1;
    memcpy(currents, baseCurrent, sizeof(currents));
    memcpy(times, baseTime, sizeof(times));
    for (unsigned i = 0; i < 4; ++i) {
      low[i] = 1; high[i] = 63;
      bestError[i] = 65535;
      bestCurrent[i] = 1; bestTime[i] = 1; bestADC[i] = 0;
    }
    prepare();
  }

  // Call once per fully settled trial with median readings. On completion,
  // currents/times contain the best profile and must be applied one last time.
  void observe(const uint16_t values[4]) {
    ++trials;
    for (unsigned i = 0; i < 4; ++i) {
      if (!(pending & (1 << i))) continue;
      const uint16_t value = values[i];
      const uint16_t error = value > Settings::targetADC
          ? value - Settings::targetADC : Settings::targetADC - value;
      const bool valid = value >= Settings::baselineMinADC &&
          value <= Settings::baselineMaxADC;
      const bool bestValid = bestADC[i] >= Settings::baselineMinADC &&
          bestADC[i] <= Settings::baselineMaxADC;
      if ((valid && !bestValid) || (valid == bestValid && error < bestError[i])) {
        bestError[i] = error; bestADC[i] = value;
        bestCurrent[i] = current[i]; bestTime[i] = chargeTime;
      }
      // Higher current gives a higher ADC reading at the same capacitance.
      if (value < Settings::targetADC) low[i] = current[i] + 1;
      else high[i] = current[i] - 1;
    }
    if (++step == 6) {
      step = 0;
      for (unsigned i = 0; i < 4; ++i) {
        if (bestError[i] <= 24) pending &= ~(1 << i);
        low[i] = 1; high[i] = 63;
      }
      ++chargeTime;
    }
    if (!pending || chargeTime > 7) {
      active = false;
      for (unsigned i = 0; i < 4; ++i) {
        if (!(requested & (1 << i))) continue;
        set(i, bestCurrent[i], bestTime[i]);
        if (bestADC[i] < Settings::baselineMinADC ||
            bestADC[i] > Settings::baselineMaxADC)
          unresolved |= 1 << i;
      }
    } else prepare();
  }

private:
  uint8_t pending = 0, requested = 0, chargeTime = 1, step = 0;
  uint8_t low[4] = {}, high[4] = {}, current[4] = {};
  uint8_t bestCurrent[4] = {}, bestTime[4] = {};
  uint16_t bestError[4] = {};
  void set(unsigned i, uint8_t cdc, uint8_t cdt) {
    const unsigned e = Settings::electrodes[i];
    currents[e] = cdc;
    const unsigned shift = (e & 1) * 4;
    times[e / 2] = (times[e / 2] & ~(0x07 << shift)) | (cdt << shift);
  }
  void prepare() {
    for (unsigned i = 0; i < 4; ++i) {
      if (!(requested & (1 << i))) continue;
      if (!(pending & (1 << i))) { set(i, bestCurrent[i], bestTime[i]); continue; }
      // A saturated endpoint can exhaust a binary-search bracket. Keep the
      // endpoint valid (CDC 1..63); the next charge time extends the range.
      unsigned midpoint = (unsigned(low[i]) + high[i]) / 2;
      if (midpoint < 1) midpoint = 1;
      if (midpoint > 63) midpoint = 63;
      current[i] = midpoint;
      set(i, current[i], chargeTime);
    }
  }
};
