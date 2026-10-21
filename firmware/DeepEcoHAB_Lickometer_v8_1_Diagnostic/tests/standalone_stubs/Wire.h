#pragma once
#include <cstdint>
#include <vector>
#include <cassert>
#include <cmath>
struct FakeWire {
  uint8_t regs[256] = {};
  uint16_t values[8] = {720,720,720,720,720,720,720,720};
  bool ack = true, shortRead = false;
  int corruptRegister = -1;
  unsigned resets = 0, begins = 0, ends = 0;
  unsigned rangeFailureStarts = 0;
  uint8_t autoFailureMask = 0, physicsMask = 0;
  double capacitance[8] = {15,15,15,15,15,15,15,15}; // idealized pF
  int adcDelta[8] = {};
  uint8_t pos = 0, unread = 0;
  std::vector<uint8_t> tx;
  void begin() { ++begins; }
  void end() { ++ends; }
  void setClock(unsigned) {}
  void setWireTimeout(unsigned, bool) {}
  void beginTransmission(uint8_t) { tx.clear(); }
  void write(uint8_t x) { tx.push_back(x); }
  int endTransmission(bool = true) {
    if (!ack) return 2;
    if (tx.empty()) return 0;
    pos=tx[0];
    if (tx.size() == 2) {
      uint8_t r=tx[0], v=tx[1];
      if (r == corruptRegister) return 0;
      if (r == 0x80 && v == 0x63) {
        ++resets; for(auto &b:regs)b=0; regs[0x5D]=0x24;
      } else if (r == 1 && v == 0x80) regs[1] &= 0x7F;
      else {
        // MPR121 configuration is writable only while electrodes are stopped.
        assert(r < 0x2B || r > 0x7F || r == 0x5E ||
               (r >= 0x73 && r <= 0x7A) || !(regs[0x5E] & 0x3F));
        regs[r]=v;
        if (r == 0x5E && (v & 0x0F) && (regs[0x7B] & 1)) {
          if (resets <= rangeFailureStarts) regs[2]=0xAA;
          regs[2] |= autoFailureMask;
          for(unsigned i=0;i<8;++i)regs[0x5F+i]=20+i;
          for(unsigned i=0;i<4;++i)regs[0x6C+i]=0x22;
        }
      }
    }
    return 0;
  }
  uint8_t requestFrom(uint8_t, uint8_t count) {
    for(unsigned i=0;i<8;++i) {
      uint16_t value=values[i];
      if (physicsMask & (1 << i)) {
        unsigned cdc=regs[0x5F+i]&63;
        if (!cdc) cdc=regs[0x5C]&63;
        unsigned cdt=(regs[0x6C+i/2] >> (4*(i%2)))&7;
        if (!cdt) cdt=regs[0x5D]>>5;
        const double us=cdt?0.5*(1u << (cdt-1)):0;
        // AN3889 idealized ADC ~ I*T*1024/(Vdd*C). This is a software
        // search model, NOT validation of an electrode or mouse sensitivity.
        int adc=std::lround(cdc*us*1024/(3.3*capacitance[i]))-adcDelta[i];
        value=adc<0?0:adc>1023?1023:adc;
      }
      regs[4+2*i]=value&255; regs[5+2*i]=value>>8;
    }
    unread=ack&&!shortRead?count:0; return unread;
  }
  int available() { return unread; }
  int read() { if(!unread)return -1; --unread; return regs[pos++]; }
};
inline FakeWire Wire;
