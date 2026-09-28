import math

import pytest

from ace_scheduler.config.models import AffinitySpec, preset
from ace_scheduler.core.cpu_topology import CpuTopology


@pytest.mark.parametrize("count", [1, 2, 4, 8, 12, 14, 16, 24, 32, 64])
def test_dynamic_presets(count):
    topology = CpuTopology(None, count, tuple(range(count)))
    assert topology.resolve(AffinitySpec()) == tuple(range(count))
    assert topology.resolve(AffinitySpec("last_n", count=1)) == (count - 1,)
    assert topology.resolve(AffinitySpec("last_n", count=2)) == tuple(range(max(0, count - 2), count))
    for percent in (25, 50):
        ids = topology.resolve(AffinitySpec("percentage", percentage=percent))
        assert len(ids) == max(1, math.ceil(count * percent / 100))
        assert ids[-1] == count - 1
    assert topology.resolve(preset("Strong").affinity) == (count - 1,)
    assert len(topology.resolve(preset("Mild").affinity)) == min(count, max(2, math.ceil(count / 4)))


def test_sparse_available_ids_and_cross_machine_custom():
    topology = CpuTopology(4, 8, tuple(range(8)))
    assert topology.resolve(AffinitySpec("custom", cpus=(6, 7, 8, 15))) == (6, 7)
    with pytest.raises(ValueError, match="全部无效"):
        topology.resolve(AffinitySpec("custom", cpus=(15,)))
    sparse = CpuTopology(4, 8, (0, 2, 5, 7))
    assert sparse.resolve(AffinitySpec("last_n", count=2)) == (5, 7)
    assert sparse.resolve(AffinitySpec()) == (0, 2, 5, 7)


@pytest.mark.parametrize("logical,groups", [(128, 2), (64, 2), (80, 1)])
def test_groups_are_explicitly_unsupported(logical, groups):
    topology = CpuTopology(None, logical, (), groups)
    assert not topology.affinity_supported
    assert "仅支持单 Processor Group" in topology.limitation


def test_no_smt_assumption_and_empty_mask():
    assert CpuTopology(14, 14, tuple(range(14))).affinity_supported
    with pytest.raises(ValueError):
        CpuTopology(None, 8, ()).resolve(AffinitySpec())
