"""GUI -> worker -> Windows API -> read-back, restricted to our own child PID."""
import copy
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import psutil
import pytest
from PySide6.QtTest import QTest

from ace_scheduler.config.models import AppConfig, ProcessRule
from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_monitor import MonitorEngine, MonitorWorker
from ace_scheduler.core.scheduler import Scheduler
from ace_scheduler.windows.process_api import WindowsProcessApi
from ace_scheduler.ui.main_window import MainWindow
from tests.test_interactive_flows import click, choose, window
from tests.test_ui import app
from tests.test_windows_api import helper


@pytest.mark.windows
@pytest.mark.parametrize("keep", [False, True], ids=["apply_once", "keep_enforced"])
def test_gui_apply_stop_and_restore_real_child(window, helper, keep):
    topology = CpuTopology.detect()
    scheduler = Scheduler(WindowsProcessApi(), topology)
    original = scheduler.inspect(helper)
    clock = [100.0]
    def only_child(attrs):
        child = psutil.Process(helper.pid)
        child.info = child.as_dict(attrs=attrs)
        return [child]
    window.config = AppConfig([ProcessRule(helper.name)])
    page = window.policy_page
    page.selected = {helper.name.casefold()}
    page.rebuild()
    window.on_ready(topology)
    engine = MonitorEngine(scheduler, copy.deepcopy(window.config), iterator=only_child, clock=lambda: clock[0])
    worker = MonitorWorker(copy.deepcopy(window.config))
    worker.engine = engine
    window.config_changed.connect(engine.configure)
    window.apply_many_requested.connect(worker.apply_rules)
    window.stop_many_requested.connect(worker.stop_rules)
    window.restore_many_requested.connect(worker.restore_rules)
    window.restore_requested.connect(worker.restore)
    worker.snapshot.connect(window.on_snapshot)
    worker.command_done.connect(window.on_command_done)
    worker.restore_done.connect(window.on_restore_done)
    worker.log_line.connect(window.log.appendPlainText)
    evidence = {"identity": asdict(helper), "before": asdict(original), "keep": keep}
    try:
        worker.scan()
        assert scheduler.inspect(helper) == original and not engine.armed
        click(window.nav_buttons[1])
        for preset_name, priority in (("Mild", 0x4000), ("Strong", 0x40), ("Default", 0x20), ("Strong", 0x40)):
            click(page.preset_buttons[preset_name])
            if keep and not page.rule(helper.name.casefold()).keep_enforced:
                click(page.controls[helper.name.casefold()].keep)
            click(page.apply_button)
            state = scheduler.inspect(helper)
            assert window.pending_commands == 0 and state.priority == priority
            assert state.affinity == topology.resolve(page.rule(helper.name.casefold()).policy.affinity)
            assert state.eco.label == ("OFF" if preset_name == "Default" else "ON")
            evidence[preset_name] = asdict(state)
        with scheduler.api.open(helper, write=True) as handle:
            scheduler.api.set_priority(handle, 0x20)
        clock[0] += 4
        worker.enforce()
        assert scheduler.inspect(helper).priority == (0x40 if keep else 0x20)
        click(page.stop_button)
        assert not engine.armed
        with scheduler.api.open(helper, write=True) as handle:
            scheduler.api.set_priority(handle, 0x20)
        clock[0] += 4
        worker.enforce()
        assert scheduler.inspect(helper).priority == 0x20
        click(window.nav_buttons[3])
        click(window.restore_all_button)
        assert not window.pending_restore and window.pending_commands == 0
        assert scheduler.inspect(helper) == original and not scheduler.originals
        evidence["restored"] = asdict(scheduler.inspect(helper))
        if os.environ.get("ACE_TEST_ARTIFACT_DIR"):
            output = Path(os.environ["ACE_TEST_ARTIFACT_DIR"])
            output.mkdir(parents=True, exist_ok=True)
            stem = "keep_enforced" if keep else "apply_once"
            (output / f"{stem}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            window.grab().save(str(output / f"{stem}.png"))
    finally:
        assert engine.restore()
        assert scheduler.inspect(helper) == original


@pytest.mark.windows
def test_actual_qthread_apply_restore_and_queued_close(app, tmp_path, helper, monkeypatch):
    from ace_scheduler.core import process_monitor
    def only_child(attrs):
        child = psutil.Process(helper.pid)
        child.info = child.as_dict(attrs=attrs)
        return [child]
    original_engine = process_monitor.MonitorEngine
    def isolated_engine(*args, **kwargs):
        return original_engine(*args, iterator=only_child, **kwargs)
    monkeypatch.setattr(process_monitor, "MonitorEngine", isolated_engine)
    inspector = Scheduler(WindowsProcessApi(), CpuTopology.detect())
    original = inspector.inspect(helper)
    actual = MainWindow(AppConfig([ProcessRule(helper.name)]), ConfigManager(tmp_path / "async.json"))
    actual.show()
    def until(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert predicate()
    try:
        until(lambda: actual.topology is not None)
        click(actual.nav_buttons[1])
        click(actual.policy_page.preset_buttons["Strong"])
        click(actual.policy_page.apply_button)
        until(lambda: actual.pending_commands == 0 and actual.restore_count == 1)
        assert inspector.inspect(helper).priority == 0x40
        click(actual.nav_buttons[3])
        click(actual.restore_all_button)
        click(actual.title_bar.close_button)
        until(lambda: actual.allow_close and not actual.thread.isRunning())
        assert actual.allow_close and not actual.isVisible()
        assert not actual.pending_restore and not actual.pending_commands
        assert inspector.inspect(helper) == original
    finally:
        if actual.thread.isRunning():
            actual.request_restore_all()
            until(lambda: actual.pending_commands == 0)
            actual._shutdown()
            until(lambda: not actual.thread.isRunning())
        actual.deleteLater()
