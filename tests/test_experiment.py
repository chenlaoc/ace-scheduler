import pytest

from ace_scheduler.core.experiment import History, summary
from ace_scheduler.core.process_metrics import Metrics, ProcessIdentity


def test_history_window_and_frozen_before():
    history = History()
    identity = ProcessIdentity(1, 10, "test.exe")
    for t in range(100):
        history.add(identity, Metrics(t, read_mbps=2, sample_seconds=1))
    assert history.samples[identity][0].timestamp == 39
    history.mark(identity, 100, "已验证")
    history.add(identity, Metrics(100, read_mbps=2, sample_seconds=1))
    history.add(identity, Metrics(161, read_mbps=1, sample_seconds=1))
    assert len(history.samples[identity]) == 1
    assert history.experiments[identity].before[-1].timestamp == 100
    assert all(40 <= s.timestamp <= 100 for s in history.experiments[identity].before)
    history.retain(set())
    assert not history.samples and not history.experiments


def test_summary_is_weighted_and_excludes_missing():
    samples = [Metrics(1, read_mbps=10, sample_seconds=1),
               Metrics(4, read_mbps=2, sample_seconds=3), Metrics(5)]
    assert "均值 4.00" in summary(samples, "read_mbps")
    assert "峰值 10.00" in summary(samples, "read_mbps")
    assert summary([Metrics(1)], "read_mbps") == "—"
