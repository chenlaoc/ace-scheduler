from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.windows.window_frame import (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT,
    HTCLIENT, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, resize_hit)
from tests.test_ui import app


@pytest.mark.parametrize("point, expected", [
    ((-1599, -899), HTTOPLEFT), ((-900, -899), HTTOP), ((-321, -899), HTTOPRIGHT),
    ((-1599, -400), HTLEFT), ((-321, -400), HTRIGHT),
    ((-1599, -41), HTBOTTOMLEFT), ((-900, -41), HTBOTTOM), ((-321, -41), HTBOTTOMRIGHT),
    ((-900, -400), HTCLIENT), ((-1601, -901), HTCLIENT),
])
def test_resize_edges_corners_and_negative_monitor_coordinates(point, expected):
    bounds = (-1600, -900, -320, -40)
    assert resize_hit(*point, bounds, 8) == expected
    assert resize_hit(*point, bounds, 8, maximized=True) == HTCLIENT


def test_caption_controls_and_process_picker_are_not_drag_regions(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.show()
    app.processEvents()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    point = window.page_title.mapTo(window, QPoint(15, 15))
    assert window.is_caption_point(point.x(), point.y())
    for widget in (window.process_picker, window.title_bar.minimize_button,
                   window.title_bar.maximize_button, window.title_bar.close_button):
        point = widget.mapTo(window, widget.rect().center())
        assert not window.is_caption_point(point.x(), point.y())
    assert not window.is_caption_point(500, window.height() - 20)
    window.close()


def test_caption_maximize_restore_minimize_and_double_click(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.show()
    app.processEvents()
    normal_size = window.size()
    bar = window.title_bar
    bar.maximize_button.click()
    app.processEvents()
    assert window.isMaximized() and bar.maximize_button.kind == "restore"
    assert bar.maximize_button.accessibleName() == "还原窗口"
    bar.maximize_button.click()
    app.processEvents()
    assert not window.isMaximized() and window.size() == normal_size
    bar.minimize_button.click()
    app.processEvents()
    assert window.isMinimized()
    window.showNormal()
    app.processEvents()
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
    app.processEvents()
    assert window.isMaximized()
    window.showNormal()
    bar.close_button.click()
    assert not window.isVisible()


def test_integrated_close_still_waits_for_pending_commands(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.show()
    window.thread = SimpleNamespace()
    window.pending_commands = 1
    window.title_bar.close_button.click()
    assert window.isVisible() and window.close_after_command
    assert not window.shutting_down
    window.thread = None
    window.close()
