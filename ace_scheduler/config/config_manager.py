from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import shutil

from .models import AppConfig


def data_directory() -> Path:
    # Compatibility: v1.4 changes the product name, not the saved settings or lock.
    return Path(os.environ.get("APPDATA", Path.home())) / "ACE-CPU-Scheduler"


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "config.json"

    def load(self) -> tuple[AppConfig, str]:
        if not self.path.exists():
            return AppConfig(), ""
        try:
            return AppConfig.parse(json.loads(self.path.read_text(encoding="utf-8"))), ""
        except (OSError, ValueError, TypeError, KeyError) as exc:
            backup = self.path.with_suffix(f".invalid-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
            try:
                self.path.replace(backup)
                message = f"原配置已保留为 {backup.name}"
            except OSError:
                message = "无法备份原配置；本次先使用默认监控配置"
            return AppConfig(), f"配置读取失败：{exc}；{message}"

    def save(self, config: AppConfig) -> None:
        AppConfig.parse(config.to_dict())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                previous = None
            if isinstance(previous, dict) and previous.get("version", 1) == 1:
                backup = self.path.with_suffix(f".v1-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
                shutil.copy2(self.path, backup)
        # Atomic replacement avoids a partially written configuration after interruption.
        descriptor, filename = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(config.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(filename, self.path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
