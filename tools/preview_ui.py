"""Render the real Qt widgets with explicitly labelled synthetic data for visual QA."""
import math
import argparse
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_metrics import Metrics, ProcessIdentity
from ace_scheduler.core.process_monitor import ProcessRow
from ace_scheduler.core.scheduler import ScheduleState
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.ui.affinity_dialog import AffinityDialog
from ace_scheduler.ui.theme import apply_palette
from ace_scheduler.windows.eco_qos import EcoState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/preview"))
    parser.add_argument("--theme", choices=("system", "light", "dark"), default="system")
    args = parser.parse_args()
    folder = args.output
    folder.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_palette(app)
    window = MainWindow(AppConfig(theme=args.theme), ConfigManager(folder / "config.json"), start_worker=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    topology = CpuTopology.detect()
    window.on_ready(topology)
    window.banner.setText("界面预览 · 以下为模拟数据，未操作真实进程")
    now = time.monotonic()
    identity = ProcessIdentity(4260, 1, "SGuard64.exe")
    for second in range(61):
        t = now - 60 + second
        base = 7.5 if second < 31 else 3.5
        metrics = Metrics(t, base + math.sin(second * .4) * .8,
                          base * 5 + math.sin(second * .36) * 4 + math.sin(second * 1.2) * 2,
                          .5 + math.sin(second * .23) * .22, 12.45, .08, 186.5, 16000, 42, 1)
        window.history.add(identity, metrics)
        if second == 30:
            window.history.mark(identity, t, "已验证（模拟）")
    other = ProcessIdentity(6812, 1, "SGuardSvc64.exe")
    row = ProcessRow(identity.name, identity.pid, identity, metrics,
                     ScheduleState(0x40, topology.available[-1:], EcoState(1, 1)), "持续维护 · 已验证")
    second = ProcessRow(other.name, other.pid, other, Metrics(now, .02, .04, .01, .28, .01, 32.6),
                        ScheduleState(0x20, topology.available, EcoState(0, 0)), "仅监控")
    window.on_snapshot([row, second], {"sguard64.exe"}, 1)
    window.log.appendPlainText("12:00:00 SGuard64.exe PID=4260 detected（模拟）\n12:00:30 Priority → Idle OK（模拟）\n12:00:30 EcoQoS → ON OK（模拟）")
    window.policy_page.set_preset(window.policy_page.selected_keys(), "Strong")
    window.policy_page.set_preset(("ace-tray.exe",), "Mild")
    window.resize(1280, 860)
    window.show()
    for index, name in enumerate(("overview", "policy", "experiment", "settings", "about")):
        window.show_page(index)
        app.processEvents()
        window.grab().save(str(folder / f"{index + 1:02}-{name}.png"))
    window.resize(1040, 700)
    for index, name in ((3, "settings"), (4, "about")):
        window.show_page(index)
        app.processEvents()
        window.grab().save(str(folder / f"compact-{name}.png"))
    window.show_page(1)
    app.processEvents()
    window.grab().save(str(folder / "05-compact-policy.png"))
    window.show_page(0)
    app.processEvents()
    window.grab().save(str(folder / "08-compact-overview.png"))
    window.close()

    many = MainWindow(AppConfig(theme=args.theme), ConfigManager(folder / "simulation-64.json"), start_worker=False)
    many.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    many.on_ready(CpuTopology(32, 64, tuple(range(64)), name="64 CPU 界面模拟"))
    many.banner.setText("界面预览 · 模拟 64 个逻辑处理器，未操作真实进程")
    many.resize(1040, 700)
    many.show_page(1)
    many.show()
    app.processEvents()
    dialog = AffinityDialog(many.topology, [many.config.rules[0]], many.policy_page)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog.show()
    app.processEvents()
    dialog.editor.area.ensureWidgetVisible(dialog.editor.checks[63])
    app.processEvents()
    dialog.grab().save(str(folder / "09-64cpu-dialog.png"))
    dialog.close()
    many.close()


if __name__ == "__main__":
    main()
