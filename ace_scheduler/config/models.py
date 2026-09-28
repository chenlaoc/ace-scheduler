from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

PRIORITIES = {"Idle": 0x40, "Below Normal": 0x4000, "Normal": 0x20,
              "Above Normal": 0x8000, "High": 0x80}
DEFAULT_NAMES = ("SGuard64.exe", "SGuardSvc64.exe", "ACE-Service64.exe",
                 "SGuardUpdate64.exe", "ACE-Tray.exe")
INTERVALS = (1, 2, 3, 5, 10)


def process_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("进程名称必须是字符串")
    name = value.strip()
    if not re.fullmatch(r'[^\\/:*?"<>|\x00-\x1f]{1,240}\.exe', name, re.I):
        raise ValueError("请输入用户态进程文件名（例如 example.exe），不能填写路径或 .sys")
    return name


def integer(value, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} 必须是 {low}~{high} 的整数")
    return value


def boolean(value, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} 必须是布尔值")
    return value


@dataclass(frozen=True)
class AffinitySpec:
    mode: str = "all"
    count: int = 1
    percentage: int = 25
    minimum: int = 1
    cpus: tuple[int, ...] = ()

    @classmethod
    def parse(cls, data: dict) -> AffinitySpec:
        if not isinstance(data, dict):
            raise ValueError("affinity 必须是对象")
        mode = data.get("mode", "all")
        if mode not in ("all", "last_n", "percentage", "custom"):
            raise ValueError("未知 Affinity 策略")
        cpus = data.get("cpus", [])
        if not isinstance(cpus, (list, tuple)) or len(cpus) > 4096:
            raise ValueError("无效 CPU ID 列表")
        return cls(mode, integer(data.get("count", 1), 1, 4096, "count"),
                   integer(data.get("percentage", 25), 1, 100, "percentage"),
                   integer(data.get("minimum", 1), 1, 4096, "minimum"),
                   tuple(sorted({v for v in cpus if type(v) is int and v >= 0})))


@dataclass(frozen=True)
class Policy:
    priority: str = "Normal"
    affinity: AffinitySpec = field(default_factory=AffinitySpec)
    eco: bool = False

    @classmethod
    def parse(cls, data: dict) -> Policy:
        if not isinstance(data, dict) or data.get("priority", "Normal") not in PRIORITIES:
            raise ValueError("无效 Priority；不允许 Realtime")
        return cls(data.get("priority", "Normal"),
                   AffinitySpec.parse(data.get("affinity", {})),
                   boolean(data.get("eco", False), "eco"))


def preset(name: str) -> Policy:
    if name == "Default":
        return Policy()
    if name == "Mild":
        return Policy("Below Normal", AffinitySpec("percentage", percentage=25, minimum=2), True)
    if name == "Strong":
        return Policy("Idle", AffinitySpec("last_n", count=1), True)
    raise ValueError("未知预设")


@dataclass(frozen=True)
class ProcessRule:
    name: str
    enabled: bool = True
    policy: Policy = field(default_factory=Policy)
    keep_enforced: bool = False

    @property
    def key(self) -> str:
        return self.name.casefold()

    @property
    def builtin(self) -> bool:
        return self.key in {name.casefold() for name in DEFAULT_NAMES}

    @classmethod
    def parse(cls, data: dict) -> ProcessRule:
        if not isinstance(data, dict):
            raise ValueError("规则必须是对象")
        return cls(process_name(data.get("name")), boolean(data.get("enabled", True), "enabled"),
                   Policy.parse(data.get("policy", {})),
                   boolean(data.get("keep_enforced", False), "keep_enforced"))


@dataclass
class AppConfig:
    rules: list[ProcessRule] = field(default_factory=lambda: [ProcessRule(n) for n in DEFAULT_NAMES])
    monitor_interval: int = 1
    enforce_interval: int = 3
    geometry: str = ""
    close_to_tray: bool = False

    @classmethod
    def parse(cls, data: dict) -> AppConfig:
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("配置版本不支持")
        raw = data.get("rules", [asdict(ProcessRule(n)) for n in DEFAULT_NAMES])
        if not isinstance(raw, list) or len(raw) > 256:
            raise ValueError("规则列表无效或超过 256 条")
        rules = [ProcessRule.parse(item) for item in raw]
        if len({r.key for r in rules}) != len(rules):
            raise ValueError("进程规则名称重复（不区分大小写）")
        keys = {r.key for r in rules}
        rules.extend(ProcessRule(n, enabled=False) for n in DEFAULT_NAMES if n.casefold() not in keys)
        monitor = integer(data.get("monitor_interval", 1), 1, 10, "监控间隔")
        enforce = integer(data.get("enforce_interval", 3), 1, 10, "维护间隔")
        geometry = data.get("geometry", "")
        if not isinstance(geometry, str) or len(geometry) > 8192:
            geometry = ""
        return cls(rules, monitor, enforce, geometry, boolean(data.get("close_to_tray", False), "close_to_tray"))

    def to_dict(self) -> dict:
        return {"version": 1, **asdict(self)}
