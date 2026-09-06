"""CLI entry point and cooperative real-time simulation scheduler."""

import argparse
import asyncio
from math import floor
from time import monotonic

from rich.console import Console
from rich.live import Live

from .algorithms.leaky_bucket import LeakyBucket
from .algorithms.sliding_window import SlidingWindowLog
from .algorithms.token_bucket import TokenBucket
from .traffic_generator import TrafficSchedule, generate_traffic
from .visualizer import Visualizer


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare three rate limiters against identical, bursty traffic.",
        epilog="Use a terminal at least 110 columns wide for the clearest side-by-side view.",
    )
    parser.add_argument("--duration", type=float, default=36.0, help="run length in seconds (default: 36)")
    parser.add_argument("--seed", type=int, default=7, help="reproducible traffic seed (default: 7)")
    parser.add_argument("--rate", type=positive_int, default=5, help="shared requests/second baseline (default: 5)")
    parser.add_argument("--capacity", type=positive_int, default=10, help="token and queue capacity (default: 10)")
    parser.add_argument("--refresh-rate", type=positive_int, default=10, help="display frames/second, 4-60 (default: 10)")
    args = parser.parse_args(argv)
    if not 4 <= args.refresh_rate <= 60:
        parser.error("--refresh-rate must be between 4 and 60")
    # Leave enough time for a full queue to finish; require a complete burst
    # pair before that phase. generate_traffic also validates finite duration.
    try:
        args.schedule = generate_traffic(
            args.duration, args.seed, drain_seconds=max(3.0, args.capacity / args.rate)
        )
    except ValueError as error:
        parser.error(str(error))
    return args


async def run_demo(
    schedule: TrafficSchedule,
    rate: int = 5,
    capacity: int = 10,
    seed: int = 7,
    refresh_rate: int = 10,
    console: Console | None = None,
) -> int:
    console = console or Console()
    if console.width < 110:
        console.print("Tip: widen your terminal to 110+ columns for the clearest three-panel view.", style="dim")

    leaky = LeakyBucket(capacity=capacity, leak_rate=rate)
    limiters = [TokenBucket(capacity=capacity, refill_rate=rate), SlidingWindowLog(limit=rate), leaky]
    visualizer = Visualizer(schedule, rate, capacity, seed)
    states = [limiter.snapshot() for limiter in limiters]
    event_index = 0
    elapsed = 0.0
    completed = False

    # Rich owns the automatic rendering refresh. The event loop only publishes
    # newly built renderables and yields cooperatively; there is no time.sleep.
    # Its refresh thread sees immutable snapshots / finished renderables, never
    # the mutable limiter queues. Quiet phases still get freshly advanced state.
    with Live(
        visualizer.render(elapsed, states), console=console,
        refresh_per_second=refresh_rate, auto_refresh=True, transient=True,
    ) as live:
        started = monotonic()
        try:
            while True:
                elapsed = min(monotonic() - started, schedule.duration)
                # Process every due input at its ORIGINAL scheduled timestamp,
                # in order, before advancing clocks to the frame's elapsed time.
                # A delayed frame therefore cannot clump inputs or change their
                # algorithm decisions, and no request goes backwards in time.
                while event_index < len(schedule.events):
                    event = schedule.events[event_index]
                    if event.timestamp > elapsed:
                        break
                    decisions = [limiter.allow_request(event.timestamp) for limiter in limiters]
                    states = [limiter.snapshot() for limiter in limiters]
                    visualizer.record(event, decisions, states)
                    event_index += 1

                for limiter in limiters:
                    limiter.advance(elapsed)
                states = [limiter.snapshot() for limiter in limiters]
                live.update(visualizer.render(elapsed, states, leaky.last_departure), refresh=False)
                if elapsed >= schedule.duration:
                    completed = True
                    break

                # Wake for either the next request or next frame. Absolute
                # deadlines avoid drift from repeatedly adding sleep durations.
                next_frame = (floor(elapsed * refresh_rate) + 1) / refresh_rate
                next_request = (
                    schedule.events[event_index].timestamp
                    if event_index < len(schedule.events) else schedule.duration
                )
                deadline = min(next_frame, next_request, schedule.duration)
                await asyncio.sleep(max(0.0, deadline - (monotonic() - started)))
        except asyncio.CancelledError:
            # asyncio.run converts the first Ctrl+C to cancellation. Keep the
            # counters, restore the terminal through Live's context, and report
            # the observed partial run instead of losing the comparison.
            pass

    console.print(visualizer.summary(states, elapsed, completed))
    return 0 if completed else 130


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run_demo(
            args.schedule, args.rate, args.capacity, args.seed, args.refresh_rate,
        ))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
