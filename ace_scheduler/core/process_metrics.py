from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: float
    name: str


@dataclass(frozen=True)
class Counters:
    timestamp: float
    cpu_seconds: float | None
    read_bytes: int | None
    write_bytes: int | None
    read_count: int | None
    write_count: int | None
    rss: int | None


@dataclass(frozen=True)
class Metrics:
    timestamp: float
    cpu_percent: float | None = None
    read_mbps: float | None = None
    write_mbps: float | None = None
    total_read_gb: float | None = None
    total_write_gb: float | None = None
    ram_mb: float | None = None
    read_count: int | None = None
    write_count: int | None = None
    sample_seconds: float | None = None


class MetricsSampler:
    """Monotonic deltas; missing/first/reset samples are unknown, never fake zeroes."""
    def __init__(self, logical_count: int):
        self.logical_count = max(1, logical_count)
        self.previous: dict[ProcessIdentity, Counters] = {}

    def sample(self, identity: ProcessIdentity, current: Counters) -> Metrics:
        previous = self.previous.get(identity)
        self.previous[identity] = current
        dt = current.timestamp - previous.timestamp if previous else 0

        def rate(field: str, scale: float) -> float | None:
            now_value = getattr(current, field)
            old_value = getattr(previous, field) if previous else None
            if dt <= 0 or now_value is None or old_value is None or now_value < old_value:
                return None
            return (now_value - old_value) / dt / scale

        def scaled(value, divisor):
            return None if value is None else value / divisor

        cpu = rate("cpu_seconds", self.logical_count / 100)
        return Metrics(current.timestamp, min(100.0, cpu) if cpu is not None else None,
                       rate("read_bytes", 1_000_000), rate("write_bytes", 1_000_000),
                       scaled(current.read_bytes, 1_000_000_000),
                       scaled(current.write_bytes, 1_000_000_000), scaled(current.rss, 1_000_000),
                       current.read_count, current.write_count, dt if dt > 0 else None)

    def retain(self, identities: set[ProcessIdentity]) -> None:
        self.previous = {key: value for key, value in self.previous.items() if key in identities}
