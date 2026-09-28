import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from ace_scheduler import __version__
from ace_scheduler.build_metadata import BUILD_DEPENDENCIES
from tools import package_release


COMMIT = "a" * 40


@pytest.fixture
def artifact(tmp_path):
    package = tmp_path / "package"
    internal = package / "_internal"
    internal.mkdir(parents=True)
    (package / "ACE-Scheduler.exe").write_bytes(b"mock executable")
    lock = b"build-time-lock==1.0\n"
    (internal / "requirements-lock.txt").write_bytes(lock)
    info = {"version": __version__, "commit": COMMIT, "dirty": False,
            "dependencies": {name: "1.0" for name in BUILD_DEPENDENCIES},
            "requirements_sha256": hashlib.sha256(lock).hexdigest()}
    (internal / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    return package, info


def invoke(monkeypatch, tmp_path, package):
    monkeypatch.setattr("sys.argv", ["package_release", "--package", str(package),
                                   "--output", str(tmp_path / "output"),
                                   "--evidence", str(tmp_path / "evidence"),
                                   "--expected-commit", COMMIT])
    package_release.main()


@pytest.mark.parametrize("update,match", [
    ({"commit": "b" * 40}, "commit differs"),
    ({"commit": "unknown"}, "commit is missing"),
    ({"dirty": True}, "clean working tree"),
    ({"dirty": None}, "dirty state"),
    ({"dependencies": None}, "dependency inventory"),
    ({"version": "0.0.0"}, "version differs"),
])
def test_invalid_provenance_rejected_before_launch_or_zip(artifact, tmp_path, monkeypatch, update, match):
    package, info = artifact
    info.update(update)
    (package / "_internal/build-info.json").write_text(json.dumps(info), encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid artifact must not be launched")
    monkeypatch.setattr(package_release.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match=match):
        invoke(monkeypatch, tmp_path, package)
    assert not (tmp_path / "output").exists()
    assert not (package / "_internal/build-manifest.json").exists()


def test_default_expected_commit_comes_from_head(artifact, tmp_path, monkeypatch):
    package, _ = artifact
    monkeypatch.setattr("sys.argv", ["package_release", "--package", str(package)])
    calls = []
    def git_only(command, **kwargs):
        calls.append(command)
        assert command == ["git", "rev-parse", "HEAD"]
        return SimpleNamespace(stdout="b" * 40)
    monkeypatch.setattr(package_release.subprocess, "run", git_only)
    with pytest.raises(ValueError, match="commit differs"):
        package_release.main()
    assert len(calls) == 1


def smoke_runner(info):
    def run(command, **kwargs):
        smoke = Path(command[-1])
        build = {**info, "distribution": "packaged"}
        pages = ["overview", "policy", "experiment", "settings", "about"]
        report = {"ready": True, "read_only": True, "frameless": True, "icon_loaded": True,
                  "name": "ACE Scheduler", "version": __version__, "about_version": __version__,
                  "build": build, "pages": pages}
        (smoke / "smoke.json").write_text(json.dumps(report), encoding="utf-8")
        with zipfile.ZipFile(smoke / "diagnostics.zip", "w") as archive:
            archive.writestr("report.json", json.dumps({"build": build}))
        for page in pages:
            (smoke / f"{page}.png").write_bytes(b"x" * 1001)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    return run


def test_manifest_uses_build_dependencies_and_lock_not_packaging_host(artifact, tmp_path, monkeypatch):
    package, info = artifact
    monkeypatch.setattr(package_release.subprocess, "run", smoke_runner(info))
    invoke(monkeypatch, tmp_path, package)
    archive = next((tmp_path / "output").glob("*.zip"))
    with zipfile.ZipFile(archive) as stream:
        manifest = json.loads(stream.read("ACE-Scheduler/_internal/build-manifest.json"))
        assert manifest["commit"] == COMMIT and manifest["dirty"] is False
        assert manifest["dependencies"] == info["dependencies"]
        assert stream.read("ACE-Scheduler/_internal/requirements-lock.txt") == b"build-time-lock==1.0\n"
    assert archive.with_suffix(".zip.sha256").read_text().startswith(hashlib.sha256(archive.read_bytes()).hexdigest())


def test_runtime_build_mismatch_blocks_zip(artifact, tmp_path, monkeypatch):
    package, info = artifact
    monkeypatch.setattr(package_release.subprocess, "run", smoke_runner({**info, "commit": "c" * 40}))
    with pytest.raises(ValueError, match="commit differs"):
        invoke(monkeypatch, tmp_path, package)
    assert not list((tmp_path / "output").glob("*.zip"))
    assert not (package / "_internal/build-manifest.json").exists()


def test_replaced_lock_rejected(artifact):
    package, _ = artifact
    (package / "_internal/requirements-lock.txt").write_bytes(b"another lock")
    with pytest.raises(ValueError, match="requirements differ"):
        package_release.validate_provenance(package, __version__, COMMIT)
