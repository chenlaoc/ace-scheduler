import copy
from dataclasses import replace

from PySide6.QtWidgets import QDialog, QMessageBox

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, AffinitySpec, ProcessRule, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_monitor import MonitorEngine, MonitorWorker
from ace_scheduler.core.scheduler import Scheduler
from ace_scheduler.ui.affinity_dialog import AffinityDialog
from ace_scheduler.ui.main_window import MainWindow
from tests.fakes import FakeApi, FakeProcess
from tests.test_ui import app


def make_window(tmp_path, config=None):
    window = MainWindow(config or AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    return window


def test_batch_preset_only_changes_checked_rules_and_saves_before_applying(app, tmp_path):
    window = make_window(tmp_path)
    page = window.policy_page
    keys = page.selected_keys()[:2]
    page.select_rules("none")
    for key in keys:
        page.controls[key].select.click()
    page.preset_buttons["Strong"].click()
    assert all(rule.policy == preset("Default") for rule in window.config.rules)
    assert set(page.drafts) == set(keys)
    events = []
    window.config_changed.connect(lambda config: events.append(("config", config)))
    window.apply_many_requested.connect(lambda keys: events.append(("apply", keys)))
    page.commit(True)
    assert [kind for kind, _ in events] == ["config", "apply"]
    assert events[1][1] == keys
    saved, warning = window.manager.load()
    assert not warning
    assert [rule.policy for rule in saved.rules] == [preset("Strong")] * 2 + [preset("Default")] * 3
    assert window.pending_commands == 1
    assert not page.apply_button.isEnabled()
    window.on_command_done()
    assert window.pending_commands == 0 and page.apply_button.isEnabled()
    window.close()


def test_multiple_row_drafts_survive_navigation_selection_and_refresh(app, tmp_path):
    window = make_window(tmp_path)
    page = window.policy_page
    a, b, *others = page.selected_keys()
    page.set_preset((a,), "Strong")
    page.set_preset((b,), "Mild")
    page.controls[b].keep.click()
    before = copy.deepcopy(page.drafts)
    window.show_page(2)
    window.on_snapshot([], {a}, 0)
    page.select_rules("none")
    page.controls[a].select.click()
    window.show_page(1)
    assert page.drafts == before
    page.commit(False)
    assert window.config.rules[0].policy == preset("Strong")
    assert window.config.rules[1].policy == preset("Default")
    assert page.drafts == {b: before[b]}
    assert "未勾选" in page.summary.text()
    page.select_rules("all")
    page.commit(True)
    assert window.config.rules[1].policy == preset("Mild")
    assert window.config.rules[1].keep_enforced
    assert all(rule.policy == preset("Default") for rule in window.config.rules[2:])
    window.close()


def test_failed_save_preserves_config_and_all_drafts_without_commands(app, tmp_path, monkeypatch):
    window = make_window(tmp_path)
    page = window.policy_page
    before = copy.deepcopy(window.config)
    page.set_preset(page.selected_keys(), "Strong")
    drafts = copy.deepcopy(page.drafts)
    signals = []
    window.config_changed.connect(signals.append)
    window.apply_many_requested.connect(signals.append)
    monkeypatch.setattr(window.manager, "save", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    page.commit(True)
    assert warnings == ["disk full"]
    assert window.config == before and page.drafts == drafts
    assert not signals and window.pending_commands == 0
    window.close()


def test_invalid_cpu_in_one_selected_rule_blocks_entire_commit(app, tmp_path, monkeypatch):
    window = make_window(tmp_path)
    page = window.policy_page
    page.set_preset(page.selected_keys(), "Strong")
    bad = page.selected_keys()[1]
    page.edit_policy(bad, affinity=AffinitySpec("custom"))
    warnings, commands = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.apply_many_requested.connect(commands.append)
    page.commit(True)
    assert warnings and page.rule(bad).name in warnings[0]
    assert not commands and not (tmp_path / "config.json").exists()
    assert all(rule.policy == preset("Default") for rule in window.config.rules)
    assert len(page.drafts) == 5
    window.close()


def test_disabled_rules_are_saved_but_skipped_when_applying(app, tmp_path):
    window = make_window(tmp_path)
    page = window.policy_page
    disabled = page.selected_keys()[1]
    page.controls[disabled].enabled.click()
    page.set_preset(page.selected_keys(), "Strong")
    commands = []
    window.apply_many_requested.connect(commands.append)
    page.commit(True)
    assert commands == [tuple(rule.key for rule in window.config.rules if rule.enabled)]
    assert disabled not in commands[0] and not window.config.rules[1].enabled
    assert window.config.rules[1].policy == preset("Strong")
    window.on_command_done()
    page.select_rules("none")
    page.controls[disabled].select.click()
    page.commit(True)
    assert len(commands) == 1 and "均已停用" in page.summary.text()
    page.select_rules("none")
    page.commit(True)
    assert len(commands) == 1 and not page.apply_button.isEnabled()
    window.close()


def test_readonly_batch_cannot_apply_stop_or_restore(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), read_only=True, start_worker=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    page = window.policy_page
    commands = []
    for signal in (window.apply_many_requested, window.stop_many_requested, window.restore_many_requested):
        signal.connect(commands.append)
    page.set_preset(page.selected_keys(), "Strong")
    page.commit(True)
    page.run_command("stop", page.selected_keys())
    page.run_command("restore", page.selected_keys())
    assert not commands and not (tmp_path / "config.json").exists()
    page.commit(False)
    assert (tmp_path / "config.json").exists()
    assert not commands
    window.close()


def test_cpu_dialog_mixed_values_requires_explicit_choice_and_cancel_keeps_drafts(app, tmp_path, monkeypatch):
    window = make_window(tmp_path)
    page = window.policy_page
    a, b = page.selected_keys()[:2]
    page.set_preset((a,), "Strong")
    rules = [page.rule(a), page.rule(b)]
    dialog = AffinityDialog(window.topology, rules, page)
    assert dialog.mixed and not dialog.confirm.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.editor.choose(AffinitySpec("last_n", count=2))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.chosen_spec.count == 2
    dialog.close()
    before = copy.deepcopy(page.drafts)
    monkeypatch.setattr(AffinityDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    page.edit_affinity((a, b))
    assert page.drafts == before
    window.close()


def test_batch_preset_and_cpu_preserve_row_maintenance_and_unrelated_fields(app, tmp_path):
    window = make_window(tmp_path)
    page = window.policy_page
    a, b = page.selected_keys()[:2]
    page.controls[a].keep.click()
    page.set_preset((a, b), "Strong")
    assert page.rule(a).keep_enforced and not page.rule(b).keep_enforced
    page.edit_policy(a, affinity=AffinitySpec("last_n", count=2))
    assert page.rule(a).policy.priority == "Idle" and page.rule(a).policy.eco
    assert page.controls[a].preset.currentData() == "Custom"
    page.select_rules("none")
    page.controls[a].select.click()
    page.discard_selected()
    assert a not in page.drafts and b in page.drafts
    window.close()


def test_worker_applies_distinct_rows_in_single_scan_and_restores_only_targets(app, tmp_path):
    window = make_window(tmp_path)
    page = window.policy_page
    a, b, c = page.selected_keys()[:3]
    page.set_preset((a,), "Strong")
    page.set_preset((b,), "Mild")
    api = FakeApi()
    processes = {i: FakeProcess(i, name=rule.name) for i, rule in enumerate(window.config.rules, 1)}
    scans = []
    def iterator(_):
        scans.append(True)
        return list(processes.values())
    engine = MonitorEngine(Scheduler(api, window.topology), copy.deepcopy(window.config), iterator=iterator,
                           process_factory=processes.__getitem__)
    worker = MonitorWorker(copy.deepcopy(window.config))
    worker.engine = engine
    window.config_changed.connect(engine.configure)
    window.apply_many_requested.connect(worker.apply_rules)
    worker.command_done.connect(window.on_command_done)
    worker.snapshot.connect(window.on_snapshot)
    page.commit(True)
    assert len(scans) == 1
    assert window.pending_commands == 0
    values = {identity.name.casefold(): value for identity, value in api.values.items()}
    assert values[a]["priority"] == 0x40 and values[a]["affinity"] == (7,)
    assert values[b]["priority"] == 0x4000 and values[b]["affinity"] == (6, 7)
    assert values[c]["priority"] == 0x20
    worker.restore_rules((a, a))
    assert len(scans) == 3  # One identity refresh and one final publish for the entire restore.
    assert a not in engine.armed and b in engine.armed
    assert values[a]["priority"] == 0x20 and values[b]["priority"] == 0x4000
    window.close()


def test_worker_batch_acknowledges_initialization_and_unexpected_failures(app, monkeypatch):
    worker = MonitorWorker(AppConfig())
    acknowledgements = []
    worker.command_done.connect(lambda: acknowledgements.append(True))
    worker.apply_rules(("missing.exe",))
    assert acknowledgements == [True]
    worker.engine = type("Broken", (), {"arm": lambda *_: (_ for _ in ()).throw(RuntimeError("failed"))})()
    worker.apply_rules(("missing.exe",))
    assert acknowledgements == [True, True]
