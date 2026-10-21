#include "../firmware/Capacitive_Lickometer_UNO_R4_v6_2/Capacitive_Lickometer_UNO_R4_v6_2.ino"
#include <cassert>
#include <iostream>
void advance(uint32_t ms) {
  for (uint32_t t = 0; t < ms; t += 5) {
    delay(5);
    loop();
  }
}
void ready() {
  Wire.ack = Wire.readOK = true;
  Wire.mask = 0;
  sensor.beginOk = true;
  record.settings = eco::Settings();
  record.armed = 0;
  startCalibration("TEST");
  advance(1510);
  assert(sensorMode == SENSOR_READY);
}
int main() {
  setup();
  advance(1510);
  assert(sensorMode == SENSOR_READY);
  // 90s, not pending duration, controls forced interruption.
  Wire.mask = 1 << 1;
  advance(10);
  assert(outputLevels[8] == HIGH);
  requestRoutine("DAILY");
  advance(89990);
  assert(sensorMode == SENSOR_READY);
  advance(10);
  assert(sensorMode == SENSOR_SETTLING);
  assert(outputLevels[8] == LOW);
  assert(!strcmp(calibrationReason, "STUCK_ACTIVE_FAULT"));
  ready();
  record.settings.stuck = 0;
  Wire.mask = 1 << 1;
  advance(10);
  requestRoutine("MANUAL");
  advance(120000);
  assert(sensorMode == SENSOR_READY && outputLevels[8] == HIGH);
  Wire.mask = 0;
  advance(10);
  advance(4990);
  assert(sensorMode == SENSOR_READY);
  advance(10);
  assert(sensorMode == SENSOR_SETTLING);
  ready();
  record.settings.recover = 1;
  Wire.ack = false;
  advance(800);
  assert(sensorMode == SENSOR_FAILED);
  auto retry = retryAt;
  advance(1000);
  assert(retryAt == retry);
  Serial.input = "STATUS\n";
  advance(20);
  assert(Serial.output.find("STATUS2,lickometer") !=
         std::string::npos); // Commands stay responsive during retries.
  Wire.ack = true;
  sensor.beginOk = false;
  advance(5000);
  assert(sensorMode == SENSOR_FAILED);
  sensor.beginOk = true;
  advance(16000);
  assert(sensorMode == SENSOR_READY);
  ready();
  Wire.readOK = false;
  advance(800);
  assert(sensorMode != SENSOR_READY);
  advance(1510);
  assert(sensorMode == SENSOR_FAILED);
  Wire.readOK = true;
  ready();
  Serial.connected = false;
  Wire.mask = 1 << 3;
  advance(10);
  assert(outputLevels[9] == HIGH);
  advance(1000);
  assert(sensorMode == SENSOR_READY);
  Serial.connected = true;
  // Uptime and schedule clocks survive the millis and micros wraps.
  eco::Clock c;
  c.raw = 0xfffffff0U;
  c.tick(20);
  assert(c.elapsedMs == 36);
  c.set(1800000000);
  c.tick(1020);
  assert(c.now() == 1800000001);
  uint32_t utc = 0;
  assert(eco::parseUtc("2026-10-25T00:00:00+00:00", utc));
  assert(!eco::parseUtc("2026-10-25T00:00:00+02:00", utc));
  assert(!eco::parseUtc("2026-02-30T00:00:00Z", utc));
  eco::Schedule s;
  strcpy(s.id, "test");
  s.anchor = utc;
  s.light = s.dark = 43200;
  assert(eco::lightOn(s, utc + 43199));
  assert(!eco::lightOn(s, utc + 43200));
  assert(eco::lightOn(s, utc + 86400));
  assert(eco::nextDaily(s, 3600, utc + 90000) == utc + 176400);
  eco::Clock rtcClock;
  rtcClock.set(1800000000);
  eco::RtcContinuity tracker;
  tracker.observe(rtcClock, true, 1800000000);
  rtcClock.tick(1000);
  tracker.observe(rtcClock, true, 1800000001);
  assert(tracker.tracking);
  rtcClock.tick(5000);
  assert(!strcmp(tracker.observe(rtcClock, true, 1800000001), "RTC_FROZEN"));
  assert(rtcClock.now() == 1800000005);
  tracker.observe(rtcClock, false, 0);
  rtcClock.tick(6000);
  assert(rtcClock.now() == 1800000006);
  tracker.observe(rtcClock, true, 1800006000);
  rtcClock.tick(7000);
  tracker.observe(rtcClock, true, 1800006001);
  assert(tracker.tracking);
  assert(rtcClock.now() == 1800000007);
  rtcClock.tick(8000);
  tracker.observe(rtcClock, true, 1800006002);
  assert(rtcClock.now() == 1800000008);
  // Source recovery follows RTC intervals with an offset; no calendar jump occurs.
  // Corruption cannot displace the last complete nonvolatile record.
  eco::Stored saved = record;
  saved.revision = 33;
  assert(saveRecord(saved));
  record = saved;
  eco::Stored newer = record;
  newer.revision = 34;
  assert(saveRecord(newer));
  int slot = (newer.generation & 1) ? 512 : 0;
  EEPROM.bytes[slot + 17] ^= 1;
  assert(loadRecord());
  assert(record.revision == 33);
  // Full field validation and checksum are enforced by the real command handler.
  char cmd[] = "SET_TIME,r17,2026-10-25T00:00:00+00:00";
  experimentClock.known = false;
  handleControl(cmd);
  assert(experimentClock.known);
  auto payload = std::string("1,3600,5000,1,90000,30000,1,20,10,1,180000,500,1,4,5,15,60,300");
  char checksum[9];
  snprintf(checksum, 9, "%08lx", (unsigned long)eco::crc32(payload.data(), payload.size()));
  std::string settings = std::string("SET_SETTINGS,r18,35,") + checksum + "," + payload;
  handleControl(settings.data());
  assert(stagedSettings);
  char sched[] = "SET_SCHEDULE,r19,35,test,1800000000,43200,43200";
  handleControl(sched);
  assert(stagedSchedule);
  char commit[] = "COMMIT,r20,35";
  handleControl(commit);
  assert(record.revision == 35);
  char arm[] = "ARM,r21,test,35";
  handleControl(arm);
  assert(record.armed);
  auto anchor = experimentClock.anchorUtc;
  char timechange[] = "SET_TIME,r22,2027-01-01T00:00:00+00:00";
  handleControl(timechange);
  assert(experimentClock.anchorUtc == anchor);
  nextDailyUtc = experimentClock.now();
  tickDaily();
  assert(pending);
  auto next = nextDailyUtc;
  tickDaily();
  assert(nextDailyUtc == next);
  std::cout << "Patch 3 firmware: quiet / 90-second fault / sensor recovery / USB independence / "
               "clocks / CRC / command commit passed.\n";
}
