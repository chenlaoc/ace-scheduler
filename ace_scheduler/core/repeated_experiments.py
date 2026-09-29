"""Offline paired-round summaries; every round gets equal weight, never pooled frames."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import tempfile

from .experiment import History, atomic_write

METRICS = {
    "frame.cpu.p95": ("CPU 帧起点间隔 P95", "ms", "frame", "cpu_frame_interval_ms", "p95_ms"),
    "frame.cpu.median": ("CPU 帧起点间隔中位数", "ms", "frame", "cpu_frame_interval_ms", "median_ms"),
    "frame.cpu.p99": ("CPU 帧起点间隔 P99", "ms", "frame", "cpu_frame_interval_ms", "p99_ms"),
    "frame.cpu.long": ("CPU 长帧比例", "比例", "frame", "cpu_frame_interval_ms", "long_frame_ratio"),
    "frame.display.p95": ("显示驻留时长 P95", "ms", "frame", "display_residency_ms", "p95_ms"),
    "frame.display.median": ("显示驻留时长中位数", "ms", "frame", "display_residency_ms", "median_ms"),
    "frame.display.p99": ("显示驻留时长 P99", "ms", "frame", "display_residency_ms", "p99_ms"),
    "frame.display.long": ("显示长帧比例", "比例", "frame", "display_residency_ms", "long_frame_ratio"),
    "process.cpu": ("进程 CPU 均值", "%（差值为百分点）", "process", "cpu_percent", "mean"),
    "process.read": ("进程读取均值", "MB/s", "process", "read_mbps", "mean"),
    "process.write": ("进程写入均值", "MB/s", "process", "write_mbps", "mean"),
    "disk.read": ("物理盘读取均值", "bytes/s", "disk", "read_B_s", "mean"),
    "disk.write": ("物理盘写入均值", "bytes/s", "disk", "write_B_s", "mean"),
    "disk.read_iops": ("物理盘读 IOPS", "次/s", "disk", "read_iops", "mean"),
    "disk.write_iops": ("物理盘写 IOPS", "次/s", "disk", "write_iops", "mean"),
    "disk.read_latency": ("物理盘请求加权读延迟", "s/请求", "disk", "read_latency_s", "mean"),
    "disk.write_latency": ("物理盘请求加权写延迟", "s/请求", "disk", "write_latency_s", "mean"),
    "disk.queue": ("物理盘平均队列", "请求", "disk", "queue_mean", "mean"),
}
DECLARATIONS = ("scene", "hardware", "application_version", "conditions")
MAX_BUNDLE_BYTES = 64 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def defaults(session):
    return {"scene": session.scene_name, "hardware": "",
            "application_version": session.context.get("target", {}).get("version") or "",
            "conditions": session.scene_notes}


def validate_declarations(value):
    if not isinstance(value, dict) or set(value) != set(DECLARATIONS):
        raise ValueError("每轮需要场景、硬件、应用版本和条件声明")
    if any(not isinstance(v, str) or len(v) > 4000 for v in value.values()):
        raise ValueError("每项声明必须为最多 4000 字的文本")
    return {key: value[key].strip() for key in DECLARATIONS}


def freeze_round(session):
    """Independent snapshot; changes to a live History cannot alter an open study."""
    data = session.to_dict()
    if session.state in ("baseline", "awaiting_apply", "transition", "collecting"):
        raise ValueError("请先结束记录，再加入多轮汇总")
    return data


class RepeatedStudy:
    def __init__(self, sessions=()):
        self.records = {}
        self.declarations = {}
        self.selected = []
        self.metric = "frame.cpu.p95"
        self.minimum_coverage = .95
        self.minimum_pairs = 3
        frame_rows = 0
        for session in sessions:
            if len(self.records) >= 64:
                raise ValueError("最多 64 轮")
            if session.id in self.records:
                raise ValueError("会话 ID 重复")
            frame_rows += session.frame_attachment['stream']['rows'] if session.frame_attachment else 0
            if frame_rows > 300_000:
                raise ValueError("多轮项目原始帧总数超过 300000 条；请减少轮次")
            self.records[session.id] = freeze_round(session)
            self.declarations[session.id] = defaults(session)
            self.selected.append(session.id)

    def validate(self):
        if self.metric not in METRICS:
            raise ValueError("未知汇总指标")
        if type(self.minimum_coverage) not in (int, float) or not math.isfinite(self.minimum_coverage) or not 0 < self.minimum_coverage <= 1:
            raise ValueError("覆盖率门槛必须在 0 到 100% 之间")
        if type(self.minimum_pairs) is not int or not 1 <= self.minimum_pairs <= 64:
            raise ValueError("有效轮数门槛必须为 1 到 64")
        if not isinstance(self.selected, list) or len(self.selected) > 64 or any(not isinstance(key, str) for key in self.selected):
            raise ValueError("轮次选择无效")
        if len(set(self.selected)) != len(self.selected) or set(self.selected) - self.records.keys():
            raise ValueError("轮次选择重复或不存在")
        if set(self.declarations) != set(self.records):
            raise ValueError("轮次声明与原始会话不匹配")
        for value in self.declarations.values():
            validate_declarations(value)

    def report(self):
        self.validate()
        _, unit, family, field, statistic = METRICS[self.metric]
        rows, groups = [], {}
        for key in self.selected:
            data = self.records[key]
            s = data["session"]
            declaration = validate_declarations(self.declarations[key])
            context = s["context"]
            attachment = s.get("frame_attachment")
            origin = "synthetic" if context.get("synthetic") is True else "observed"
            issues = []
            for name, title in zip(DECLARATIONS, ("场景", "硬件", "应用版本", "条件")):
                if not declaration[name]:
                    issues.append(f"缺少{title}声明")
            if s["state"] != "completed":
                issues.append("轮次未完成或已中断")
            if s["issues"]:
                issues.append("会话存在异常：" + "；".join(s["issues"]))
            if not s["baseline_verified"]:
                issues.append("基线实际设置未核验")
            applied = [e for e in s["events"] if e["kind"] == "apply"]
            operation = applied[0] if len(applied) == 1 else {}
            actual_after = operation.get('after', {})
            if (not actual_after or actual_after.get('errors') or
                    any(actual_after.get(name) is None for name in ('priority', 'affinity', 'eco'))):
                issues.append("缺少完整的应用后实际设置")
            if operation.get('before') != s['baseline_state'] or operation.get('policy') != context.get('requested_policy'):
                issues.append("基线或策略与应用事件不一致")
            result = operation.get("result", {})
            operations = [op for op in result.get("operations", []) if not op.get("untouched")]
            if (result.get("ok") is not True or not operations or
                    any(op.get("ok") is not True or op.get("verified") is not True or op.get("recovery_pending") for op in operations)):
                issues.append("策略应用缺少完整成功回读")
            if not context.get("requested_policy"):
                issues.append("缺少策略记录")
            if s.get("after_start") is None or s.get("after_end") is None:
                issues.append("缺少配对的实验段")
            elif s["ended"] is None or s["ended"] < s["after_end"]:
                issues.append("实验段提前结束")
            if s["clock"]["breaks"]:
                issues.append("存在时钟断点")
            disk = s.get("disk_selection")
            device = disk["device"] if disk else None
            disk_key = {k: device.get(k) for k in ("fingerprint", "number", "name", "volumes")} if device else None
            effective_coverage = self.minimum_coverage
            frame_basis = None
            if family == "frame":
                if not attachment:
                    issues.append("缺少外部帧记录")
                else:
                    a = attachment["alignment"]
                    effective_coverage = max(effective_coverage, attachment["minimum_coverage"])
                    frame_basis = {"version": attachment["source"]["version_declared"],
                                   "runtime": attachment["stream"]["runtime"],
                                   "time_column": attachment["source"]["time_column"],
                                   "alignment": a["mode"], "long_frame_ms": attachment["long_frame_threshold_ms"],
                                   "minimum_coverage": effective_coverage}
                    if a["mode"] != "qpc":
                        issues.append("帧记录仅手动近似对齐")
                    log = attachment["source"]["log"]
                    if log is None:
                        issues.append("ETW 丢失情况未知")
                    elif log["events_lost_reported"]:
                        issues.append(f"ETW 丢失 {log['events_lost_reported']} 个事件")
                    elif log.get("capture_complete") is not True:
                        issues.append("采集日志不完整，ETW 丢失情况未知")
            if family == "disk":
                if not device or not device.get("fingerprint"):
                    issues.append("缺少可核验的物理盘身份")
                if disk and disk.get("stop_reason"):
                    issues.append("磁盘记录提前停止")
            basis = {**declaration, "origin": origin, "process": s["identity"]["name"].casefold(),
                     "policy": context.get("requested_policy"), "baseline_actual": s["baseline_state"],
                     "after_actual": operation.get("after"), "topology": context.get("topology"),
                     "sampling": context.get("sampling"), "scheduler_version": context.get("app_version"),
                     "disk": disk_key, "metric": self.metric, "frame_basis": frame_basis,
                     "window_seconds": [s["baseline_end"] - s["started"],
                                        s["after_end"] - s["after_start"] if s.get("after_start") is not None else None]}
            group_key = hashlib.sha256(canonical(basis).encode("utf-8")).hexdigest()
            groups.setdefault(group_key, {"id": group_key, "basis": basis, "round_ids": []})["round_ids"].append(key)
            reports = data["statistics"] if family == "process" else (data.get("frame_recording") or {}).get("statistics", {}) if family == "frame" else data["disk_recording"]["statistics"]
            values, coverages = [], []
            for phase, title in (("before", "基线"), ("after", "实验段")):
                stats = reports.get(phase, {}).get(field, {})
                value, coverage = stats.get(statistic), stats.get("coverage")
                values.append(value)
                coverages.append(coverage)
                if value is None:
                    issues.append(f"{title}指标缺失")
                if coverage is None or coverage + 1e-9 < effective_coverage:
                    issues.append(f"{title}覆盖不足（要求 {effective_coverage:.1%}）")
            rows.append({"session_id": key, "group_id": group_key, "origin": origin,
                         "label": s["label"], "before": values[0], "after": values[1],
                         "coverage_before": coverages[0], "coverage_after": coverages[1],
                         "effective_minimum_coverage": effective_coverage,
                         "reasons": issues, "included": False, "delta": None, "relative_percent": None})
        # Exclude *all* copies of a repeated round, independent of file selection order.
        for i, row in enumerate(rows):
            s = self.records[row["session_id"]]["session"]
            for other in rows[i + 1:]:
                t = self.records[other["session_id"]]["session"]
                root_s = s["context"].get("frame_analysis_source_session", s["id"])
                root_t = t["context"].get("frame_analysis_source_session", t["id"])
                same_instance = s["identity"] == t["identity"]
                left = max(s["started"] + s["utc_offset"], t["started"] + t["utc_offset"])
                right = min((s.get("after_end") or s["baseline_end"]) + s["utc_offset"],
                            (t.get("after_end") or t["baseline_end"]) + t["utc_offset"])
                same_machine = self.declarations[row["session_id"]]["hardware"].strip() == self.declarations[other["session_id"]]["hardware"].strip()
                if root_s == root_t or (same_instance and same_machine and row["origin"] == other["origin"] and left < right):
                    for item in (row, other):
                        reason = "重复或重叠轮次（含分析副本），请仅选择一个独立记录"
                        if reason not in item["reasons"]:
                            item["reasons"].append(reason)
        for row in rows:
            row["included"] = not row["reasons"]
            if row["included"]:
                row["delta"] = row["after"] - row["before"]
                relative = row["delta"] / row["before"] * 100 if row["before"] else None
                row["relative_percent"] = relative if relative is not None and math.isfinite(relative) else None
        for group in groups.values():
            selected = [r for r in rows if r["group_id"] == group["id"]]
            valid = [r for r in selected if r["included"]]
            deltas = [r["delta"] for r in valid]
            relative = [r["relative_percent"] for r in valid if r["relative_percent"] is not None]
            median = statistics.median(deltas) if deltas else None
            group.update({"total_rounds": len(selected), "valid_rounds": len(valid), "excluded_rounds": len(selected) - len(valid),
                          "enough_rounds": len(valid) >= self.minimum_pairs, "median_delta": median,
                          "median_absolute_deviation": statistics.median(abs(v - median) for v in deltas) if deltas else None,
                          "delta_min": min(deltas) if deltas else None, "delta_max": max(deltas) if deltas else None,
                          "median_relative_percent": statistics.median(relative) if relative else None,
                          "relative_valid_rounds": len(relative)})
        return {"schema": 1, "metric": self.metric, "metric_title": METRICS[self.metric][0], "unit": unit,
                "minimum_coverage": self.minimum_coverage, "minimum_pairs": self.minimum_pairs,
                "groups": list(groups.values()), "rounds": rows,
                "synthetic_rounds": sum(r["origin"] == "synthetic" for r in rows),
                "method": "每会话 before/after 配对；差值=after-before；各轮等权，不拼接帧；MAD 为差值对中位差值的绝对偏差中位数",
                "limitations": ["测试数据不代表游戏收益", "条件声明由用户提供，未独立核验", "达到轮数门槛不代表统计显著性或因果收益", "基线为零或相对变化溢出时保留绝对差值，相对变化不可用"]}

    def save(self, path):
        report = self.report()
        data = {"format": "ace-repeated-study", "schema": 1,
                "settings": {"metric": self.metric, "minimum_coverage": self.minimum_coverage, "minimum_pairs": self.minimum_pairs},
                "selected": self.selected, "declarations": self.declarations,
                "sessions": list(self.records.values()), "report": report}
        def write(stream):
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if stream.tell() > MAX_BUNDLE_BYTES:
                raise ValueError("多轮项目超过 64 MiB，原文件保留；请减少轮次")
        atomic_write(path, write)

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BUNDLE_BYTES + 1)
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("多轮项目超过 64 MiB")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("format") != "ace-repeated-study" or type(data.get("schema")) is not int or data["schema"] != 1:
            raise ValueError("多轮项目格式不支持")
        sessions = data["sessions"]
        if not isinstance(sessions, list) or len(sessions) > 64:
            raise ValueError("多轮项目最多 64 轮")
        history = History()
        seen = set()
        try:
            with tempfile.TemporaryDirectory(prefix="ace-study-") as temporary:
                source = Path(temporary) / "session.json"
                for item in sessions:
                    source.write_text(canonical(item), encoding="utf-8")
                    session = history.load(source)
                    if session.id in seen:
                        raise ValueError("多轮项目包含重复 ID")
                    seen.add(session.id)
            study = cls(history.sessions.values())
        finally:
            for session in history.sessions.values():
                for name in ("_disk_store", "_frame_store"):
                    store = getattr(session, name, None)
                    if store:
                        store.close()
        study.metric = data["settings"]["metric"]
        study.minimum_coverage = data["settings"]["minimum_coverage"]
        study.minimum_pairs = data["settings"]["minimum_pairs"]
        study.declarations = data["declarations"]
        study.selected = data["selected"]
        study.validate()
        # Derived report in the file is informational; all statistics are rebuilt above.
        return study

    def export_csv(self, path):
        report = self.report()
        def write(stream):
            fields = ["row_type", "session_id", "group_id", "origin", "included", "before", "after", "delta", "relative_percent",
                      "coverage_before", "coverage_after", "context_json"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerow({"row_type": "metadata", "context_json": canonical({k: v for k, v in report.items() if k not in ("groups", "rounds")})})
            for row in report["rounds"]:
                writer.writerow({"row_type": "round", **{key: row[key] for key in fields if key in row}, "context_json": canonical(row)})
            for group in report["groups"]:
                writer.writerow({"row_type": "group", "group_id": group["id"], "context_json": canonical(group)})
        atomic_write(path, write, encoding="utf-8-sig")
