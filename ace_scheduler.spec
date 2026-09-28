# Onedir intentionally avoids onefile's extraction on every launch.
from pathlib import Path
import importlib.util
import os
import sys
import json
import subprocess
from PyInstaller.utils.hooks import copy_metadata

project = Path(SPECPATH)
source_version = {}
exec((project / 'ace_scheduler/__init__.py').read_text(encoding='utf-8'), source_version)
commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=project, capture_output=True, text=True)
status = subprocess.run(['git', 'status', '--porcelain'], cwd=project, capture_output=True, text=True)
build_info = Path(workpath) / 'build-info.json'
build_info.parent.mkdir(parents=True, exist_ok=True)
build_info.write_text(json.dumps({'version': source_version['__version__'],
    'commit': commit.stdout.strip() if commit.returncode == 0 else 'unknown',
    'dirty': bool(status.stdout)}), encoding='utf-8')
# Resolve dependencies only from Python/Qt and Windows. Unrelated applications on
# PATH may ship DLLs with the same basename but incompatible exports (e.g. ICU).
system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
runtime_paths = [Path(sys.executable).parent, Path(sys.base_prefix),
                 system_root / 'System32', system_root]
for package in ('PySide6', 'shiboken6'):
    runtime_paths.append(Path(importlib.util.find_spec(package).origin).parent)
os.environ['PATH'] = os.pathsep.join(map(str, runtime_paths))
license_data = []
for dependency in ('PySide6-Essentials', 'shiboken6', 'psutil'):
    license_data += copy_metadata(dependency)
analysis = Analysis(
    [str(project / 'launcher.py')],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / 'README.md'), '.'), (str(project / 'LICENSE'), '.'),
           (str(build_info), '.'),
           (str(project / 'THIRD_PARTY_NOTICES.md'), '.'),
           (str(project / 'docs'), 'docs'), (str(project / 'assets' / 'brand'), 'assets/brand'),
           (str(project / 'licenses'), 'licenses'),
           (str(Path(sys.base_prefix) / 'LICENSE.txt'), 'licenses/python')] + license_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtCharts', 'tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name='ACE-Scheduler',
    icon=str(project / 'assets' / 'brand' / 'logo.png'),
    version=str(project / 'assets' / 'brand' / 'version_info.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get('ACE_SCHEDULER_CONSOLE') == '1',
    # main requests elevation at normal startup; keep explicit read-only CLI modes unelevated.
    uac_admin=False,
)
collect = COLLECT(exe, analysis.binaries, analysis.datas,
                  strip=False, upx=False, name='ACE-Scheduler')
