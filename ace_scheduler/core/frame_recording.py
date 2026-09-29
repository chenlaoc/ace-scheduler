"""Bounded, offline PresentMon v2 import. No capture or scheduling APIs."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import tempfile
import weakref

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 100_000
VERSIONS = ("2.6.0 / --v2_metrics", "2.3.0")
BASE = {"Application", "ProcessID", "SwapChainAddress", "PresentRuntime", "FrameTime"}
OPTIONAL = {"SyncInterval", "PresentFlags", "AllowsTearing", "PresentMode", "CPUBusy", "CPUWait",
            "GPULatency", "GPUTime", "GPUBusy", "GPUWait", "VideoBusy", "DisplayLatency", "DisplayedTime",
            "AnimationError", "AnimationTime", "ClickToPhotonLatency", "AllInputToPhotonLatency", "MsFlipDelay"}
TIMES = {"CPUStartQPC", "CPUStartTime"}
ROW_FIELDS = ["source_time", "cpu_frame_ms", "displayed_ms", "display_latency_ms"]


def source_units(clock):
    return {clock: "QPC ticks" if clock == "CPUStartQPC" else "milliseconds",
            "FrameTime": "CPU frame start interval, ms", "DisplayedTime": "display residency, ms",
            "DisplayLatency": "CPU start to display, ms"}


def number(value, *, optional=False):
    if optional and value in (None, "", "NA", "N/A"):
        return None
    if isinstance(value, bool):
        raise ValueError("布尔值不是帧数值")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("帧数值必须为非负有限数")
    return result


class FrameStore:
    """SQLite spool; created in parser worker and read only after it finishes."""
    def __init__(self):
        self.folder = tempfile.TemporaryDirectory(prefix="ace-frames-")
        self.db = sqlite3.connect(Path(self.folder.name) / "frames.sqlite", check_same_thread=False)
        self._finalizer = weakref.finalize(self, self._cleanup, self.db, self.folder)
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA max_page_count=16384")
        self.db.execute("CREATE TABLE frames (stream INTEGER, stamp NUMERIC, cpu REAL, display REAL, latency REAL)")
        self.db.execute("CREATE INDEX stream_order ON frames(stream, stamp)")
        self.metadata = {}
        self.streams = []
        self.count = 0

    @staticmethod
    def _cleanup(db, folder):
        db.close()
        folder.cleanup()

    def close(self):
        self._finalizer()

    def rows(self, stream):
        return self.db.execute("SELECT stamp,cpu,display,latency FROM frames WHERE stream=? ORDER BY stamp", (stream,))


def read_log(path):
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("PresentMon 日志超过 2 MB")
    text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "events_lost_reported": sum(int(v) for v in re.findall(r"(\d+) ETW events were lost", text)),
            "capture_complete": text.rfind("Stopped recording.") > text.rfind("Started recording.") >= 0}


def read_presentmon(path, version, log_path=None, cancelled=lambda: False):
    if version not in VERSIONS:
        raise ValueError("仅支持明确声明的 PresentMon 2.3.0 或 2.6.0 v2 格式")
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("帧文件超过 32 MiB；请先拆分采集文件")
    store = FrameStore()
    try:
        # Hash and parse the same bounded bytes, avoiding a two-read replacement race.
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("帧文件超过 32 MiB")
        import io
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = reader.fieldnames or []
        clocks = TIMES.intersection(header)
        if (len(header) != len(set(header)) or not BASE.issubset(header) or len(clocks) != 1
                or set(header) - BASE - OPTIONAL - TIMES):
            raise ValueError("未知 CSV 表头：需要 v2 FrameTime 及 CPUStartQPC 或 CPUStartTime；不支持混合/扩展帧格式")
        clock = next(iter(clocks))
        keys, last = {}, {}
        with store.db:
            for line, row in enumerate(reader, 2):
                if cancelled():
                    raise ValueError("导入已取消")
                if store.count >= MAX_ROWS:
                    raise ValueError("帧文件超过 100000 行")
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("列数不一致")
                    pid = int(row["ProcessID"])
                    key = (row["Application"], pid, row["SwapChainAddress"], row["PresentRuntime"])
                    if pid <= 0 or any(not v or len(v) > 260 for v in (key[0], key[2], key[3])):
                        raise ValueError("数据流身份无效")
                    if key not in keys:
                        if len(keys) >= 64:
                            raise ValueError("数据流超过 64 个")
                        keys[key] = len(keys)
                        store.streams.append({"application": key[0], "pid": pid, "swap_chain": key[2], "runtime": key[3], "rows": 0})
                    index = keys[key]
                    stamp = int(row[clock]) if clock == "CPUStartQPC" else number(row[clock])
                    if not 0 <= stamp < 2**63 or stamp <= last.get(index, -1):
                        raise ValueError("时间戳重复、乱序或超出范围")
                    values = [number(row["FrameTime"]), number(row.get("DisplayedTime"), optional=True),
                              number(row.get("DisplayLatency"), optional=True)]
                    store.db.execute("INSERT INTO frames VALUES (?,?,?,?,?)", (index, stamp, *values))
                    store.count += 1
                    store.streams[index]["rows"] += 1
                    last[index] = stamp
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError(f"CSV 第 {line} 行：{exc}") from exc
        if not store.count:
            raise ValueError("CSV 没有帧记录")
        store.metadata = {"version_declared": version, "sha256": hashlib.sha256(raw).hexdigest(),
                          "bytes": len(raw), "header": header, "time_column": clock,
                          "units": source_units(clock),
                          "log": read_log(log_path) if log_path else None}
        return store
    except Exception:
        store.close()
        raise


def alignment(session, source, first, mode, offset=0, same_boot=False):
    if mode == "manual":
        if type(offset) not in (int, float) or not math.isfinite(offset) or abs(offset) > 1_000_000:
            raise ValueError("手动偏移无效")
        if source["time_column"] != "CPUStartTime":
            raise ValueError("QPC 文件请使用同次系统启动的时钟锚点；相对时间手动校准仅支持 CPUStartTime")
        return {"mode": mode, "source_origin": first, "monotonic_origin": session.started + offset,
                "units_per_second": 1000, "offset_seconds": offset, "error_seconds": None, "same_boot_confirmed": False}
    if mode != "qpc" or source["time_column"] != "CPUStartQPC" or not same_boot:
        raise ValueError("QPC 对齐需要确认 CSV 与会话来自同一电脑、同一次系统启动")
    anchors = session.clock["anchors"]
    if not anchors:
        raise ValueError("会话没有 QPC 锚点，不能精确对齐；请使用相对时间 CSV 手动校准")
    a = anchors[0]
    if any(type(v["qpc"]) is not int or v["qpc_frequency"] != a["qpc_frequency"] for v in anchors):
        raise ValueError("QPC 锚点频率或类型不一致")
    residual = max(abs(v["monotonic"] - a["monotonic"] - (v["qpc"] - a["qpc"]) / a["qpc_frequency"]) for v in anchors)
    error = max(v["pairing_error_seconds"] for v in anchors)
    if residual > 2 * error + .005:
        raise ValueError("时钟锚点不一致，不能跨断点统一对齐")
    return {"mode": mode, "source_origin": a["qpc"], "monotonic_origin": a["monotonic"],
            "units_per_second": a["qpc_frequency"], "offset_seconds": a["monotonic"] - a["qpc"] / a["qpc_frequency"],
            "error_seconds": error + residual, "same_boot_confirmed": True}


def mapped(stamp, align):
    return align["monotonic_origin"] + (stamp - align["source_origin"]) / align["units_per_second"]


def make_attachment(session, store, stream, mode, offset=0, same_boot=False, threshold=33.333, coverage=.95):
    if type(stream) is not int or not 0 <= stream < len(store.streams):
        raise ValueError("请选择一个数据流；不合并不同交换链")
    selected = store.streams[stream]
    if selected["pid"] != session.identity.pid or selected["application"].casefold() != session.identity.name.casefold():
        raise ValueError("所选数据流的 PID / 进程名与会话不一致")
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 10000:
        raise ValueError("长帧阈值必须在 0 到 10000 ms 之间")
    if type(coverage) not in (int, float) or not math.isfinite(coverage) or not 0 < coverage <= 1:
        raise ValueError("覆盖率阈值必须在 0 到 100% 之间")
    first = next(iter(store.rows(stream)))[0]
    align = alignment(session, store.metadata, first, mode, offset, same_boot)
    return {"source": store.metadata, "stream": selected, "alignment": align,
            "long_frame_threshold_ms": threshold, "minimum_coverage": coverage,
            "identity_note": "CSV has no process creation time; PID/name match is not instance proof",
            "row_fields": ROW_FIELDS}


def metric_report(rows, align, window, breaks, metric, threshold, cutoff):
    start, end = window
    cursor, covered, gaps, values, intersecting = start, 0., [], [], 0
    # Display order may differ from CPU order: sort only the selected bounded stream.
    intervals = []
    for stamp, cpu, display, latency in rows:
        left = mapped(stamp, align)
        duration = cpu
        if metric == "display_residency_ms":
            if display is None or latency is None:
                continue
            left += latency / 1000
            duration = display
        right = left + duration / 1000
        original_right = right
        if cutoff is not None:
            right = min(right, cutoff)
        if any(left < b["timestamp"] and right > b.get("started", b["timestamp"] - 1e-9) for b in breaks):
            continue
        if right > max(start, left) and left < end:
            intervals.append((left, right, duration, original_right))
    for left, right, duration, original_right in sorted(intervals):
        intersecting += 1
        if start <= left and original_right <= end and (cutoff is None or original_right <= cutoff):
            values.append(duration)
        a, b = max(start, cursor, left), min(end, right)
        if b > a:
            if a - cursor > 1e-6:
                gaps.append([cursor, a])
            covered += b - a
            cursor = b
    if end - cursor > 1e-6:
        gaps.append([cursor, end])
    values.sort()
    def percentile(q):
        return values[max(0, math.ceil(q * len(values)) - 1)] if values else None
    return {"window_seconds": max(0, end - start), "covered_seconds": covered,
            "coverage": covered / (end - start) if end > start else 0,
            "frames_fully_inside": len(values), "intervals_intersecting": intersecting,
            "median_ms": statistics.median(values) if values else None,
            "p95_ms": percentile(.95), "p99_ms": percentile(.99),
            "long_frames": sum(v > threshold for v in values),
            "long_frame_ratio": sum(v > threshold for v in values) / len(values) if values else None,
            "gap_count": len(gaps), "gaps_first_50": gaps[:50]}


def frame_report(session):
    attachment = session.frame_attachment
    if not attachment:
        return {}
    rows = list(session._frame_store.rows(session._frame_stream))
    result = {}
    for phase, window in session.windows().items():
        if phase == "transition":
            continue
        fields = {}
        for metric in ("cpu_frame_interval_ms", "display_residency_ms"):
            stats = metric_report(rows, attachment["alignment"], window, session.clock["breaks"], metric,
                                  attachment["long_frame_threshold_ms"], session.ended)
            reasons = []
            if not stats["intervals_intersecting"]:
                reasons.append("无有效窗口交集")
            if stats["coverage"] < attachment["minimum_coverage"]:
                reasons.append("覆盖率不足")
            if not stats["frames_fully_inside"]:
                reasons.append("没有完整落入窗口的帧")
            log = attachment["source"]["log"]
            if log is None:
                reasons.append("未提供采集日志，ETW 丢失情况未知")
            elif log["events_lost_reported"]:
                reasons.append(f"日志报告 {log['events_lost_reported']} 个 ETW 事件丢失")
            elif log.get("capture_complete") is not True:
                reasons.append("采集日志不完整，ETW 丢失情况未知")
            if attachment["alignment"]["mode"] == "manual":
                reasons.append("手动近似对齐，误差未核验")
            if session.clock["breaks"]:
                reasons.append("存在时钟断点，交叉区间已排除")
            stats.update({"quality_reasons": reasons, "coverage_and_log_gate_passed": not reasons})
            fields[metric] = stats
        result[phase] = fields
    return result


def portable_frames(session):
    if not session.frame_attachment:
        return None
    return {"rows": [list(row) for row in session._frame_store.rows(session._frame_stream)],
            "statistics": frame_report(session)}


def restore_frames(session, recording):
    """Revalidate portable raw values; derived statistics are never trusted."""
    attachment = session.frame_attachment
    if not attachment:
        if recording is not None:
            raise ValueError("帧数据缺少导入来源")
        return
    if not isinstance(attachment, dict) or not isinstance(recording, dict):
        raise ValueError("帧附件无效")
    if len(json.dumps(attachment, ensure_ascii=False, allow_nan=False)) > 16384:
        raise ValueError("帧附件元数据超过 16 KB")
    source = attachment["source"]
    if source["version_declared"] not in VERSIONS or source["time_column"] not in TIMES:
        raise ValueError("帧来源版本或时钟无效")
    if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) or type(source["bytes"]) is not int or not 0 < source["bytes"] <= MAX_BYTES:
        raise ValueError("帧来源哈希或大小无效")
    header = source["header"]
    if not isinstance(header, list) or len(header) != len(set(header)) or not BASE.issubset(header) or set(header) - BASE - OPTIONAL - TIMES or TIMES.intersection(header) != {source["time_column"]}:
        raise ValueError("帧来源表头无效")
    if source.get("units") != source_units(source["time_column"]):
        raise ValueError("帧来源单位与表头不一致")
    log = source["log"]
    if log is not None and (not isinstance(log, dict) or type(log.get("events_lost_reported")) is not int or log["events_lost_reported"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", log.get("sha256", ""))):
        raise ValueError("帧日志来源无效")
    if log is not None and "capture_complete" in log and type(log["capture_complete"]) is not bool:
        raise ValueError("帧日志完整性标记无效")
    rows = recording["rows"]
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise ValueError("帧记录数量无效")
    store = FrameStore()
    try:
        store.metadata, store.streams = source, [attachment["stream"]]
        selected = attachment["stream"]
        if type(selected["pid"]) is not int or type(selected["rows"]) is not int or selected["rows"] != len(rows) or any(not isinstance(selected[k], str) or not 0 < len(selected[k]) <= 260 for k in ("application", "swap_chain", "runtime")):
            raise ValueError("帧数据流无效")
        previous = -1
        with store.db:
            for row in rows:
                if not isinstance(row, list) or len(row) != 4:
                    raise ValueError("帧记录列数无效")
                stamp = row[0]
                if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= stamp < 2**63 or stamp <= previous:
                    raise ValueError("帧记录时间戳无效")
                if source["time_column"] == "CPUStartQPC" and type(stamp) is not int:
                    raise ValueError("QPC 必须为整数")
                if any(v is not None and type(v) not in (int, float) for v in row[1:]) or row[1] is None:
                    raise ValueError("帧记录数值类型无效")
                values = [number(row[1]), number(row[2], optional=True), number(row[3], optional=True)]
                store.db.execute("INSERT INTO frames VALUES (0,?,?,?,?)", (stamp, *values))
                previous = stamp
        store.count = len(rows)
        a = attachment["alignment"]
        expected = make_attachment(session, store, 0, a["mode"],
                                   a["offset_seconds"] if a["mode"] == "manual" else 0,
                                   a["same_boot_confirmed"], attachment["long_frame_threshold_ms"], attachment["minimum_coverage"])
        if expected != attachment:
            raise ValueError("帧附件元数据与原始记录不一致")
        session._frame_store, session._frame_stream = store, 0
    except Exception:
        store.close()
        raise
