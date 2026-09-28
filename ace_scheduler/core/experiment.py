from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .process_metrics import Metrics, ProcessIdentity


@dataclass
class Experiment:
    marker: float
    label: str
    before: list[Metrics]


class History:
    def __init__(self):
        self.samples: dict[ProcessIdentity, deque] = {}
        self.experiments: dict[ProcessIdentity, Experiment] = {}

    def add(self, identity, metrics):
        values = self.samples.setdefault(identity, deque(maxlen=240))
        if values and metrics.timestamp <= values[-1].timestamp:
            return
        values.append(metrics)
        while values and values[0].timestamp < metrics.timestamp - 60:
            values.popleft()
        experiment = self.experiments.get(identity)
        if experiment and experiment.marker - 60 <= metrics.timestamp <= experiment.marker:
            if not experiment.before or metrics.timestamp > experiment.before[-1].timestamp:
                experiment.before.append(metrics)

    def mark(self, identity, timestamp, label):
        before = [s for s in self.samples.get(identity, []) if timestamp - 60 <= s.timestamp <= timestamp]
        self.experiments[identity] = Experiment(timestamp, label, before)

    def retain(self, identities):
        self.samples = {i: s for i, s in self.samples.items() if i in identities}
        self.experiments = {i: e for i, e in self.experiments.items() if i in identities}


def summary(samples, field):
    valid = [s for s in samples if getattr(s, field) is not None and s.sample_seconds]
    if not valid:
        return "—"
    duration = sum(s.sample_seconds for s in valid)
    mean = sum(getattr(s, field) * s.sample_seconds for s in valid) / duration
    return f"均值 {mean:.2f} / 峰值 {max(getattr(s, field) for s in valid):.2f}（{len(valid)} 点）"
