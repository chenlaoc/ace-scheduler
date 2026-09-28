import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys


def is_admin() -> bool:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = wintypes.BOOL
    return bool(shell.IsUserAnAdmin())


def request_elevation(arguments=None) -> None:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_ssize_t
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if getattr(sys, "frozen", False):
        directory = str(Path(sys.executable).parent)
    else:
        arguments = ["-m", "ace_scheduler", *arguments]
        directory = str(Path(__file__).resolve().parents[2])
    result = shell.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(arguments), directory, 1)
    if result <= 32:
        raise OSError(f"UAC 提权取消或失败（ShellExecuteW={result}）")
