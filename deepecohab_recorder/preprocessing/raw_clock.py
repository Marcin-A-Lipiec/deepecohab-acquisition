"""Explicit interpretation of offset-free timestamps from recording PCs."""

from datetime import timedelta, timezone
from functools import lru_cache
import re


class RawClockError(ValueError):
    """The recording's wall clock cannot identify a unique UTC instant."""


@lru_cache(maxsize=64)
def fixed_utc_offset(text):
    """Blank keeps timezone rules; a signed HH:MM declares a fixed PC clock."""
    if not isinstance(text, str):
        raise ValueError("A recording-PC offset must be blank or use +HH:MM / -HH:MM.")
    if not text:
        return None
    if not re.fullmatch(r"[+-][0-9]{2}:[0-9]{2}", text):
        raise ValueError("Enter a recording-PC UTC offset as +HH:MM or -HH:MM, for example +02:00.")
    hours, minutes = int(text[1:3]), int(text[4:6])
    if hours > 23 or minutes > 59:
        raise ValueError("A UTC offset must be less than 24 hours, with minutes between 00 and 59.")
    total = (hours * 60 + minutes) * (-1 if text[0] == "-" else 1)
    return timezone(timedelta(minutes=total))
