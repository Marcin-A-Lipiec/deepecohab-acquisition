#pragma once
#include <cstdint>
struct FakeWire {
  bool ack = true, readOK = true;
  uint16_t mask = 0;
  int n = 0;
  void begin() {}
  void setClock(unsigned) {}
  void setWireTimeout(unsigned, bool) {}
  void beginTransmission(int) {}
  void write(uint8_t) {}
  int endTransmission(bool = true) { return ack ? 0 : 2; }
  int requestFrom(uint8_t, uint8_t count) {
    n = 0;
    return ack && readOK ? count : 0;
  }
  int read() { return (mask >> (8 * n++)) & 255; }
};
inline FakeWire Wire;
