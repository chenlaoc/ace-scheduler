from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

import psutil
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ace_scheduler.config.models import AppConfig
from .cpu_topology import CpuTopology
from .process_metrics import Counters, Metrics, MetricsSampler, ProcessIdentity
from .scheduler import ScheduleState, Scheduler, error_text


@dataclass
class ProcessRow:
    name: str
    pid: int
    identity: ProcessIdentity | None = None
    metrics: Metrics | None = None
    state: ScheduleState = field(default_factory=ScheduleState)
    status: str = "仅监控"


class MonitorEngine:
    """Single-threaded engine. Worker owns it; UI sends queued commands."""
    def __init__(self, scheduler: Scheduler, config: AppConfig, emit_log=lambda _: None,
                 emit_applied=lambda *_: None, iterator=None, process_factory=None, clock=time.monotonic):
        self.scheduler = scheduler
        self.config = config
        self.log = emit_log
        self.applied = emit_applied
        self.iterator = iterator or psutil.process_iter
        self.process_factory = process_factory or psutil.Process
        self.clock = clock
        self.sampler = MetricsSampler(scheduler.topology.logical)
        self.armed: dict[str, int] = {}
        self.generation = 0
        self.attempted: dict[ProcessIdentity, int] = {}
        self.next_enforce: dict[ProcessIdentity, float] = {}
        self.outcomes: dict[ProcessIdentity, str] = {}
        self.known: set[ProcessIdentity] = set()
        self.rows: list[ProcessRow] = []
        self.last_errors: dict[ProcessIdentity, str] = {}

    def configure(self, config: AppConfig) -> None:
        old = {r.key: r for r in self.config.rules}
        self.config = config
        new = {r.key: r for r in config.rules}
        # Editing a policy only saves it. Applying must always be explicit.
        for key in list(self.armed):
            if key not in new or not new[key].enabled or old.get(key) != new[key]:
                self.disarm(key)

    def arm(self, key: str) -> None:
        rule = next((r for r in self.config.rules if r.key == key and r.enabled), None)
        if rule is None:
            self.log(f"{key} 规则未启用，未应用")
            return
        self.generation += 1
        self.armed[key] = self.generation
        self.log(f"{rule.name} 已启动本会话调度；{'Keep Enforced' if rule.keep_enforced else 'Apply Once'}")

    def disarm(self, key: str) -> None:
        self.armed.pop(key, None)
        for identity in list(self.outcomes):
            if identity.name.casefold() == key:
                self.outcomes.pop(identity, None)
        self.log(f"{key} 已停止自动应用；现有调度值保留，可点击恢复原设置")

    def restore(self, key: str | None = None) -> bool:
        if key is None:
            self.armed.clear()
        else:
            self.disarm(key)
        success = True
        for identity in list(self.scheduler.originals):
            if key is not None and identity.name.casefold() != key:
                continue
            result = self.scheduler.restore(identity)
            success = result.ok and success
            self.outcomes[identity] = "已恢复原设置" if result.ok else result.status
            for operation in result.operations:
                self.log(f"{identity.name} PID={identity.pid} 恢复 {operation.field} {operation.message}")
            self.sampler.previous.pop(identity, None)
            self.applied(identity, self.clock(), "恢复原设置：" + result.status)
        return success

    def scan(self) -> list[ProcessRow]:
        rules = {r.key: r for r in self.config.rules if r.enabled}
        names = set(rules) | {i.name.casefold() for i in self.scheduler.originals}
        found: set[ProcessIdentity] = set()
        uncertain_pids: set[int] = set()
        rows = []
        for candidate in self.iterator(["pid", "name"]):
            name = candidate.info.get("name")
            if not name and any(i.pid == candidate.pid for i in self.scheduler.originals):
                uncertain_pids.add(candidate.pid)
            if not name or name.casefold() not in names:
                continue
            row = ProcessRow(name, candidate.pid)
            try:
                # A fresh object avoids process_iter's cached create_time after PID reuse.
                process = self.process_factory(candidate.pid)
                actual_name = process.name()
                if actual_name.casefold() != name.casefold():
                    continue
                identity = ProcessIdentity(process.pid, process.create_time(), actual_name)
                if not process.is_running():
                    continue
                found.add(identity)
                row.identity = identity
                if identity not in self.known:
                    self.log(f"{name} PID={identity.pid} create_time={identity.created:.6f} detected")
                row.metrics, metric_errors = self._metrics(process, identity)
                row.state = self.scheduler.inspect(identity)
                rule = rules.get(name.casefold())
                generation = self.armed.get(name.casefold())
                now = self.clock()
                if rule and generation is not None:
                    new_attempt = self.attempted.get(identity) != generation
                    due = rule.keep_enforced and now >= self.next_enforce.get(identity, 0)
                    if new_attempt or due:
                        result = self.scheduler.apply(identity, rule.policy)
                        self.attempted[identity] = generation
                        self.next_enforce[identity] = now + (self.config.enforce_interval if result.ok else max(30, self.config.enforce_interval))
                        status = result.status
                        if new_attempt or status != self.outcomes.get(identity) or any(op.changed for op in result.operations):
                            for operation in result.operations:
                                self.log(f"{name} PID={identity.pid} {operation.field} {operation.message}")
                        self.outcomes[identity] = status
                        if new_attempt:
                            self.sampler.previous.pop(identity, None)
                            self.applied(identity, self.clock(), status)
                        row.state = self.scheduler.inspect(identity)
                    row.status = ("Keep Enforced · " if rule.keep_enforced else "Apply Once · ") + self.outcomes.get(identity, "等待应用")
                else:
                    row.status = self.outcomes.get(identity, "仅监控")
                errors = metric_errors + list(row.state.errors.values())
                if errors:
                    message = " | ".join(dict.fromkeys(errors))
                    row.status += " | " + message
                    if self.last_errors.get(identity) != message:
                        self.log(f"{name} PID={identity.pid} 读取: {message}")
                    self.last_errors[identity] = message
                else:
                    self.last_errors.pop(identity, None)
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError, RuntimeError, ValueError) as exc:
                row.status = error_text(exc)
                uncertain_pids.add(candidate.pid)
            rows.append(row)
        # Don't lose restore data on a transient access denial.
        retained = found | {i for i in self.known | set(self.scheduler.originals) if i.pid in uncertain_pids}
        for identity in self.known - retained:
            self.log(f"{identity.name} PID={identity.pid} exited / 已不再是原进程实例")
        self.known = retained
        self.sampler.retain(found)
        self.scheduler.retain(retained)
        for mapping in (self.attempted, self.next_enforce, self.outcomes, self.last_errors):
            for identity in list(mapping):
                if identity not in retained:
                    del mapping[identity]
        self.rows = rows
        return rows

    def enforce(self) -> bool:
        """Independent timer, using known identities without another full process scan."""
        updated = False
        rules = {r.key: r for r in self.config.rules if r.enabled and r.keep_enforced}
        for identity in tuple(self.known):
            key = identity.name.casefold()
            if key not in self.armed or key not in rules or self.clock() < self.next_enforce.get(identity, 0):
                continue
            result = self.scheduler.apply(identity, rules[key].policy)
            self.next_enforce[identity] = self.clock() + (self.config.enforce_interval if result.ok else max(30, self.config.enforce_interval))
            if result.status != self.outcomes.get(identity) or any(op.changed for op in result.operations):
                for operation in result.operations:
                    self.log(f"{identity.name} PID={identity.pid} {operation.field} {operation.message}")
            self.outcomes[identity] = result.status
            updated = True
        return updated

    def _metrics(self, process, identity):
        values = {"cpu_seconds": None, "read_bytes": None, "write_bytes": None,
                  "read_count": None, "write_count": None, "rss": None}
        errors = []
        for kind in ("cpu", "io", "ram"):
            try:
                if kind == "cpu":
                    cpu = process.cpu_times()
                    values["cpu_seconds"] = cpu.user + cpu.system
                elif kind == "io":
                    io = process.io_counters()
                    for name in ("read_bytes", "write_bytes", "read_count", "write_count"):
                        values[name] = getattr(io, name)
                else:
                    values["rss"] = process.memory_info().rss
            except psutil.NoSuchProcess:
                raise
            except (psutil.AccessDenied, OSError) as exc:
                errors.append(f"{kind}: {error_text(exc)}")
        if not process.is_running():
            raise psutil.NoSuchProcess(process.pid)
        counters = Counters(self.clock(), **values)
        return self.sampler.sample(identity, counters), errors


