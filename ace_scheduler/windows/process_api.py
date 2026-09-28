from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import PureWindowsPath
import sys

from ace_scheduler.core.process_metrics import ProcessIdentity
from . import eco_qos

QUERY_LIMITED = 0x1000
SET_INFORMATION = 0x0200


class ProcessChangedError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def kernel32():
    if sys.platform != "win32":
        raise RuntimeError("Windows API 仅支持 Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                       ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "GetPriorityClass": ([wintypes.HANDLE], wintypes.DWORD),
        "SetPriorityClass": ([wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL),
        "GetProcessAffinityMask": ([wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                    ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
        "SetProcessAffinityMask": ([wintypes.HANDLE, ctypes.c_size_t], wintypes.BOOL),
        "GetProcessInformation": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "SetProcessInformation": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "GetActiveProcessorGroupCount": ([], wintypes.WORD),
        "GetActiveProcessorCount": ([wintypes.WORD], wintypes.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


class WindowsProcessApi:
    """Every mutation uses a verified handle, never reopens an unverified PID."""
    def __init__(self):
        self.api = kernel32()

    @contextmanager
    def open(self, identity: ProcessIdentity, write: bool = False):
        handle = self.api.OpenProcess(QUERY_LIMITED | (SET_INFORMATION if write else 0), False, identity.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise ctypes.WinError(ctypes.get_last_error())
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            created = ticks / 10_000_000 - 11_644_473_600
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not self.api.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            # FILETIME and psutil's epoch conversions may round by a few microseconds.
            if abs(created - identity.created) > 0.00001 or PureWindowsPath(path.value).name.casefold() != identity.name.casefold():
                raise ProcessChangedError("进程实例已改变（PID 重用或名称变化），操作已取消")
            yield handle
        finally:
            self.api.CloseHandle(handle)

    def get_priority(self, handle) -> int:
        value = self.api.GetPriorityClass(handle)
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def set_priority(self, handle, value: int) -> None:
        if not self.api.SetPriorityClass(handle, value):
            raise ctypes.WinError(ctypes.get_last_error())

    def get_affinity(self, handle) -> tuple[int, ...]:
        process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
        if not self.api.GetProcessAffinityMask(handle, ctypes.byref(process_mask), ctypes.byref(system_mask)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not process_mask.value:
            raise RuntimeError("Affinity 掩码为空或进程跨 Processor Group")
        return tuple(i for i in range(ctypes.sizeof(ctypes.c_size_t) * 8) if process_mask.value & (1 << i))

    def set_affinity(self, handle, cpus: tuple[int, ...]) -> None:
        if not cpus or any(type(i) is not int or not 0 <= i < ctypes.sizeof(ctypes.c_size_t) * 8 for i in cpus):
            raise ValueError("Affinity 必须包含合法 CPU ID 且不能为空")
        mask = sum(1 << i for i in set(cpus))
        if not self.api.SetProcessAffinityMask(handle, mask):
            raise ctypes.WinError(ctypes.get_last_error())

    def get_eco(self, handle) -> eco_qos.EcoState:
        return eco_qos.read(self.api, handle)

    def set_eco(self, handle, target: bool | eco_qos.EcoState) -> None:
        eco_qos.write(self.api, handle, target)
