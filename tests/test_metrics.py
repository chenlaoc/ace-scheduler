import pytest

from ace_scheduler.core.process_metrics import Counters, MetricsSampler, ProcessIdentity


def counters(t, cpu=0, read=0, write=0):
    return Counters(t, cpu, read, write, 5, 7, 10_000_000)


def test_elapsed_time_units_and_machine_cpu():
    sampler = MetricsSampler(4)
    key = ProcessIdentity(100, 1.0, "test.exe")
    first = sampler.sample(key, counters(10))
    assert first.cpu_percent is None and first.read_mbps is None
    second = sampler.sample(key, counters(12, cpu=2, read=8_000_000, write=2_000_000))
    assert second.cpu_percent == 25
    assert second.read_mbps == 4
    assert second.write_mbps == 1
    assert second.total_read_gb == .008
    assert second.ram_mb == 10
    assert second.read_count == 5
    assert second.sample_seconds == 2


def test_pid_reuse_and_counter_reset_do_not_spike():
    sampler = MetricsSampler(8)
    old = ProcessIdentity(5, 10, "test.exe")
    new = ProcessIdentity(5, 20, "test.exe")
    sampler.sample(old, counters(1, read=100))
    assert sampler.sample(new, counters(2, read=1000)).read_mbps is None
    assert sampler.sample(new, counters(3, read=1)).read_mbps is None
    sampler.retain({new})
    assert old not in sampler.previous


def test_missing_counter_and_zero_interval():
    sampler = MetricsSampler(1)
    key = ProcessIdentity(3, 1, "test.exe")
    sampler.sample(key, counters(1, read=None))
    assert sampler.sample(key, counters(1)).read_mbps is None
    assert sampler.sample(key, counters(2, read=None)).read_mbps is None
    assert sampler.sample(key, counters(3, read=2)).read_mbps is None
