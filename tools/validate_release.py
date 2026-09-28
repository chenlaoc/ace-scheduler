"""Validate the checked-in identity and optional release tag without executing GUI code."""
import argparse
from pathlib import Path
import re

from ace_scheduler import __version__

ROOT = Path(__file__).resolve().parents[1]


def validate(tag=None):
    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        raise ValueError("Version must use X.Y.Z")
    if tag is not None and tag != f"v{__version__}":
        raise ValueError(f"Expected v{__version__}, got {tag!r}")
    resource = (ROOT / "assets/brand/version_info.txt").read_text(encoding="utf-8")
    expected = tuple(map(int, __version__.split("."))) + (0,)
    for field in ("filevers", "prodvers"):
        found = re.search(rf"{field}=\(([^)]+)\)", resource)
        if not found or tuple(int(v.strip()) for v in found.group(1).split(",")) != expected:
            raise ValueError(f"Windows {field} differs from {__version__}")
    for field in ("FileVersion", "ProductVersion"):
        if f"StringStruct('{field}', '{__version__}')" not in resource:
            raise ValueError(f"Windows {field} differs from {__version__}")
    if f"## [{__version__}]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        raise ValueError("Missing changelog entry")
    if not (ROOT / "assets/brand/logo.png").is_file():
        raise ValueError("Missing logo")
    return __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(f"ACE Scheduler {validate(args.tag)}: metadata valid")


if __name__ == "__main__":
    main()
