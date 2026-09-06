"""Deterministic correctness checks; no wall-clock waits or network access.

Run with pytest, or with the standard library:
    python -m unittest discover -s tests -v
pytest discovers these unittest.TestCase classes automatically.
"""

import unittest

from src.algorithms.leaky_bucket import LeakyBucket
from src.algorithms.sliding_window import SlidingWindowLog
from src.algorithms.token_bucket import TokenBucket
from src.traffic_generator import generate_traffic


class TokenBucketTests(unittest.TestCase):
    def test_starts_full_and_rejects_when_empty(self):
        bucket = TokenBucket(capacity=3, refill_rate=5)
        self.assertEqual([bucket.allow_request(0) for _ in range(4)], [True, True, True, False])
        self.assertEqual(bucket.tokens, 0)
        self.assertEqual((bucket.allowed_count, bucket.throttled_count), (3, 1))

    def test_fractional_refill_survives_denied_requests(self):
        bucket = TokenBucket(capacity=1, refill_rate=5)
        self.assertTrue(bucket.allow_request(0))
        self.assertFalse(bucket.allow_request(0.1))
        self.assertEqual(bucket.tokens, 0.5)
        self.assertFalse(bucket.allow_request(0.19))
        self.assertAlmostEqual(bucket.tokens, 0.95)
        self.assertTrue(bucket.allow_request(0.2))
        self.assertEqual(bucket.tokens, 0)

    def test_many_small_updates_preserve_a_whole_token(self):
        bucket = TokenBucket(capacity=1, refill_rate=5)
        bucket.allow_request(0)
        for tick in range(1, 20):
            bucket.advance(tick / 100)
        self.assertTrue(bucket.allow_request(0.2))
        self.assertEqual(bucket.tokens, 0)

    def test_decimal_refill_rate(self):
        bucket = TokenBucket(capacity=1, refill_rate=2.5)
        bucket.allow_request(0)
        self.assertFalse(bucket.allow_request(0.399999))
        self.assertTrue(bucket.allow_request(0.4))

    def test_refill_capped_and_idle_credit_discarded(self):
        bucket = TokenBucket(capacity=2, refill_rate=5)
        bucket.advance(100)
        self.assertEqual(bucket.tokens, 2)
        self.assertTrue(bucket.allow_request(100))
        self.assertTrue(bucket.allow_request(100))
        self.assertFalse(bucket.allow_request(100))
        self.assertFalse(bucket.allow_request(100.1))
        self.assertTrue(bucket.allow_request(100.2))

    def test_rejected_request_does_not_consume_fractional_tokens(self):
        bucket = TokenBucket(capacity=1, refill_rate=5)
        bucket.allow_request(0)
        for _ in range(5):
            self.assertFalse(bucket.allow_request(0.1))
        self.assertEqual(bucket.tokens, 0.5)

    def test_idle_advance_updates_state_without_requests(self):
        bucket = TokenBucket(capacity=2, refill_rate=5)
        bucket.allow_request(0)
        bucket.allow_request(0)
        bucket.advance(0.3)
        self.assertEqual(bucket.snapshot().value, 1.5)
        self.assertEqual((bucket.allowed_count, bucket.throttled_count), (2, 0))

    def test_nonzero_start_time(self):
        bucket = TokenBucket(capacity=1, refill_rate=5, start_time=100)
        bucket.allow_request(100)
        self.assertTrue(bucket.allow_request(100.2))


