#pragma once
#include "Settings.h"
#include <math.h>
#include <stdlib.h>

namespace Lickometer {
enum class Mode : uint8_t { Calibrating, Ready, WaitRelease, Fault };
enum class Fault : uint8_t { None, Link, Range, Noise, Stuck };
enum class RangeReason : uint8_t { None, Low, High, Hardware, Search };
inline uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }
inline int compareSamples(const void *a, const void *b) {
  const uint16_t x = *static_cast<const uint16_t *>(a);
  const uint16_t y = *static_cast<const uint16_t *>(b);
  return (x > y) - (x < y);
}

// One detector per spout. No Arduino, USB, wall clock, heap allocation, or I/O.
class Channel {
public:
  Mode mode = Mode::Calibrating;
  Fault fault = Fault::None;
  RangeReason rangeReason = RangeReason::None;
  bool active = false;
  bool calibrated = false;
  float baseline = 0, onThreshold = Settings::minThreshold;
  float offThreshold = Settings::minThreshold / 2, lastDelta = 0;
  uint32_t onsets = 0, stuckFaults = 0;

  void unavailable(uint32_t now) {
    active = candidate = false;
    mode = Mode::WaitRelease;
    fault = Fault::Link;
    haveRelease = false;
    count = 0;
    lastSample = now;
    haveSample = false;
    // Preserve baseline/thresholds. Driver restores the same CDC/CDT on retry.
  }

  void sample(uint16_t value, bool rangeFault, uint32_t now,
              RangeReason reason = RangeReason::Hardware) {
    const uint32_t dt = haveSample ? elapsed(now, lastSample) : Settings::sampleMs;
    lastSample = now;
    haveSample = true;
    if (dt > Settings::maxSampleGapMs && calibrated) {
      unavailable(now); // Never join contact states across an unobserved gap.
      haveSample = true;
    }
    if (rangeFault || value < Settings::measurementMinADC ||
        value > Settings::measurementMaxADC) {
      rangeReason = rangeFault ? reason : value < Settings::measurementMinADC
          ? RangeReason::Low : RangeReason::High;
      if (fault != Fault::Range || mode != Mode::Fault)
        fail(Fault::Range, now);
      return;
    }
    if (mode == Mode::Fault) {
      // Retain the pre-fault baseline on an established channel. In particular,
      // never learn a sustained touch as the new quiet reference after a fault.
      if (elapsed(now, faultAt) < Settings::channelRetryMs) return;
      mode = Mode::WaitRelease;
      haveRelease = false;
    }
    if (mode == Mode::WaitRelease) {
      if (!calibrated) { beginCalibration(); }
      else {
        lastDelta = baseline - value;
        if (lastDelta > offThreshold) { haveRelease = false; return; }
        if (!haveRelease) { haveRelease = true; releaseAt = now; }
        if (elapsed(now, releaseAt) < Settings::releaseBeforeRearmMs) return;
        // Release is confirmed against the OLD reference, not a reset baseline.
        mode = Mode::Ready;
        fault = Fault::None;
        rangeReason = RangeReason::None;
        active = candidate = false;
        quietAt = rateAt = now;
        rateCount = 0;
        count = 0;
        return;
      }
    }
    if (mode == Mode::Calibrating) {
      samples[count++] = value;
      if (count == Settings::calibrationSamples) finishCalibration(now, true);
      return;
    }

    lastDelta = baseline - value;
    bool target = active ? lastDelta > offThreshold : lastDelta >= onThreshold;
    if (target != candidate) { candidate = target; candidateAt = now; }
    if (candidate != active && elapsed(now, candidateAt) >=
        (candidate ? Settings::touchDebounceMs : Settings::releaseDebounceMs)) {
      active = candidate;
      count = 0;
      quietAt = now;
      if (active) {
        activeAt = now;
        ++onsets;
        if (elapsed(now, rateAt) >= 1000) { rateAt = now; rateCount = 0; }
        if (++rateCount > Settings::maxOnsetsPerSecond) {
          fail(Fault::Noise, now);
          return;
        }
      }
    }
    if (active) {
      if (elapsed(now, activeAt) >= Settings::stuckMs) {
        ++stuckFaults;
        active = candidate = false;
        mode = Mode::WaitRelease;
        fault = Fault::Stuck;
        haveRelease = false;
        count = 0;
      }
      return;
    }
    if (candidate || fabsf(lastDelta) > offThreshold) {
      quietAt = now;
      count = 0;
      // Fast recovery after removal of a large capacitive object: capacitance
      // falls and the raw value rises. Moving the reference UP cannot create
      // a rising-edge event here; never move it down during possible contacts.
      if (!candidate && lastDelta < -offThreshold) {
        baseline += (value - baseline) * fminf(float(dt) / 2000.0f, 0.02f);
      }
      return;
    }
    if (elapsed(now, quietAt) < Settings::quietMs) return;
    // Quiet-only drift tracking (slow down, faster up). Frozen throughout
    // candidate/active contacts; no application of MPR121's quantized baseline.
    baseline += (value - baseline) *
        fminf(float(dt) / (lastDelta > 0 ? 30000.0f : 5000.0f), 0.01f);
    if (elapsed(now, calibratedAt) >= Settings::maintenanceMs) {
      // Observe quiet samples while still detecting licks. Any possible touch
      // discards this window; daily maintenance NEVER stops live acquisition.
      samples[count++] = value;
      if (count == Settings::calibrationSamples) finishCalibration(now, false);
    }
  }

private:
  uint16_t samples[Settings::calibrationSamples] = {};
  uint16_t count = 0, rateCount = 0;
  bool candidate = false, haveRelease = false, haveSample = false;
  uint32_t candidateAt = 0, activeAt = 0, releaseAt = 0, quietAt = 0;
  uint32_t calibratedAt = 0, faultAt = 0, lastSample = 0, rateAt = 0;

