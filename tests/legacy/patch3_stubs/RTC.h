#pragma once
#include <ctime>
struct RTCTime {
  time_t v;
  RTCTime(time_t t = 0) : v(t) {}
  int getYear() { return v ? 2026 : 1970; }
  time_t getUnixTime() { return v; }
};
struct FakeRTC {
  time_t value = 0;
  bool writeOK = true;
  void begin() {}
  bool isRunning() { return value; }
  bool getTime(RTCTime &t) {
    t.v = value;
    return value;
  }
  bool setTime(RTCTime &t) {
    if (writeOK)
      value = t.v;
    return writeOK;
  }
};
inline FakeRTC RTC;
