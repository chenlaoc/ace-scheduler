"""Read-only disk probe and an optional disposable file workload; no process scheduling."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import psutil

from ace_scheduler.core.clock import ClockTracker
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.windows.disk_counters import DiskCollector


def file_load(path, seconds):
    chunk = b"ACE validation\0" * (1024 * 512)
    end = time.monotonic() + seconds
    with open(path, "w+b", buffering=0) as stream:
        while time.monotonic() < end:
            stream.seek(0)
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            stream.read(len(chunk))
            time.sleep(.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/stage-four/disk-probe.json"))
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--load-file", type=Path)
    args = parser.parse_args()
    if not 3 <= args.seconds <= 600:
        parser.error("seconds must be 3..600")
    if args.load_file:
        file_load(args.load_file, args.seconds)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    before = process.cpu_times()
    clock, collector = ClockTracker(), DiskCollector()
    child = None
    payload = {"scope": "physical-device total; includes unrelated activity", "python": sys.version,
               "topology": asdict(CpuTopology.detect()), "packets": [], "load": args.load}
    with tempfile.TemporaryDirectory(prefix="ace-file-load-", dir=args.output.parent) as folder:
        try:
            payload["devices"] = collector.discover()
            if args.load:
                child = subprocess.Popen([sys.executable, "-m", "tools.stage_four_probe", "--load-file",
                                          str(Path(folder) / "probe.bin"), "--seconds", str(args.seconds)],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(args.seconds):
                start = time.perf_counter()
                rows = collector.sample()
                cost = time.perf_counter() - start
                payload["packets"].append({"rows": rows, "clock": clock.sample(), "cost_seconds": cost,
                                           "rss": process.memory_info().rss, "handles": process.num_handles()})
                time.sleep(max(.01, 1 - cost))
        finally:
            collector.close()
            if child:
                child.wait(timeout=args.seconds + 10)
    after = process.cpu_times()
    payload["cpu_seconds"] = after.user + after.system - before.user - before.system
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Saved {len(payload['packets'])} intervals to {args.output}")


if __name__ == "__main__":
    main()
