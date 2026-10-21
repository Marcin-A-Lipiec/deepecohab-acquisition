/* DeepEcoHAB Patch 3 lighting controller v5.
   Arduino GIGA R1 WiFi (M7), GIGA Display, DS3231 on Wire1 D102/D101,
   MOSFET D7 active HIGH. The RTC stores UTC. Once initialized, the experiment
   clock advances continuously; RTC faults/recovery never realign a running cycle.
   USB disconnect and RFID stop do not change output. STOP_CONTROL is explicit.
   Nonvolatile settings use namespaced keys in the standard 1 MiB QSPI KV partition
   (partition 3, 6–7 MiB), after verifying its MBR bounds. No disk formatting.
*/
#include "DeviceRuntime.h"
#include <Arduino.h>
#include <Arduino_GigaDisplay_GFX.h>
#include <BlockDevice.h>
#include <MBRBlockDevice.h>
#include <TDBStore.h>
#include <Wire.h>

constexpr uint8_t lightPin = 7, rtcAddress = 0x68;
GigaDisplay_GFX display;
mbed::BlockDevice *flash = mbed::BlockDevice::get_default_instance();
mbed::MBRBlockDevice kvPartition(flash, 3);
mbed::TDBStore kv(&kvPartition);
bool storageReady = false, rtcHealthy = false, lightState = false;
uint32_t rtcChecked = 0, displayAt = 0;
eco::RtcContinuity rtcContinuity;
const char *clockFault = "CLOCK_UNKNOWN";