class SlidingWindowLogTests(unittest.TestCase):
    def test_exact_window_boundary_is_expired(self):
        limiter = SlidingWindowLog(limit=5, window_seconds=1)
        self.assertTrue(all(limiter.allow_request(0) for _ in range(5)))
        self.assertFalse(limiter.allow_request(0.999999))
        self.assertTrue(limiter.allow_request(1))
        self.assertEqual(limiter.timestamps, (1.0,))

    def test_staggered_requests_expire_individually(self):
        limiter = SlidingWindowLog(limit=3, window_seconds=1)
        for timestamp in (0.1, 0.2, 0.8):
            self.assertTrue(limiter.allow_request(timestamp))
        self.assertFalse(limiter.allow_request(1.09))
        self.assertTrue(limiter.allow_request(1.1))
        self.assertEqual(limiter.timestamps, (0.2, 0.8, 1.1))
        self.assertTrue(limiter.allow_request(1.2))
        self.assertEqual(limiter.timestamps, (0.8, 1.1, 1.2))

    def test_not_a_fixed_window_counter(self):
        limiter = SlidingWindowLog(limit=2, window_seconds=1)
        self.assertTrue(limiter.allow_request(0.99))
        self.assertTrue(limiter.allow_request(0.99))
        self.assertFalse(limiter.allow_request(1.01))
        self.assertTrue(limiter.allow_request(1.99))

    def test_denied_requests_do_not_extend_blocking(self):
        limiter = SlidingWindowLog(limit=1)
        self.assertTrue(limiter.allow_request(0))
        self.assertFalse(limiter.allow_request(0.9))
        self.assertTrue(limiter.allow_request(1))
        self.assertEqual(limiter.timestamps, (1.0,))

    def test_duplicate_timestamps_are_distinct_requests(self):
        limiter = SlidingWindowLog(limit=3)
        for _ in range(3):
            self.assertTrue(limiter.allow_request(0.5))
        self.assertFalse(limiter.allow_request(0.5))
        self.assertEqual(limiter.timestamps, (0.5, 0.5, 0.5))

    def test_log_is_bounded_by_accepted_requests(self):
        limiter = SlidingWindowLog(limit=5)
        for _ in range(500):
            limiter.allow_request(0)
        self.assertEqual(len(limiter.timestamps), 5)
        self.assertEqual(limiter.throttled_count, 495)

    def test_idle_advance_evicts_without_changing_counters(self):
        limiter = SlidingWindowLog(limit=2)
        limiter.allow_request(0)
        limiter.allow_request(0.2)
        limiter.advance(1.1)
        self.assertEqual(limiter.requests_in_window, 1)
        limiter.advance(1.2)
        self.assertEqual(limiter.requests_in_window, 0)
        self.assertEqual((limiter.allowed_count, limiter.throttled_count), (2, 0))

    def test_fractional_window(self):
        limiter = SlidingWindowLog(limit=1, window_seconds=0.25)
        self.assertTrue(limiter.allow_request(0.1))
        self.assertFalse(limiter.allow_request(0.349999))
        self.assertTrue(limiter.allow_request(0.35))


