import json

import pytest

from ace_scheduler.config.models import preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.core.recovery import RecoveryJournal
from ace_scheduler.core.scheduler import Scheduler
from tests.fakes import FakeApi


KEY = ProcessIdentity(41, 100, "test.exe")
TOPOLOGY = CpuTopology(4, 8, tuple(range(8)))


def start(path, api=None):
    return Scheduler(api or FakeApi(), TOPOLOGY, RecoveryJournal(path, boot=10))


def test_restart_observes_then_restores_and_cleans_each_field(tmp_path):
    path = tmp_path / "recovery.json"
    first = start(path)
    assert first.apply(KEY, preset("Strong")).ok
    writes = len(first.api.writes)
    second = start(path, first.api)
    assert len(second.api.writes) == writes
    assert second.journal.warning and second.originals
    assert second.restore(KEY).ok
    assert first.api.values[KEY]["priority"] == 0x20
    assert not start(path).originals


def test_write_ahead_failure_blocks_mutations(tmp_path, monkeypatch):
    scheduler = start(tmp_path / "recovery.json")
    monkeypatch.setattr(scheduler.journal, "_save", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    assert not scheduler.apply(KEY, preset("Strong")).ok
    assert not scheduler.api.writes and not scheduler.originals


def test_crash_between_write_and_confirmation_requires_explicit_conflict_choice(tmp_path, monkeypatch):
    path = tmp_path / "recovery.json"
    scheduler = start(path)
    monkeypatch.setattr(scheduler.journal, "confirm", lambda *args: (_ for _ in ()).throw(OSError("power loss")))
    assert not scheduler.apply(KEY, preset("Strong")).ok
    recovered = start(path, scheduler.api)
    count = len(scheduler.api.writes)
    assert "恢复冲突" in recovered.restore(KEY).status
    assert len(scheduler.api.writes) == count
    assert recovered.restore(KEY, force=True).ok
    assert not recovered.originals


def test_external_change_partial_restore_and_retry(tmp_path):
    path = tmp_path / "recovery.json"
    scheduler = start(path)
    assert scheduler.apply(KEY, preset("Strong")).ok
    scheduler.api.values[KEY]["priority"] = 0x4000
    assert not scheduler.restore(KEY).ok
    assert scheduler.api.values[KEY]["priority"] == 0x4000
    assert set(scheduler.originals[KEY]) == {"priority"}
    second = start(path, scheduler.api)
    second.api.fail.add("set_priority")
    assert not second.restore(KEY, force=True).ok
    assert second.originals
    second.api.fail.clear()
    assert second.restore(KEY, force=True).ok


def test_restore_cleanup_failure_is_retryable_without_overwriting_original(tmp_path, monkeypatch):
    path = tmp_path / "recovery.json"
    scheduler = start(path)
    assert scheduler.apply(KEY, preset("Strong")).ok
    save = scheduler.journal._save
    monkeypatch.setattr(scheduler.journal, "_save", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    assert not scheduler.restore(KEY).ok
    assert scheduler.originals
    monkeypatch.setattr(scheduler.journal, "_save", save)
    assert scheduler.restore(KEY).ok


@pytest.mark.parametrize("payload", ["{broken", '{"schema": 2}', '{"schema": 1, "boot": null}',
                                      '{"schema": 1, "boot": 10, "updated": 1, "records": {}}'])
def test_corrupt_or_unknown_journal_is_preserved_and_blocks_writes(tmp_path, payload):
    path = tmp_path / "recovery.json"
    path.write_text(payload, encoding="utf-8")
    scheduler = start(path)
    assert scheduler.journal.blocked
    assert not scheduler.apply(KEY, preset("Strong")).ok
    assert not scheduler.api.writes and path.read_text(encoding="utf-8") == payload
    scheduler.abandon()
    assert list(tmp_path.glob("recovery-archive-*.json"))
    assert scheduler.apply(KEY, preset("Strong")).ok


@pytest.mark.parametrize("boot,now", [(1000, None), (10, 10**12)])
def test_reboot_or_expired_records_archived_without_restore(tmp_path, boot, now):
    path = tmp_path / "recovery.json"
    assert start(path).apply(KEY, preset("Strong")).ok
    journal = RecoveryJournal(path, boot=boot, now=now)
    assert not journal.records and "归档" in journal.warning
    assert list(tmp_path.glob("recovery-archive-*.json"))


def test_reused_or_exited_identity_discarded_durably(tmp_path):
    path = tmp_path / "recovery.json"
    scheduler = start(path)
    assert scheduler.apply(KEY, preset("Strong")).ok
    scheduler.retain({ProcessIdentity(KEY.pid, 200, KEY.name)})
    assert not start(path).originals
    assert json.loads(path.read_text(encoding="utf-8"))["records"] == []


def test_keep_decision_completes_session(tmp_path):
    path = tmp_path / "recovery.json"
    scheduler = start(path)
    assert scheduler.apply(KEY, preset("Strong")).ok
    scheduler.abandon()
    assert not start(path).originals
    assert scheduler.api.values[KEY]["priority"] == 0x40


def test_corrupt_journal_cannot_report_successful_restore(tmp_path):
    from ace_scheduler.config.models import AppConfig
    from ace_scheduler.core.process_monitor import MonitorEngine
    path = tmp_path / "recovery.json"
    path.write_text("invalid")
    engine = MonitorEngine(start(path), AppConfig())
    assert not engine.restore()
