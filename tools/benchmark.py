"""Read-only GUI idle measurement. Run from the repository root with -m tools.benchmark."""
from dataclasses import asdict
import json
from pathlib import Path
import time

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.ui.theme import apply_palette


def main():
    output = Path("artifacts/benchmark-v1.1")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_palette(app)
    window = MainWindow(AppConfig(), ConfigManager(output / "config.json"), read_only=True)
    window.show()
    process = psutil.Process()
    baseline = {}

    def begin():
        cpu = process.cpu_times()
        baseline.update(time=time.monotonic(), cpu=cpu.user + cpu.system,
                        handles=process.num_handles(), io=process.io_counters())
        QTimer.singleShot(20000, finish)

    def finish():
        cpu = process.cpu_times()
        elapsed = time.monotonic() - baseline["time"]
        percent = (cpu.user + cpu.system - baseline["cpu"]) / elapsed * 100
        io = process.io_counters()
        payload = {"seconds": elapsed, "cpu_percent_one_logical_cpu": percent,
                   "cpu_percent_machine": percent / (psutil.cpu_count() or 1),
                   "working_set_MB": process.memory_info().rss / 1e6,
                   "handles_before": baseline["handles"], "handles_after": process.num_handles(),
                   "read_bytes_delta": io.read_bytes - baseline["io"].read_bytes,
                   "write_bytes_delta": io.write_bytes - baseline["io"].write_bytes,
                   "monitored_instances": window.table.rowCount(),
                   "topology": asdict(window.topology) if window.topology else None}
        (output / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        window.close()

    QTimer.singleShot(2000, begin)
    app.exec()


if __name__ == "__main__":
    main()
