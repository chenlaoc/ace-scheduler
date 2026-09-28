import ctypes
from ctypes import wintypes
import subprocess
import sys

import psutil
import pytest

from ace_scheduler.config.models import preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.core.scheduler import Scheduler
from ace_scheduler.windows import eco_qos
from ace_scheduler.windows.process_api import ProcessChangedError, WindowsProcessApi


@pytest.fixture
def helper():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    # A cooperative child exits on stdin newline; never target unrelated processes.
    child = subprocess.Popen([sys.executable, "-u", "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert child.stdout.readline().strip() == "ready"
    process = psutil.Process(child.pid)
    identity = ProcessIdentity(child.pid, process.create_time(), process.name())
    try:
        yield identity
    finally:
        child.communicate(input="\n", timeout=10)
        assert child.returncode == 0


@pytest.mark.windows
def test_real_priority_affinity_eco_on_off_restore(helper):
    topology = CpuTopology.detect()
    api = WindowsProcessApi()
    scheduler = Scheduler(api, topology)
    original = scheduler.inspect(helper)
    assert original.priority is not None
    try:
        result = scheduler.apply(helper, preset("Strong"))
        assert result.ok, result.status
        strong = scheduler.inspect(helper)
        assert strong.priority == 0x40
        if topology.affinity_supported:
            assert strong.affinity == (topology.available[-1],)
        assert strong.eco.label == "ON"
        result = scheduler.apply(helper, preset("Default"))
        assert result.ok, result.status
        assert scheduler.inspect(helper).eco.label == "OFF"
    finally:
        restored = scheduler.restore(helper)
        assert restored.ok, restored.status
    current = scheduler.inspect(helper)
    assert current.priority == original.priority
    assert current.affinity == original.affinity
    assert current.eco == original.eco


@pytest.mark.windows
def test_handle_identity_rejection_and_no_leak(helper):
    api = WindowsProcessApi()
    process = psutil.Process()
    baseline = process.num_handles()
    for _ in range(100):
        with api.open(helper) as handle:
            assert api.get_priority(handle)
        with pytest.raises(ProcessChangedError):
            with api.open(ProcessIdentity(helper.pid, helper.created + 1, helper.name), write=True):
                pytest.fail("A reused PID must not yield a mutation handle")
    assert process.num_handles() <= baseline + 2


def test_eco_masks_preserve_unrelated_bits_and_restore_auto():
    class Api:
        control, state = 4, 4
        def GetProcessInformation(self, handle, info_class, pointer, size):
            value = ctypes.cast(pointer, ctypes.POINTER(eco_qos.PowerThrottlingState)).contents
            value.ControlMask, value.StateMask = self.control, self.state
            return True
        def SetProcessInformation(self, handle, info_class, pointer, size):
            value = ctypes.cast(pointer, ctypes.POINTER(eco_qos.PowerThrottlingState)).contents
            assert value.Version == 1 and info_class == 4 and size == 12
            self.control, self.state = value.ControlMask, value.StateMask
            return True
    api = Api()
    eco_qos.write(api, 1, True)
    assert (api.control, api.state) == (5, 5)
    eco_qos.write(api, 1, False)
    assert (api.control, api.state) == (5, 4)
    eco_qos.write(api, 1, eco_qos.EcoState(0, 0))
    assert (api.control, api.state) == (4, 4)


def test_64_bit_affinity_mask_including_high_bit():
    class Api:
        mask = None
        def SetProcessAffinityMask(self, handle, mask):
            self.mask = mask
            return True
    api = WindowsProcessApi.__new__(WindowsProcessApi)
    api.api = Api()
    api.set_affinity(1, (0, 32, 63))
    assert api.api.mask == (1 << 63) | (1 << 32) | 1
    assert ctypes.c_size_t(api.api.mask).value == api.api.mask
    with pytest.raises(ValueError):
        api.set_affinity(1, ())


@pytest.mark.windows
def test_real_monitor_apply_enforce_and_restore_cooperative_child(helper):
    from ace_scheduler.config.models import AppConfig, ProcessRule
    from ace_scheduler.core.process_monitor import MonitorEngine
    clock = [100.0]
    scheduler = Scheduler(WindowsProcessApi(), CpuTopology.detect())
    def only_child(attrs):
        process = psutil.Process(helper.pid)
        process.info = process.as_dict(attrs=attrs)
        return [process]
    engine = MonitorEngine(scheduler, AppConfig([ProcessRule(helper.name, policy=preset("Strong"), keep_enforced=True)]),
                           iterator=only_child, clock=lambda: clock[0])
    original = scheduler.inspect(helper)
    assert engine.scan()[0].status == "仅监控"
    assert not scheduler.originals
    try:
        engine.arm(helper.name.casefold())
        row = engine.scan()[0]
        assert "已验证" in row.status
        assert row.state.priority == 0x40
        with scheduler.api.open(helper, write=True) as handle:
            scheduler.api.set_priority(handle, 0x20)
        clock[0] += 3
        engine.enforce()
        assert scheduler.inspect(helper).priority == 0x40
    finally:
        assert engine.restore()
    assert scheduler.inspect(helper) == original
