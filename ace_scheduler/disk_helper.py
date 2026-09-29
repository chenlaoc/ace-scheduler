"""A read-only, killable collector; the mailbox is one bounded atomic snapshot."""
import json
import os
from pathlib import Path
import time


def run(path):
    try:
        _run(path)
    except Exception as exc:
        # A windowed packaged helper must not display an unhandled-error dialog.
        try:
            destination = Path(path)
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps({"error": str(exc)}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, destination)
        except OSError:
            pass
        return 1
    return 0


def _run(path):
    from ace_scheduler.windows.disk_counters import DiskCollector
    destination = Path(path)
    collector = DiskCollector()
    sequence = 0
    try:
        while True:
            started = time.perf_counter()
            if time.monotonic() - collector.last_discovery >= 30:
                collector.discover()
            packet = {"sequence": sequence, "timestamp": time.monotonic(),
                      "devices": [q.device for q in collector.queries.values()],
                      "rows": collector.sample()}
            packet["cost_seconds"] = time.perf_counter() - started
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(packet, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            os.replace(temporary, destination)
            sequence += 1
            time.sleep(max(.05, 1 - (time.perf_counter() - started)))
    finally:
        collector.close()
