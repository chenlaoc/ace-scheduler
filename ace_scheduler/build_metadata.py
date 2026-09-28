"""Build provenance shared by the frozen application and release validation."""
import re


BUILD_DEPENDENCIES = ("PySide6-Essentials", "shiboken6", "psutil", "PyInstaller", "Pillow")


def validate_build_info(data, version, *, expected_commit=None, require_clean=False):
    if not isinstance(data, dict) or data.get("version") != version:
        raise ValueError("Embedded build version differs from source")
    commit = data.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Embedded build commit is missing or invalid")
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("Embedded build commit differs from expected release commit")
    if type(data.get("dirty")) is not bool:
        raise ValueError("Embedded build dirty state is missing or invalid")
    if require_clean and data["dirty"]:
        raise ValueError("Release package must be built from a clean working tree")
    dependencies = data.get("dependencies")
    if not isinstance(dependencies, dict) or set(dependencies) != set(BUILD_DEPENDENCIES):
        raise ValueError("Embedded build dependency inventory is missing or invalid; rebuild the application")
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9][a-zA-Z0-9.!+_-]{0,99}", v)
           for v in dependencies.values()):
        raise ValueError("Embedded dependency version is invalid")
    digest = data.get("requirements_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Embedded requirements digest is missing or invalid")
    return {key: data[key] for key in ("version", "commit", "dirty", "dependencies", "requirements_sha256")}
