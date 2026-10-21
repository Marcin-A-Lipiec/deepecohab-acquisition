#pragma once
#include <cstring>
struct EEPROMClass {
  char bytes[1024] = {};
  template <class T> void get(int at, T &v) { memcpy(&v, bytes + at, sizeof(v)); }
  template <class T> void put(int at, const T &v) { memcpy(bytes + at, &v, sizeof(v)); }
};
inline EEPROMClass EEPROM;
