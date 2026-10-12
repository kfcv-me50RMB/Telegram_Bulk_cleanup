"""Application branding; resources live separately from account data."""

import ctypes
import sys
import tkinter as tk
from pathlib import Path


APP_USER_MODEL_ID = "TelegramBulkCleanup.Desktop.1"


def icon_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "assets" / "app_icon.ico"


def _diagnostic(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def set_taskbar_identity():
    if sys.platform != "win32":
        return
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(APP_USER_MODEL_ID)
        if result < 0:
            raise OSError(f"HRESULT {result:#x}")
    except (AttributeError, OSError) as exc:
        _diagnostic(f"无法设置任务栏标识：{exc}")


def apply_window_icon(root):
    try:
        root.iconbitmap(default=str(icon_path()))
    except (tk.TclError, OSError) as exc:
        _diagnostic(f"无法加载程序图标：{exc}")
