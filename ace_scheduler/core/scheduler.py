from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ace_scheduler.config.models import Policy
from .cpu_topology import CpuTopology
from .process_metrics import ProcessIdentity
from .recovery import decode
from .policy import resolve_targets
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
    skipped: bool = False
    write_succeeded: bool | None = None
    verified: bool | None = None
    recovery_pending: bool = False
    untouched: bool = False
    requested: object | None = None
    original: object | None = None
    actual: object | None = None
    error_code: str | None = None


@dataclass
class ApplyResult:
    operations: list[Operation]

    @property
    def ok(self) -> bool:
        return bool(self.operations) and all(op.ok and not op.skipped and not op.recovery_pending
                                             for op in self.operations)

    @property
    def status(self) -> str:
        if self.ok:
            if all(op.untouched for op in self.operations):
                return "未修改（全部字段均不接管）"
            return "已验证" if any(op.changed for op in self.operations) else "无需修改（已验证）"
        details = [f"{op.field}: {op.message}" for op in self.operations
                   if not op.ok or op.skipped or op.recovery_pending]
        if any(op.recovery_pending for op in self.operations):
            status = "已暂停 · 恢复记录待确认"
        elif any(op.ok and not op.skipped and not op.untouched for op in self.operations):
            status = "部分成功"
        else:
            status = "未应用" if self.operations and all(op.skipped for op in self.operations) else "失败"
        return status + (" | " + " | ".join(details) if details else "")


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
        pending = self.pending_fields(identity)
        if pending:
            return ApplyResult([Operation(name, False, "上次写入未确认；请恢复原设置或归档并保留现值",
                                          recovery_pending=True) for name in pending])
        if self.journal and self.journal.blocked:
            return ApplyResult([Operation("恢复记录", False, "恢复记录无法读取；请先归档处理")])
        try:
            # Validate all inputs before making even the first change.
            targets = resolve_targets(policy, self.topology)
        except (ValueError, KeyError) as exc:
            return ApplyResult([Operation("配置", False, str(exc))])
        result = self._set(identity, targets, restore=False) if targets else ApplyResult([])
        for name, unchanged in (("priority", policy.priority == "unchanged"),
                                ("affinity", policy.affinity.mode == "unchanged"),
                                ("eco", policy.eco == "unchanged")):
            if unchanged:
                result.operations.append(Operation(name, True, "不修改（本次不接管）", untouched=True))
        if policy.affinity.mode != "unchanged" and not self.topology.affinity_supported:
            result.operations.append(Operation("affinity", False, "不支持，已跳过：" + self.topology.limitation,
                                               skipped=True))
        return result

    def pending_fields(self, identity) -> tuple[str, ...]:
        if not self.journal:
            return ()
        fields = self.journal.records.get(identity, {}).get("fields", {})
        return tuple(name for name, entry in fields.items() if entry["pending"])

    def pause_reason(self, identity) -> str:
        pending = self.pending_fields(identity)
        return ("已暂停 · 恢复记录待确认（" + ", ".join(pending)
                + "）；请恢复原设置或归档并保留现值") if pending else ""

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
                    written = None
                    verified = None
                    current = actual = None
                    error_code = None
                    try:
                        current = getattr(self.api, "get_" + name)(handle)
                        actual = current
                        changed = not self.matches(name, current, target)
                        verified = not changed
                        if name == "affinity" and not self.topology.affinity_supported:
                            raise RuntimeError("当前拓扑不支持恢复 Affinity")
                        if restore and changed and self.journal and not force:
                            entry = self.journal.entry(identity, name)
                            if not entry or entry["pending"] or not self.matches(name, current, decode(name, entry["last"])):
                                error_code = "recovery_conflict"
                                raise RuntimeError("恢复冲突：当前值被外部修改或上次写入未确认；请保留现值或明确覆盖恢复")
                        if changed:
                            if not restore:
                                if self.journal:
                                    saved_target = EcoState(1, int(target)) if name == "eco" and isinstance(target, bool) else target
                                    decode(name, current.__dict__ if isinstance(current, EcoState) else current)
                                    self.journal.prepare(identity, name, current, saved_target)
                                self.originals.setdefault(identity, {}).setdefault(name, current)
                            written = False
                            getattr(self.api, "set_" + name)(handle, target)
                            written = True
                            verified = False
                            actual = None
                            actual = getattr(self.api, "get_" + name)(handle)
                            if not self.matches(name, actual, target):
                                raise RuntimeError(f"回读不一致：{actual!r}")
                            verified = True
                            if not restore and self.journal:
                                self.journal.confirm(identity, name, actual)
                        if restore:
                            if self.journal:
                                self.journal.remove(identity, name)
                            self.originals[identity].pop(name, None)
                        operations.append(Operation(name, True, f"-> {target!r} OK" + ("" if changed else "（已符合）"),
                                                    changed, write_succeeded=written, verified=True))
                        operations[-1].requested = target
                        operations[-1].original = current
                        operations[-1].actual = actual
                    except (OSError, RuntimeError, ValueError) as exc:
                        pending = name in self.pending_fields(identity)
                        message = error_text(exc)
                        if pending:
                            message += "；恢复记录待确认"
                            if written:
                                message += "（设置已改变" + ("，回读符合目标" if verified else "，回读未验证") + "）"
                        operations.append(Operation(name, False, message, changed=written is True,
                                                    write_succeeded=written, verified=verified,
                                                    recovery_pending=pending, requested=target, original=current,
                                                    actual=actual, error_code=error_code or ("recovery_pending" if pending else "operation_failed")))
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
