#pragma once
#include <cstdint>
struct FakeWDT {
  unsigned refreshes=0; uint32_t timeout=0; bool success=true;
  int begin(uint32_t ms) { timeout=ms; return success; }
  void refresh() { ++refreshes; }
};
inline FakeWDT WDT;
