"""Write-ahead recovery journal, owned exclusively by the monitor thread.

A pending write is deliberately ambiguous after a crash. Safe recovery never
assumes an intended value was actually written; the user must resolve it.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

import psutil

from ace_scheduler.config.models import PRIORITIES, process_name
from ace_scheduler.windows.eco_qos import EcoState
from .process_metrics import ProcessIdentity


def encode(value):
    return asdict(value) if isinstance(value, EcoState) else value


def decode(name, value):
    if name == "priority" and type(value) is int and value in PRIORITIES.values():
        return value
    if name == "affinity" and isinstance(value, (list, tuple)) and 0 < len(value) <= 64:
        if all(type(i) is int and 0 <= i < 64 for i in value) and len(set(value)) == len(value):
            return tuple(value)
    if name == "eco" and isinstance(value, dict) and set(value) == {"control", "state"}:
        if all(type(v) is int and 0 <= v <= 0xffffffff for v in value.values()):
            return EcoState(**value)
    raise ValueError("恢复字段无效")


class RecoveryJournal:
    def __init__(self, path: Path, *, boot=None, now=None):
        self.path = Path(path)
        self.boot = psutil.boot_time() if boot is None else boot
        self.now = time.time() if now is None else now
        self.session = uuid.uuid4().hex
        self.records = {}
        self.warning = ""
        self.blocked = False
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > 4_000_000:
                raise ValueError("恢复记录过大")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["schema"] != 1:
                raise ValueError("不支持的恢复记录版本")
            if not math.isfinite(data["boot"]) or not math.isfinite(data["updated"]):
                raise ValueError("恢复记录时间无效")
            if not isinstance(data["records"], list) or len(data["records"]) > 4096:
                raise ValueError("恢复实例列表无效")
            loaded = {}
            for item in data["records"]:
                identity = ProcessIdentity(**item["identity"])
                if type(identity.pid) is not int or identity.pid <= 0 or not math.isfinite(identity.created) or identity.created <= 0:
                    raise ValueError("恢复实例身份无效")
                process_name(identity.name)
                if identity in loaded or not isinstance(item["session"], str) or len(item["session"]) > 64:
                    raise ValueError("重复实例或会话无效")
                fields = item["fields"]
                if not isinstance(fields, dict) or not fields or set(fields) - {"priority", "affinity", "eco"}:
                    raise ValueError("恢复字段列表无效")
                for name, entry in fields.items():
                    decode(name, entry["original"])
                    decode(name, entry["last"])
                    if type(entry["pending"]) is not bool:
                        raise ValueError("恢复写入状态无效")
                loaded[identity] = item
            if abs(data["boot"] - self.boot) > 5 or self.now - data["updated"] > 30 * 86400:
                self._archive()
                self.warning = "旧恢复记录已归档（系统重启或超过 30 天），不会恢复旧实例。"
                return
            self.records = loaded
            if loaded:
                self.warning = f"发现未完成会话：{len(loaded)} 个实例的恢复记录。当前只观察；请在设置中选择恢复或保留。"
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self.blocked = True
            self.warning = f"恢复记录无法读取，已禁止新的调度写入：{exc}。可在设置中归档并放弃记录。"

    @property
    def originals(self):
        return {identity: {name: decode(name, entry["original"]) for name, entry in item["fields"].items()}
                for identity, item in self.records.items()}

    def _archive(self):
        self.path.replace(self.path.with_name(f"recovery-archive-{uuid.uuid4().hex}.json"))

    def _save(self, records):
        if self.blocked:
            raise OSError("恢复记录损坏或版本不支持；请先处理旧记录")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix="recovery-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema": 1, "boot": self.boot, "updated": time.time(),
                           "records": list(records.values())}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
            self.records = records
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def prepare(self, identity, name, original, target):
        records = copy.deepcopy(self.records)
        item = records.setdefault(identity, {"identity": asdict(identity), "session": self.session, "fields": {}})
        entry = item["fields"].setdefault(name, {"original": encode(original)})
        entry.update(last=encode(target), pending=True)
        self._save(records)

    def confirm(self, identity, name, actual):
        records = copy.deepcopy(self.records)
        records[identity]["fields"][name].update(last=encode(actual), pending=False)
        self._save(records)

    def entry(self, identity, name):
        return self.records.get(identity, {}).get("fields", {}).get(name)

    def remove(self, identity, name):
        records = copy.deepcopy(self.records)
        records[identity]["fields"].pop(name, None)
        if not records[identity]["fields"]:
            del records[identity]
        self._save(records)

    def retain(self, identities):
        records = {key: value for key, value in self.records.items() if key in identities}
        if len(records) != len(self.records):
            self._save(records)

    def abandon(self):
        # Explicit user decision; preserve the old file for diagnosis, never overwrite it.
        if self.path.exists():
            self._archive()
        self.records = {}
        self.blocked = False
        self.warning = ""
