"""Run the built EXE in isolated read-only mode, then produce a verified portable ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from tools.validate_release import ROOT, validate
from ace_scheduler.build_metadata import validate_build_info


def validate_provenance(package, version, expected_commit):
    """Validate the artifact before launching it or writing any release output."""
    info = json.loads((package / "_internal/build-info.json").read_text(encoding="utf-8"))
    build = validate_build_info(info, version, expected_commit=expected_commit, require_clean=True)
    lock = package / "_internal/requirements-lock.txt"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != build["requirements_sha256"]:
        raise ValueError("Packaged requirements differ from embedded build information")
    return build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=ROOT / "dist/ACE-Scheduler")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--evidence", type=Path, default=ROOT / "artifacts/release-smoke")
    parser.add_argument("--expected-commit", help="Expected full Git commit; defaults to the current HEAD")
    args = parser.parse_args()
    version = validate()
    package, output, evidence = (path.resolve() for path in (args.package, args.output, args.evidence))
    executable = package / "ACE-Scheduler.exe"
    if not executable.is_file() or not (package / "_internal").is_dir():
        raise FileNotFoundError("Build ACE-Scheduler.exe with PyInstaller first")
    expected_commit = args.expected_commit
    if expected_commit is None:
        expected_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                         capture_output=True, text=True, check=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("Expected release commit must be a full 40-character Git SHA")
    build = validate_provenance(package, version, expected_commit)
    output.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    # A fresh output directory prevents an old smoke.json from masking a failed run.
    smoke = Path(tempfile.mkdtemp(prefix="run-", dir=evidence))
    result = subprocess.run([str(executable), "--smoke-test", str(smoke)],
                            cwd=tempfile.gettempdir(), capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (smoke / "stdout.txt").write_bytes(result.stdout)
    (smoke / "stderr.txt").write_bytes(result.stderr)
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(f"Packaged startup failed: exit={result.returncode}; see {smoke}")
    report = json.loads((smoke / "smoke.json").read_text(encoding="utf-8"))
    for field in ("ready", "read_only", "frameless", "icon_loaded"):
        if report.get(field) is not True:
            raise RuntimeError(f"Startup validation failed: {field}")
    if report.get("name") != "ACE Scheduler" or report.get("version") != version:
        raise RuntimeError("Packaged application identity differs from source")
    with zipfile.ZipFile(smoke / "diagnostics.zip") as diagnostics:
        diagnostic_report = json.loads(diagnostics.read("report.json"))
    if diagnostic_report["build"]["version"] != version or version not in report.get("about_version", ""):
        raise RuntimeError("Diagnostic or About version differs from the packaged application")
    for reported_build in (report.get("build", {}), diagnostic_report.get("build", {})):
        if (reported_build.get("distribution") != "packaged"
                or validate_build_info(reported_build, version, expected_commit=expected_commit,
                                       require_clean=True) != build):
            raise RuntimeError("Running executable build information differs from the packaged artifact")
    if report.get("pages") != ["overview", "policy", "experiment", "settings", "about"]:
        raise RuntimeError("Incomplete page capture")
    for page in report["pages"]:
        if (smoke / f"{page}.png").stat().st_size < 1000:
            raise RuntimeError(f"Empty page screenshot: {page}")
    manifest = {"name": "ACE Scheduler", **build,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "startup_verified": True}
    (package / "_internal/build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = output / f"ACE-Scheduler-v{version}-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for file in sorted(package.rglob("*")):
            if file.is_file():
                stream.write(file, Path("ACE-Scheduler") / file.relative_to(package))
    with zipfile.ZipFile(archive) as stream:
        damaged = stream.testzip()
        if damaged:
            raise RuntimeError(f"ZIP CRC failed: {damaged}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(r"^## \[" + re.escape(version) + r"\].*?\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    notes = f"# ACE Scheduler v{version}\n\n{section.group(1).strip()}\n\n"
    notes += "Windows x64 便携版。请解压整个目录并保留 `_internal`。旧版用户继续使用原配置目录。\n\n"
    notes += f"SHA-256 (`{archive.name}`):\n\n```text\n{digest}\n```\n"
    (output / "release-notes.md").write_text(notes, encoding="utf-8")
    (evidence / "result.json").write_text(json.dumps({**manifest, "archive": archive.name,
        "archive_sha256": digest, "bytes": archive.stat().st_size, "smoke_directory": smoke.name}, indent=2), encoding="utf-8")
    print(f"Verified {archive.name} ({archive.stat().st_size:,} bytes)\nSHA256 {digest}")


if __name__ == "__main__":
    main()
