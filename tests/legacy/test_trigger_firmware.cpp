#include "firmware_stubs/Arduino.h"
#include <cassert>
#include <iostream>
uint32_t fakeUs = 0;
int pinLevel = 0;
FakeSerialPort Serial;
#include "../firmware/Acquisition_GUI_v2_Trigger/Acquisition_GUI_v2_Trigger.ino"
void send(const char* text) {
  Serial.input = text;
  while (Serial.available()) loop();
}
int main() {
  setup(); assert(Serial.output.find("READY ACQ_TRIGGER_V2") != std::string::npos);
  send("ARMUS 1000 100 3 00:00:00 00:00:00\n");
  assert(state == RUNNING && pinLevel == HIGH && remaining == 2);
  fakeUs = 100; loop(); assert(pinLevel == LOW);
  fakeUs = 1000; loop(); assert(pinLevel == HIGH && remaining == 1);
  send("STOP\n"); assert(state == IDLE && pinLevel == LOW);
  assert(Serial.output.find("STOPPED") != std::string::npos);
  send("ARMUS 1000 100 4294967296 00:00:00 00:00:00\n"); assert(state == IDLE);
  send("ARMUS 1000 100 1 00:00:00 00:00:01\n"); assert(state == WAITING);
  fakeUs = 1001000; loop(); assert(state == RUNNING && pinLevel == HIGH);
  fakeUs += 100; loop(); assert(state == IDLE && pinLevel == LOW);
  std::cout << "Trigger native harness passed; actual Arduino timing still requires bench verification.\n";
}
