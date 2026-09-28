"""Custom client chrome with native Windows dragging, resizing and work-area bounds.

Only operates on this application's HWND. No target-process APIs are used here.
See https://learn.microsoft.com/en-us/windows/win32/dwm/customframe
"""
import ctypes
from ctypes import wintypes

WM_GETMINMAXINFO = 0x0024
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class MinMaxInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.POINT) for name in
                ("ptReserved", "ptMaxSize", "ptMaxPosition", "ptMinTrackSize", "ptMaxTrackSize")]


class Margins(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]


def resize_hit(x, y, bounds, margin, maximized=False):
    """Physical screen coordinates; supports monitors left/above the primary one."""
    left, top, right, bottom = bounds
    if maximized or not (left <= x < right and top <= y < bottom):
        return HTCLIENT
    on_left, on_right = x < left + margin, x >= right - margin
    on_top, on_bottom = y < top + margin, y >= bottom - margin
    if on_top:
        return HTTOPLEFT if on_left else HTTOPRIGHT if on_right else HTTOP
    if on_bottom:
        return HTBOTTOMLEFT if on_left else HTBOTTOMRIGHT if on_right else HTBOTTOM
    return HTLEFT if on_left else HTRIGHT if on_right else HTCLIENT


class WindowFrame:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            "SetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ScreenToClient": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "MonitorFromWindow": ([wintypes.HWND, wintypes.DWORD], wintypes.HMONITOR),
            "GetMonitorInfoW": ([wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)], wintypes.BOOL),
            "IsZoomed": ([wintypes.HWND], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def install(self):
        # Keep native window semantics while WM_NCCALCSIZE removes its visible chrome.
        style = self.api.GetWindowLongPtrW(self.hwnd, -16)
        style |= WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        ctypes.set_last_error(0)
        previous = self.api.SetWindowLongPtrW(self.hwnd, -16, style)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0037):
            raise ctypes.WinError(ctypes.get_last_error())
        # DWM supplies the OS shadow and rounded corners when available.
        try:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(Margins)]
            dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            dwm.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(Margins(1, 1, 1, 1)))
            rounded = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(rounded), ctypes.sizeof(rounded))
        except (OSError, AttributeError):
            pass  # Optional appearance; native hit testing remains available.

    def monitor_info(self):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = self.api.MonitorFromWindow(self.hwnd, 2)
        return info if monitor and self.api.GetMonitorInfoW(monitor, ctypes.byref(info)) else None

    def handle(self, message, window):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE:
            # RECT is also the first member of NCCALCSIZE_PARAMS (wParam == TRUE).
            if msg.lParam and self.api.IsZoomed(self.hwnd):
                info = self.monitor_info()
                if info:
                    rect = wintypes.RECT.from_address(msg.lParam)
                    rect.left, rect.top = info.rcWork.left, info.rcWork.top
                    rect.right, rect.bottom = info.rcWork.right, info.rcWork.bottom
            return True, 0
        if msg.message == WM_GETMINMAXINFO and msg.lParam:
            info = self.monitor_info()
            if info:
                limits = MinMaxInfo.from_address(msg.lParam)
                limits.ptMaxPosition.x = info.rcWork.left - info.rcMonitor.left
                limits.ptMaxPosition.y = info.rcWork.top - info.rcMonitor.top
                limits.ptMaxSize.x = info.rcWork.right - info.rcWork.left
                limits.ptMaxSize.y = info.rcWork.bottom - info.rcWork.top
                ratio = window.devicePixelRatioF()
                limits.ptMinTrackSize.x = round(window.minimumWidth() * ratio)
                limits.ptMinTrackSize.y = round(window.minimumHeight() * ratio)
                return True, 0
        if msg.message == WM_NCHITTEST:
            x = ctypes.c_short(msg.lParam & 0xffff).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
            rect = wintypes.RECT()
            if not self.api.GetWindowRect(self.hwnd, ctypes.byref(rect)):
                return False, 0
            ratio = window.devicePixelRatioF()
            hit = resize_hit(x, y, (rect.left, rect.top, rect.right, rect.bottom),
                             max(4, round(6 * ratio)), bool(self.api.IsZoomed(self.hwnd)))
            if hit != HTCLIENT:
                return True, hit
            point = wintypes.POINT(x, y)
            if self.api.ScreenToClient(self.hwnd, ctypes.byref(point)):
                if window.is_caption_point(round(point.x / ratio), round(point.y / ratio)):
                    return True, HTCAPTION
            # The three caption buttons are Qt widgets, not invisible native buttons.
            return True, HTCLIENT
        return False, 0
