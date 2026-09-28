"""Release closed test windows before another test changes the global Qt theme."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def release_closed_windows():
    yield
    app = QApplication.instance()
    if app is None:
        return
    from ace_scheduler.ui.main_window import MainWindow
    for window in app.topLevelWidgets():
        if isinstance(window, MainWindow) and not window.isVisible():
            if window.thread is None or not window.thread.isRunning():
                window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
