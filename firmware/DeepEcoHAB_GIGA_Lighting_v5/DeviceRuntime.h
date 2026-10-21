#pragma once
#include "EcoControl.h"
#include <Arduino.h>

// Bounded nonblocking USB output. TTL/sensor work always precedes serial draining.
struct UsbQueue {
  char bytes[4096];
  unsigned head = 0, tail = 0;
  uint32_t dropped = 0;
  void line(const char *s) {
    unsigned n = strlen(s);
    unsigned free = (tail + sizeof(bytes) - head - 1) % sizeof(bytes);
    if (n + 1 > free) {
      ++dropped;
      return;
    }
    while (*s) {
      bytes[head] = *s++;
      head = (head + 1) % sizeof(bytes);
    }
    bytes[head] = '\n';
    head = (head + 1) % sizeof(bytes);
  }
  void pump() {
    if (!Serial) {
      while (tail != head) {
        if (bytes[tail] == '\n')
          ++dropped;
        tail = (tail + 1) % sizeof(bytes);
      }
      return;
    }
    unsigned count = (head + sizeof(bytes) - tail) % sizeof(bytes);
    int available = Serial.availableForWrite();
    if (available <= 0 || !count)
      return;
    if (count > 64)
      count = 64;
    if (count > (unsigned)available)
      count = available;
    if (count > sizeof(bytes) - tail)
      count = sizeof(bytes) - tail;
    size_t written = Serial.write((const uint8_t *)bytes + tail, count);
    tail = (tail + written) % sizeof(bytes);
  }
};

// Implemented by each board adapter; declarations precede Arduino auto-prototypes.
bool saveRecord(eco::Stored &candidate);
bool setBoardClock(uint32_t utc);
void requestManualCalibration();
void afterCommit(bool dailyChanged);
void afterArm();
const char *deviceMode();
const char *clockSource();
const char *deviceFault();
uint8_t channelMask();

UsbQueue usb;
eco::Clock experimentClock;
eco::Stored record, staged;
uint32_t stageRevision = 0;
bool stagedSchedule = false, stagedSettings = false;
char uid[40] = "unknown", bootId[40] = "unknown";
uint64_t uptimeUs = 0;
uint32_t lastMicros = 0;
const char *deviceKind;
const char *firmwareName;
bool lightingDevice = false;
uint32_t lastHeartbeat = 0;
char rx[512];
unsigned rxLength = 0;
bool rxOverflow = false;
struct HistoryEntry {
  uint32_t seq = 0, utc = 0;
  char line[160] = {0};
};
HistoryEntry history[24];
unsigned historyHead = 0, historyCount = 0;
uint32_t historySequence = 0, historyOverwritten = 0;
void rememberHealth(const char *line) {
  HistoryEntry &entry = history[historyHead];
  entry.seq = ++historySequence;
  entry.utc = experimentClock.now();
  strncpy(entry.line, line, sizeof(entry.line) - 1);
  entry.line[sizeof(entry.line) - 1] = 0;
  historyHead = (historyHead + 1) % 24;
  if (historyCount < 24)
    ++historyCount;
  else
    ++historyOverwritten;
}
void sendHistory() {
  char line[240];
  snprintf(line, sizeof(line), "HEALTH_SUMMARY,%s,%lu,%lu", bootId,
           (unsigned long)historyOverwritten, (unsigned long)usb.dropped);
  usb.line(line);
  for (unsigned i = 0; i < historyCount; i++) {
    const HistoryEntry &entry = history[(historyHead + 24 - historyCount + i) % 24];
    snprintf(line, sizeof(line), "HISTORY,%s,%lu,%lu,%s", bootId, (unsigned long)entry.seq,
             (unsigned long)entry.utc, entry.line);
    usb.line(line);
  }
}

