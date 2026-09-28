"""Shared identity for the application and its distributable assets."""
from pathlib import Path

from PySide6.QtGui import QIcon

APP_NAME = "ACE Scheduler"
BINARY_NAME = "ACE-Scheduler"
# Keep the existing storage namespace so upgrades retain settings and the same lock.
DATA_NAMESPACE = "ACE-CPU-Scheduler"


def asset_path(name: str) -> Path:
    # PyInstaller preserves __file__ below the bundled _internal directory.
    return Path(__file__).resolve().parents[1] / "assets" / "brand" / name


def app_icon() -> QIcon:
    return QIcon(str(asset_path("logo.png")))