class LeakyBucketTests(unittest.TestCase):
    def test_capacity_and_overflow(self):
        bucket = LeakyBucket(capacity=10, leak_rate=5)
        self.assertTrue(all(bucket.allow_request(0) for _ in range(10)))
        self.assertFalse(bucket.allow_request(0))
        self.assertEqual(bucket.queue_depth, 10)
        self.assertEqual(bucket.emitted_count, 0)
        self.assertEqual((bucket.allowed_count, bucket.throttled_count), (10, 1))

    def test_known_fixed_departure_times(self):
        bucket = LeakyBucket(capacity=10, leak_rate=5)
        for _ in range(10):
            bucket.allow_request(0)
        self.assertEqual(bucket.departure_times, tuple(i / 5 for i in range(1, 11)))

    def test_no_departure_before_full_service_interval(self):
        bucket = LeakyBucket(capacity=1, leak_rate=5)
        bucket.allow_request(0)
        bucket.advance(0.199999)
        self.assertEqual(bucket.emitted_count, 0)
        bucket.advance(0.2)
        self.assertEqual((bucket.emitted_count, bucket.queue_depth), (1, 0))

    def test_irregular_updates_keep_partial_service_progress(self):
        bucket = LeakyBucket(capacity=10, leak_rate=5)
        for _ in range(10):
            bucket.allow_request(0)
        for timestamp in (0.03, 0.11, 0.19, 0.21, 0.37, 0.7):
            bucket.advance(timestamp)
        self.assertEqual((bucket.emitted_count, bucket.queue_depth), (3, 7))
        self.assertEqual(bucket.last_departure, 0.6)
        bucket.advance(2)
        self.assertEqual((bucket.emitted_count, bucket.queue_depth), (10, 0))

    def test_departure_at_arrival_time_frees_capacity_first(self):
        bucket = LeakyBucket(capacity=1, leak_rate=5)
        bucket.allow_request(0)
        self.assertTrue(bucket.allow_request(0.2))
        self.assertEqual(bucket.emitted_count, 1)
        self.assertEqual(bucket.departure_times, (0.4,))

    def test_busy_arrivals_do_not_reset_departures(self):
        bucket = LeakyBucket(capacity=3, leak_rate=5)
        bucket.allow_request(0)
        bucket.allow_request(0.1)
        bucket.allow_request(0.3)
        self.assertEqual(bucket.emitted_count, 1)
        self.assertEqual(bucket.departure_times, (0.4, 0.6))

    def test_idle_time_creates_no_service_credit(self):
        bucket = LeakyBucket(capacity=2, leak_rate=5)
        bucket.advance(10)
        bucket.allow_request(10)
        self.assertEqual(bucket.departure_times, (10.2,))
        bucket.advance(10.199999)
        self.assertEqual(bucket.emitted_count, 0)
        bucket.advance(10.2)
        self.assertEqual(bucket.emitted_count, 1)

    def test_empty_queue_restarts_service_at_new_arrival(self):
        bucket = LeakyBucket(capacity=2, leak_rate=5)
        bucket.allow_request(0)
        bucket.allow_request(0.35)
        self.assertEqual(bucket.departure_times, (0.55,))
        self.assertEqual(bucket.emitted_count, 1)

    def test_repeating_fraction_service_rate(self):
        bucket = LeakyBucket(capacity=3, leak_rate=3)
        for _ in range(3):
            bucket.allow_request(0)
        bucket.advance(0.999999)
        self.assertEqual(bucket.emitted_count, 2)
        bucket.advance(1)
        self.assertEqual(bucket.emitted_count, 3)

    def test_admission_emission_queue_conservation(self):
        bucket = LeakyBucket(capacity=3, leak_rate=5)
        for timestamp in (0, 0, 0, 0, 0.1, 0.2, 0.2, 0.7, 3, 3):
            bucket.allow_request(timestamp)
            self.assertEqual(bucket.allowed_count, bucket.emitted_count + bucket.queue_depth)
            self.assertLessEqual(bucket.queue_depth, bucket.capacity)
        bucket.advance(10)
        self.assertEqual(bucket.allowed_count, bucket.emitted_count)


