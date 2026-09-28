from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

PROCESS_POWER_THROTTLING = 4
EXECUTION_SPEED = 0x1


class PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("ControlMask", wintypes.DWORD),
                ("StateMask", wintypes.DWORD)]


@dataclass(frozen=True)
class EcoState:
    control: int
    state: int

    @property
    def label(self) -> str:
        if not self.control & EXECUTION_SPEED:
            return "系统管理"
        return "ON" if self.state & EXECUTION_SPEED else "OFF"

    def matches(self, enabled: bool) -> bool:
        return bool(self.control & EXECUTION_SPEED) and bool(self.state & EXECUTION_SPEED) == enabled


def read(api, handle) -> EcoState:
    value = PowerThrottlingState(1, 0, 0)
    if not api.GetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.byref(value),
                                     ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return EcoState(value.ControlMask, value.StateMask)


def write(api, handle, target: bool | EcoState) -> None:
    current = read(api, handle)
    # Preserve unrelated mechanisms such as IGNORE_TIMER_RESOLUTION.
    control = current.control & ~EXECUTION_SPEED
    state = current.state & ~EXECUTION_SPEED
    if isinstance(target, EcoState):
        control |= target.control & EXECUTION_SPEED
        state |= target.state & EXECUTION_SPEED
    else:
        control |= EXECUTION_SPEED
        if target:
            state |= EXECUTION_SPEED
    value = PowerThrottlingState(1, control, state)
    if not api.SetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.byref(value),
                                     ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())
