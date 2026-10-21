#define LICKOMETER_HOST_TEST
#include "../firmware/Capacitive_Lickometer_UNO_R4_v8_0_Standalone/Capacitive_Lickometer_UNO_R4_v8_0_Standalone.ino"
#include <cassert>
#include <iostream>
#include <string>
using Lickometer::Mode;
using Lickometer::Fault;
void advance(uint32_t ms) {
  const uint32_t start=millis();
  while (uint32_t(millis()-start)<ms) { delay(4); loop(); }
}
void ready() {
  setup();
  assert(WDT.timeout==2000);
  advance(4200);
  assert(linkMode==LinkMode::Running);
  for(unsigned i=0;i<4;++i) {
    assert(channels[i].mode==Mode::Ready);
    assert(!channels[i].active && risingEdges[Settings::outputs[i]]==0);
    assert(channels[i].onThreshold==3);
  }
  assert(matrix.last[6][0]);
}
void contact(unsigned i, uint16_t delta=5) {
  Wire.values[Settings::electrodes[i]]=720-delta; advance(20);
}
void release(unsigned i) {
  Wire.values[Settings::electrodes[i]]=720; advance(120);
}
void sensing() {
  ready();
  for(unsigned i=0;i<4;++i) {
    contact(i); assert(outputLevels[Settings::outputs[i]]==HIGH);
    assert(risingEdges[Settings::outputs[i]]==1);
    advance(100); assert(risingEdges[Settings::outputs[i]]==1);
  }
  for(unsigned i=0;i<4;++i)release(i);
  for(unsigned i=0;i<4;++i) {
    assert(!channels[i].active);contact(i);release(i);
    assert(risingEdges[Settings::outputs[i]]==2);
  }
  assert(WDT.refreshes>1000);
}
void debounce() {
  ready();
  for(unsigned i=0;i<100;++i) {
    Wire.values[1]=715; advance(4); Wire.values[1]=720; advance(12);
  }
  assert(risingEdges[8]==0);
  contact(0);
  for(unsigned i=0;i<20;++i) {
    Wire.values[1]=720; advance(4); Wire.values[1]=715; advance(12);
  }
  assert(risingEdges[8]==1 && channels[0].active);
}
void noise() {
  setup();
  for(unsigned i=0;i<1400;++i) {
    Wire.values[1]=i%2?705:735;
    // Different real quiet levels per channel, independent of the touch bits.
    Wire.values[3]=680; Wire.values[5]=760; Wire.values[7]=640;
    advance(4);
  }
  assert(channels[0].fault==Fault::Noise && risingEdges[8]==0);
  for(unsigned i=1;i<4;++i)assert(channels[i].mode==Mode::Ready);
  Wire.values[3]=675;advance(20);assert(outputLevels[9]==HIGH);
  Wire.values[1]=720;advance(32000);
  assert(channels[0].mode==Mode::Ready && risingEdges[8]==0);
}
void stuck() {
  ready(); contact(0,8);
  advance(90100);
  assert(channels[0].fault==Fault::Stuck && !channels[0].active);
  assert(risingEdges[8]==1 && channels[0].stuckFaults==1);
  advance(5000);assert(risingEdges[8]==1);
  contact(1);release(1);assert(risingEdges[9]==1);
  release(0);assert(channels[0].mode==Mode::Ready);
  contact(0);assert(risingEdges[8]==2);
}
void faults() {
  ready(); contact(0);
  const auto current=sensor.chargeCurrent[1];
  Wire.regs[1]=0x80;Wire.regs[0x5E]=0;
  advance(4);
  assert(linkMode==LinkMode::Retry && sensor.error==2 && !outputLevels[8]);
  advance(3000);
  assert(linkMode==LinkMode::Running && sensor.haveRange);
  assert(Wire.regs[0x5F+1]==current && Wire.regs[0x7B]==0x08);
  assert(channels[0].mode==Mode::WaitRelease && risingEdges[8]==1);
  release(0);contact(0);assert(risingEdges[8]==2);release(0);
  Wire.shortRead=true;advance(4);assert(linkMode==LinkMode::Retry);
  Wire.shortRead=false;advance(3000);
  assert(channels[0].mode==Mode::Ready && risingEdges[8]==2);
  Wire.regs[0x5E]=0;advance(4);assert(sensor.error==3);
  advance(3000);assert(channels[0].mode==Mode::Ready);
}
void autorange() {
  Wire.rangeFailureStarts=2;setup();advance(18000);
  assert(Wire.resets==3 && channels[0].mode==Mode::Ready);
  for(unsigned i=0;i<4;++i)assert(risingEdges[Settings::outputs[i]]==0);
  assert(sensor.haveRange);
}

void startup_fault() {
  Wire.ack=false; setup();advance(5000);
  assert(linkMode==LinkMode::Retry && WDT.refreshes>1000);
  for(unsigned i=0;i<4;++i)assert(risingEdges[Settings::outputs[i]]==0);
  Wire.ack=true;advance(36000);
  assert(channels[0].mode==Mode::Ready);
}
void readback() {
  Wire.corruptRegister=0x5C;setup();advance(5000);
  assert(linkMode==LinkMode::Retry);
  Wire.corruptRegister=-1;advance(36000);assert(channels[0].mode==Mode::Ready);
}
void range() {
  ready(); Wire.regs[2]=1<<1; advance(12);
  assert(channels[0].fault==Fault::Range && !channels[0].active);
  contact(1);release(1);assert(risingEdges[9]==1);
  Wire.regs[2]=0;advance(31000);assert(channels[0].mode==Mode::Ready);
  Wire.values[1]=0;advance(4);assert(channels[0].fault==Fault::Range);
}
void bus() {
  heldLow[SCL]=true;setup();advance(5000);
  assert(linkMode==LinkMode::Retry && WDT.refreshes>10);
  heldLow[SCL]=false;advance(36000);assert(channels[0].mode==Mode::Ready);
}
void wraps() {
  fakeNowUs=(uint64_t(UINT32_MAX)-2000)*1000;ready();
  contact(0);release(0);assert(risingEdges[8]==1);
}
void daily() {
  Lickometer::Channel c;
  uint32_t now=0;
  for(;now<4200;now+=4)c.sample(720,false,now);
  assert(c.mode==Mode::Ready);
  const uint32_t end=Settings::maintenanceMs+2100;
  for(;now<end;now+=4)c.sample(720,false,now);
  // Cross maintenance deadline during normal activity: must not interrupt it.
  for(unsigned i=0;i<500;++i,now+=4)c.sample(712,false,now);
  assert(c.active && c.onsets==1);
  for(unsigned i=0;i<3000;++i,now+=4)c.sample(720,false,now);
  assert(c.mode==Mode::Ready && !c.active && c.onsets==1);
}
int main(int argc,char**argv) {
  assert(argc==2);const std::string name=argv[1];
  if(name=="sensing")sensing();else if(name=="debounce")debounce();
  else if(name=="noise")noise();else if(name=="stuck")stuck();
  else if(name=="faults")faults();else if(name=="startup")startup_fault();
  else if(name=="autorange")autorange();else if(name=="readback")readback();else if(name=="range")range();
  else if(name=="bus")bus();else if(name=="wraps")wraps();
  else if(name=="daily")daily();else return 2;
  std::cout<<name<<" passed\n";
}