class AlgorithmContractTests(unittest.TestCase):
    def test_same_initial_burst_demonstrates_distinct_behavior(self):
        token, window, leaky = TokenBucket(), SlidingWindowLog(), LeakyBucket()
        for limiter in (token, window, leaky):
            for _ in range(25):
                limiter.allow_request(0)
        self.assertEqual([x.allowed_count for x in (token, window, leaky)], [10, 5, 10])
        self.assertEqual([x.snapshot().emitted for x in (token, window, leaky)], [10, 5, 0])
        self.assertEqual(leaky.queue_depth, 10)
        for limiter in (token, window, leaky):
            limiter.advance(0.2)
        self.assertEqual(token.tokens, 1)
        self.assertEqual(window.requests_in_window, 5)
        self.assertEqual(leaky.queue_depth, 9)

    def test_timestamps_must_be_finite_and_nondecreasing(self):
        for factory in (TokenBucket, SlidingWindowLog, LeakyBucket):
            with self.subTest(algorithm=factory.__name__):
                limiter = factory()
                limiter.allow_request(1)
                for timestamp in (0.9, float('nan'), float('inf'), -1):
                    before = limiter.snapshot()
                    with self.assertRaises(ValueError):
                        limiter.allow_request(timestamp)
                    self.assertEqual(limiter.snapshot(), before)

    def test_invalid_capacities_and_rates(self):
        for value in (0, -1, 1.5, True):
            for factory, key in ((TokenBucket, 'capacity'), (SlidingWindowLog, 'limit'), (LeakyBucket, 'capacity')):
                with self.subTest(algorithm=factory.__name__, value=value):
                    with self.assertRaises(ValueError):
                        factory(**{key: value})
        for value in (0, -1, float('nan'), float('inf'), True):
            for factory, key in ((TokenBucket, 'refill_rate'), (SlidingWindowLog, 'window_seconds'), (LeakyBucket, 'leak_rate')):
                with self.subTest(algorithm=factory.__name__, value=value):
                    with self.assertRaises(ValueError):
                        factory(**{key: value})

    def test_rendering_cadence_does_not_change_decisions(self):
        schedule = generate_traffic(duration=8)

        def replay(fps):
            limiters = [TokenBucket(), SlidingWindowLog(), LeakyBucket()]
            actions = [(e.timestamp, 0, e) for e in schedule.events]
            if fps:
                actions += [(i / fps, 1, None) for i in range(8 * fps + 1)]
            actions.sort(key=lambda action: (action[0], action[1]))
            outcomes = []
            for timestamp, _, event in actions:
                if event is None:
                    for limiter in limiters:
                        limiter.advance(timestamp)
                else:
                    outcomes.append(tuple(limiter.allow_request(timestamp) for limiter in limiters))
            for limiter in limiters:
                limiter.advance(8)
            return outcomes, [limiter.snapshot() for limiter in limiters]

        expected = replay(0)
        for fps in (4, 10, 60):
            with self.subTest(fps=fps):
                self.assertEqual(replay(fps), expected)


class TrafficGeneratorTests(unittest.TestCase):
    def test_seed_reproducibility(self):
        self.assertEqual(generate_traffic(seed=7), generate_traffic(seed=7))
        self.assertNotEqual(generate_traffic(seed=7), generate_traffic(seed=8))

    def test_each_burst_is_20_to_30_requests_in_under_one_second(self):
        schedule = generate_traffic()
        self.assertGreaterEqual(len(schedule.bursts), 6)
        for burst in schedule.bursts:
            with self.subTest(burst=burst.burst_id):
                events = [e for e in schedule.events if e.burst_id == burst.burst_id]
                self.assertEqual(len(events), burst.count)
                self.assertTrue(20 <= len(events) <= 30)
                self.assertLess(events[-1].timestamp - events[0].timestamp, 0.6)
                self.assertTrue(all(burst.start <= e.timestamp < burst.end for e in events))

    def test_chronological_ids_and_quiet_periods(self):
        schedule = generate_traffic()
        times = [e.timestamp for e in schedule.events]
        self.assertEqual(times, sorted(times))
        self.assertEqual([e.request_id for e in schedule.events], list(range(1, len(times) + 1)))
        self.assertTrue(any(e.burst_id is None for e in schedule.events))
        self.assertGreater(max(b - a for a, b in zip(times, times[1:])), 1)

    def test_no_arrivals_in_drain_phase(self):
        schedule = generate_traffic()
        self.assertEqual(schedule.duration, 36)
        self.assertEqual(schedule.drain_start, 33)
        self.assertTrue(all(e.timestamp < schedule.drain_start for e in schedule.events))
        self.assertIn('DRAIN', schedule.phase_at(33)[0])

    def test_full_default_run_drains_every_admitted_request(self):
        schedule = generate_traffic()
        bucket = LeakyBucket()
        for event in schedule.events:
            bucket.allow_request(event.timestamp)
        bucket.advance(schedule.duration)
        self.assertEqual(bucket.queue_depth, 0)
        self.assertEqual(bucket.allowed_count, bucket.emitted_count)

    def test_invalid_duration_or_drain(self):
        for duration, drain in ((6, 3), (36, 0), (36, -1), (float('nan'), 3), (36, float('inf'))):
            with self.subTest(duration=duration, drain=drain):
                with self.assertRaises(ValueError):
                    generate_traffic(duration, drain_seconds=drain)


if __name__ == '__main__':
    unittest.main()
