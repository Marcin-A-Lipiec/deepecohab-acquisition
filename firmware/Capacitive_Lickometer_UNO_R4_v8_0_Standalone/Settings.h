#pragma once
#include <stdint.h>

// Existing Adafruit MPR121 board (regulated 3.3 V sensing supply), UNO R4 WiFi.
// These are commissioning defaults, NOT validated mouse-detection thresholds.
namespace Settings {
constexpr uint8_t address = 0x5A;
constexpr uint8_t electrodes[4] = {1, 3, 5, 7};
constexpr uint8_t outputs[4] = {8, 9, 10, 11};
constexpr uint32_t sampleMs = 4;
constexpr uint32_t touchDebounceMs = 8;
constexpr uint32_t releaseDebounceMs = 8;
constexpr uint32_t releaseBeforeRearmMs = 100;
constexpr uint16_t calibrationSamples = 512; // ~2 s plus sensor settling
constexpr float minThreshold = 3.0f;
constexpr float maxThreshold = 8.0f; // refuse excessive noise, do not hide it
constexpr float noiseMargin = 2.0f;
constexpr uint32_t quietMs = 5000;
constexpr uint32_t maintenanceMs = 24UL * 60UL * 60UL * 1000UL;
constexpr uint32_t stuckMs = 90000;
constexpr uint32_t channelRetryMs = 30000;
constexpr uint32_t maxSampleGapMs = 50;
constexpr uint16_t maxOnsetsPerSecond = 40; // chatter fault, not a lick-rate filter
constexpr uint32_t powerSettleMs = 250;
constexpr uint32_t sensorSettleMs = 1500;
constexpr uint32_t watchdogMs = 2000;
// Adafruit breakout's MPR121 supply is 3.3 V; these are NOT DC-jack voltages.
constexpr uint8_t autoUpper = 200, autoLower = 130, autoTarget = 180;
}
