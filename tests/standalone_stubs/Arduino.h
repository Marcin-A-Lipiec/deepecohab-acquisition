#pragma once
#include <cstdint>
#include <cstring>
inline uint64_t fakeNowUs = 0;
inline int outputLevels[32] = {}, pinModes[32] = {};
inline unsigned risingEdges[32] = {};
inline bool heldLow[32] = {};
constexpr int HIGH=1, LOW=0, OUTPUT=1, INPUT=0, INPUT_PULLUP=2;
constexpr int SDA=18, SCL=19, LED_BUILTIN=13;
inline uint32_t millis() { return uint32_t(fakeNowUs / 1000); }
inline uint32_t micros() { return uint32_t(fakeNowUs++); }
inline void delay(uint32_t ms) { fakeNowUs += uint64_t(ms) * 1000; }
inline void delayMicroseconds(uint32_t us) { fakeNowUs += us; }
inline void pinMode(int p, int mode) { pinModes[p] = mode; }
inline void digitalWrite(int p, int v) {
  if (v && !outputLevels[p]) ++risingEdges[p];
  outputLevels[p] = v;
}
inline int digitalRead(int p) {
  return heldLow[p] ? LOW : pinModes[p] != OUTPUT ? HIGH : outputLevels[p];
}
// Intentionally no Serial object: any accidental USB dependency fails to compile.
