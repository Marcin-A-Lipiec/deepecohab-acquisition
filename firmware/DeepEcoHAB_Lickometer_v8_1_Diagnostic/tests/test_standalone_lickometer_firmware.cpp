#define LICKOMETER_HOST_TEST
#include "../firmware/Capacitive_Lickometer_UNO_R4_v8_1_Diagnostic/Capacitive_Lickometer_UNO_R4_v8_1_Diagnostic.ino"
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
  assert(diagnosticCode(0)=='O');
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
  assert(Wire.resets==2 && channels[0].mode==Mode::Ready);
  assert(rangeSearch.trials==6 && rangeSelected && !failedRangeMask);
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
void low_valid_baseline() {
  Wire.values[1]=350; Wire.values[3]=450; Wire.values[5]=600;
  setup(); advance(4500);
  assert(rangeSearch.trials==0);
  for(unsigned i=0;i<4;++i) {
    assert(channels[i].mode==Mode::Ready);
    assert(risingEdges[Settings::outputs[i]]==0);
    Wire.values[Settings::electrodes[i]]-=5; advance(20);
    assert(risingEdges[Settings::outputs[i]]==1);
  }
}
void fallback_large_bottle(double pf) {
  Wire.autoFailureMask=Wire.physicsMask=0x02;
  Wire.capacitance[1]=pf;
  setup(); advance(20000);
  assert(rangeSelected && !failedRangeMask && rangeSearch.trials<=42);
  assert(rangeSearch.trials>6);
  for(unsigned i=0;i<4;++i) {
    assert(channels[i].mode==Mode::Ready);
    assert(risingEdges[Settings::outputs[i]]==0);
  }
  assert(channels[0].baseline>=Settings::baselineMinADC);
  assert(channels[0].baseline<=Settings::baselineMaxADC);
  // Healthy profiles are unchanged while only the failed electrode is searched.
  assert(sensor.chargeCurrent[3]==23 && (sensor.chargeTime[1]>>4)==2);
  Wire.adcDelta[1]=5; advance(20);
  assert(risingEdges[8]==1 && outputLevels[8]);
  advance(200); assert(risingEdges[8]==1);
  Wire.adcDelta[1]=0; advance(20); assert(!outputLevels[8]);
  Wire.adcDelta[1]=5; advance(20); assert(risingEdges[8]==2);
  const unsigned trials=rangeSearch.trials;
  const uint8_t cdc=sensor.chargeCurrent[1], cdt=sensor.chargeTime[0];
  Wire.regs[1]=0x80; Wire.regs[0x5E]=0;
  advance(4); assert(linkMode==LinkMode::Retry && !outputLevels[8]);
  advance(3000);
  assert(rangeSearch.trials==trials && risingEdges[8]==2);
  assert(sensor.chargeCurrent[1]==cdc && sensor.chargeTime[0]==cdt);
  assert(channels[0].mode==Mode::WaitRelease);
  Wire.adcDelta[1]=0; advance(120);
  Wire.adcDelta[1]=5; advance(20); assert(risingEdges[8]==3);
}
void failed_search(bool high) {
  Wire.autoFailureMask=Wire.physicsMask=0x02;
  Wire.capacitance[1]=high?0.05:20000;
  setup(); advance(20000);
  assert(rangeSearch.trials==42 && failedRangeMask==1);
  assert(channels[0].fault==Fault::Range && risingEdges[8]==0);
  assert(channels[0].rangeReason==(high?Lickometer::RangeReason::High:
                                       Lickometer::RangeReason::Low));
  for(unsigned i=1;i<4;++i)assert(channels[i].mode==Mode::Ready);
  contact(1); assert(risingEdges[9]==1);
  // A later quiet-looking value cannot bypass the failed-search latch.
  Wire.physicsMask=0; Wire.values[1]=720;
  advance(35000); assert(risingEdges[8]==0 && channels[0].fault==Fault::Range);
  const unsigned resets=Wire.resets;
  advance(30*60*1000); assert(Wire.resets==resets && risingEdges[8]==0);
}
void tune_link_fault() {
  Wire.autoFailureMask=Wire.physicsMask=0x02;
  Wire.capacitance[1]=900;
  setup(); advance(2400); assert(linkMode==LinkMode::Tuning);
  Wire.ack=false; advance(200); assert(linkMode==LinkMode::Retry);
  Wire.ack=true; advance(25000);
  assert(rangeSelected && !failedRangeMask && channels[0].mode==Mode::Ready);
  for(unsigned i=0;i<4;++i)assert(risingEdges[Settings::outputs[i]]==0);
}
void multichannel_search() {
  Wire.autoFailureMask=Wire.physicsMask=0xAA;
  Wire.capacitance[1]=15; Wire.capacitance[3]=200;
  Wire.capacitance[5]=900; Wire.capacitance[7]=20000;
  setup(); advance(20000);
  assert(failedRangeMask==8 && rangeSearch.trials==42);
  for(unsigned i=0;i<3;++i) {
    assert(channels[i].mode==Mode::Ready);
    assert(risingEdges[Settings::outputs[i]]==0);
    Wire.adcDelta[Settings::electrodes[i]]=5;advance(20);
    assert(risingEdges[Settings::outputs[i]]==1);
  }
  assert(channels[3].fault==Fault::Range && risingEdges[11]==0);
}
void measurement_fault_reasons() {
  ready();
  Wire.values[1]=100; Wire.values[3]=900; Wire.regs[2]=1<<5;
  advance(12);updateDisplay(millis(),true);
  assert(channels[0].rangeReason==Lickometer::RangeReason::Low);
  assert(channels[1].rangeReason==Lickometer::RangeReason::High);
  assert(channels[2].rangeReason==Lickometer::RangeReason::Hardware);
  assert(diagnosticCode(0)=='L');
  assert(diagnosticCode(1)=='H');
  assert(diagnosticCode(2)=='R');
  for(unsigned i=0;i<4;++i)assert(risingEdges[Settings::outputs[i]]==0);
}
void range_bounds() {
  // Voltage-valid readings must outrank an invalid point closer to the target.
  RangeSearch search;
  const uint8_t currents[8]={20,20,20,20,20,20,20,20};
  const uint8_t times[4]={0x11,0x11,0x11,0x11};
  search.begin(1,currents,times);
  uint16_t values[4]={640,720,720,720}; search.observe(values);
  values[0]=799; search.observe(values);
  assert(search.bestADC[0]==640);
  for(unsigned i=0;search.active && i<50;++i) {
    values[0]=200;search.observe(values);
  }
  assert(!search.active && search.bestADC[0]==640 && !search.unresolved);
}
void noisy_wait_reproduction() {
  setup(); Serial.connected=true;
  for (unsigned t=0;t<60000;t+=4) {
    Wire.values[1]=std::lround(720+4*std::sin(6.283185307179586*50*t/1000.0));
    advance(4);
  }
  // Reproduce the v8.1 defect without concealing it: accepted baseline/noise,
  // then release qualification can never complete on this periodic noise.
  assert(channels[0].calibrated && channels[0].mode==Mode::WaitRelease);
  assert(channels[0].onThreshold==6 && channels[0].offThreshold==3);
  assert(diagnosticCode(0)=='W');
  assert(Serial.received.find("CH1 state=WAIT_RELEASE")!=std::string::npos);
  assert(risingEdges[8]==0);
}
void noisy_fault_reproduction() {
  setup(); Serial.connected=true;
  for (unsigned t=0;t<12000;t+=4) {
    Wire.values[1]=std::lround(720+8*std::sin(6.283185307179586*50*t/1000.0));
    advance(4);
  }
  assert(channels[0].fault==Fault::Noise && !channels[0].calibrated);
  assert(channels[0].proposedThreshold==10 && channels[0].calibrationSpread==8);
  assert(diagnosticCode(0)=='N');
  assert(Serial.received.find("CH1 state=NOISE")!=std::string::npos);
  assert(Serial.received.find("proposed_on_x10=100")!=std::string::npos);
}
void usb_backpressure() {
  Serial.connected=true; Serial.capacity=0;ready();
  advance(15000);assert(diagnostics.dropped>0);
  contact(0);release(0);assert(risingEdges[8]==1);
  const unsigned refreshes=WDT.refreshes;
  Serial.capacity=7;advance(3000);
  assert(WDT.refreshes>refreshes+500);
  assert(Serial.received.find("V8.1-DIAG1")!=std::string::npos);
  Serial.connected=false;advance(10);
  assert(diagnostics.head==diagnostics.tail);
  contact(0);release(0);assert(risingEdges[8]==2);
  Serial.connected=true;Serial.capacity=64;advance(3000);
  assert(Serial.received.find("state=READY")!=std::string::npos);
  assert(Serial.received.find("n=250")!=std::string::npos);
  assert(Serial.received.find("cal_median_x10=7200")!=std::string::npos);
}
int main(int argc,char**argv) {
  assert(argc==2);const std::string name=argv[1];
  if(name=="sensing")sensing();else if(name=="debounce")debounce();
  else if(name=="noise")noise();else if(name=="stuck")stuck();
  else if(name=="faults")faults();else if(name=="startup")startup_fault();
  else if(name=="autorange")autorange();else if(name=="readback")readback();else if(name=="range")range();
  else if(name=="bus")bus();else if(name=="wraps")wraps();
  else if(name=="daily")daily();
  else if(name=="low_valid")low_valid_baseline();
  else if(name=="large_bottle")fallback_large_bottle(900);
  else if(name=="wide_baseline")fallback_large_bottle(2000);
  else if(name=="failed_low")failed_search(false);
  else if(name=="failed_high")failed_search(true);
  else if(name=="tune_link")tune_link_fault();
  else if(name=="multi_search")multichannel_search();
  else if(name=="reasons")measurement_fault_reasons();
  else if(name=="range_bounds")range_bounds();
  else if(name=="noisy_wait")noisy_wait_reproduction();
  else if(name=="noisy_fault")noisy_fault_reproduction();
  else if(name=="usb_backpressure")usb_backpressure();
  else return 2;
  std::cout<<name<<" passed\n";
}
