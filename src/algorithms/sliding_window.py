"""Exact trailing-window limiting using one timestamp per accepted request."""

from collections import deque
from fractions import Fraction

from .base import LimiterSnapshot, RateLimiter, positive_capacity, positive_number


class SlidingWindowLog(RateLimiter):
    def __init__(
        self, limit: int = 5, window_seconds: float = 1.0, start_time: float = 0.0
    ) -> None:
        super().__init__(start_time)
        self.limit = positive_capacity(limit, "limit")
        self._window = positive_number(window_seconds, "window_seconds")
        self._timestamps: deque[Fraction] = deque()

    @property
    def window_seconds(self) -> float:
        return float(self._window)

    @property
    def requests_in_window(self) -> int:
        return len(self._timestamps)

    @property
    def timestamps(self) -> tuple[float, ...]:
        return tuple(float(t) for t in self._timestamps)

    def advance(self, timestamp: float) -> None:
        self._advance_clock(timestamp)
        cutoff = self._now - self._window
        # The active interval is (now - window, now], open on the left.
        # A request exactly one full window old no longer consumes capacity.
        # Evict on EVERY check, including checks which will ultimately fail.
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def allow_request(self, timestamp: float) -> bool:
        self.advance(timestamp)
        if len(self._timestamps) >= self.limit:
            # Only admitted requests occupy the log. Logging rejected attempts
            # would prolong blocking and implement a different policy.
            return self._record(False)
        self._timestamps.append(self._now)
        return self._record(True)

    def snapshot(self) -> LimiterSnapshot:
        # This is a real per-request log, not two weighted window counters.
        # It uses O(limit) timestamps per limiter instead of O(1) aggregate
        # counts. Each timestamp is added/evicted once: amortized O(1) per check,
        # although one eviction pass may remove many entries.
        return LimiterSnapshot(
            name="Sliding Window Log", label="Requests in window",
            value=self.requests_in_window, capacity=self.limit,
            allowed=self.allowed_count, throttled=self.throttled_count,
            emitted=self.allowed_count,
        )
