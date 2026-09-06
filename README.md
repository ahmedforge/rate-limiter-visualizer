# Rate Limiter Visualizer

Watch **Token Bucket**, **Sliding Window Log**, and **Leaky Bucket** handle the
same bursty traffic live, side by side, in your terminal.

The demo makes three different behaviors visible: spending stored tokens,
enforcing an exact trailing-window limit, and buffering work into evenly spaced
departures. Matching request totals do not necessarily mean matching output timing.

## Demo

**Demo GIF coming soon.** Record the terminal and save the result as
`assets/demo.gif`, then uncomment the image below.

<!-- ![Live comparison of three rate limiters](assets/demo.gif) -->

## Quick start

Requires **Python 3.10+**. From the extracted or cloned project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.main
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m src.main
```

Use a terminal at least **110 columns wide and 30 rows tall** for the clearest
view. The default run lasts **36 seconds**, including a final quiet drain phase.
Press **Ctrl+C** to stop early and see a partial summary.

Run `python -m src.main` from the project root; the modules use package imports.
Rich supplies the display, and pytest is included for running the tests.

## What you see

Each of the three panels contains:

- The latest request's ID and decision: green **ALLOWED / QUEUED** or red
  **THROTTLED / DROPPED**.
- A live state number and bar: tokens remaining, requests in the trailing
  window, or queue depth.
- Running allowed and blocked totals, plus the number actually emitted.
- A ribbon of the last **60 individual decisions**: green `+` for admission,
  red `x` for blocking, and `.` for an unused history position.
- Four recent requests with their timestamps, decisions, and state immediately
  after each decision. The live state above can continue changing afterward.
- Separate admission and blocking counts for burst requests.

The Leaky Bucket also shows the last scheduled departure time. Its **allowed**
count means accepted into the queue; its **emitted** count means service has
finished. Queue depth includes every accepted request awaiting departure,
including the request currently being serviced.

At the end, the CLI prints totals, burst-period counts, observed behavior, and
one real-world tradeoff per algorithm. Burst counts include only requests
generated as part of a spike; total counts also include sparse quiet traffic.

## Traffic pattern

The generator creates a seeded schedule before the run. Every algorithm receives
the same request IDs and timestamps, in the same order.

- Each six-second cycle includes sparse arrivals at offsets `0.5s`, `1.4s`,
  and `4.6s`.
- Bursts start at offsets `2.00s` and `2.85s`, each containing **20-30 requests
  jittered across less than 0.6 seconds**.
- The close second spike arrives before every limiter has necessarily recovered.
- Incomplete bursts are omitted at the end of the traffic period.
- The final `max(3, capacity / rate)` seconds contain no arrivals, allowing a
  full Leaky Bucket to drain.

With the defaults, the schedule has **300 requests across 11 bursts**. There are
283 burst requests and 17 quiet-period requests, followed by the drain phase
starting at 33 seconds. This is deliberately bursty, not a steady request stream.

## Algorithm math and correctness

All algorithms implement `allow_request(timestamp) -> bool`. They also provide
`advance(timestamp)` for idle-time updates and `snapshot()` for display.
Timestamps are nonnegative, finite seconds in a shared time domain and must be
nondecreasing; repeated timestamps are valid.

The implementation uses the standard library's `Fraction` internally. Decimal
timestamp and rate values are converted into rational numbers, preserving
fractional credit and avoiding accumulated binary floating-point rounding at
boundaries. Only displayed values are converted back to floats. This arithmetic
does not increase the precision of the operating system's clock.

### Token Bucket

Defaults: **10 tokens**, initially full, with **5 tokens/second** of refill.

Before checking a request at time `t`:

```text
tokens = min(capacity, tokens + refill_rate * (t - last_update))
last_update = t
```

Allow the request if `tokens >= 1`, then subtract exactly one token. Otherwise
reject it without consuming any partial token balance.

For example, an empty bucket earns `0.5` tokens after `0.1s` at `5/s`, so a
request is denied. At `0.2s`, a whole token has accumulated and one request can
pass. Frequent checks never discard the fractional remainder.

The clock advances even when the bucket is full. Excess idle refill is discarded
at capacity, so it cannot become extra burst credit later. Accepted requests
are forwarded immediately; there is no output queue.

### Sliding Window Log

Defaults: **5 admitted requests in the trailing 1 second**.

Keep a real deque of accepted request timestamps. Before each check, evict every
timestamp satisfying:

```text
request_timestamp <= now - window_seconds
```

The active interval is **`(now - window_seconds, now]`**. Allow the request only
if fewer than five entries remain, and append its timestamp only when admitted.
Rejected attempts are not logged and do not extend the blocking period.

Five requests accepted at `t=0` block another at `t=0.999999`. At exactly `t=1`,
those five entries expire. Staggered timestamps expire individually, not at an
aligned clock boundary.

Storing individual timestamps makes this the exact log algorithm, rather than
a weighted approximation using adjacent counters. The log uses **O(limit)**
entries per limiter; aggregate window counters use **O(1)** counters. Each log
entry is appended and removed once, giving amortized O(1) work per check,
although one check can evict several entries.

### Leaky Bucket

Defaults: **10 queue slots**, with **5 departures/second** while the queue is busy.

Each queued request has one scheduled departure. Before admission, remove all
departures due at or before `now` and increment the emitted count. If the queue
is full, drop the incoming request. Otherwise schedule it using:

```text
service_interval = 1 / leak_rate
departure = max(arrival_time, last_queued_departure) + service_interval
```

For an empty queue, the first departure is `arrival_time + service_interval`.
Thus ten simultaneous requests at `t=0` leave at `0.2, 0.4, ..., 2.0` seconds;
an eleventh request is dropped. At `t=0.7`, three have departed and seven remain.

A departure exactly at an arrival's timestamp frees capacity before the new
admission. Fractional service progress is preserved between updates. Idle time
creates no future service credit, and empty queues produce no output.

The invariant is:

```text
allowed_count = emitted_count + queue_depth
```

Departures stay evenly spaced while backlogged. A delayed screen update may
observe multiple departures together, but their logical departure times remain
unchanged. With these defaults, an admitted request can wait up to two seconds
before departure when filling the last queue slot.

## Defaults and tradeoffs

All three have the same **5 requests/second baseline**. Token Bucket has stored
burst credit, and Leaky Bucket has buffering capacity; Sliding Window Log
enforces the stricter five-per-trailing-second policy. These are intentionally
different short-term admission guarantees.

| Algorithm | State memory per limiter | Burst behavior | Real-world tradeoff |
| --- | --- | --- | --- |
| Token Bucket | O(1) balance and clock state | Stored tokens admit a burst immediately; later admissions follow refill. | Low state cost and burst tolerance, but accepted spikes reach downstream services immediately. |
| Sliding Window Log | O(limit) timestamps | Accepts up to the trailing-window limit; capacity returns as individual entries expire. | Precise rolling limits cost more memory than counters and still allow up to the limit simultaneously. |
| Leaky Bucket | O(capacity) queued requests | Buffers excess arrivals until full, then drops overflow; departures remain evenly spaced while busy. | Smooth output requires buffering and adds queueing delay. |

These costs describe algorithm state. The visualizer separately retains bounded
decision histories, and the traffic generator retains the run's input schedule.

### Default run results

For `--duration 36 --seed 7 --rate 5 --capacity 10`:

| Algorithm | Allowed | Throttled / dropped | Burst allowed / blocked | Emitted by end | Queued at end |
| --- | ---: | ---: | ---: | ---: | ---: |
| Token Bucket | 111 | 189 | 94 / 189 | 111 | 0 |
| Sliding Window Log | 71 | 229 | 54 / 229 | 71 | 0 |
| Leaky Bucket | 111 | 189 | 94 / 189 | 111 | 0 |

The Leaky Bucket reaches its full depth of **10 requests** during the spikes.
Token Bucket and Leaky Bucket admit the same number in this particular run,
but Token Bucket forwards accepted work immediately while Leaky Bucket spreads
it over time. Observe **emitted**, **queue depth**, and **last out** during a
burst to see that difference. Equal admission counts are not guaranteed for
every possible arrival schedule or parameter choice.

## CLI options

```bash
python -m src.main --help
python -m src.main --duration 45 --seed 42
python -m src.main --rate 8 --capacity 16
python -m src.main --refresh-rate 20
python -m src.main --duration 8
```

| Option | Default | Meaning |
| --- | ---: | --- |
| `--duration` | 36 | Run length in seconds, including the drain phase. |
| `--seed` | 7 | Seed for repeatable burst sizes and arrival times. |
| `--rate` | 5 | Positive integer refill/leak rate and sliding-window limit per second. |
| `--capacity` | 10 | Positive integer token capacity and queue capacity. |
| `--refresh-rate` | 10 | Rich refresh target in frames/second, from 4 to 60. |

Duration must leave at least four seconds for traffic before the drain phase:
`duration >= max(3, capacity / rate) + 4`.

## Why rendering stays responsive

`rich.live.Live` owns automatic rendering with `refresh_per_second=10` by
default. An asyncio task processes requests and publishes new renderables,
yielding with `await asyncio.sleep(...)` until the next request or frame deadline.
There are **no `time.sleep()` calls** in the application.

The scheduler uses a monotonic clock and absolute deadlines. If a frame runs
late, every due request is still processed at its original scheduled timestamp
before algorithm clocks advance to the frame time. The same logical decisions
therefore result at different rendering cadences. Quiet frames still refill
tokens, evict old log entries, and drain queued requests.

Immutable snapshots keep the Rich refresh thread separate from mutable limiter
state. Redirecting output to a file does not provide an animated terminal; use
an interactive terminal to watch the live display. See the
[Rich Live documentation](https://rich.readthedocs.io/en/latest/live.html).

## Tests

Run all 36 deterministic tests:

```bash
python -m pytest -q
```

The tests also run without installing pytest:

```bash
python -m unittest discover -s tests -v
```

They cover fractional token refill, capacity capping, discarded excess idle
credit, exact window boundaries, staggered eviction, rejected-request handling,
queue overflow, fixed departure times, idle service behavior, repeating-fraction
rates, counter conservation, invalid inputs, burst generation, final drainage,
and unchanged decisions with 4, 10, and 60 frame updates per second.

## Files

| File | Responsibility |
| --- | --- |
| `src/__init__.py` | Marks the source package. |
| `src/algorithms/__init__.py` | Marks the algorithms package. |
| `src/algorithms/base.py` | Common interface, validation, counters, exact clock arithmetic, and snapshots. |
| `src/algorithms/token_bucket.py` | Continuous fractional refill and immediate admission. |
| `src/algorithms/sliding_window.py` | Exact timestamp log and trailing-window eviction. |
| `src/algorithms/leaky_bucket.py` | Bounded FIFO admission and fixed-rate departures. |
| `src/traffic_generator.py` | Seeded bursts, quiet periods, and the drain phase. |
| `src/visualizer.py` | Three adjacent panels, colored decision histories, and summaries. |
| `src/main.py` | Argument parsing, monotonic scheduling, Rich Live, and Ctrl+C handling. |
| `tests/test_algorithms.py` | Deterministic algorithm and traffic correctness tests. |
| `assets/` | Empty until you add `demo.gif`; Git begins tracking it when a file is added. |

This is a single-process educational simulation of unit-size requests. Queue
departures are simulated work completions; the CLI does not send network requests.
