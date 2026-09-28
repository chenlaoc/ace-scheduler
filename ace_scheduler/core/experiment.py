"""GUI-independent experiment sessions, fixed windows and portable records."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import copy
import csv
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from ace_scheduler import __version__
from ace_scheduler.config.models import Policy, process_name
from .process_metrics import Metrics, ProcessIdentity

FIELDS = ("cpu_percent", "read_mbps", "write_mbps")
STATE_LABELS = {"baseline": "采集基线", "awaiting_apply": "等待应用策略", "transition": "过渡期",
                "collecting": "采集实验段", "completed": "已完成", "partial_failure": "应用未全部成功",
                "interrupted": "已中断", "not_applied": "未开始实验（没有接管字段）"}
ACTIVE = {"baseline", "awaiting_apply", "transition", "collecting"}


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def validate_setting(name, value):
    if value is None:
        return
    if name == "priority" and type(value) is int and 0 <= value <= 0xffffffff:
        return
    if name == "affinity" and isinstance(value, list) and len(value) <= 4096 and all(type(v) is int and 0 <= v < 4096 for v in value):
        return
    if name == "eco" and (type(value) is bool or isinstance(value, dict) and set(value) == {"control", "state"}
                          and all(type(v) is int and 0 <= v <= 0xffffffff for v in value.values())):
        return
    if name not in ("priority", "affinity", "eco"):
        return
    raise ValueError("实际设置的类型无效")


def validate_state(state):
    if not isinstance(state, dict) or set(state) - {"priority", "affinity", "eco", "errors"}:
        raise ValueError("设置快照无效")
    for name in ("priority", "affinity", "eco"):
        validate_setting(name, state.get(name))
    errors = state.get("errors", {})
    if not isinstance(errors, dict) or any(not isinstance(s, str) for s in errors.values()):
        raise ValueError("设置读取错误无效")


def overlap(sample, start, end):
    if not sample.sample_seconds or sample.sample_seconds <= 0:
        return 0.0
    return max(0.0, min(end, sample.timestamp) - max(start, sample.timestamp - sample.sample_seconds))


def statistics(samples, name, start, end):
    """Clip interval means to window boundaries, without double-counting overlap."""
    end = max(start, end)
    weighted, spans, cursor = [], [], start
    for sample in sorted(samples, key=lambda s: s.timestamp):
        value = getattr(sample, name)
        if value is None or not sample.sample_seconds or not math.isfinite(value):
            continue
        left, right = max(start, cursor, sample.timestamp - sample.sample_seconds), min(end, sample.timestamp)
        if right > left:
            weighted.append((value, right - left))
            spans.append((left, right))
            cursor = right
    valid = sum(duration for _, duration in weighted)
    gaps, cursor = [], start
    for left, right in spans:
        if left > cursor:
            gaps.append([cursor, left])
        cursor = right
    if cursor < end:
        gaps.append([cursor, end])
    area = sum(value * duration for value, duration in weighted)
    percentile, cumulative = None, 0
    for value, duration in sorted(weighted):
        cumulative += duration
        if cumulative >= valid * .95:
            percentile = value
            break
    return {"valid_seconds": valid, "window_seconds": end - start,
            "coverage": valid / (end - start) if end > start else 0,
            "mean": area / valid if valid else None,
            "peak_interval_mean": max((v for v, _ in weighted), default=None), "p95_time_weighted": percentile,
            "estimated_MB": area if valid and name in ("read_mbps", "write_mbps") else None,
            "points": len(weighted), "gaps": gaps}


def summary(samples, name, start=None, end=None):
    samples = list(samples)
    if not samples:
        return "—"
    start = min(s.timestamp - (s.sample_seconds or 0) for s in samples) if start is None else start
    end = max(s.timestamp for s in samples) if end is None else end
    value = statistics(samples, name, start, end)
    if value["mean"] is None:
        return "— · 有效覆盖 0%"
    text = f"均值 {value['mean']:.2f} / 峰值 {value['peak_interval_mean']:.2f}"
    text += f"\n有效 {value['valid_seconds']:.1f}s · 覆盖 {value['coverage']:.0%}"
    if value["estimated_MB"] is not None:
        text += f" · 区间累计约 {value['estimated_MB']:.2f} MB"
    return text


@dataclass
class ExperimentSession:
    id: str
    identity: ProcessIdentity
    started: float
    baseline_end: float
    transition_seconds: float = 3
    after_seconds: float = 60
    utc_offset: float = 0
    state: str = "baseline"
    marker: float | None = None
    after_start: float | None = None
    after_end: float | None = None
    ended: float | None = None
    label: str = "等待应用"
    baseline_state: dict = field(default_factory=dict)
    baseline_verified: bool = False
    context: dict = field(default_factory=dict)
    observations: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    imported: bool = False

    @property
    def samples(self):
        return [Metrics(**o["metrics"]) for o in self.observations]

    @property
    def before(self):
        return [s for s in self.samples if overlap(s, self.started, self.baseline_end) or self.started <= s.timestamp <= self.baseline_end]

    def windows(self):
        windows = {"before": (self.started, self.baseline_end)}
        if self.marker is not None and self.after_start is not None:
            windows["transition"] = (self.marker, self.after_start)
            windows["after"] = (self.after_start, self.after_end)
        return windows

    def report(self):
        return {phase: {name: statistics(self.samples, name, *window) for name in FIELDS}
                for phase, window in self.windows().items() if phase != "transition"}

    def finish(self, timestamp, reason=""):
        if self.state not in ACTIVE:
            return
        self.ended = timestamp
        if reason and reason not in self.issues:
            self.issues.append(reason)
        self.state = "interrupted" if self.issues else "completed"

    def advance(self, timestamp):
        if self.after_end is not None and timestamp >= self.after_end:
            self.finish(self.after_end)

    def event(self, event):
        if len(self.events) >= 1000:
            if "事件数量达到上限，请开始新会话" not in self.issues:
                self.issues.append("事件数量达到上限，请开始新会话")
            self.finish(event["timestamp"], "事件数量达到上限，请开始新会话")
            return
        self.events.append(copy.deepcopy(event))

    def add(self, metrics, state, errors=None):
        if self.imported or self.state not in ACTIVE:
            return
        if self.observations and metrics.timestamp <= self.observations[-1]["metrics"]["timestamp"]:
            return
        if any(overlap(metrics, *w) or w[0] <= metrics.timestamp <= w[1] for w in self.windows().values()):
            if len(self.observations) >= 4096:
                self.finish(metrics.timestamp, "样本数量达到上限")
                return
            self.observations.append({"metrics": asdict(metrics), "state": copy.deepcopy(state), "metric_errors": list(errors or [])})
        if self.marker is None:
            if metrics.timestamp >= self.baseline_end:
                self.state = "awaiting_apply"
        elif metrics.timestamp >= self.after_end:
            self.finish(self.after_end)
        elif metrics.timestamp >= self.after_start:
            self.state = "collecting"

    def to_dict(self):
        data = asdict(self)
        data.pop("imported")
        return {"schema": 1, "session": data, "statistics": self.report(),
                "times_utc_approx": {name: datetime.fromtimestamp(getattr(self, name) + self.utc_offset, timezone.utc).isoformat()
                                     if getattr(self, name) is not None else None
                                     for name in ("started", "baseline_end", "marker", "after_start", "after_end", "ended")},
                "units": {"MB": 1_000_000, "CPU": "machine-normalized percent",
                          "peak": "maximum sample-interval mean", "totals": "interval-weighted estimate",
                          "UTC": "fixed offset captured when recording began; approximate"}}


class History:
    def __init__(self):
        self.samples, self.observed, self.last_state = {}, {}, {}
        self.contexts = {}
        self.sessions: dict[str, ExperimentSession] = {}
        self.latest = {}
        self.warning = ""

    @property
    def experiments(self):
        return {identity: self.sessions[key] for identity, key in self.latest.items() if key in self.sessions}

    def begin(self, identity, timestamp, *, baseline_seconds=60, transition_seconds=3, after_seconds=60, utc_offset=None, supersede=True):
        if len(self.sessions) >= 64:
            self.warning = "已有 64 个实验会话；请先保存并移除旧会话，再开始记录。"
            return None
        for value, lower in ((baseline_seconds, 1), (transition_seconds, 0), (after_seconds, 1)):
            if type(value) not in (float, int) or not math.isfinite(value) or not lower <= value <= 600:
                raise ValueError("观察时长无效")
        previous = self.experiments.get(identity)
        if previous and not previous.imported and supersede:
            previous.advance(timestamp)
            previous.event({"kind": "new_session", "timestamp": timestamp, "reason": "开始新的基线记录"})
            previous.finish(timestamp, "开始了下一次实验")
        session = ExperimentSession(uuid.uuid4().hex, identity, timestamp, timestamp + baseline_seconds,
                                    transition_seconds, after_seconds,
                                    time.time() - time.monotonic() if utc_offset is None else utc_offset)
        session.context = copy.deepcopy(self.contexts.get(identity, {"app_version": __version__}))
        self.sessions[session.id] = session
        self.latest[identity] = session.id
        self.warning = ""
        return session

    def add(self, identity, metrics, state=None, errors=None):
        values = self.samples.setdefault(identity, deque(maxlen=1200))
        if values and metrics.timestamp <= values[-1].timestamp:
            return
        state = plain(state or {})
        old = self.last_state.get(identity)
        session = self.experiments.get(identity)
        if old and state and state != old and session and not session.imported and session.marker is not None:
            session.event({"kind": "observed_change", "timestamp": metrics.timestamp, "before": old, "after": state})
            if session.state in ACTIVE and (session.after_end is None or metrics.timestamp <= session.after_end) and "观察到设置变化" not in session.issues:
                session.issues.append("观察到设置变化")
        self.last_state[identity] = state
        values.append(metrics)
        observations = self.observed.setdefault(identity, deque(maxlen=1200))
        observations.append({"metrics": asdict(metrics), "state": state, "metric_errors": list(errors or [])})
        while len(values) > 1 and values[1].timestamp < metrics.timestamp - 60:
            values.popleft()
            observations.popleft()
        if session:
            session.add(metrics, state, errors)

    def operation(self, event):
        event = plain(event)
        identity = ProcessIdentity(**event["identity"])
        timestamp, kind = event["timestamp"], event["kind"]
        session = self.experiments.get(identity)
        if session and not session.imported:
            session.advance(timestamp)
        if kind == "apply":
            if session and not session.imported and session.marker is not None:
                session.event({**event, "kind": "next_apply"})
                session.finish(timestamp, "观察结束前再次应用策略")
            if not session or session.imported or session.marker is not None or session.state not in ACTIVE:
                session = self.begin(identity, timestamp - 60, utc_offset=event.get("utc_offset"), supersede=False)
                if session is None:
                    self.latest.pop(identity, None)
                    return None
                session.observations = copy.deepcopy([o for o in self.observed.get(identity, [])
                    if o["metrics"]["timestamp"] >= session.started])
            if timestamp < session.baseline_end:
                session.baseline_end = timestamp
                session.issues.append("未完成计划的基线观察时长")
            session.marker = timestamp
            session.label = event["result"]["status"]
            session.context = copy.deepcopy(event.get("context", {}))
            session.context["requested_policy"] = event.get("policy")
            session.context["application_result"] = event["result"]
            session.baseline_state = event.get("before", {})
            baseline = [o["state"] for o in session.observations if o["metrics"]["timestamp"] <= session.baseline_end]
            session.baseline_verified = bool(baseline and session.baseline_state and
                session.baseline_state.get("priority") is not None and session.baseline_state.get("affinity") and
                session.baseline_state.get("eco") is not None and
                not session.baseline_state.get("errors") and all(s == session.baseline_state for s in baseline))
            if not session.baseline_verified:
                session.issues.append("基线设置缺失、无法读取或发生变化")
            if event["result"]["ok"] and any(not op.get("untouched") for op in event["result"]["operations"]):
                session.after_start = event.get("ended", timestamp) + session.transition_seconds
                session.after_end = session.after_start + session.after_seconds
                session.state = "transition"
            else:
                session.state = "partial_failure" if not event["result"]["ok"] else "not_applied"
                session.ended = event.get("ended", timestamp)
        elif kind == "restore" and (not session or session.imported):
            session = self.begin(identity, timestamp, baseline_seconds=1, transition_seconds=0, after_seconds=1,
                                 utc_offset=event.get("utc_offset"))
            if session is None:
                return None
            session.baseline_end = timestamp
            session.context = event.get("context", {})
            session.label = "仅恢复记录：" + event["result"]["status"]
            session.finish(timestamp, "没有本次应用的基线；仅保存恢复事件")
        if session and not session.imported:
            if kind == "maintenance" and session.state not in ACTIVE and event["result"]["ok"] and not any(op["changed"] for op in event["result"]["operations"]):
                return session
            session.event(event)
            if kind == "restore":
                session.finish(timestamp, "观察结束前恢复了原设置")
            if kind == "maintenance":
                if event.get("before") != self.last_state.get(identity) or any(op["changed"] for op in event["result"]["operations"]):
                    if session.state in ACTIVE and "维护期间设置被重置或改变" not in session.issues:
                        session.issues.append("维护期间设置被重置或改变")
                if not event["result"]["ok"]:
                    session.finish(timestamp, "维护失败")
            self.last_state[identity] = event.get("after", {})
        return session

    def mark(self, identity, timestamp, label):
        return self.operation({"identity": asdict(identity), "kind": "apply", "timestamp": timestamp,
            "ended": timestamp, "result": {"ok": True, "status": label,
            "operations": [{"field": "unknown", "ok": True, "changed": False}]}, "context": {"synthetic": True}})

    def interrupt(self, identity, timestamp, reason, kind="interrupted"):
        session = self.experiments.get(identity)
        if session and not session.imported:
            session.advance(timestamp)
            session.event({"kind": kind, "timestamp": timestamp, "reason": reason})
            session.finish(timestamp, reason)

    def consume(self, event):
        if event["kind"] == "tick":
            for session in self.sessions.values():
                if not session.imported:
                    session.advance(event["timestamp"])
                    if session.state == "baseline" and event["timestamp"] >= session.baseline_end:
                        session.state = "awaiting_apply"
            return None
        if event["kind"] == "sample":
            identity = ProcessIdentity(**event["identity"])
            if event.get("context"):
                self.contexts[identity] = plain(event["context"])
                session = self.experiments.get(identity)
                if session and session.marker is None and session.state in ACTIVE:
                    session.context = copy.deepcopy(self.contexts[identity])
            self.add(identity, Metrics(**event["metrics"]), event["state"], event.get("metric_errors"))
            return None
        if event["kind"] in ("apply", "restore", "maintenance"):
            return self.operation(event)
        if event.get("identity"):
            self.interrupt(ProcessIdentity(**event["identity"]), event["timestamp"], event["reason"], event["kind"])
        else:
            for identity in list(self.latest):
                self.interrupt(identity, event["timestamp"], event["reason"], event["kind"])
        return None

    def retain(self, identities):
        # Removing rolling history never deletes experiment sessions.
        for mapping in (self.samples, self.observed, self.last_state, self.contexts):
            for identity in list(mapping):
                if identity not in identities:
                    del mapping[identity]

    def remove(self, session_id):
        session = self.sessions[session_id]
        if not session.imported and session.state in ACTIVE:
            raise ValueError("请先结束当前实验")
        del self.sessions[session_id]
        if self.latest.get(session.identity) == session_id:
            self.latest.pop(session.identity)
        self.warning = ""

    def load(self, path):
        path = Path(path)
        if path.stat().st_size > 8_000_000:
            raise ValueError("实验文件过大")
        data = json.loads(path.read_text(encoding="utf-8"))
        plain(data)
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data.get("schema") != 1:
            raise ValueError("实验文件版本不支持")
        raw = copy.deepcopy(data["session"])
        identity = ProcessIdentity(**raw.pop("identity"))
        process_name(identity.name)
        if type(identity.pid) is not int or identity.pid <= 0 or not math.isfinite(identity.created) or identity.created <= 0:
            raise ValueError("实例身份无效")
        session = ExperimentSession(identity=identity, **raw)
        if not isinstance(session.id, str) or len(session.id) != 32 or any(c not in "0123456789abcdef" for c in session.id):
            raise ValueError("实验 ID 无效")
        if session.state not in STATE_LABELS or not isinstance(session.baseline_verified, bool) or not isinstance(session.label, str):
            raise ValueError("实验状态无效")
        for name in ("started", "baseline_end", "transition_seconds", "after_seconds", "utc_offset", "marker", "after_start", "after_end", "ended"):
            value = getattr(session, name)
            if value is None and name in ("marker", "after_start", "after_end", "ended"):
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("实验时间无效")
        if (session.baseline_end < session.started or not 0 <= session.transition_seconds <= 600 or
                session.baseline_end - session.started > 600 or
                not 1 <= session.after_seconds <= 600 or (session.after_start is None) != (session.after_end is None) or
                (session.marker is not None and session.marker < session.baseline_end) or
                (session.after_start is not None and (session.marker is None or session.after_start < session.marker or
                 abs(session.after_end - session.after_start - session.after_seconds) > .001))):
            raise ValueError("观察窗口无效")
        if not isinstance(session.observations, list) or len(session.observations) > 4096 or not isinstance(session.events, list) or len(session.events) > 1000:
            raise ValueError("实验记录数量无效")
        if not isinstance(session.context, dict) or not isinstance(session.baseline_state, dict) or not isinstance(session.issues, list) or any(not isinstance(s, str) for s in session.issues):
            raise ValueError("实验上下文无效")
        validate_state(session.baseline_state)
        if session.context.get("requested_policy") is not None:
            Policy.parse(session.context["requested_policy"])
        target = session.context.get("target", {})
        if not isinstance(target, dict) or any(target.get(key) is not None and not isinstance(target[key], str) for key in ("version", "executable_path")):
            raise ValueError("目标文件信息无效")
        last = -math.inf
        for observation in session.observations:
            sample = Metrics(**observation["metrics"])
            if sample.timestamp <= last or not isinstance(observation["state"], dict):
                raise ValueError("样本顺序无效")
            validate_state(observation["state"])
            if not isinstance(observation.get("metric_errors", []), list) or any(not isinstance(s, str) for s in observation.get("metric_errors", [])):
                raise ValueError("采样错误记录无效")
            for value in asdict(sample).values():
                if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                    raise ValueError("样本数值无效")
            last = sample.timestamp
        for event in session.events:
            if not isinstance(event, dict) or not isinstance(event.get("kind"), str) or type(event.get("timestamp")) not in (float, int):
                raise ValueError("事件无效")
            if not isinstance(event.get("reason", ""), str):
                raise ValueError("事件说明无效")
            for name in ("before", "after"):
                if name in event:
                    validate_state(event[name])
            result = event.get("result", {})
            if not isinstance(result, dict) or not isinstance(result.get("status", ""), str) or not isinstance(result.get("operations", []), list):
                raise ValueError("操作结果无效")
            for op in result.get("operations", []):
                if not isinstance(op, dict) or not isinstance(op.get("field"), str) or not isinstance(op.get("message", ""), str):
                    raise ValueError("字段结果无效")
                for name in ("requested", "original", "actual"):
                    validate_setting(op["field"], op.get(name))
        for stamp in [session.started, session.baseline_end, session.after_end or session.baseline_end] + [e["timestamp"] for e in session.events]:
            datetime.fromtimestamp(stamp + session.utc_offset, timezone.utc)
        if session.id in self.sessions:
            if self.sessions[session.id].to_dict() != session.to_dict():
                raise ValueError("已存在同 ID 的不同实验；请先移除旧快照")
            return self.sessions[session.id]
        if len(self.sessions) >= 64:
            raise ValueError("实验数量达到 64，请先保存并移除旧会话")
        session.imported = True
        self.sessions[session.id] = session
        return session


def atomic_write(path, write, *, encoding="utf-8"):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="experiment-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_session(session, path):
    atomic_write(path, lambda stream: json.dump(session.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False))


def export_csv(session, path):
    def write(stream):
        headers = ["row_type", "session_id", "process", "pid", "process_created_unix", "sample_utc_approx", "phase",
                   "operation_result", "sample_seconds", "window_overlap_seconds", "cpu_percent_machine", "read_MB_s",
                   "write_MB_s", "ram_MB", "total_read_GB", "total_write_GB", "read_count", "write_count", "context_json", "event_json", "metric_errors_json"]
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        def literal(value):
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
        base = {"session_id": session.id, "process": literal(session.identity.name), "pid": session.identity.pid,
                "process_created_unix": session.identity.created, "operation_result": literal(session.label)}
        metadata = session.to_dict()
        metadata["session"].pop("observations")
        metadata["session"].pop("events")
        writer.writerow({**base, "row_type": "metadata", "context_json": json.dumps(metadata, ensure_ascii=False)})
        for observation in session.observations:
            sample = Metrics(**observation["metrics"])
            for phase, window in session.windows().items():
                duration = overlap(sample, *window)
                if not duration and not (sample.sample_seconds is None and window[0] < sample.timestamp <= window[1]):
                    continue
                writer.writerow({**base, "row_type": "sample", "phase": phase,
                    "sample_utc_approx": datetime.fromtimestamp(sample.timestamp + session.utc_offset, timezone.utc).isoformat(),
                    "sample_seconds": sample.sample_seconds, "window_overlap_seconds": duration,
                    "cpu_percent_machine": sample.cpu_percent, "read_MB_s": sample.read_mbps, "write_MB_s": sample.write_mbps,
                    "ram_MB": sample.ram_mb, "total_read_GB": sample.total_read_gb, "total_write_GB": sample.total_write_gb,
                    "read_count": sample.read_count, "write_count": sample.write_count,
                    "metric_errors_json": json.dumps(observation.get("metric_errors", []), ensure_ascii=False)})
        for event in session.events:
            writer.writerow({**base, "row_type": "event", "phase": literal(event["kind"]),
                "sample_utc_approx": datetime.fromtimestamp(event["timestamp"] + session.utc_offset, timezone.utc).isoformat(),
                "event_json": json.dumps(event, ensure_ascii=False)})
    atomic_write(path, write, encoding="utf-8-sig")
