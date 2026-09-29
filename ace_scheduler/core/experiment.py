"""GUI-independent experiment sessions, fixed windows and portable records."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
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
from .disk_recording import DiskStore, FIELDS as DISK_FIELDS, UNITS as DISK_UNITS, disk_statistics, validate_rows
from .frame_recording import make_attachment, frame_report, portable_frames, restore_frames

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
    scene_name: str = ""
    scene_notes: str = ""
    clock: dict = field(default_factory=lambda: {"source": "legacy_fixed_utc_offset", "anchors": [], "breaks": []})
    disk_selection: dict | None = None
    frame_attachment: dict | None = None

    @property
    def samples(self):
        samples = [Metrics(**o["metrics"]) for o in self.observations]
        for index, sample in enumerate(samples):
            if any(sample.timestamp - (sample.sample_seconds or 0) < b["timestamp"] and sample.timestamp > b.get("started", b["timestamp"] - .000001)
                   for b in self.clock["breaks"]):
                samples[index] = replace(sample, cpu_percent=None, read_mbps=None, write_mbps=None, sample_seconds=None)
        return samples

    def set_scene(self, name, notes):
        if self.imported:
            raise ValueError("已打开的实验只读")
        if not isinstance(name, str) or not isinstance(notes, str) or len(name) > 200 or len(notes) > 4000:
            raise ValueError("场景名称最多 200 字，备注最多 4000 字")
        self.scene_name, self.scene_notes = name, notes

    def scene_marker(self, timestamp, label):
        if self.imported or self.state not in ACTIVE:
            raise ValueError("请选择正在记录的本机会话")
        if not isinstance(label, str) or not label.strip() or len(label) > 200 or not math.isfinite(timestamp) or timestamp < self.started:
            raise ValueError("场景标记无效")
        self.event({"kind": "scene_marker", "timestamp": timestamp, "reason": label.strip()})

    def disk_rows(self):
        store = getattr(self, "_disk_store", None)
        if not store or not self.disk_selection:
            return []
        cutoff = self.disk_selection.get("cutoff")
        if self.ended is not None:
            cutoff = min(cutoff, self.ended) if cutoff is not None else self.ended
        rows = store.rows(self.disk_selection["device"]["id"], list(self.windows().values()), cutoff)
        for row in rows:
            if any(row["timestamp"] - (row["seconds"] or 0) < b["timestamp"] and row["timestamp"] > b.get("started", b["timestamp"] - .000001) for b in self.clock["breaks"]):
                row["values"] = dict.fromkeys(DISK_FIELDS)
                row["seconds"], row["gap"] = None, "clock_break"
        return rows

    def disk_report(self):
        rows = self.disk_rows()
        return {phase: {name: disk_statistics(rows, name, *window) for name in DISK_FIELDS}
                for phase, window in self.windows().items() if phase != "transition"} if self.disk_selection else {}

    def frame_report(self):
        return frame_report(self)

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
        return {"schema": 3, "session": data, "statistics": self.report(),
                "frame_recording": portable_frames(self),
                "disk_recording": {"rows": self.disk_rows(), "statistics": self.disk_report(), "units": DISK_UNITS,
                                   "scope": "physical device total, not process-attributed"},
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
        self.disk_store = None
        self.disk_devices = {}
        self.selected_disk = None
        self.last_clock = None

    def disk_packet(self, packet):
        if packet.get("devices") is not None:
            devices = {d["id"]: plain(d) for d in packet["devices"]}
            removed = set(self.disk_devices) - set(devices)
            for key in removed:
                self.disk_stop("磁盘枚举已变化，原选择失效；请重新选择并开始新会话", packet["timestamp"], key)
            self.disk_devices = devices
            if self.selected_disk not in devices:
                self.selected_disk = None
        if self.disk_store is None:
            self.disk_store = DiskStore()
        for row in packet["rows"]:
            self.disk_store.append(row["device_id"], [row])

    def disk_stop(self, reason, timestamp, device_id=None):
        for session in self.sessions.values():
            selection = session.disk_selection
            if session.imported or session.state not in ACTIVE or not selection or selection.get("cutoff") is not None:
                continue
            if device_id is not None and selection["device"]["id"] != device_id:
                continue
            selection["cutoff"] = timestamp
            selection["stop_reason"] = reason
            session.event({"kind": "disk_stopped", "timestamp": timestamp, "reason": reason})
        if device_id is None or self.selected_disk == device_id:
            self.selected_disk = None

    def clock_update(self, packet):
        anchor = plain(packet["anchor"])
        previous = self.last_clock
        self.last_clock = anchor
        for session in self.sessions.values():
            if session.imported or session.state not in ACTIVE:
                continue
            if previous and anchor["monotonic"] <= previous["monotonic"]:
                session.event({"kind": "clock_break", "timestamp": previous["monotonic"],
                               "reason": "monotonic_reset", "observed_anchor": anchor})
                session.finish(previous["monotonic"], "单调时钟回退；停止会话以避免跨时间轴连接")
                continue
            anchors = session.clock["anchors"]
            reasons = packet["breaks"]
            if reasons:
                point = {"timestamp": anchor["monotonic"], "started": previous["monotonic"] if previous else anchor["monotonic"], "reasons": reasons}
                if len(session.clock["breaks"]) < 256:
                    session.clock["breaks"].append(point)
                session.event({"kind": "clock_break", "timestamp": anchor["monotonic"], "reason": ", ".join(reasons)})
                if "时钟或采样存在断点，请核对时间对齐" not in session.issues:
                    session.issues.append("时钟或采样存在断点，请核对时间对齐")
            if not anchors or reasons or anchor["monotonic"] - anchors[-1]["monotonic"] >= 30:
                if len(anchors) < 256:
                    anchors.append(copy.deepcopy(anchor))
                    session.clock["source"] = "paired_local_clocks"
                else:
                    session.finish(anchor["monotonic"], "时钟锚点达到 256 条上限")

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
        if self.last_clock:
            session.clock = {"source": "paired_local_clocks", "anchors": [copy.deepcopy(self.last_clock)], "breaks": []}
        if self.selected_disk in self.disk_devices:
            session.disk_selection = {"device": copy.deepcopy(self.disk_devices[self.selected_disk]), "cutoff": None,
                                      "selected_at": timestamp, "stop_reason": ""}
            session._disk_store = self.disk_store
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

    def attach_frames(self, session, store, stream, **options):
        """Create a read-only analysis copy, preserving the original experiment."""
        if session.state in ACTIVE and not session.imported:
            raise ValueError("请先结束记录再导入帧文件")
        if len(self.sessions) >= 64:
            raise ValueError("实验数量达到 64，请先保存并移除旧会话")
        attachment = make_attachment(session, store, stream, **options)
        stores = {id(s._frame_store): s._frame_store for s in self.sessions.values() if s.frame_attachment}
        stores[id(store)] = store
        if sum(s.count for s in stores.values()) > 300_000:
            raise ValueError("本次运行帧附件达到 300000 条上限；请先保存并移除旧分析")
        clone = copy.copy(session)
        for name in ("context", "observations", "events", "issues", "baseline_state", "clock", "disk_selection"):
            setattr(clone, name, copy.deepcopy(getattr(session, name)))
        clone.id, clone.imported = uuid.uuid4().hex, True
        clone.context["frame_analysis_source_session"] = session.context.get("frame_analysis_source_session", session.id)
        clone.frame_attachment = plain(attachment)
        clone._frame_store, clone._frame_stream = store, stream
        self.sessions[clone.id] = clone
        return clone

    def load(self, path):
        path = Path(path)
        if path.stat().st_size > 24_000_000:
            raise ValueError("实验文件过大")
        with path.open("rb") as stream:
            payload = stream.read(24_000_001)
        if len(payload) > 24_000_000:
            raise ValueError("实验文件过大")
        data = json.loads(payload.decode("utf-8"))
        plain(data)
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data.get("schema") not in (1, 2, 3):
            raise ValueError("实验文件版本不支持")
        if data["schema"] == 1 and len(payload) > 8_000_000:
            raise ValueError("旧版实验文件过大")
        if not isinstance(data.get("session"), dict):
            raise ValueError("实验会话容器无效")
        raw = copy.deepcopy(data["session"])
        identity = ProcessIdentity(**raw.pop("identity"))
        process_name(identity.name)
        if type(identity.pid) is not int or identity.pid <= 0 or not math.isfinite(identity.created) or identity.created <= 0:
            raise ValueError("实例身份无效")
        session = ExperimentSession(identity=identity, **raw)
        if data["schema"] < 3 and (session.frame_attachment is not None or data.get("frame_recording") is not None):
            raise ValueError("旧版会话不可包含帧附件")
        if data["schema"] == 1:
            session.clock = {"source": "legacy_fixed_utc_offset", "anchors": [], "breaks": []}
            session.disk_selection = None
        validate_extensions(session)
        recording = data.get("disk_recording", {})
        if not isinstance(recording, dict):
            raise ValueError("磁盘记录容器无效")
        disk_rows = recording.get("rows", []) if data["schema"] >= 2 else []
        validate_rows(disk_rows)
        if disk_rows and not session.disk_selection:
            raise ValueError("磁盘数据缺少设备选择")
        if disk_rows and any(row.get("device_id") != session.disk_selection["device"]["id"] for row in disk_rows):
            raise ValueError("磁盘样本与所选设备不一致")
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
        restore_frames(session, data.get("frame_recording"))
        stores = {id(s._frame_store): s._frame_store for s in self.sessions.values() if s.frame_attachment}
        if session.id not in self.sessions and sum(s.count for s in stores.values()) + (session.frame_attachment["stream"]["rows"] if session.frame_attachment else 0) > 300_000:
            raise ValueError("本次运行帧附件达到 300000 条上限")
        temporary_store = None
        if disk_rows:
            temporary_store = DiskStore()
            try:
                temporary_store.append(session.disk_selection["device"]["id"], disk_rows)
                session._disk_store = temporary_store
                if len(session.disk_rows()) != len(disk_rows):
                    raise ValueError("磁盘数据超出会话窗口")
            except Exception:
                temporary_store.close()
                raise
        if session.id in self.sessions:
            if self.sessions[session.id].to_dict() != session.to_dict():
                if temporary_store:
                    temporary_store.close()
                raise ValueError("已存在同 ID 的不同实验；请先移除旧快照")
            if temporary_store:
                temporary_store.close()
            return self.sessions[session.id]
        if len(self.sessions) >= 64:
            if temporary_store:
                temporary_store.close()
            raise ValueError("实验数量达到 64，请先保存并移除旧会话")
        session.imported = True
        self.sessions[session.id] = session
        return session


def validate_extensions(session):
    if not isinstance(session.scene_name, str) or len(session.scene_name) > 200 or not isinstance(session.scene_notes, str) or len(session.scene_notes) > 4000:
        raise ValueError("场景内容无效")
    clock = session.clock
    if not isinstance(clock, dict) or clock.get("source") not in ("legacy_fixed_utc_offset", "paired_local_clocks"):
        raise ValueError("时钟来源无效")
    for field in ("anchors", "breaks"):
        if not isinstance(clock.get(field), list) or len(clock[field]) > 256:
            raise ValueError("时钟记录数量无效")
    if clock["source"] == "legacy_fixed_utc_offset" and (clock["anchors"] or clock["breaks"]):
        raise ValueError("旧版时钟不可伪造精确锚点")
    previous = -math.inf
    for anchor in clock["anchors"]:
        if not isinstance(anchor, dict):
            raise ValueError("时钟锚点无效")
        for key in ("monotonic", "utc_unix", "qpc", "qpc_frequency", "pairing_error_seconds"):
            v = anchor.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                raise ValueError("时钟锚点数值无效")
        if anchor["qpc_frequency"] <= 0 or anchor["monotonic"] <= previous:
            raise ValueError("时钟锚点顺序无效")
        awake = anchor.get("awake_seconds")
        if awake is not None and (type(awake) not in (int, float) or not math.isfinite(awake) or awake < 0):
            raise ValueError("活动时钟无效")
        previous = anchor["monotonic"]
    for point in clock["breaks"]:
        if not isinstance(point, dict) or type(point.get("timestamp")) not in (int, float) or not math.isfinite(point["timestamp"]):
            raise ValueError("时钟断点无效")
        if not isinstance(point.get("reasons"), list) or any(not isinstance(reason, str) for reason in point["reasons"]):
            raise ValueError("时钟断点原因无效")
        if type(point.get("started", point["timestamp"])) not in (int, float) or not math.isfinite(point.get("started", point["timestamp"])) or point.get("started", point["timestamp"]) > point["timestamp"]:
            raise ValueError("时钟断点区间无效")
    selection = session.disk_selection
    if selection is not None:
        if not isinstance(selection, dict) or not isinstance(selection.get("device"), dict):
            raise ValueError("磁盘选择无效")
        device = selection["device"]
        if not isinstance(device.get("id"), str) or not device["id"] or len(device["id"]) > 128 or not isinstance(device.get("name"), str):
            raise ValueError("磁盘身份无效")
        if type(device.get("number")) is not int or device["number"] < 0 or not isinstance(device.get("instance"), str):
            raise ValueError("磁盘编号无效")
        for key in ("volumes", "uncertainty"):
            if not isinstance(device.get(key), list) or len(device[key]) > 128 or any(not isinstance(v, str) for v in device[key]):
                raise ValueError("磁盘映射信息无效")
        if not isinstance(selection.get("stop_reason", ""), str):
            raise ValueError("磁盘停止原因无效")
        if len(json.dumps(selection, ensure_ascii=False)) > 16384:
            raise ValueError("磁盘身份过大")
        for field in ("selected_at", "cutoff"):
            value = selection.get(field)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError("磁盘选择时间无效")


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
    def write(stream):
        json.dump(session.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False)
        if stream.tell() > 24_000_000:
            raise ValueError("实验超过 24 MB 保存上限，原文件保留；请导出 CSV")
    atomic_write(path, write)


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
        disk_rows = metadata["disk_recording"].pop("rows")
        frames = metadata.get("frame_recording")
        frame_rows = frames.pop("rows") if frames else []
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
        for row in disk_rows:
            writer.writerow({**base, "row_type": "disk_sample", "sample_seconds": row["seconds"],
                             "sample_utc_approx": datetime.fromtimestamp(row["timestamp"] + session.utc_offset, timezone.utc).isoformat(),
                             "event_json": json.dumps(row, ensure_ascii=False)})
        for row in frame_rows:
            writer.writerow({**base, "row_type": "frame_sample", "event_json": json.dumps(row, ensure_ascii=False)})
    atomic_write(path, write, encoding="utf-8-sig")
