"""Rich renderables and bounded request histories; no algorithm clock updates."""

from collections import deque
from dataclasses import dataclass

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from .algorithms.base import LimiterSnapshot
from .traffic_generator import RequestEvent, TrafficSchedule


@dataclass(frozen=True)
class Decision:
    request_id: int
    timestamp: float
    allowed: bool
    state: float


class Visualizer:
    COLORS = ("cyan", "magenta", "blue")
    HISTORY_SIZE = 60  # Retains an entire pair of 20-30-request spikes.

    def __init__(self, schedule: TrafficSchedule, rate: int, capacity: int, seed: int) -> None:
        self.schedule = schedule
        self.rate = rate
        self.capacity = capacity
        self.seed = seed
        self.histories: list[deque[Decision]] = [
            deque(maxlen=self.HISTORY_SIZE) for _ in range(3)
        ]
        self.burst_allowed = [0, 0, 0]
        self.burst_throttled = [0, 0, 0]
        self.peak_queue = 0
        self.seen = 0

    def record(
        self, event: RequestEvent, decisions: list[bool], states: list[LimiterSnapshot]
    ) -> None:
        # Record every decision, including multiple arrivals between frames.
        # The ribbon preserves each result; the recent rows include the exact
        # post-decision state instead of repeating only the last frame's value.
        self.seen += 1
        for i, (allowed, state) in enumerate(zip(decisions, states)):
            self.histories[i].append(Decision(event.request_id, event.timestamp, allowed, state.value))
            if event.burst_id is not None:
                if allowed:
                    self.burst_allowed[i] += 1
                else:
                    self.burst_throttled[i] += 1
        self.peak_queue = max(self.peak_queue, int(states[2].value))

    def _panel(self, i: int, state: LimiterSnapshot, last_departure: float | None) -> Panel:
        color = self.COLORS[i]
        history = self.histories[i]
        hints = (
            f"{self.capacity} tokens | +{self.rate}/s",
            f"{self.rate} requests | trailing 1s",
            f"{self.capacity} slots | {self.rate} out/s",
        )
        latest = Text("Waiting for a request", style="dim")
        if history:
            decision = history[-1]
            label = ("QUEUED" if i == 2 else "ALLOWED") if decision.allowed else (
                "DROPPED" if i == 2 else "THROTTLED"
            )
            latest = Text(f"#{decision.request_id} {label}", style="bold green" if decision.allowed else "bold red")

        counts = Text()
        counts.append(f"Allowed {state.allowed}", style="green")
        counts.append(" | ")
        counts.append(f"Blocked {state.throttled}", style="red")

        # Every character represents ONE input decision. Sixty markers occupy
        # only a few terminal rows, so a burst cannot overwrite a single status
        # label before its intermediate decisions become visible.
        ribbon = Text("." * (self.HISTORY_SIZE - len(history)), style="dim", overflow="fold")
        for item in history:
            ribbon.append("+" if item.allowed else "x", style="green" if item.allowed else "red")

        recent = Table.grid(padding=(0, 1))
        recent.add_column("Time", justify="right")
        recent.add_column("ID", justify="right")
        recent.add_column("Result")
        recent.add_column("State", justify="right")
        recent.add_row("Time", "ID", "Result", "State", style="dim")
        items = list(history)[-4:]
        for _ in range(4 - len(items)):
            recent.add_row("-", "-", "-", "-")
        for item in items:
            label = ("QUEUE" if i == 2 else "ALLOW") if item.allowed else (
                "DROP" if i == 2 else "BLOCK"
            )
            recent.add_row(
                f"{item.timestamp:.2f}", f"#{item.request_id}",
                Text(label, style="green" if item.allowed else "red"),
                f"{item.state:.{state.precision}f}",
            )

        if i == 2:
            output = Text(f"Emitted {state.emitted} | queued {int(state.value)}", style="cyan")
            detail = "Last out: --" if last_departure is None else f"Last out: {last_departure:.2f}s"
        else:
            output = Text(f"Forwarded immediately: {state.emitted}", style="cyan")
            detail = "Allowed requests pass through"

        return Panel(
            Group(
                Text(hints[i], style="dim"), latest, Text(""),
                Text(f"{state.label}: {state.value:.{state.precision}f}/{state.capacity}"),
                Align(ProgressBar(total=state.capacity, completed=state.value, complete_style=color, finished_style=color)),
                counts, output, Text(detail, style="dim"),
                Text("\nLast 60 inputs: old -> new", style="dim"), ribbon,
                Text("\nRecent requests", style="bold"), recent,
                Text(f"Bursts: {self.burst_allowed[i]} OK / {self.burst_throttled[i]} blocked", style="dim"),
            ),
            title=state.name, border_style=color, padding=(0, 1), expand=True,
        )

    def render(
        self, elapsed: float, states: list[LimiterSnapshot], last_departure: float | None = None
    ) -> Group:
        phase, style = self.schedule.phase_at(elapsed)
        columns = Table.grid(expand=True, padding=(0, 1))
        for _ in states:
            columns.add_column(ratio=1)
        columns.add_row(*(self._panel(i, state, last_departure) for i, state in enumerate(states)))
        legend = Text("+ green = admitted   x red = blocked   |   Leaky admission means QUEUED")
        legend.highlight_words(["admitted"], style="green")
        legend.highlight_words(["blocked"], style="red")
        return Group(
            Text("RATE LIMITER VISUALIZER", style="bold"),
            Text(
                f"{elapsed:5.1f}/{self.schedule.duration:g}s | requests {self.seen}/{len(self.schedule.events)}"
                f" | baseline {self.rate}/s | seed {self.seed} | Ctrl+C to stop"
            ),
            Text(phase, style=style),
            Align(ProgressBar(total=self.schedule.duration, completed=elapsed, complete_style="yellow")),
            columns, legend,
        )

    def summary(self, states: list[LimiterSnapshot], elapsed: float, completed: bool) -> Group:
        totals = Table(title="Request totals", expand=True)
        for title in ("Algorithm", "Allowed", "Throttled / dropped", "Burst allowed / blocked", "Emitted", "Queued"):
            totals.add_column(title, justify="left" if title == "Algorithm" else "right")
        for i, state in enumerate(states):
            totals.add_row(
                state.name, str(state.allowed), str(state.throttled),
                f"{self.burst_allowed[i]} / {self.burst_throttled[i]}",
                str(state.emitted), str(int(state.value)) if i == 2 else "0",
            )

        comparison = Table(title="Burst behavior and real-world tradeoffs", expand=True)
        comparison.add_column("Algorithm", style="bold")
        comparison.add_column("Observed during this run", ratio=1)
        comparison.add_column("Tradeoff", ratio=1)
        comparison.add_row(
            "Token Bucket",
            f"Forwarded {self.burst_allowed[0]} burst requests immediately; blocked {self.burst_throttled[0]} when tokens ran short.",
            "O(1) limiter state and stored burst credit, but accepted spikes reach downstream services immediately.",
        )
        comparison.add_row(
            "Sliding Window Log",
            f"Admitted {self.burst_allowed[1]} burst requests under the exact trailing-window limit; blocked {self.burst_throttled[1]}.",
            "An exact trailing-window cap costs O(limit) timestamps per key and still permits a simultaneous burst up to that limit.",
        )
        comparison.add_row(
            "Leaky Bucket",
            f"Queued {self.burst_allowed[2]} burst requests, dropped {self.burst_throttled[2]}, and reached {self.peak_queue}/{self.capacity} queue slots.",
            f"O(capacity) buffering smooths departures to one every {1 / self.rate:g}s while busy, adding queueing delay.",
        )
        status = "Completed" if completed else "Interrupted"
        return Group(
            Text(f"\n{status}: {self.seen} identical inputs per algorithm over {elapsed:.2f}s.", style="bold"),
            totals, comparison,
            Text("Leaky allowed = admitted to queue; allowed = emitted + still queued.", style="dim"),
            Text("Burst columns count only inputs tagged as spikes; totals include quiet-period requests.", style="dim"),
        )
