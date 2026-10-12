"""Compatibility imports and executable entrypoint for Telegram cleanup."""

import asyncio
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from cleanup_app.app import CleanupApp
from cleanup_app.constants import SHUTDOWN_TIMEOUT
from cleanup_app.dialogs import LoginDialog, LoginMethodDialog, QRLoginDialog
from cleanup_app.models import CleanupSelection, CleanupStopped, StoredSessionInvalidError, QRLoginCancelled
from cleanup_app.instance import SingleInstance, enable_dpi_awareness
from cleanup_app.icons import apply_window_icon, set_taskbar_identity


def main():
    # Must precede the first Tk window so text stays crisp on scaled displays.
    enable_dpi_awareness()
    set_taskbar_identity()
    instance = SingleInstance()
    if not instance.acquired:
        root = tk.Tk()
        apply_window_icon(root)
        root.withdraw()
        messagebox.showwarning("程序已运行", "已经有一个 Telegram 批量清理窗口正在运行。")
        root.destroy()
        instance.close()
        return
    root = tk.Tk()
    app = None
    try:
        app = CleanupApp(root)
        root.mainloop()
    finally:
        if app and not app.loop_closed.is_set():
            # Also handle an unexpected mainloop exit: keep the mutex until
            # all clients, sessions and background tasks have been released.
            app.closing = True
            shutdown_error = None
            try:
                shutdown = asyncio.run_coroutine_threadsafe(app._shutdown_background(), app.loop)
                deadline = time.monotonic() + SHUTDOWN_TIMEOUT
                while not shutdown.done() and time.monotonic() < deadline:
                    app._drain_ui_requests()
                    time.sleep(0.01)
                if shutdown.done():
                    shutdown_error = shutdown.exception()
                else:
                    shutdown_error = RuntimeError(
                        f"退出超过 {SHUTDOWN_TIMEOUT} 秒仍未释放会话。"
                    )
            except Exception as exc:
                shutdown_error = exc
            finally:
                # The mutex must not outlive a still-running loop thread, and
                # the loop must stop even when session release failed.
                if app.loop.is_running():
                    app.loop.call_soon_threadsafe(app.loop.stop)
                app.loop_thread.join(SHUTDOWN_TIMEOUT)
                if shutdown_error:
                    print(f"退出未能安全释放会话：{shutdown_error}", file=sys.stderr)
        instance.close()


if __name__ == "__main__":
    main()
