"""Versioned control defaults and validation shared by configuration and UI."""

from __future__ import annotations
import copy
from datetime import time

RECALIBRATION = {
    "daily_enabled": True,
    "daily_time_hhmm": "01:00",
    "quiet_seconds": 5,
    "stuck_active_enabled": True,
    "stuck_active_seconds": 90,
    "long_active_warning_seconds": 30,
    "high_rate_enabled": True,
    "high_rate_edges_per_second": 20,
    "high_rate_window_seconds": 10,
    "continuous_activity_enabled": True,
    "continuous_activity_seconds": 180,
    "natural_pause_ms": 500,
    "sensor_fault_recovery_enabled": True,
    "retry_delays_seconds": [5, 15, 60, 300],
}
CONNECTION = {
    "heartbeat_interval_seconds": 2,
    "heartbeat_timeout_seconds": 10,
    "reconnect_enabled": True,
    "reconnect_delays_seconds": [1, 2, 5, 10, 30],
}


def recalibration_defaults():
    return copy.deepcopy(RECALIBRATION)


def connection_defaults():
    return copy.deepcopy(CONNECTION)


def validate_options(recalibration, connection):
    r, c = recalibration_defaults(), connection_defaults()
    r.update(copy.deepcopy(recalibration or {}))
    c.update(copy.deepcopy(connection or {}))
    for data, defaults in ((r, RECALIBRATION), (c, CONNECTION)):
        for key, default in defaults.items():
            value = data[key]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{key}: expected true/false.")
            elif isinstance(default, int):
                if type(value) is not int or not 1 <= value <= 86400:
                    raise ValueError(f"{key}: expected a positive integer up to 86400.")
            elif isinstance(default, list):
                if (
                    not isinstance(value, list)
                    or not 1 <= len(value) <= 8
                    or any(type(x) is not int or not 1 <= x <= 3600 for x in value)
                    or value != sorted(value)
                ):
                    raise ValueError(
                        f"{key}: use 1–8 increasing delays, each 1–3600 seconds."
                    )
    hour = r["daily_time_hhmm"]
    if not isinstance(hour, str) or len(hour) != 5 or hour[2] != ":":
        raise ValueError("Daily recalibration hour must use HH:MM.")
    parsed = time.fromisoformat(hour)
    if parsed.tzinfo or parsed.second:
        raise ValueError("Use HH:MM.")
    if r["long_active_warning_seconds"] >= r["stuck_active_seconds"]:
        raise ValueError("Long-active warning must precede the stuck-active fault.")
    if c["heartbeat_timeout_seconds"] < c["heartbeat_interval_seconds"] * 3:
        raise ValueError(
            "Heartbeat timeout must be at least three heartbeat intervals."
        )
    return r, c


def settings_wire(r, phases):
    """Fixed field order understood by v6.2. Times are elapsed milliseconds."""
    r, _ = validate_options(r, None)

    def minutes(text):
        h, m = map(int, text.split(":"))
        return h * 60 + m

    offset = (
        (minutes(r["daily_time_hhmm"]) - minutes(phases["light_phase"])) % 1440
    ) * 60
    values = [
        int(r["daily_enabled"]),
        offset,
        r["quiet_seconds"] * 1000,
        int(r["stuck_active_enabled"]),
        r["stuck_active_seconds"] * 1000,
        r["long_active_warning_seconds"] * 1000,
        int(r["high_rate_enabled"]),
        r["high_rate_edges_per_second"],
        r["high_rate_window_seconds"],
        int(r["continuous_activity_enabled"]),
        r["continuous_activity_seconds"] * 1000,
        r["natural_pause_ms"],
        int(r["sensor_fault_recovery_enabled"]),
        len(r["retry_delays_seconds"]),
        *r["retry_delays_seconds"],
    ]
    return ",".join(map(str, values))
