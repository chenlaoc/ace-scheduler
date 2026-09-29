"""Shared, bounded disk spool and request-weighted summaries, independent of scheduling."""
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import weakref

FIELDS = ("read_B_s", "write_B_s", "read_iops", "write_iops", "read_latency_s", "write_latency_s", "queue_mean", "queue_current")
UNITS = {"read_B_s": "bytes/s interval mean", "write_B_s": "bytes/s interval mean",
         "read_iops": "requests/s interval mean", "write_iops": "requests/s interval mean",
         "read_latency_s": "seconds/request interval mean; null when no requests",
         "write_latency_s": "seconds/request interval mean; null when no requests",
         "queue_mean": "requests interval mean", "queue_current": "requests instantaneous at sample end"}


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 8192:
        raise ValueError("磁盘记录数量无效（最多 8192 条）")
    last = -math.inf
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("磁盘记录无效")
        stamp, seconds = row.get("timestamp"), row.get("seconds")
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= last:
            raise ValueError("磁盘时间顺序无效")
        last = stamp
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 3):
            raise ValueError("磁盘采样区间无效")
        values = row.get("values")
        if not isinstance(values, dict) or set(values) != set(FIELDS):
            raise ValueError("磁盘指标无效")
        for value in values.values():
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("磁盘数值无效")
        if not isinstance(row.get("errors", {}), dict) or any(not isinstance(v, str) for v in row.get("errors", {}).values()):
            raise ValueError("磁盘错误无效")
        if row.get("gap") is not None and not isinstance(row["gap"], str):
            raise ValueError("磁盘缺口无效")
        if (row.get("gap") or seconds is None) and any(v is not None for v in values.values()):
            raise ValueError("无效区间包含磁盘数值")
        for direction in ("read", "write"):
            if values[direction + "_iops"] in (0, None) and values[direction + "_latency_s"] is not None:
                raise ValueError("无请求区间包含延迟")


class DiskStore:
    MAX_ROWS = 100_000
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self):
        self.folder = tempfile.TemporaryDirectory(prefix="ace-disk-")
        self.path = Path(self.folder.name) / "samples.sqlite"
        self.db = sqlite3.connect(self.path)
        self._finalizer = weakref.finalize(self, self._cleanup, self.db, self.folder)
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA max_page_count=16384")
        self.db.execute("CREATE TABLE samples (device TEXT, stamp REAL, data TEXT, PRIMARY KEY(device, stamp))")
        self.count = 0

    def append(self, device, rows):
        validate_rows(rows)
        if self.count + len(rows) > self.MAX_ROWS or self.path.stat().st_size >= self.MAX_BYTES:
            raise ValueError("磁盘记录达到 100000 条 / 64 MiB 上限；请保存实验并重开程序")
        before = self.db.total_changes
        with self.db:
            for row in rows:
                encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > 16384:
                    raise ValueError("单条磁盘记录过大")
                old = self.db.execute("SELECT data FROM samples WHERE device=? AND stamp=?", (device, row["timestamp"])).fetchone()
                if old and old[0] != encoded:
                    raise ValueError("相同磁盘时间戳的记录内容冲突")
                self.db.execute("INSERT OR IGNORE INTO samples VALUES (?, ?, ?)", (device, row["timestamp"], encoded))
        self.count += self.db.total_changes - before

    def rows(self, device, windows, cutoff=None):
        if not windows:
            return []
        start, end = min(w[0] for w in windows), max(w[1] for w in windows)
        if cutoff is not None:
            end = min(end, cutoff)
        result = []
        for (data,) in self.db.execute("SELECT data FROM samples WHERE device=? AND stamp>=? AND stamp<=? ORDER BY stamp", (device, start, end + 3)):
            row = json.loads(data)
            if cutoff is not None and row["timestamp"] > cutoff:
                continue
            if any(row["timestamp"] >= a and row["timestamp"] - (row["seconds"] or 0) <= b for a, b in windows):
                result.append(row)
                if len(result) > 8192:
                    raise ValueError("单会话磁盘记录超过 8192 条")
        return result

    def close(self):
        if self.db is not None:
            self._finalizer()
            self.db = None

    @staticmethod
    def _cleanup(db, folder):
        db.close()
        folder.cleanup()


def disk_statistics(rows, field, start, end):
    weighted, spans, cursor = [], [], start
    points = []
    for row in rows:
        value, seconds = row["values"][field], row["seconds"]
        if value is None or row.get("gap") or not seconds:
            continue
        if field == "queue_current":
            if start < row["timestamp"] <= end:
                points.append(value)
            continue
        left, right = max(start, cursor, row["timestamp"] - seconds), min(end, row["timestamp"])
        if right <= left:
            continue
        weight = right - left
        if field.endswith("latency_s"):
            count = row["values"][field.split("_")[0] + "_iops"]
            if not count:
                continue
            weight *= count
        weighted.append((value, weight))
        spans.append((left, right))
        cursor = right
    covered = sum(b - a for a, b in spans)
    gaps, cursor = [], start
    for a, b in spans:
        if a > cursor:
            gaps.append([cursor, a])
        cursor = b
    if cursor < end:
        gaps.append([cursor, end])
    weight = sum(w for _, w in weighted)
    return {"mean": sum(v * w for v, w in weighted) / weight if weight else None,
            "coverage": covered / (end - start) if end > start and field != "queue_current" else None,
            "valid_seconds": covered if field != "queue_current" else None,
            "weighting": "estimated request count" if field.endswith("latency_s") else "time",
            "estimated_requests": weight if field.endswith("latency_s") and weight else None,
            "gaps": gaps if field != "queue_current" else [],
            "instantaneous_max": max(points) if points else None, "instantaneous_points": len(points),
            "unit": UNITS[field]}
