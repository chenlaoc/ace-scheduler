from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ace_scheduler.config.models import Policy, PRIORITIES
from .cpu_topology import CpuTopology
from .process_metrics import ProcessIdentity
from .recovery import decode
from ace_scheduler.windows.eco_qos import EcoState


class SchedulingExtension(Protocol):
    """Future explicit opt-in extensions; none registered or executed in v1."""
    def apply(self, identity: ProcessIdentity) -> str: ...


def error_text(exc: Exception) -> str:
    code = getattr(exc, "winerror", None)
    if code == 5 or exc.__class__.__name__ == "AccessDenied":
        return "AccessDenied：目标进程拒绝访问（管理员权限仍可能不足）"
    if code in (50, 87, 120):
        return f"API 不支持或参数被系统拒绝（WinError {code}）：{exc}"
    return str(exc) or exc.__class__.__name__


@dataclass
class ScheduleState:
    priority: int | None = None
    affinity: tuple[int, ...] | None = None
    eco: object | None = None
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Operation:
    field: str
    ok: bool
    message: str
    changed: bool = False


@dataclass
class ApplyResult:
    operations: list[Operation]

    @property
    def ok(self) -> bool:
        return all(op.ok for op in self.operations)

    @property
    def status(self) -> str:
        failures = [f"{op.field}: {op.message}" for op in self.operations if not op.ok]
        return "已验证" if not failures else "部分/全部失败 | " + " | ".join(failures)


class Scheduler:
    def __init__(self, api, topology: CpuTopology, journal=None):
        self.api = api
        self.topology = topology
        self.journal = journal
        self.originals: dict[ProcessIdentity, dict[str, object]] = journal.originals if journal else {}

    def inspect(self, identity: ProcessIdentity) -> ScheduleState:
        state = ScheduleState()
        try:
            with self.api.open(identity) as handle:
                for name in ("priority", "affinity", "eco"):
                    if name == "affinity" and not self.topology.affinity_supported:
                        state.errors[name] = self.topology.limitation
                        continue
                    try:
                        setattr(state, name, getattr(self.api, "get_" + name)(handle))
                    except (OSError, RuntimeError, ValueError) as exc:
                        state.errors[name] = error_text(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            state.errors["process"] = error_text(exc)
        return state

    @staticmethod
    def matches(name, current, target) -> bool:
        if name == "eco":
            if isinstance(target, bool):
                return current.matches(target)
            return (current.control & 1, current.state & 1) == (target.control & 1, target.state & 1)
        return current == target

    def apply(self, identity: ProcessIdentity, policy: Policy) -> ApplyResult:
        try:
            # Validate all inputs before making even the first change.
            targets = {"priority": PRIORITIES[policy.priority]}
            if self.topology.affinity_supported:
                targets["affinity"] = self.topology.resolve(policy.affinity)
            targets["eco"] = policy.eco
        except (ValueError, KeyError) as exc:
            return ApplyResult([Operation("配置", False, str(exc))])
        result = self._set(identity, targets, restore=False)
        if not self.topology.affinity_supported:
            result.operations.append(Operation("affinity", True, "跳过：" + self.topology.limitation))
        return result

    def restore(self, identity: ProcessIdentity, force=False) -> ApplyResult:
        targets = self.originals.get(identity, {}).copy()
        if not targets:
            return ApplyResult([Operation("恢复", True, "本会话没有已保存的原设置")])
        return self._set(identity, targets, restore=True, force=force)

    def _set(self, identity, targets, restore: bool, force=False) -> ApplyResult:
        operations = []
        try:
            with self.api.open(identity, write=True) as handle:
                for name, target in targets.items():
                    try:
                        current = getattr(self.api, "get_" + name)(handle)
                        changed = not self.matches(name, current, target)
                        if name == "affinity" and not self.topology.affinity_supported:
                            raise RuntimeError("当前拓扑不支持恢复 Affinity")
                        if restore and changed and self.journal and not force:
                            entry = self.journal.entry(identity, name)
                            if not entry or entry["pending"] or not self.matches(name, current, decode(name, entry["last"])):
                                raise RuntimeError("恢复冲突：当前值被外部修改或上次写入未确认；请保留现值或明确覆盖恢复")
                        if changed:
                            if not restore:
                                if self.journal:
                                    saved_target = EcoState(1, int(target)) if name == "eco" and isinstance(target, bool) else target
                                    decode(name, current.__dict__ if isinstance(current, EcoState) else current)
                                    self.journal.prepare(identity, name, current, saved_target)
                                self.originals.setdefault(identity, {}).setdefault(name, current)
                            getattr(self.api, "set_" + name)(handle, target)
                            actual = getattr(self.api, "get_" + name)(handle)
                            if not self.matches(name, actual, target):
                                raise RuntimeError(f"回读不一致：{actual!r}")
                            if not restore and self.journal:
                                self.journal.confirm(identity, name, actual)
                        if restore:
                            if self.journal:
                                self.journal.remove(identity, name)
                            self.originals[identity].pop(name, None)
                        operations.append(Operation(name, True, f"-> {target!r} OK" + ("" if changed else "（已符合）"), changed))
                    except (OSError, RuntimeError, ValueError) as exc:
                        operations.append(Operation(name, False, error_text(exc)))
        except (OSError, RuntimeError, ValueError) as exc:
            operations.append(Operation("process", False, error_text(exc)))
        if identity in self.originals and not self.originals[identity]:
            del self.originals[identity]
        return ApplyResult(operations)

    def retain(self, identities: set[ProcessIdentity]) -> None:
        if self.journal:
            self.journal.retain(identities)
        self.originals = {key: value for key, value in self.originals.items() if key in identities}

    def abandon(self):
        if self.journal:
            self.journal.abandon()
        self.originals.clear()
