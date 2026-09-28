from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import struct
import sys
import psutil

from ace_scheduler import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="ACE Scheduler")
    parser.add_argument("--version", action="version", version=f"ACE Scheduler {__version__}")
    parser.add_argument("--monitor-only", action="store_true", help="只读监控；不请求 UAC，不允许修改调度")
    parser.add_argument("--smoke-test", type=Path, help="开发验证：只读启动，输出截图/JSON 并自动退出")
    parser.add_argument("--handoff", help=argparse.SUPPRESS)
    parser.add_argument("--elevated-start", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8 or sys.version_info < (3, 11):
        print("ACE Scheduler requires Windows and 64-bit Python 3.11+.")
        return 1

    from PySide6.QtCore import QLockFile, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ace_scheduler.ui.theme import apply_palette
    from ace_scheduler.config.config_manager import ConfigManager, data_directory
    from ace_scheduler.config.models import AppConfig
    from ace_scheduler.ui.main_window import MainWindow
    from ace_scheduler.windows.elevation import is_admin, request_elevation
    from ace_scheduler.branding import APP_NAME, DATA_NAMESPACE, app_icon
    from ace_scheduler.instance import InstanceServer, activate_existing
    from ace_scheduler.handoff import ElevationHandoff, prepare_child
    from ace_scheduler.errors import ExceptionReporter

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(DATA_NAMESPACE)
    app.setWindowIcon(app_icon())
    apply_palette(app)
    read_only = args.monitor_only or bool(args.smoke_test)
    admin = is_admin()
    warning = ""
    if args.handoff and (read_only or not admin):
        QMessageBox.warning(None, APP_NAME, "请使用当前 Windows 用户的管理员权限。原窗口仍会保留。")
        return 1
    try:
        data = args.smoke_test.resolve() if args.smoke_test else data_directory()
        data.mkdir(parents=True, exist_ok=True)
        if not read_only and not admin and not args.handoff:
            # Activate before requesting UAC. Never hold the instance lock while
            # starting the elevated child, and never create an ordinary editor.
            if activate_existing(data):
                return 0
            if args.elevated_start:
                QMessageBox.critical(None, APP_NAME, "未能获得管理员权限。请重新启动程序，或使用 --monitor-only 只读模式。")
                return 1
            try:
                request_elevation(["--elevated-start"])
            except OSError as exc:
                if getattr(exc, "winerror", None) != 1223:
                    QMessageBox.critical(None, "无法启动管理员窗口", str(exc))
                return 1
            return 0
        lock = QLockFile(str(data / "instance.lock"))
        lock.setStaleLockTime(0)
        transfer_guard = QLockFile(str(data / "transfer.lock"))
        transfer_guard.setStaleLockTime(0)
        if not args.handoff and (not transfer_guard.tryLock(0) or not lock.tryLock(0)):
            if activate_existing(data):
                return 0
            QMessageBox.information(None, APP_NAME, "已有实例正在运行，或配置目录被锁定。")
            return 1
        transfer_guard.unlock()
        manager = ConfigManager(data / "config.json")
        window = None
        if args.handoff:
            window = prepare_child(data, args.handoff, lock, app,
                                   lambda config: MainWindow(config, manager, start_worker=False,
                                                             enable_tray=True, write_enabled=True))
        handler = RotatingFileHandler(data / "scheduler.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger("ace_scheduler").addHandler(handler)
        logging.getLogger("ace_scheduler").setLevel(logging.INFO)
        config, config_warning = (window.config, "") if window else manager.load() if not args.smoke_test else (AppConfig(), "")
    except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
        QMessageBox.critical(None, "无法初始化配置目录", str(exc))
        return 1
    if window:
        window.start_monitor()
        warning = "已获得管理员权限，之前的编辑都已保留。当前只观察，请再次点击要执行的操作。"
    else:
        window = MainWindow(config, manager, read_only, enable_tray=not bool(args.smoke_test), write_enabled=admin)
    instance = None
    try:
        instance = InstanceServer(data, app)
        instance.activated.connect(window.activate_window)
    except OSError as exc:
        window.log.appendPlainText("已有实例唤醒服务不可用：" + str(exc))
    handoff = ElevationHandoff(window, lock, instance)
    errors = ExceptionReporter(window)
    if not read_only and not admin:
        window.elevation_request = handoff.start
    window.show()
    if warning or config_warning:
        window.log.appendPlainText("\n".join(filter(None, (warning, config_warning))))
        window.banner.setText(window.banner.text() + "\n" + "\n".join(filter(None, (warning, config_warning))))
    if args.smoke_test:
        def capture():
            from dataclasses import asdict
            from ace_scheduler.diagnostics import build_info, export_diagnostics
            payload = {"name": APP_NAME, "version": __version__, "icon_loaded": not window.windowIcon().isNull(),
                       "build": build_info(), "about_version": window.about_page.version.text(),
                       "ready": window.topology is not None, "read_only": window.read_only,
                       "frameless": bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint),
                       "native_frame": window.native_frame is not None,
                       "window_controls": [button.accessibleName() for button in
                                           (window.title_bar.minimize_button, window.title_bar.maximize_button,
                                            window.title_bar.close_button)],
                       "topology": asdict(window.topology) if window.topology else None,
                       "rules": len(window.config.rules), "live_rows": window.table.rowCount(),
                       "log": window.log.toPlainText()}
            window.grab().save(str(data / "window.png"))
            payload["pages"] = []
            for index, name in enumerate(("overview", "policy", "experiment", "settings", "about")):
                window.show_page(index)
                app.processEvents()
                window.grab().save(str(data / f"{name}.png"))
                payload["pages"].append(name)
            export_diagnostics(data / "diagnostics.zip", window.config, window.topology,
                               window.log.toPlainText(), data, read_only=True)
            (data / "smoke.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
        QTimer.singleShot(3000, capture)
    code = app.exec()
    errors.close()
    if instance:
        instance.close()
    if handoff.instance and handoff.instance is not instance:
        handoff.instance.close()
    handler.close()
    lock.unlock()
    return code
