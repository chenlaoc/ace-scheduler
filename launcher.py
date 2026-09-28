"""PyInstaller entry point (absolute imports also work in onedir)."""
from ace_scheduler.main import main

if __name__ == "__main__":
    raise SystemExit(main())
