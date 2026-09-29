"""Paired local clock anchors; the bracket bounds pairing, not UTC accuracy."""
import ctypes
import time
from ctypes import wintypes


class ClockTracker:
    def __init__(self):
        self.previous = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in ("QueryPerformanceCounter", "QueryPerformanceFrequency", "QueryUnbiasedInterruptTime"):
            function = getattr(self.api, name)
            function.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
            function.restype = wintypes.BOOL
        frequency = ctypes.c_longlong()
        if not self.api.QueryPerformanceFrequency(ctypes.byref(frequency)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.frequency = frequency.value

    def sample(self):
        start = time.monotonic()
        qpc, awake = ctypes.c_longlong(), ctypes.c_longlong()
        if not self.api.QueryPerformanceCounter(ctypes.byref(qpc)):
            raise ctypes.WinError(ctypes.get_last_error())
        utc = time.time()
        awake_ok = self.api.QueryUnbiasedInterruptTime(ctypes.byref(awake))
        end = time.monotonic()
        anchor = {"monotonic": (start + end) / 2, "utc_unix": utc,
                  "qpc": qpc.value, "qpc_frequency": self.frequency,
                  "awake_seconds": awake.value / 10_000_000 if awake_ok else None,
                  "pairing_error_seconds": (end - start) / 2 + time.get_clock_info("monotonic").resolution,
                  "source": "QPC / time.monotonic / system UTC; bracketed pairing",
                  "utc_accuracy": "not measured"}
        reasons = discontinuities(self.previous, anchor)
        self.previous = anchor
        return {"anchor": anchor, "breaks": reasons}


def discontinuities(previous, current):
    if previous is None:
        return []
    elapsed = current["monotonic"] - previous["monotonic"]
    reasons = []
    if elapsed <= 0:
        reasons.append("monotonic_reset")
    if elapsed > 5:
        reasons.append("sampling_pause")
    tolerance = .25 + previous["pairing_error_seconds"] + current["pairing_error_seconds"]
    if abs(current["utc_unix"] - previous["utc_unix"] - elapsed) > tolerance:
        reasons.append("utc_jump")
    if current["awake_seconds"] is not None and previous["awake_seconds"] is not None:
        if elapsed - (current["awake_seconds"] - previous["awake_seconds"]) > .5:
            reasons.append("sleep_resume")
    return reasons
