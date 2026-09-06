"""A FIFO traffic shaper: bounded admission and fixed-rate departures."""

from collections import deque
from fractions import Fraction

from .base import LimiterSnapshot, RateLimiter, positive_capacity, positive_number


class LeakyBucket(RateLimiter):
    def __init__(
        self, capacity: int = 10, leak_rate: float = 5.0, start_time: float = 0.0
    ) -> None:
        super().__init__(start_time)
        self.capacity = positive_capacity(capacity)
        self._leak_rate = positive_number(leak_rate, "leak_rate")
        self._service_interval = 1 / self._leak_rate
        # One entry represents one actual unit request awaiting departure.
        # Capacity includes the request currently being serviced, if any.
        self._departures: deque[Fraction] = deque()
        self.emitted_count = 0
        self.last_departure: float | None = None

    @property
    def leak_rate(self) -> float:
        return float(self._leak_rate)

    @property
    def queue_depth(self) -> int:
        return len(self._departures)

    @property
    def departure_times(self) -> tuple[float, ...]:
        return tuple(float(t) for t in self._departures)

    def advance(self, timestamp: float) -> None:
        self._advance_clock(timestamp)
        # Drain even when no new requests arrive. All due requests complete at
        # their SCHEDULED times, not at the next rendering tick. A late frame
        # may observe multiple departures; it does not reschedule them into a
        # burst or discard partial service progress.
        while self._departures and self._departures[0] <= self._now:
            self.last_departure = float(self._departures.popleft())
            self.emitted_count += 1

    def allow_request(self, timestamp: float) -> bool:
        self.advance(timestamp)
        # A departure exactly at this timestamp frees space BEFORE admission.
        if len(self._departures) >= self.capacity:
            return self._record(False)
        tail = self._departures[-1] if self._departures else self._now
        # d_new = max(arrival, d_previous) + 1/rate.
        # Thus departures are 1/rate apart while backlogged. On an empty
        # queue, service starts now; idle time never earns future leak credit.
        # Fraction preserves e.g. three intervals at 3/s as exactly one second.
        self._departures.append(max(self._now, tail) + self._service_interval)
        return self._record(True)

    def snapshot(self) -> LimiterSnapshot:
        # True means QUEUED, not already forwarded. At every instant:
        # allowed_count == emitted_count + queue_depth.
        return LimiterSnapshot(
            name="Leaky Bucket", label="Queue depth", value=self.queue_depth,
            capacity=self.capacity, allowed=self.allowed_count,
            throttled=self.throttled_count, emitted=self.emitted_count,
        )