void tickClocks() {
  experimentClock.tick(millis());
  uint32_t raw = micros();
  uptimeUs += (uint32_t)(raw - lastMicros);
  lastMicros = raw;
}
void legacy(const char *type, const char *details = "") {
  tickClocks();
  char line[384];
  snprintf(line, sizeof(line), "%s,%lu,%lu%s%s", type, (unsigned long)(uptimeUs >> 32),
           (unsigned long)(uint32_t)uptimeUs, *details ? "," : "", details);
  usb.line(line);
  if (strcmp(type, "EVENT") && strcmp(type, "STATE"))
    rememberHealth(line);
}
void sendStatus(const char *prefix = "STATUS2") {
  tickClocks();
  char line[450];
  snprintf(line, sizeof(line), "%s,%s,%s,%s,%s,%s,%lu,%s,%lu,%lu,%lu,%lu,%08lx,%u,%s,%llu,%u",
           prefix, deviceKind, uid, bootId, deviceMode(), clockSource(),
           (unsigned long)experimentClock.now(), record.schedule.id,
           (unsigned long)record.schedule.anchor, (unsigned long)record.schedule.light,
           (unsigned long)record.schedule.dark, (unsigned long)record.revision,
           (unsigned long)record.settingsCrc, (unsigned)channelMask(), deviceFault(),
           (unsigned long long)uptimeUs, (unsigned)record.armed);
  usb.line(line);
}
void reply(const char *req, const char *verb, const char *error = nullptr) {
  char line[192];
  if (error)
    snprintf(line, sizeof(line), "ERR,%s,%s,%s", req, verb, error);
  else
    snprintf(line, sizeof(line), "ACK,%s,%s,%lu", req, verb, (unsigned long)record.revision);
  usb.line(line);
}
void stage(uint32_t revision) {
  if (stageRevision != revision) {
    staged = record;
    stageRevision = revision;
    stagedSchedule = false;
    stagedSettings = false;
  }
}
void handleControl(char *line) {
  if (!strcmp(line, "HELLO")) {
    char out[192];
    snprintf(out, sizeof(out), "HELLO,2,%s,%s,%s,%s", deviceKind, uid, bootId, firmwareName);
    usb.line(out);
    sendHistory();
    return;
  }
  if (!strcmp(line, "STATUS")) {
    sendStatus();
    return;
  }
  if (!strcmp(line, "RECALIBRATE") && !lightingDevice) {
    requestManualCalibration();
    return;
  }
  // Keep a canonical settings string for its checksum before field tokenization.
  char original[512];
  strncpy(original, line, sizeof(original));
  original[sizeof(original) - 1] = 0;
  char *fields[32];
  unsigned count = 0;
  char *p = line;
  fields[count++] = p;
  for (; *p; p++)
    if (*p == ',') {
      *p = 0;
      if (count >= 32) {
        usb.line("ERR,-,FRAME,TOO_MANY_FIELDS");
        return;
      }
      fields[count++] = p + 1;
    }
  if (count < 2 || !eco::identifier(fields[1], 24)) {
    usb.line("ERR,-,FRAME,INVALID_REQUEST");
    return;
  }
  const char *verb = fields[0], *req = fields[1];
  uint32_t revision = 0;
  if (!strcmp(verb, "SET_TIME")) {
    uint32_t utc;
    if (count != 3 || !eco::parseUtc(fields[2], utc)) {
      reply(req, verb, "INVALID_UTC");
      return;
    }
    if (experimentClock.known && (!lightingDevice || record.armed)) {
      reply(req, verb, "CLOCK_LOCKED");
      return;
    }
    if (!setBoardClock(utc)) {
      reply(req, verb, "CLOCK_UNAVAILABLE");
      return;
    }
    reply(req, verb);
    return;
  }
  if (!strcmp(verb, "SET_SCHEDULE")) {
    if (count != 7 || !eco::number(fields[2], revision) || revision <= record.revision) {
      reply(req, verb, "INVALID_REVISION");
      return;
    }
    if (lightingDevice && record.armed) {
      reply(req, verb, "SCHEDULE_LOCKED");
      return;
    }
    eco::Schedule s;
    strncpy(s.id, fields[3], 40);
    s.id[40] = 0;
    if (strlen(fields[3]) > 40 || !eco::number(fields[4], s.anchor) ||
        !eco::number(fields[5], s.light) || !eco::number(fields[6], s.dark) ||
        !eco::valid(s, !lightingDevice)) {
      reply(req, verb, "INVALID_SCHEDULE");
      return;
    }
    stage(revision);
    staged.schedule = s;
    stagedSchedule = true;
    reply(req, verb);
    return;
  }
  if (!strcmp(verb, "SET_SETTINGS") && !lightingDevice) {
    if (count < 19 || !eco::number(fields[2], revision) || revision <= record.revision) {
      reply(req, verb, "INVALID_REVISION");
      return;
    }
    char *payload = original;
    for (int i = 0; i < 4; i++) {
      payload = strchr(payload, ',');
      if (!payload) {
        reply(req, verb, "INVALID_SETTINGS");
        return;
      }
      ++payload;
    }
    char checksum[9];
    snprintf(checksum, sizeof(checksum), "%08lx",
             (unsigned long)eco::crc32(payload, strlen(payload)));
    eco::Settings settings;
    if (strcmp(checksum, fields[3]) || !eco::parseSettings(fields + 4, count - 4, settings)) {
      reply(req, verb, "INVALID_SETTINGS");
      return;
    }
    stage(revision);
    staged.settings = settings;
    staged.settingsCrc = eco::crc32(payload, strlen(payload));
    stagedSettings = true;
    reply(req, verb);
    return;
  }
  if (!strcmp(verb, "COMMIT")) {
    if (count != 3 || !eco::number(fields[2], revision) || revision != stageRevision ||
        revision <= record.revision || !stagedSchedule || (!lightingDevice && !stagedSettings)) {
      reply(req, verb, "INCOMPLETE_STAGE");
      return;
    }
    bool changed = !eco::same(record.schedule, staged.schedule) ||
                   record.settings.offset != staged.settings.offset ||
                   record.settings.daily != staged.settings.daily;
    staged.revision = revision;
    staged.armed = (!lightingDevice && record.armed && !changed)
                       ? 1
                       : 0; // Keep an unchanged daily schedule armed.
    if (!saveRecord(staged)) {
      reply(req, verb, "STORAGE_UNAVAILABLE");
      return;
    }
    record = staged;
    stageRevision = 0;
    afterCommit(changed);
    reply(req, verb);
    sendStatus();
    return;
  }
  if (!strcmp(verb, "ARM")) {
    if (count != 4 || strcmp(fields[2], record.schedule.id) || !eco::number(fields[3], revision) ||
        revision != record.revision || !eco::valid(record.schedule) || !experimentClock.known) {
      reply(req, verb, "ARM_MISMATCH_OR_CLOCK_UNKNOWN");
      return;
    }
    if (!record.armed) {
      eco::Stored candidate = record;
      candidate.armed = 1;
      if (!saveRecord(candidate)) {
        reply(req, verb, "STORAGE_UNAVAILABLE");
        return;
      }
      record = candidate;
      afterArm();
    }
    reply(req, verb);
    sendStatus();
    return;
  }
  if (!strcmp(verb, "STOP_CONTROL") && lightingDevice) {
    if (count != 2) {
      reply(req, verb, "INVALID_ARGUMENTS");
      return;
    }
    eco::Stored candidate = record;
    candidate.armed = 0;
    if (!saveRecord(candidate)) {
      record.armed = 0;
      afterArm();
      reply(req, verb, "STOPPED_BUT_STORAGE_FAILED");
      sendStatus();
      return;
    }
    record = candidate;
    afterArm();
    reply(req, verb);
    sendStatus();
    return;
  }
  reply(req, verb, "UNKNOWN_COMMAND");
}
void pollControl() {
  // Budget serial input so even a noisy host cannot starve sensing or light control.
  for (unsigned budget = 0; budget < 64 && Serial.available(); budget++) {
    char c = (char)Serial.read();
    if (c == '\r')
      continue;
    if (c == '\n') {
      if (rxOverflow)
        usb.line("ERR,-,FRAME,TOO_LONG");
      else if (rxLength) {
        rx[rxLength] = 0;
        handleControl(rx);
      }
      rxLength = 0;
      rxOverflow = false;
    } else if (!rxOverflow) {
      if (rxLength >= sizeof(rx) - 1)
        rxOverflow = true;
      else if (c >= 32 && c <= 126)
        rx[rxLength++] = c;
      else
        rxOverflow = true;
    }
  }
  if ((uint32_t)(millis() - lastHeartbeat) >= 2000) {
    lastHeartbeat = millis();
    sendStatus("HEARTBEAT");
  }
  usb.pump();
}
