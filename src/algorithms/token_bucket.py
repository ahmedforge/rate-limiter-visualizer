"""A burst-tolerant limiter with continuous fractional token accumulation."""

from fractions import Fraction

from .base import LimiterSnapshot, RateLimiter, positive_capacity, positive_number


class TokenBucket(RateLimiter):
    def __init__(
        self, capacity: int = 10, refill_rate: float = 5.0, start_time: float = 0.0
    ) -> None:
        super().__init__(start_time)
        self.capacity = positive_capacity(capacity)
        self._refill_rate = positive_number(refill_rate, "refill_rate")
        self._tokens = Fraction(capacity)  # Start full: the burst budget is ready.

    @property
    def tokens(self) -> float:
        return float(self._tokens)

    @property
    def refill_rate(self) -> float:
        return float(self._refill_rate)

    def advance(self, timestamp: float) -> None:
        elapsed = self._advance_clock(timestamp)
        # T(t) = min(C, T(previous) + rate * elapsed_seconds).
        # Keep every fractional remainder: 0.1s at 5/s earns 0.5 tokens.
        # Updating the clock even when full discards excess idle credit; it
        # cannot be spent later to exceed capacity. No integer refill ticks.
        self._tokens = min(self.capacity, self._tokens + self._refill_rate * elapsed)

    def allow_request(self, timestamp: float) -> bool:
        self.advance(timestamp)
        if self._tokens < 1:
            return self._record(False)
        self._tokens -= 1
        return self._record(True)

    def snapshot(self) -> LimiterSnapshot:
        # Token Bucket forwards accepted work immediately; it has no queue.
        return LimiterSnapshot(
            name="Token Bucket", label="Tokens remaining", value=self.tokens,
            capacity=self.capacity, allowed=self.allowed_count,
            throttled=self.throttled_count, emitted=self.allowed_count, precision=2,
        )