uint32_t little32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
bool beginStorage() {
  if (!flash || flash->init() != 0)
    return false;
  alignas(32) uint8_t mbr[512];
  if (flash->read(mbr, 0, sizeof(mbr)) != 0 || mbr[510] != 0x55 || mbr[511] != 0xAA)
    return false;
  const uint8_t *entry = mbr + 446 + 2 * 16;
  if (little32(entry + 8) != 12288 || little32(entry + 12) != 2048)
    return false;
  // Verify no other partition overlaps the dedicated KV range.
  for (unsigned i = 0; i < 4; i++)
    if (i != 2) {
      const uint8_t *e = mbr + 446 + i * 16;
      uint32_t begin = little32(e + 8), size = little32(e + 12);
      if (size && begin < 14336 && (uint64_t)begin + size > 12288)
        return false;
    }
  return kvPartition.init() == 0 && kvPartition.size() == 1024 * 1024 && kv.init() == 0;
}
bool loadSlot(const char *key, eco::Stored &out) {
  size_t actual = 0;
  return storageReady && kv.get(key, &out, sizeof(out), &actual) == 0 && actual == sizeof(out) &&
         out.validRecord();
}
void loadRecord() {
  strcpy(record.schedule.id, "-");
  eco::Stored a, b;
  bool va = loadSlot("deepecohab_lighting_v5_a", a), vb = loadSlot("deepecohab_lighting_v5_b", b);
  if (va && (!vb || a.generation >= b.generation))
    record = a;
  else if (vb)
    record = b;
}
bool saveRecord(eco::Stored &candidate) {
  if (!storageReady)
    return false;
  candidate.generation = record.generation + 1;
  candidate.seal();
  const char *key =
      (candidate.generation & 1) ? "deepecohab_lighting_v5_b" : "deepecohab_lighting_v5_a";
  if (kv.set(key, &candidate, sizeof(candidate), 0) != 0)
    return false;
  eco::Stored check;
  return loadSlot(key, check) && !memcmp(&candidate, &check, sizeof(check));
}
uint8_t bcd(uint8_t value) { return (value / 10) * 16 + value % 10; }
int decimal(uint8_t value) {
  return (value & 15) > 9 || (value >> 4) > 9 ? -1 : (value >> 4) * 10 + (value & 15);
}
bool readRegisters(uint8_t reg, uint8_t *out, uint8_t count) {
  Wire1.beginTransmission(rtcAddress);
  Wire1.write(reg);
  if (Wire1.endTransmission(false) != 0)
    return false;
  if (Wire1.requestFrom(rtcAddress, count) != count)
    return false;
  for (unsigned i = 0; i < count; i++)
    out[i] = Wire1.read();
  return true;
}
bool rtcRead(uint32_t &utc) {
  uint8_t raw[7], status;
  if (!readRegisters(0, raw, 7) || !readRegisters(0x0F, &status, 1) || (status & 0x80))
    return false;
  if (raw[0] & 0x80 || raw[5] & 0x80)
    return false;
  int second = decimal(raw[0]), minute = decimal(raw[1]), hour;
  if (raw[2] & 0x40) {
    int h = decimal(raw[2] & 0x1F);
    if (h < 1 || h > 12)
      return false;
    hour = h % 12 + ((raw[2] & 0x20) ? 12 : 0);
  } else
    hour = decimal(raw[2] & 0x3F);
  int day = decimal(raw[4]), month = decimal(raw[5] & 0x1F), year = decimal(raw[6]) + 2000;
  if (year < 2020 || year > 2099 || month < 1 || month > 12 || day < 1 ||
      day > (int)eco::monthDays(year, month) || hour < 0 || hour > 23 || minute < 0 ||
      minute > 59 || second < 0 || second > 59)
    return false;
  utc = eco::epoch(year, month, day, hour, minute, second);
  return true;
}
bool rtcWrite(uint32_t utc) {
  unsigned y, m, d, h, mi, s;
  eco::calendar(utc, y, m, d, h, mi, s);
  Wire1.beginTransmission(rtcAddress);
  Wire1.write((uint8_t)0);
  for (uint8_t v : {bcd(s), bcd(mi), bcd(h), (uint8_t)1, bcd(d), bcd(m), bcd(y - 2000)})
    Wire1.write(v);
  if (Wire1.endTransmission() != 0)
    return false;
  uint8_t status;
  if (!readRegisters(0x0F, &status, 1))
    return false;
  Wire1.beginTransmission(rtcAddress);
  Wire1.write((uint8_t)0x0F);
  Wire1.write((uint8_t)(status & ~0x80));
  if (Wire1.endTransmission() != 0)
    return false;
  uint32_t readback;
  return rtcRead(readback) && readback >= utc && readback <= utc + 1;
}
bool setBoardClock(uint32_t utc) {
  experimentClock.set(utc);
  rtcHealthy = rtcWrite(utc);
  clockFault = rtcHealthy ? "NONE" : "RTC_WRITE_FAILED";
  rtcContinuity = eco::RtcContinuity();
  legacy("WARNING", rtcHealthy ? "UTC_CLOCK_INITIALIZED" : "RTC_WRITE_FAILED_FALLBACK_ACTIVE");
  return true;
}
const char *clockSource() {
  return experimentClock.known ? (rtcHealthy ? "RTC_CONTINUOUS" : "MONOTONIC_FALLBACK") : "UNKNOWN";
}
const char *deviceMode() {
  return record.armed ? (experimentClock.known ? "RUNNING" : "CLOCK_FAULT") : "IDLE";
}
const char *deviceFault() { return !storageReady ? "STORAGE_UNAVAILABLE" : clockFault; }
uint8_t channelMask() { return lightState ? 1 : 0; }
void updateLight() {
  bool next =
      record.armed && experimentClock.known && eco::lightOn(record.schedule, experimentClock.now());
  if (next != lightState) {
    lightState = next;
    digitalWrite(lightPin, next ? HIGH : LOW);
    legacy("WARNING", next ? "LIGHT_ON" : "LIGHT_OFF");
  }
}
void requestManualCalibration() {}
void afterCommit(bool) {}
void afterArm() { updateLight(); }
void checkRtc() {
  uint32_t now = millis();
  if ((uint32_t)(now - rtcChecked) < 1000)
    return;
  rtcChecked = now;
  uint32_t value = 0;
  bool readable = rtcRead(value);
  const char *previous = clockFault;
  clockFault = rtcContinuity.observe(experimentClock, readable, value);
  rtcHealthy = rtcContinuity.tracking;
  if (strcmp(previous, clockFault)) {
    legacy("WARNING", clockFault);
    sendStatus();
  }
}
void drawDisplay() {
  if ((uint32_t)(millis() - displayAt) < 1000)
    return;
  displayAt = millis();
  display.fillScreen(0x0841);
  display.setTextColor(0xFFFF);
  display.setTextSize(3);
  display.setCursor(24, 24);
  display.println("DeepEcoHAB lighting v5");
  display.setCursor(24, 84);
  display.print("State: ");
  display.println(deviceMode());
  display.setCursor(24, 138);
  display.print("Output: ");
  display.println(lightState ? "LIGHT" : "DARK / OFF");
  display.setTextSize(2);
  display.setCursor(24, 202);
  display.print("Clock: ");
  display.println(clockSource());
  display.setCursor(24, 244);
  display.print("UTC: ");
  if (experimentClock.known) {
    unsigned y, m, d, h, mi, s;
    eco::calendar(experimentClock.now(), y, m, d, h, mi, s);
    char text[40];
    snprintf(text, sizeof(text), "%04u-%02u-%02uT%02u:%02u:%02u+00:00", y, m, d, h, mi, s);
    display.println(text);
  } else
    display.println("unknown");
  display.setCursor(24, 292);
  display.print("Diagnostic: ");
  display.println(deviceFault());
  display.setCursor(24, 348);
  display.println("USB disconnect does not stop lighting.");
  display.setCursor(24, 386);
  display.println("Use explicit Stop lighting in the GUI.");
}
void setup() {
  deviceKind = "lighting";
  firmwareName = "DEEPECOHAB_GIGA_LIGHTING_V5";
  lightingDevice = true;
  pinMode(lightPin, OUTPUT);
  digitalWrite(lightPin, LOW);
  Serial.begin(115200);
  Wire1.begin();
  Wire1.setClock(100000);
  snprintf(uid, sizeof(uid), "%08lx%08lx%08lx", (unsigned long)HAL_GetUIDw0(),
           (unsigned long)HAL_GetUIDw1(), (unsigned long)HAL_GetUIDw2());
  storageReady = beginStorage();
  loadRecord();
  eco::Stored next = record;
  next.bootCount++;
  if (saveRecord(next))
    record = next;
  snprintf(bootId, sizeof(bootId), "%lu-%lu", (unsigned long)record.bootCount,
           (unsigned long)micros());
  tickClocks();
  uint32_t utc;
  if (rtcRead(utc)) {
    experimentClock.set(utc);
    rtcHealthy = true;
    clockFault = "NONE";
    rtcContinuity.observe(experimentClock, true, utc);
  }
  updateLight();
  display.begin();
  display.setRotation(1);
  legacy("BOOT", firmwareName);
  sendStatus();
}
void loop() {
  tickClocks();
  checkRtc();
  updateLight();
  pollControl();
  drawDisplay();
}
