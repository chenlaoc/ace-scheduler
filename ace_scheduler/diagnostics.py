"""Local, bounded support bundle. Arbitrary log text is never copied verbatim."""
from dataclasses import asdict
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import zipfile

from ace_scheduler import __version__


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def build_info():
    info = {"version": __version__, "commit": "unknown", "dirty": None,
            "distribution": "packaged" if getattr(sys, "frozen", False) else "source"}
    try:
        path = ROOT / "build-info.json"
        if getattr(sys, "frozen", False) and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == __version__ and re.fullmatch(r"[0-9a-f]{7,40}", data.get("commit", "")):
                info.update(commit=data["commit"], dirty=data.get("dirty") is True)
        elif not getattr(sys, "frozen", False):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, timeout=2, creationflags=flags)
            state = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                   text=True, timeout=2, creationflags=flags)
            if commit.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit.stdout.strip()):
                info.update(commit=commit.stdout.strip(), dirty=bool(state.stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return info


def summarize_logs(text):
    """Allowlisted event summaries exclude names, paths, PIDs, tokens and free text."""
    categories = (("恢复冲突", "recovery-conflict"), ("恢复", "recovery"),
                  ("后台", "worker"), ("采样", "sampling"), ("维护", "maintenance"),
                  ("detected", "process-observed"), ("exited", "process-exited"),
                  ("提权", "elevation"), ("配置", "configuration"), ("启动", "startup"))
    output = []
    for line in text.splitlines()[-500:]:
        category = next((value for marker, value in categories if marker in line), "event")
        stamp = re.match(r"(?:\d{4}-\d\d-\d\d )?\d\d:\d\d:\d\d", line)
        status = "failure" if any(word in line for word in ("失败", "冲突", "ERROR", "AccessDenied", "Error")) else "info"
        fields = [field for field in ("priority", "affinity", "eco") if re.search(r"\b" + field + r"\b", line)]
        codes = re.findall(r"WinError[ =\]]*(\d{1,5})", line)[:3]
        output.append(f"{stamp.group() if stamp else '-'} {status} {category} fields={','.join(fields) or '-'} winerror={','.join(codes) or '-'}")
    return "\n".join(output)


def export_diagnostics(destination, config, topology, log_text, directory, *, read_only=False, worker_failed=False):
    destination = Path(destination)
    if destination.suffix.casefold() != ".zip":
        destination = destination.with_name(destination.name + ".zip")
    config_summary = {"monitor_interval": config.monitor_interval, "enforce_interval": config.enforce_interval,
                      "close_to_tray": config.close_to_tray, "theme": config.theme,
                      "rules": [{"index": index, "builtin": rule.builtin, "enabled": rule.enabled,
                                 "keep_enforced": rule.keep_enforced, "policy": asdict(rule.policy)}
                                for index, rule in enumerate(config.rules)]}
    topology_summary = None if topology is None else {
        "physical": topology.physical, "logical": topology.logical, "groups": topology.groups,
        "available_cpu_count": len(topology.available), "affinity_supported": topology.affinity_supported}
    report = {"schema": 1, "build": build_info(), "system": {"os": platform.system(), "release": platform.release(),
              "version": platform.version(), "machine": platform.machine(), "python": platform.python_version()},
              "topology": topology_summary, "config": config_summary,
              "session": {"read_only": read_only, "worker_failed": bool(worker_failed)}}
    log_parts = []
    for name in ("scheduler.log.2", "scheduler.log.1", "scheduler.log"):
        path = Path(directory) / name
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 96_000))
                log_parts.append(summarize_logs(stream.read(96_000).decode("utf-8", errors="replace")))
        except OSError:
            continue
    log_parts.append(summarize_logs(log_text[-96_000:]))
    # Build beside the destination; failure leaves an existing archive unchanged.
    fd, temporary = tempfile.mkstemp(prefix="ace-diagnostics-", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))
            archive.writestr("events.txt", "\n".join(log_parts))
            archive.writestr("README.txt", "Local diagnostics only; no upload.\n"
                            "Logs contain allowlisted event categories, fields and error codes, not raw messages.\n"
                            "No process names/PIDs, user paths, raw config, recovery or handoff files are included.\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
