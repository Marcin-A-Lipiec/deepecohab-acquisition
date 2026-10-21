#pragma once
#include "Wire.h"
struct Adafruit_MPR121 {
  bool beginOk = true;
  unsigned attempts = 0;
  bool begin(int, FakeWire *, int, int) {
    ++attempts;
    return beginOk;
  }
  void setThresholds(int, int) {}
};