  void beginCalibration() {
    mode = Mode::Calibrating;
    active = candidate = false;
    count = 0;
  }
  void fail(Fault reason, uint32_t now) {
    active = candidate = false;
    fault = reason;
    mode = Mode::Fault;
    faultAt = now;
    haveRelease = false;
    count = 0;
  }
  void finishCalibration(uint32_t now, bool initial) {
    qsort(samples, Settings::calibrationSamples, sizeof(samples[0]), compareSamples);
    const unsigned n = Settings::calibrationSamples;
    const float median = (samples[n / 2 - 1] + samples[n / 2]) * 0.5f;
    const float spread = fmaxf(median - samples[n / 100],
                               samples[n - 1 - n / 100] - median);
    const float proposed = fmaxf(Settings::minThreshold,
                                 ceilf(spread + Settings::noiseMargin));
    count = 0;
    // A target near 720 improves sensitivity, but is not the lower physical
    // validity limit. v8.0 incorrectly rejected every baseline below 480.
    if (median < Settings::baselineMinADC || median > Settings::baselineMaxADC) {
      rangeReason = median < Settings::baselineMinADC
          ? RangeReason::Low : RangeReason::High;
      fail(Fault::Range, now); return;
    }
    if (proposed > Settings::maxThreshold ||
        samples[n - 1] - samples[0] > Settings::maxThreshold * 4) {
      fail(Fault::Noise, now); return;
    }
    baseline = median;
    onThreshold = proposed;
    offThreshold = fmaxf(1.0f, proposed * 0.5f);
    calibratedAt = quietAt = now;
    calibrated = true;
    fault = Fault::None;
    rangeReason = RangeReason::None;
    mode = Mode::Ready;
    if (initial) {
      // Suppress startup/recovery contacts; first require a stable release.
      mode = Mode::WaitRelease;
      haveRelease = false;
    }
  }
};
}
