"""Common interface, counters, snapshots, and a deterministic logical clock."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite


def exact_number(value: float, name: str) -> Fraction:
    """Preserve the supplied decimal value without binary rounding drift.

    Fraction is in the standard library. It keeps fractional refill and service
    math exact for the supplied timestamps/rates; conversion to float happens
    only for display. It does not make the operating system clock more precise.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise ValueError(f"{name} must be a finite number")
    if not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return Fraction(str(value))


def positive_number(value: float, name: str) -> Fraction:
    result = exact_number(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def positive_capacity(value: int, name: str = "capacity") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class LimiterSnapshot:
    """An immutable view: Rich's refresh thread never reads mutable queues."""

    name: str
    label: str
    value: float
    capacity: int
    allowed: int
    throttled: int
    emitted: int
    precision: int = 0


class RateLimiter(ABC):
    """All clocks use seconds in one shared, nondecreasing time domain.

    allow_request(t) advances time before deciding. advance(t) also lets the
    runner update idle states without inventing requests or changing counters.
    These objects are intended for the single simulation task, not concurrent
    callers. Unit tests can supply times directly without waiting in real time.
    """

    def __init__(self, start_time: float = 0.0) -> None:
        self._now = exact_number(start_time, "start_time")
        if self._now < 0:
            raise ValueError("start_time must be nonnegative")
        self.allowed_count = 0
        self.throttled_count = 0

    def _advance_clock(self, timestamp: float) -> Fraction:
        now = exact_number(timestamp, "timestamp")
        if now < self._now:
            raise ValueError("timestamps must be nondecreasing")
        elapsed = now - self._now
        self._now = now
        return elapsed

    def _record(self, allowed: bool) -> bool:
        if allowed:
            self.allowed_count += 1
        else:
            self.throttled_count += 1
        return allowed

    @abstractmethod
    def allow_request(self, timestamp: float) -> bool:
        """Return True for immediate admission or, for Leaky Bucket, queuing."""

    @abstractmethod
    def advance(self, timestamp: float) -> None:
        """Perform time-based housekeeping without adding a request."""

    @abstractmethod
    def snapshot(self) -> LimiterSnapshot:
        """Read the state after the most recent advance or request."""
