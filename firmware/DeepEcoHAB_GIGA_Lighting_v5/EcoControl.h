#pragma once
// Pure fixed-clock and safeguard primitives, shared by both Patch 3 sketches.
#include <initializer_list>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace eco {
inline uint32_t crc32(const void *data, size_t length) {
  uint32_t crc = 0xFFFFFFFFU;
  const uint8_t *p = (const uint8_t *)data;
  while (length--) {
    crc ^= *p++;
    for (uint8_t i = 0; i < 8; i++)
      crc = (crc >> 1) ^ ((0UL - (crc & 1)) & 0xEDB88320UL);
  }
  return ~crc;
}
inline bool number(const char *text, uint32_t &value) {
  if (!text || !*text)
    return false;
  uint64_t n = 0;
  for (const char *p = text; *p; p++) {
    if (*p < '0' || *p > '9')
      return false;
    n = n * 10 + (*p - '0');
    if (n > 0xFFFFFFFFULL)
      return false;
  }
  value = (uint32_t)n;
  return true;
}
inline bool identifier(const char *s, size_t maxLen = 40) {
  if (!s || !*s || strlen(s) > maxLen)
    return false;
  for (; *s; s++)
    if (!((*s >= 'A' && *s <= 'Z') || (*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') ||
          *s == '_' || *s == '-'))
      return false;
  return true;
}
inline bool leap(unsigned y) { return y % 4 == 0 && (y % 100 != 0 || y % 400 == 0); }
inline unsigned monthDays(unsigned y, unsigned m) {
  const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  return m >= 1 && m <= 12 ? days[m - 1] + (m == 2 && leap(y)) : 0;
}
inline uint32_t epoch(unsigned y, unsigned m, unsigned d, unsigned h, unsigned min, unsigned s) {
  uint32_t days = 0;
  for (unsigned a = 1970; a < y; a++)
    days += leap(a) ? 366 : 365;
  for (unsigned a = 1; a < m; a++)
    days += monthDays(y, a);
  return ((days + d - 1) * 24 + h) * 3600 + min * 60 + s;
}
inline bool parseUtc(const char *text, uint32_t &out) {
  // Accept whole seconds with an explicit UTC designator, and consume the whole string.
  size_t n = strlen(text);
  if (n != 20 && n != 25)
    return false;
  if (text[4] != '-' || text[7] != '-' || text[10] != 'T' || text[13] != ':' || text[16] != ':')
    return false;
  if ((n == 20 && text[19] != 'Z') || (n == 25 && strcmp(text + 19, "+00:00")))
    return false;
  for (unsigned i = 0; i < 19; i++)
    if (i != 4 && i != 7 && i != 10 && i != 13 && i != 16 && (text[i] < '0' || text[i] > '9'))
      return false;
  unsigned y, m, d, h, mi, s;
  if (sscanf(text, "%4u-%2u-%2uT%2u:%2u:%2u", &y, &m, &d, &h, &mi, &s) != 6)
    return false;
  if (y < 2020 || y > 2099 || m < 1 || m > 12 || d < 1 || d > monthDays(y, m) || h > 23 ||
      mi > 59 || s > 59)
    return false;
  out = epoch(y, m, d, h, mi, s);
  return true;
}
inline void calendar(uint32_t value, unsigned &y, unsigned &m, unsigned &d, unsigned &h,
                     unsigned &mi, unsigned &s) {
  s = value % 60;
  value /= 60;
  mi = value % 60;
  value /= 60;
  h = value % 24;
  value /= 24;
  y = 1970;
  while (value >= (leap(y) ? 366U : 365U))
    value -= leap(y++) ? 366 : 365;
  m = 1;
  while (value >= monthDays(y, m))
    value -= monthDays(y, m++);
  d = value + 1;
}
struct Clock {
  uint64_t elapsedMs = 0, anchorMs = 0;
  uint32_t raw = 0, anchorUtc = 0;
  mutable uint32_t lastReturned = 0;
  bool known = false;
  void tick(uint32_t now) {
    elapsedMs += (uint32_t)(now - raw);
    raw = now;
  }
  uint32_t now() const {
    if (!known)
      return 0;
    uint32_t v = anchorUtc + (uint32_t)((elapsedMs - anchorMs) / 1000);
    if (v > lastReturned)
      lastReturned = v;
    return lastReturned;
  }
  void set(uint32_t utc) {
    anchorUtc = utc;
    anchorMs = elapsedMs;
    lastReturned = utc;
    known = true;
  }
  void discipline(uint32_t utc) {
    anchorUtc = utc;
    anchorMs = elapsedMs;
    known = true;
  }
};
// Follow advancing RTC ticks while preserving the continuous timeline at recovery.
// A slow-clock correction briefly holds a second; it never reverses phase state.
struct RtcContinuity {
  uint32_t previous = 0;
  uint64_t advancedAt = 0;
  int64_t offset = 0;
  bool tracking = false;
  const char *observe(Clock &clock, bool readable, uint32_t rtc) {
    if (!readable) {
      tracking = false;
      previous = 0;
      return "RTC_UNAVAILABLE";
    }
    if (!clock.known)
      clock.set(rtc);
    if (!previous) {
      previous = rtc;
      advancedAt = clock.elapsedMs;
      return "RTC_CHECKING";
    }
    if (rtc == previous) {
      if (clock.elapsedMs - advancedAt > 3500) {
        tracking = false;
        return "RTC_FROZEN";
      }
      return tracking ? (offset > 2 || offset < -2 ? "RTC_OFFSET_APPLIED" : "NONE")
                      : "RTC_CHECKING";
    }
    int64_t step = (int64_t)rtc - previous;
    int64_t measured = (int64_t)(clock.elapsedMs - advancedAt);
    previous = rtc;
    advancedAt = clock.elapsedMs;
    if (step < 1 || step * 1000 - measured > 2000 || step * 1000 - measured < -2000) {
      tracking = false;
      return "RTC_JUMP";
    }
    if (!tracking) {
      offset = (int64_t)clock.now() - rtc;
      tracking = true;
    }
    int64_t desired = (int64_t)rtc + offset;
    if (desired < 0 || desired > 0xFFFFFFFFLL) {
      tracking = false;
      return "RTC_RANGE_ERROR";
    }
    clock.discipline((uint32_t)desired);
    return offset > 2 || offset < -2 ? "RTC_OFFSET_APPLIED" : "NONE";
  }
};
struct Schedule {
  char id[41] = {0};
  uint32_t anchor = 0, light = 43200, dark = 43200;
};
inline bool valid(const Schedule &s, bool allowEmpty = false) {
  return identifier(s.id) && s.light > 0 && s.dark > 0 && (uint64_t)s.light + s.dark == 86400 &&
         ((allowEmpty && s.anchor == 0 && strcmp(s.id, "-") == 0) ||
          (s.anchor >= 1577836800UL && s.anchor <= 4102444799UL));
}
inline bool same(const Schedule &a, const Schedule &b) {
  return !strcmp(a.id, b.id) && a.anchor == b.anchor && a.light == b.light && a.dark == b.dark;
}
inline int64_t cycleOffset(uint32_t now, uint32_t anchor) {
  int64_t v = ((int64_t)now - anchor) % 86400;
  return v < 0 ? v + 86400 : v;
}
inline bool lightOn(const Schedule &s, uint32_t now) {
  return cycleOffset(now, s.anchor) < s.light;
}
inline uint32_t nextDaily(const Schedule &s, uint32_t offset, uint32_t now) {
  int64_t base = (int64_t)s.anchor + offset;
  int64_t delta = (int64_t)now - base;
  int64_t day = delta / 86400;
  if (delta < 0 && delta % 86400)
    --day;
  return (uint32_t)(base + (day + 1) * 86400);
}
struct Settings {
  uint32_t daily = 1, offset = 3600, quietMs = 5000, stuck = 1, stuckMs = 90000, longMs = 30000;
  uint32_t highRate = 1, edges = 20, rateSeconds = 10, continuous = 1, continuousMs = 180000,
           pauseMs = 500, recover = 1;
  uint32_t retryCount = 4, retries[8] = {5, 15, 60, 300, 0, 0, 0, 0};
};
inline bool parseSettings(char **fields, unsigned count, Settings &s) {
  if (count < 15 || count > 22)
    return false;
  uint32_t v[22] = {0};
  for (unsigned i = 0; i < count; i++)
    if (!number(fields[i], v[i]))
      return false;
  if (v[13] < 1 || v[13] > 8 || count != 14 + v[13])
    return false;
  for (unsigned i : {0U, 3U, 6U, 9U, 12U})
    if (v[i] > 1)
      return false;
  if (v[1] >= 86400 || v[2] < 1 || v[2] > 86400000UL || v[4] < 1 || v[4] > 86400000UL || v[5] < 1 ||
      v[5] >= v[4] || v[7] < 1 || v[7] > 86400 || v[8] < 1 || v[8] > 86400 || v[10] < 1 ||
      v[10] > 86400000UL || v[11] < 1 || v[11] > 86400)
    return false;
  for (unsigned i = 0; i < v[13]; i++)
    if (!v[14 + i] || v[14 + i] > 3600 || (i && v[14 + i] < v[13 + i]))
      return false;
  s.daily = v[0];
  s.offset = v[1];
  s.quietMs = v[2];
  s.stuck = v[3];
  s.stuckMs = v[4];
  s.longMs = v[5];
  s.highRate = v[6];
  s.edges = v[7];
  s.rateSeconds = v[8];
  s.continuous = v[9];
  s.continuousMs = v[10];
  s.pauseMs = v[11];
  s.recover = v[12];
  s.retryCount = v[13];
  for (unsigned i = 0; i < 8; i++)
    s.retries[i] = i < s.retryCount ? v[14 + i] : 0;
  return true;
}
struct Stored {
  uint32_t magic = 0x45434F33, format = 2, generation = 0, revision = 0, settingsCrc = 0;
  Schedule schedule;
  Settings settings;
  uint32_t armed = 0, bootCount = 0;
  uint32_t crc = 0;
  void seal() { crc = crc32(this, offsetof(Stored, crc)); }
  bool validRecord() const {
    return magic == 0x45434F33 && format == 2 && crc == crc32(this, offsetof(Stored, crc)) &&
           eco::valid(schedule, true) && armed <= 1;
  }
};
} // namespace eco
