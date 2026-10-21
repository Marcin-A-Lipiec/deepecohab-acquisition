#pragma once
#include <stdint.h>
#include <string>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
extern uint32_t fakeUs;
extern int pinLevel;
inline uint32_t micros() { return fakeUs; }
inline uint32_t millis() { return fakeUs / 1000; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int value) { pinLevel = value; }
struct FakeSerialPort {
  std::string input, output;
  void begin(int) {}
  int available() { return static_cast<int>(input.size()); }
  int read() { char c = input[0]; input.erase(0, 1); return c; }
  void println(const char* value) { output += value; output += '\n'; }
};
extern FakeSerialPort Serial;
