#pragma once
#include <cstdint>
struct ArduinoLEDMatrix {
  uint8_t last[8][12] = {};
  void begin() {}
  void renderBitmap(uint8_t frame[8][12], int, int) {
    for(unsigned y=0;y<8;++y)for(unsigned x=0;x<12;++x)last[y][x]=frame[y][x];
  }
};
