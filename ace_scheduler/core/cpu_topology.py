from __future__ import annotations

from dataclasses import dataclass
import math
import platform
import struct

import psutil

from ace_scheduler.config.models import AffinitySpec


@dataclass(frozen=True)
class CpuTopology:
    physical: int | None
    logical: int
    available: tuple[int, ...]
    groups: int = 1
    name: str = "CPU"
    error: str = ""

    @property
    def affinity_supported(self) -> bool:
        return self.groups == 1 and self.logical <= 64 and bool(self.available) and not self.error

    @property
    def limitation(self) -> str:
        if self.groups != 1 or self.logical > 64:
            return "当前版本仅支持单 Processor Group；Affinity 已禁用，仍可监控及设置 Priority / EcoQoS。"
        return self.error

    def resolve(self, spec: AffinitySpec) -> tuple[int, ...]:
        ids = tuple(sorted(set(self.available)))
        if not ids:
            raise ValueError("未检测到可用逻辑处理器")
        if spec.mode == "all":
            return ids
        if spec.mode == "last_n":
            return ids[-max(1, min(spec.count, len(ids))):]
        if spec.mode == "percentage":
            size = max(spec.minimum, math.ceil(len(ids) * spec.percentage / 100))
            return ids[-max(1, min(size, len(ids))):]
        if spec.mode == "custom":
            result = tuple(i for i in ids if i in spec.cpus)
            if not result:
                raise ValueError("Custom CPU ID 在本机全部无效；请重新选择至少一个 CPU")
            return result
        raise ValueError("未知 Affinity 策略")

    @classmethod
    def detect(cls) -> CpuTopology:
        logical = psutil.cpu_count(logical=True) or 1
        physical = psutil.cpu_count(logical=False)
        name = platform.processor() or "CPU"
        if platform.system() != "Windows":
            return cls(physical, logical, tuple(range(logical)), name=name,
                       error="仅支持 Windows x64")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        from ace_scheduler.windows.process_api import kernel32
        api = kernel32()
        groups = api.GetActiveProcessorGroupCount()
        active = api.GetActiveProcessorCount(0xFFFF)
        logical = max(logical, active)
        if not groups or not active:
            return cls(physical, logical, (), name=name, error="Processor Group 检测失败；Affinity 已禁用")
        if groups > 1 or logical > 64:
            return cls(physical, logical, (), groups, name)
        if struct.calcsize("P") != 8:
            return cls(physical, logical, (), groups, name, "请使用 64 位 Python / EXE")
        import ctypes
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        if not api.GetProcessAffinityMask(api.GetCurrentProcess(), ctypes.byref(process_mask),
                                          ctypes.byref(system_mask)):
            return cls(physical, logical, (), groups, name, "系统 Affinity 掩码读取失败")
        # Use the system mask, not this app's possibly restricted current affinity.
        ids = tuple(i for i in range(64) if system_mask.value & (1 << i))
        return cls(physical, logical, ids, groups, name,
                   "" if ids else "系统 Affinity 掩码为空")