class MonitorWorker(QObject):
    ready = Signal(object)
    snapshot = Signal(object, object, int)
    log_line = Signal(str)
    applied = Signal(object, float, str)
    restore_done = Signal(bool)
    command_done = Signal()

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.engine = None
        self.monitor_timer = None
        self.enforce_timer = None

    def _log(self, message):
        logging.getLogger("ace_scheduler").info(message)
        self.log_line.emit(time.strftime("%H:%M:%S ") + message)

    @Slot()
    def start(self):
        try:
            from ace_scheduler.windows.process_api import WindowsProcessApi
            topology = CpuTopology.detect()
            self.engine = MonitorEngine(Scheduler(WindowsProcessApi(), topology), self.config,
                                        self._log, self.applied.emit)
            self.monitor_timer = QTimer(self)
            self.monitor_timer.timeout.connect(self.scan)
            self.enforce_timer = QTimer(self)
            self.enforce_timer.timeout.connect(self.enforce)
            self._set_intervals()
            self.ready.emit(topology)
            self._log("启动完成：仅监控；保存的策略尚未应用")
            self.scan()
        except Exception as exc:
            self._log(f"后台初始化失败：{error_text(exc)}")
            logging.getLogger("ace_scheduler").exception("Worker initialization")

    def _set_intervals(self):
        self.monitor_timer.start(self.config.monitor_interval * 1000)
        self.enforce_timer.start(self.config.enforce_interval * 1000)

    def _publish(self):
        self.snapshot.emit(list(self.engine.rows), set(self.engine.armed), len(self.engine.scheduler.originals))

    @Slot()
    def scan(self):
        if not self.engine:
            return
        try:
            self.engine.scan()
            self._publish()
        except Exception as exc:
            self._log(f"本次采样失败：{error_text(exc)}")
            logging.getLogger("ace_scheduler").exception("Monitor tick")

    @Slot()
    def enforce(self):
        if self.engine:
            try:
                self.engine.enforce()
            except Exception as exc:
                self._log(f"维护策略失败：{error_text(exc)}")

    @Slot(object)
    def configure(self, config):
        self.config = config
        if self.engine:
            self.engine.configure(config)
            self._set_intervals()

    @Slot(str)
    def apply_rule(self, key):
        self.apply_rules((key,))

    @Slot(object)
    def apply_rules(self, keys):
        self._batch("apply", keys)

    @Slot(object)
    def stop_rules(self, keys):
        self._batch("stop", keys)

    @Slot(object)
    def restore_rules(self, keys):
        self._batch("restore", keys)

    def _batch(self, kind, keys):
        """Queue one operation and scan once for the whole set, not once per rule."""
        try:
            if not self.engine:
                self._log("后台尚未就绪，批量操作未执行")
                return
            keys = tuple(dict.fromkeys(keys))
            if kind == "restore":
                # Stop all selected rules before refreshing process identities.
                for key in keys:
                    self.engine.armed.pop(key, None)
                self.scan()
                success = True
                for key in keys:
                    success = self.engine.restore(key) and success
                self.scan()
                self.restore_done.emit(success)
            else:
                for key in keys:
                    (self.engine.arm if kind == "apply" else self.engine.disarm)(key)
                self.scan()
        except Exception as exc:
            self._log(f"批量操作失败：{error_text(exc)}")
            logging.getLogger("ace_scheduler").exception("Batch rule operation")
        finally:
            self.command_done.emit()

    @Slot(str)
    def stop_rule(self, key):
        if self.engine:
            self.engine.disarm(key)
            self.scan()

    @Slot(object)
    def restore(self, key):
        ok = False
        try:
            if not self.engine:
                self._log("后台尚未就绪，恢复未执行")
                return
            # Refresh exits/PID reuse before trying saved handles.
            if key is None:
                self.engine.armed.clear()
            else:
                self.engine.armed.pop(key, None)
            self.scan()
            ok = self.engine.restore(key)
            self.scan()
        except Exception as exc:
            self._log(f"恢复失败：{error_text(exc)}")
            logging.getLogger("ace_scheduler").exception("Restore operation")
        finally:
            self.restore_done.emit(ok)

    @Slot()
    def stop(self):
        if self.monitor_timer:
            self.monitor_timer.stop()
            self.enforce_timer.stop()
        QThread.currentThread().quit()
