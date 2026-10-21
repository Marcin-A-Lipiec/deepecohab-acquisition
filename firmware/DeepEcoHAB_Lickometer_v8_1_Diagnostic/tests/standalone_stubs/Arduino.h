#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <cassert>
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
struct FakeSerial {
  bool connected = false;
  int capacity = 64;
  unsigned baud = 0;
  std::string received;
  void begin(unsigned value) { baud = value; }
  explicit operator bool() const { return connected; }
  int availableForWrite() const { return capacity; }
  size_t write(const uint8_t *data, size_t n) {
    assert(n <= unsigned(capacity) && n <= 64);
    if (!connected) return 0;
    received.append(reinterpret_cast<const char *>(data), n);
    return n;
  }
};
inline FakeSerial Serial;
