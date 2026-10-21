"""Experiment calendar time and phase boundaries, independent of the GUI."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


class ExperimentClock:
    def __init__(self, zone: str = "Europe/Warsaw") -> None:
        self.zone = ZoneInfo(zone)

    @property
    def name(self) -> str:
        return self.zone.key

    def now(self) -> datetime:
        return datetime.now(UTC).astimezone(self.zone)

    def iso(self) -> str:
        return self.now().isoformat(timespec="microseconds")

    def parse(self, text: str, *, allow_naive: bool = False) -> datetime:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        if value.tzinfo is None:
            if not allow_naive:
                raise ValueError(
                    "Datetime needs a UTC offset or an explicit experiment timezone."
                )
            return localize(value, self.zone)
        return value.astimezone(self.zone)


def localize(value: datetime, zone: ZoneInfo) -> datetime:
    """Reject nonexistent/ambiguous local times; callers can enter an offset."""
    possible = {}
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value:
            possible[candidate.utcoffset()] = candidate
    if len(possible) != 1:
        raise ValueError(
            "Ambiguous or nonexistent local time during a timezone change; enter an explicit UTC offset."
        )
    return next(iter(possible.values()))


def hhmm(value: str) -> time:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("Phase times must use HH:MM.")
    result = time.fromisoformat(value)
    if result.second or result.microsecond or result.tzinfo:
        raise ValueError("Phase times must use local HH:MM.")
    return result


def phase_origin(start: str, zone: str, phases: dict, start_from: str) -> dict:
    timeline = establish_schedule(dict(recording_timezone=zone, phases=phases), start)
    return fixed_phase_origin(start, zone, timeline['phase_schedule'], start_from)


def schedule_defaults(phases=None):
    phases = phases or {"light_phase": "00:00", "dark_phase": "12:00"}
    light, dark = (hhmm(phases[p]) for p in ("light_phase", "dark_phase"))
    seconds = (
        (dark.hour * 60 + dark.minute - light.hour * 60 - light.minute) % 1440
    ) * 60
    if not seconds:
        raise ValueError("Light and dark phase starts must differ.")
    return {
        "mode": "fixed_cycle",
        "schedule_id": None,
        "initial_utc_offset_seconds": None,
        "anchor_phase": "light_phase",
        "anchor_datetime": None,
        "light_duration_seconds": seconds,
        "dark_duration_seconds": 86400 - seconds,
    }


def validate_schedule(schedule, zone="Europe/Warsaw", *, require_anchor=False):
    import re

    if not isinstance(schedule, dict):
        raise ValueError("Phase schedule must be an object.")
    if (
        schedule.get("mode") != "fixed_cycle"
        or schedule.get("anchor_phase") != "light_phase"
    ):
        raise ValueError("Unsupported phase scheduling policy.")
    light, dark = (
        schedule.get(k) for k in ("light_duration_seconds", "dark_duration_seconds")
    )
    if (
        any(type(v) is not int or not 1 <= v <= 86399 for v in (light, dark))
        or light + dark != 86400
    ):
        raise ValueError(
            "Phase durations must be positive integers totaling 86400 seconds."
        )
    anchor, sid = schedule.get("anchor_datetime"), schedule.get("schedule_id")
    if anchor or sid or require_anchor:
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sid):
            raise ValueError("The phase schedule needs a valid identity.")
        if not anchor:
            raise ValueError("The phase schedule needs an absolute light-phase anchor.")
        ExperimentClock(zone).parse(anchor)
        offset = schedule.get("initial_utc_offset_seconds")
        if type(offset) is not int or not -50400 <= offset <= 50400:
            raise ValueError("The initial schedule UTC offset is invalid.")
    return schedule


def establish_schedule(timeline, at=None):
    """Establish once; retain an existing anchor across recording/GUI restarts."""
    import copy
    import uuid

    result = copy.deepcopy(timeline)
    current = result.get("phase_schedule", {})
    if current.get("mode") == "fixed_cycle" and current.get("anchor_datetime"):
        validate_schedule(current, result["recording_timezone"], require_anchor=True)
        return result
    clock = ExperimentClock(result["recording_timezone"])
    now = clock.parse(at) if isinstance(at, str) else (at or clock.now())
    if now.tzinfo is None:
        raise ValueError("Schedule initialization requires an aware datetime.")
    now = now.astimezone(clock.zone)
    fixed_zone = timezone(now.utcoffset())
    fixed_now = now.astimezone(fixed_zone)
    light_time = hhmm(result["phases"]["light_phase"])
    anchor = datetime.combine(fixed_now.date(), light_time, fixed_zone)
    if anchor > fixed_now:
        anchor -= timedelta(days=1)
    value = schedule_defaults(result["phases"])
    value.update(
        schedule_id=uuid.uuid4().hex,
        initial_utc_offset_seconds=int(now.utcoffset().total_seconds()),
        anchor_datetime=anchor.astimezone(clock.zone).isoformat(),
    )
    result["phase_schedule"] = value
    return result


def fixed_phase_state(schedule, at, zone="Europe/Warsaw"):
    """Current phase and next transition on a fixed elapsed-time cycle."""
    validate_schedule(schedule, zone, require_anchor=True)
    clock = ExperimentClock(zone)
    actual = clock.parse(at) if isinstance(at, str) else at
    if actual.tzinfo is None:
        raise ValueError("Expected an aware datetime.")
    actual = actual.astimezone(UTC)
    anchor = clock.parse(schedule["anchor_datetime"]).astimezone(UTC)
    duration = schedule["light_duration_seconds"]
    cycle = schedule["light_duration_seconds"] + schedule["dark_duration_seconds"]
    index = int((actual - anchor).total_seconds() // cycle)
    light_start = anchor + timedelta(seconds=index * cycle)
    dark_start = light_start + timedelta(seconds=duration)
    if actual < dark_start:
        phase, begin, end = "light_phase", light_start, dark_start
    else:
        phase, begin, end = (
            "dark_phase",
            dark_start,
            light_start + timedelta(seconds=cycle),
        )
    return {
        "phase": phase,
        "phase_start_datetime": begin.astimezone(clock.zone).isoformat(),
        "next_transition_datetime": end.astimezone(clock.zone).isoformat(),
    }


def fixed_phase_origin(start, zone, schedule, start_from):
    if start_from not in ("light_phase", "dark_phase"):
        raise ValueError("Invalid analysis phase.")
    state = fixed_phase_state(schedule, start, zone)
    clock = ExperimentClock(zone)
    origin = (
        state["phase_start_datetime"]
        if state["phase"] == start_from
        else state["next_transition_datetime"]
    )
    difference = (
        clock.parse(start).astimezone(UTC) - clock.parse(origin).astimezone(UTC)
    ).total_seconds()
    return {
        "analysis_start_datetime": origin,
        "missing_initial_seconds": max(0, difference),
        "recorded_before_origin_seconds": max(0, -difference),
    }


def next_daily_occurrence(schedule, phases, daily_hhmm, after, zone="Europe/Warsaw"):
    validate_schedule(schedule, zone, require_anchor=True)
    clock = ExperimentClock(zone)
    daily, light = hhmm(daily_hhmm), hhmm(phases["light_phase"])
    offset = (
        (daily.hour * 60 + daily.minute - light.hour * 60 - light.minute) % 1440
    ) * 60
    base = clock.parse(schedule["anchor_datetime"]).astimezone(UTC) + timedelta(
        seconds=offset
    )
    actual = clock.parse(after).astimezone(UTC)
    count = int((actual - base).total_seconds() // 86400) + 1
    return (base + timedelta(seconds=count * 86400)).astimezone(clock.zone).isoformat()
