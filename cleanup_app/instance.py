import ctypes
from ctypes import wintypes
from .constants import INSTANCE_MUTEX_NAME


def enable_dpi_awareness():
    """Render the GUI crisply on scaled displays; a no-op when unsupported.

    System-DPI awareness is enough for a single display: Tk then draws at the
    monitor's real DPI instead of letting Windows stretch a bitmap of the
    window. Must run before the first ``tk.Tk()`` and is safe to call twice.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class SingleInstance:
    """Process-wide Windows mutex preventing concurrent SQLite session access."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self.handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
        self.acquired = bool(self.handle) and kernel32.GetLastError() != self.ERROR_ALREADY_EXISTS

    def close(self):
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None

