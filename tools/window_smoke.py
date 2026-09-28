"""Exercise this app's own HWND; no monitoring worker or target-process changes."""
import ctypes
from ctypes import wintypes
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.ui.theme import apply_palette
from ace_scheduler.windows.window_frame import (HTCAPTION, HTCLIENT, HTTOPLEFT, HTTOP,
    HTTOPRIGHT, HTLEFT, HTRIGHT, HTBOTTOMLEFT, HTBOTTOM, HTBOTTOMRIGHT, WM_NCHITTEST)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/window-v1.3"))
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_palette(app)
    window = MainWindow(AppConfig(), ConfigManager(output / "config.json"), read_only=True, start_worker=False)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.show()
    QTest.qWait(100)
    frame = window.native_frame
    assert frame is not None
    api = frame.api
    api.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.SendMessageW.restype = ctypes.c_ssize_t
    api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    api.ClientToScreen.restype = wintypes.BOOL
    api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetClientRect.restype = wintypes.BOOL

    def screen_point(point):
        ratio = window.devicePixelRatioF()
        result = wintypes.POINT(round(point.x() * ratio), round(point.y() * ratio))
        assert api.ClientToScreen(frame.hwnd, ctypes.byref(result))
        return result.x, result.y

    def hit(x, y):
        return api.SendMessageW(frame.hwnd, WM_NCHITTEST, 0, (x & 0xffff) | ((y & 0xffff) << 16))

    def bounds():
        outer, client = wintypes.RECT(), wintypes.RECT()
        assert api.GetWindowRect(frame.hwnd, ctypes.byref(outer))
        assert api.GetClientRect(frame.hwnd, ctypes.byref(client))
        x, y = screen_point(QPoint(0, 0))
        outer = [outer.left, outer.top, outer.right, outer.bottom]
        client = [x, y, x + client.right, y + client.bottom]
        return outer, client

    normal, client = bounds()
    assert normal == client, (normal, client)  # Actual native frame, not just a cropped Qt screenshot.
    assert hit(normal[0] + 2, normal[1] + 2) == HTTOPLEFT
    left, top, right, bottom = normal
    mid_x, mid_y = (left + right) // 2, (top + bottom) // 2
    edge_points = [(left+2, top+2, HTTOPLEFT), (mid_x, top+2, HTTOP),
                   (right-2, top+2, HTTOPRIGHT), (left+2, mid_y, HTLEFT),
                   (right-2, mid_y, HTRIGHT), (left+2, bottom-2, HTBOTTOMLEFT),
                   (mid_x, bottom-2, HTBOTTOM), (right-2, bottom-2, HTBOTTOMRIGHT)]
    for x, y, expected in edge_points:
        assert hit(x, y) == expected, (x, y, expected)
    title_point = screen_point(window.page_title.mapTo(window, QPoint(15, 15)))
    assert hit(*title_point) == HTCAPTION
    for button in (window.title_bar.minimize_button, window.title_bar.maximize_button,
                   window.title_bar.close_button, window.process_picker):
        assert hit(*screen_point(button.mapTo(window, button.rect().center()))) == HTCLIENT
    window.grab().save(str(output / "normal.png"))
    window.title_bar.maximize_button.click()
    QTest.qWait(120)
    assert window.isMaximized()
    _, maximized = bounds()
    work = frame.monitor_info().rcWork
    work_area = [work.left, work.top, work.right, work.bottom]
    assert maximized == work_area, (maximized, work_area)
    assert hit(maximized[0] + 2, maximized[1] + 2) == HTCLIENT
    window.grab().save(str(output / "maximized.png"))
    window.title_bar.maximize_button.click()
    QTest.qWait(80)
    restored, _ = bounds()
    assert restored == normal, (restored, normal)
    # Send a native caption double-click; the OS should handle maximize/restore.
    point = screen_point(window.page_title.mapTo(window, QPoint(15, 15)))
    api.SendMessageW(frame.hwnd, 0x00A3, HTCAPTION, (point[0] & 0xffff) | ((point[1] & 0xffff) << 16))
    QTest.qWait(80)
    assert window.isMaximized()
    window.showNormal()
    window.title_bar.minimize_button.click()
    QTest.qWait(80)
    assert window.isMinimized()
    window.showNormal()
    QTest.qWait(80)
    window.title_bar.close_button.click()
    assert not window.isVisible()
    result = {"device_pixel_ratio": window.devicePixelRatioF(), "native_titlebar_height": 0,
              "normal_bounds": normal, "maximized_client_bounds": maximized, "work_area": work_area,
              "restored_bounds": restored, "resize_hit_test": True, "eight_resize_directions": True, "caption_hit_test": True,
              "native_double_click": True, "minimize_restore_close": True}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
