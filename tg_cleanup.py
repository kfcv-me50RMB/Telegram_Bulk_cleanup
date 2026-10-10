"""Compatibility imports and executable entrypoint for Telegram cleanup."""

import asyncio
import time
import tkinter as tk
from tkinter import messagebox, ttk

from cleanup_app.app import CleanupApp
from cleanup_app.dialogs import LoginDialog, LoginMethodDialog, QRLoginDialog
from cleanup_app.models import CleanupSelection, CleanupStopped, StoredSessionInvalidError, QRLoginCancelled
from cleanup_app.instance import SingleInstance


def main():
    instance = SingleInstance()
    if not instance.acquired:
        root = tk.Tk()
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
            shutdown = asyncio.run_coroutine_threadsafe(app._shutdown_background(), app.loop)
            while not shutdown.done():
                app._drain_ui_requests()
                time.sleep(0.01)
            shutdown.result()
            app.loop.call_soon_threadsafe(app.loop.stop)
            app.loop_thread.join()
        instance.close()


if __name__ == "__main__":
    main()
