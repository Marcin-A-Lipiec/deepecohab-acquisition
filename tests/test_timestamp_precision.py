"""Short ISO fractions retain the same instant on every supported Python."""
from datetime import datetime, timezone

import pytest

from deepecohab_recorder.clock import ExperimentClock, iso_datetime
from deepecohab_recorder.preprocessing.models import EPOCH, parse_pc
from deepecohab_recorder.preprocessing.raw_clock import RawClockError


@pytest.mark.parametrize('fraction', ['1', '12', '123', '1234', '12345', '123456'])
@pytest.mark.parametrize('offset', ['+02:00', '-05:30', 'Z', ''])
def test_fractional_timestamps_preserve_microseconds_and_offset(fraction, offset):
    value = f'2026-09-01T12:00:00.{fraction}{offset}'
    canonical = f'2026-09-01T12:00:00.{fraction.ljust(6, "0")}{offset}'
    expected = datetime.fromisoformat(canonical.replace('Z', '+00:00'))
    assert iso_datetime(value) == expected
    clock = ExperimentClock()
    actual = clock.parse(value, allow_naive=True)
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=clock.zone)
    assert actual == expected
    delta = expected.astimezone(timezone.utc) - EPOCH
    assert parse_pc(value, clock.name) == (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


@pytest.mark.parametrize('value', [
    '2026-09-01T12:00:00.invalid+02:00',
    '2026-13-01T12:00:00.1+02:00',
    '2026-09-01T12:00:00.1junk',
])
def test_invalid_timestamps_are_still_rejected(value):
    with pytest.raises(ValueError):
        parse_pc(value, 'Europe/Warsaw')


def test_short_fraction_does_not_resolve_ambiguous_local_clock():
    with pytest.raises(RawClockError, match='Ambiguous or nonexistent'):
        parse_pc('2021-10-31T02:00:00.1', 'Europe/Warsaw')
    assert parse_pc('2021-10-31T02:00:00.1', 'Europe/Warsaw', '+02:00') == parse_pc(
        '2021-10-31T02:00:00.100000+02:00', 'Europe/Warsaw')
