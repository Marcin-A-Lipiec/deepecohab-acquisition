#pragma once
#include <cstdint>
#include <cstring>
#include <sstream>
#include <string>
inline uint64_t fakeNowUs = 0;
inline int outputLevels[32] = {};
inline uint32_t millis() { return uint32_t(fakeNowUs / 1000); }
inline uint32_t micros() { return uint32_t(fakeNowUs); }
inline void delay(uint32_t ms) { fakeNowUs += uint64_t(ms) * 1000; }
inline void pinMode(int, int) {}
inline void digitalWrite(int p, int v) { outputLevels[p] = v; }
constexpr int HIGH = 1, LOW = 0, OUTPUT = 1;
struct FakeSerial {
  std::string input, output;
  bool connected = true;
  int capacity = 64;
  void begin(int) {}
  explicit operator bool() const { return connected; }
  int availableForWrite() { return capacity; }
  size_t write(const uint8_t *data, size_t n) {
    output.append((const char *)data, n);
    return n;
  }
  int available() { return input.size(); }
  int read() {
    char c = input.front();
    input.erase(0, 1);
    return c;
  }
};
inline FakeSerial Serial;
struct bsp_unique_id_t {
  uint32_t unique_id_words[4] = {1, 2, 3, 4};
};
inline const bsp_unique_id_t *R_BSP_UniqueIdGet() {
  static bsp_unique_id_t id;
  return &id;
}
