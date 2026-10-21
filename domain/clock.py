"""Explicit instants, civil-time disambiguation and injectable clocks.

Persistence precision is one millisecond. UTC conversion never depends on the
machine timezone; ambiguous/nonexistent local times require caller resolution.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time
from typing import Protocol
from zoneinfo import ZoneInfo

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AmbiguousLocalTime(ValueError):
    pass


class NonexistentLocalTime(ValueError):
    pass


def as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an offset-aware instant is required")
    value = value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def to_epoch_ms(value: datetime) -> int:
    delta = as_utc(value) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def from_epoch_ms(value: int) -> datetime:
    if type(value) is not int:
        raise ValueError("epoch milliseconds must be an integer")
    return EPOCH + timedelta(milliseconds=value)


def iso_instant(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="milliseconds")


def resolve_local(value: datetime, timezone_name: str, *, fold: int | None = None) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("resolve_local expects a naive civil datetime")
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError("fold must be 0 or 1")
    zone = ZoneInfo(timezone_name)
    candidates = {}
    for choice in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=choice)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value:
            candidates[choice] = candidate
    if not candidates:
        raise NonexistentLocalTime("civil time does not exist in this timezone")
    if len({item.utcoffset() for item in candidates.values()}) > 1 and fold is None:
        raise AmbiguousLocalTime("civil time is ambiguous; specify fold explicitly")
    return as_utc(candidates[fold if fold is not None else min(candidates)])


def to_local(value: datetime, timezone_name: str) -> datetime:
    return as_utc(value).astimezone(ZoneInfo(timezone_name))


def local_day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    if type(day) is not date:
        raise ValueError("a civil date is required")
    start = resolve_local(datetime.combine(day, datetime.min.time()), timezone_name)
    end = resolve_local(datetime.combine(day + timedelta(days=1), datetime.min.time()), timezone_name)
    return start, end


class Clock(Protocol):
    def now_utc(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def to_local(self, value: datetime, timezone_name: str) -> datetime: ...
    def local_day_bounds(self, day: date, timezone_name: str) -> tuple[datetime, datetime]: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return as_utc(datetime.now(UTC))

    def monotonic(self) -> float:
        return time.monotonic()

    to_local = staticmethod(to_local)
    local_day_bounds = staticmethod(local_day_bounds)


class ManualClock:
    """Wall time can jump independently; elapsed time never moves backward."""
    def __init__(self, now: datetime):
        self._now = as_utc(now)
        self._elapsed = 0.0

    def now_utc(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._elapsed

    def set_utc(self, value: datetime) -> None:
        self._now = as_utc(value)

    def advance(self, elapsed: timedelta) -> None:
        if elapsed < timedelta(0):
            raise ValueError("elapsed time cannot move backward; use set_utc for wall jumps")
        self._now = as_utc(self._now + elapsed)
        self._elapsed += elapsed.total_seconds()

    to_local = staticmethod(to_local)
    local_day_bounds = staticmethod(local_day_bounds)
