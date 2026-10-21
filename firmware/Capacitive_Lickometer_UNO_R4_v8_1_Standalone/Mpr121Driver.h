#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <string.h>
#include "Settings.h"

// Small checked register driver: configure in STOP, then enter RUN once.
// No Adafruit library required; Wire/LED_Matrix/WDT ship with UNO R4 Boards.
class Mpr121Driver {
public:
  static constexpr uint8_t runMode = 0x88; // CL=10, ELE0..7, no proximity
  static constexpr uint8_t usedMask = 0xAA; // E1,E3,E5,E7
  bool haveRange = false;
  uint8_t chargeCurrent[8] = {}, chargeTime[4] = {};
  uint8_t error = 0; // 1=I2C, 2=OVCF, 3=stopped/config changed

  bool recoverBus() {
    Wire.end();
    // Open-drain style: drive LOW or release to the breakout's external
    // pull-ups. Do not add UNO 5 V internal pull-ups to the sensor bus.
    pinMode(SDA, INPUT);
    pinMode(SCL, INPUT);
    for (uint8_t i = 0; i < 9 && digitalRead(SDA) == LOW; ++i) {
      digitalWrite(SCL, LOW); pinMode(SCL, OUTPUT); delayMicroseconds(5);
      pinMode(SCL, INPUT);
      if (!waitClock()) { beginWire(); return false; }
      delayMicroseconds(5);
    }
    // STOP condition, if SCL is not being held down by a peripheral.
    digitalWrite(SCL, LOW); pinMode(SCL, OUTPUT);
    digitalWrite(SDA, LOW); pinMode(SDA, OUTPUT); delayMicroseconds(5);
    pinMode(SCL, INPUT);
    const bool clockFree = waitClock();
    delayMicroseconds(5);
    pinMode(SDA, INPUT); delayMicroseconds(5);
    const bool free = clockFree && digitalRead(SDA) == HIGH;
    beginWire();
    return free;
  }

  bool configure() {
    error = 1;
    if (!recoverBus()) return false;
    // OVCF is write-one-to-clear; soft reset does not reset the I2C interface.
    if (!write(0x01, 0x80) || !write(0x80, 0x63)) return false;
    delay(2);
    uint8_t resetValue;
    if (!read(0x5D, &resetValue, 1) || resetValue != 0x24) return false;
    if (!checked(0x5E, 0)) return false;
    const uint8_t config[][2] = {
      {0x2B, 1}, {0x2C, 1}, {0x2D, 4}, {0x2E, 0}, // rising baseline
      {0x2F, 1}, {0x30, 1}, {0x31, 0xFF}, {0x32, 2}, // slow falling
      {0x33, 0}, {0x34, 0}, {0x35, 0}, // freeze touched baseline
      {0x5B, 0}, {0x5C, 0x10}, {0x5D, 0x20}, // FFI=6,SFI=4,ESI=1ms
      {0x7D, Settings::autoUpper}, {0x7E, Settings::autoLower},
      {0x7F, Settings::autoTarget}, {0x7C, 0}
    };
    for (const auto &entry : config)
      if (!checked(entry[0], entry[1])) return false;
    // Raw filtered readings drive software thresholds. HW thresholds only
    // control its baseline tracking; they do not generate our BNC events.
    for (uint8_t i = 0; i < 8; ++i)
      if (!checked(0x41 + 2 * i, 8) || !checked(0x42 + 2 * i, 4)) return false;
    if (haveRange) {
      // Preserve sensing scale across resets, allowing release qualification
      // against the last good pre-fault software baseline.
      for (uint8_t i = 0; i < 8; ++i)
        if (!checked(0x5F + i, chargeCurrent[i])) return false;
      for (uint8_t i = 0; i < 4; ++i)
        if (!checked(0x6C + i, chargeTime[i])) return false;
    }
    // BVA=10. Auto-range only on first start; no spontaneous rescaling in licks.
    if (!checked(0x7B, haveRange ? 0x08 : 0x09) ||
        !checked(0x5E, runMode)) return false;
    error = 0;
    return true;
  }

  bool snapshot(uint16_t values[4], uint8_t &rangeFaults) {
    uint8_t status[4], raw[16], mode;
    error = 1;
    if (!read(0, status, 4) || !read(0x5E, &mode, 1)) return false;
    if (status[1] & 0x80) { error = 2; return false; }
    if (mode != runMode) { error = 3; return false; }
    if (!read(4, raw, 16)) return false;
    rangeFaults = status[2] & usedMask;
    // OOR bits identify affected electrodes. Failures on unused electrodes do
    // not disable otherwise valid channels just because ACFF is also set.
    for (unsigned i = 0; i < 4; ++i) {
      const unsigned e = Settings::electrodes[i];
      values[i] = raw[2 * e] | (uint16_t(raw[2 * e + 1]) << 8);
    }
    if (!haveRange) {
      if (!read(0x5F, chargeCurrent, 8) || !read(0x6C, chargeTime, 4)) return false;
      haveRange = true;
    }
    error = 0;
    return true;
  }

  // Startup fallback only. A soft reset first clears the latched OOR status;
  // auto-range stays off, and later trials use checked manual CDC/CDT settings.
  // Calling configure with haveRange=true restores the same known profiles.
  bool beginManualRange() {
    return haveRange && configure();
  }

  bool applyRange(const uint8_t currents[8], const uint8_t times[4]) {
    error = 1;
    if (!checked(0x5E, 0) || !checked(0x7B, 0x08)) return false;
    for (uint8_t i = 0; i < 8; ++i)
      if (!checked(0x5F + i, currents[i])) return false;
    for (uint8_t i = 0; i < 4; ++i)
      if (!checked(0x6C + i, times[i])) return false;
    if (!checked(0x5E, runMode)) return false;
    // Commit only after every write/readback succeeds. I2C recovery can then
    // restore a complete profile, never a mixture from a failed transaction.
    memcpy(chargeCurrent, currents, sizeof(chargeCurrent));
    memcpy(chargeTime, times, sizeof(chargeTime));
    haveRange = true;
    error = 0;
    return true;
  }

private:
  void beginWire() {
    Wire.begin();
    Wire.setClock(100000);
    // Renesas core ignores reset_on_timeout. recoverBus explicitly handles it.
    Wire.setWireTimeout(25000, false);
  }
  bool waitClock() {
    const uint32_t started = micros();
    while (digitalRead(SCL) == LOW)
      if (uint32_t(micros() - started) >= 25000) return false;
    return true;
  }
  bool write(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(Settings::address);
    Wire.write(reg); Wire.write(value);
    return Wire.endTransmission() == 0;
  }
  bool read(uint8_t reg, uint8_t *data, uint8_t count) {
    Wire.beginTransmission(Settings::address); Wire.write(reg);
    if (Wire.endTransmission(false) != 0 ||
        Wire.requestFrom(Settings::address, count) != count) return false;
    for (unsigned i = 0; i < count; ++i) {
      if (!Wire.available()) return false;
      data[i] = Wire.read();
    }
    return true;
  }
  bool checked(uint8_t reg, uint8_t value) {
    uint8_t actual;
    return write(reg, value) && read(reg, &actual, 1) && actual == value;
  }
};
