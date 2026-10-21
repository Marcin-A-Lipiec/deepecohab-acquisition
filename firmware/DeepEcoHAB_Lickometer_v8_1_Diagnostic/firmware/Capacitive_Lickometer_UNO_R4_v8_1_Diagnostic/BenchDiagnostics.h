#pragma once
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

// Passive bench observations, never a command receiver. No wait for USB and
// no extra sensor transactions. A slow/disconnected host loses diagnostic
// messages under backpressure. UNO R4 WiFi's default UART-to-USB bridge needs
// short synchronous writes; its availableForWrite() inherits a constant zero.
// This is a bench diagnostic, NOT an experiment logger or timing guarantee.
struct BenchDiagnostics {
  struct Window {
    uint16_t last = 0, low = 65535, high = 0;
    uint32_t count = 0, lastAt = 0;
    void observe(uint16_t value, uint32_t now) {
      last = value; lastAt = now; ++count;
      if (value < low) low = value;
      if (value > high) high = value;
    }
    void clear() { low = 65535; high = 0; count = 0; }
  } window[4];
  char queue[4096] = {};
  unsigned head = 0, tail = 0;
  uint32_t dropped = 0, reportAt = 0, markerAt = 0;
  bool connected = false;

  void observe(const uint16_t values[4], uint32_t now) {
    for (unsigned i = 0; i < 4; ++i) window[i].observe(values[i], now);
  }
  void line(const char *text) {
    const unsigned length = strlen(text);
    const unsigned free = (tail + sizeof(queue) - head - 1) % sizeof(queue);
    if (length + 1 > free) { ++dropped; return; }
    while (*text) { queue[head] = *text++; head = (head + 1) % sizeof(queue); }
    queue[head] = '\n'; head = (head + 1) % sizeof(queue);
  }
  bool service() {
    // No connection wait or commands. With NO_USB, Serial is a UART and its
    // bool conversion is always true: it does NOT detect the PC monitor.
    // Native USB builds retain the actual connection/backpressure checks.
    const bool nowConnected = bool(Serial);
    if (!nowConnected) { head = tail = 0; connected = false; return false; }
    if (!connected) {
      connected = true;
      head = tail = 0;
      line("DEEPECOHAB V8.1-DIAG1: observations only; calibration rules unchanged");
      line("Matrix: channel+state. O=ready A=active C=calibration T=range-search W=wait-release N=noise L=low H=high R=chip-range S=stuck I=link");
      line("Numbers ending _x10 are ADC counts times 10. uptime_ms is elapsed time, not a datetime.");
    }
    const uint32_t now = millis();
    if (uint32_t(now - markerAt) >= 5000) {
      markerAt = now;
#if defined(ARDUINO_UNOR4_WIFI) && defined(NO_USB)
      line("SERIAL_FIX_1: UNO R4 WiFi UART bridge; diagnostic output active");
#else
      line("SERIAL_FIX_1: native USB diagnostic output active");
#endif
    }
    unsigned count = (head + sizeof(queue) - tail) % sizeof(queue);
#if defined(ARDUINO_UNOR4_WIFI) && defined(NO_USB)
    // Arduino UNO R4 Boards 1.6.0 maps Serial to _UART1_. UART does not override
    // Print::availableForWrite() (always 0), so testing it prevents ALL output.
    // UART.write transmits without waiting for a PC/USB buffer. Limit each
    // synchronous write to 8 bytes (~0.7 ms at 115200, 8N1), returning to sensing
    // between chunks. Keep the native USB path below for other build settings.
    if (count) {
      if (count > 8) count = 8;
      if (count > sizeof(queue) - tail) count = sizeof(queue) - tail;
      const size_t written = Serial.write(reinterpret_cast<const uint8_t *>(queue + tail), count);
      tail = (tail + written) % sizeof(queue);
    }
#else
    const int available = Serial.availableForWrite();
    if (count && available > 0) {
      if (count > unsigned(available)) count = unsigned(available);
      if (count > 64) count = 64;
      if (count > sizeof(queue) - tail) count = sizeof(queue) - tail;
      tail = (tail + Serial.write(reinterpret_cast<const uint8_t *>(queue + tail), count)) % sizeof(queue);
    }
#endif
    return true;
  }
};
