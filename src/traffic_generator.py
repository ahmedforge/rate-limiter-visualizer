"""A seeded schedule of sparse traffic and sharp, repeated burst pairs."""

from dataclasses import dataclass
from math import isfinite
from random import Random


@dataclass(frozen=True)
class RequestEvent:
    timestamp: float
    request_id: int
    burst_id: int | None = None


@dataclass(frozen=True)
class Burst:
    burst_id: int
    start: float
    end: float
    count: int


@dataclass(frozen=True)
class TrafficSchedule:
    duration: float
    drain_start: float
    events: tuple[RequestEvent, ...]
    bursts: tuple[Burst, ...]

    def phase_at(self, timestamp: float) -> tuple[str, str]:
        if timestamp >= self.drain_start:
            return "DRAIN: no arrivals; queued work finishes", "cyan"
        for burst in self.bursts:
            if burst.start <= timestamp < burst.end:
                return f"BURST {burst.burst_id}: {burst.count} requests in 0.6s", "bold yellow"
        return "QUIET: sparse requests; limiters recover", "dim"


def generate_traffic(
    duration: float = 36.0, seed: int = 7, drain_seconds: float = 3.0
) -> TrafficSchedule:
    """Generate once; every limiter receives the exact same ordered events.

    Six-second cycles contain bursts at +2.00s and +2.85s. Each contains
    20-30 requests jittered across just 0.6s, with sparse requests around them.
    The close second spike exposes partially depleted tokens, trailing-window
    expiry, and a queue which may still be busy. This is not uniform traffic.
    """
    if not isfinite(duration) or not isfinite(drain_seconds):
        raise ValueError("duration and drain_seconds must be finite")
    if drain_seconds <= 0 or duration < drain_seconds + 4:
        raise ValueError("duration must leave at least 4s of traffic before the drain phase")

    rng = Random(seed)
    drain_start = duration - drain_seconds
    pending: list[tuple[float, int | None]] = []
    bursts: list[Burst] = []
    cycle = 0.0
    while cycle < drain_start:
        for offset in (0.5, 1.4, 4.6):
            timestamp = round(cycle + offset, 6)
            if timestamp < drain_start:
                pending.append((timestamp, None))
        for offset in (2.0, 2.85):
            start = cycle + offset
            end = start + 0.6
            # Never truncate a burst to squeeze it into the selected duration.
            if end > drain_start:
                continue
            burst_id = len(bursts) + 1
            count = rng.randint(20, 30)
            bursts.append(Burst(burst_id, start, end, count))
            for _ in range(count):
                pending.append((round(start + rng.uniform(0, 0.599999), 6), burst_id))
        cycle += 6.0

    pending.sort(key=lambda item: item[0])
    events = tuple(
        RequestEvent(timestamp, i, burst_id)
        for i, (timestamp, burst_id) in enumerate(pending, start=1)
    )
    return TrafficSchedule(duration, drain_start, events, tuple(bursts))
