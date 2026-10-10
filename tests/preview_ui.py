"""Offline UI preview using synthetic data, with all account actions disabled.

Run with --output-dir to save window-only screenshots (requires Pillow).
No config or session files are read, and no Telegram clients are constructed.
"""

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".venv" / "Lib" / "site-packages"))
import tg_cleanup as module
from telethon.tl.types import Chat, User


def capture_window(window, path):
    """Capture this preview HWND directly, never read the user's desktop."""
    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    hwnd = window.winfo_id()
    width, height = window.winfo_width(), window.winfo_height()
    dc = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(dc)
    bitmap = gdi32.CreateCompatibleBitmap(dc, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, 3):
            raise RuntimeError("Preview HWND could not be rendered")
        gdi32.SelectObject(memory, old)
        # BITMAPINFOHEADER; a negative height produces top-to-bottom pixels.
        import struct
        header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, pixels, header, 0):
            raise RuntimeError("Preview bitmap could not be read")
        Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX").save(path)
    finally:
        gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, dc)


def preview_qr(output_dir=None):
    for scale in (1, 1.25, 1.5):
        root = module.tk.Tk()
        root.geometry("1000x760+20+20")
        root.tk.call("tk", "scaling", 96 / 72 * scale)
        app = module.CleanupApp.__new__(module.CleanupApp)
        app.root = root
        app._configure_styles()
        root.update()
        choice = module.LoginMethodDialog(app)
        choice.finish("qr")
        assert choice.closed and choice.result == "qr"
        on_cancel = Mock()
        dialog = module.QRLoginDialog(app, on_cancel, demo=True)
        root.update()
        assert dialog.image is None
        dialog.show_code("https://example.invalid/qr-demo-not-for-login", 30)
        root.update()
        assert dialog.image is not None and "剩余" in dialog.status.get()
        for widget in dialog.window.winfo_children():
            assert widget.winfo_rooty() + widget.winfo_height() <= dialog.window.winfo_rooty() + dialog.window.winfo_height()
        if output_dir:
            time.sleep(0.15)
            capture_window(dialog.window, output_dir / f"qr-demo-{scale:g}.png")
        dialog.loading()
        assert dialog.image is None
        dialog.show_code("https://example.invalid/qr-demo-refreshed", 30)
        root.update()
        dialog.cancel()
        assert dialog.closed and dialog.image is None and dialog.timer is None
        on_cancel.assert_called_once()
        root.destroy()
        print(f"QR preview verified: scaling {scale:g}; synthetic code only")


def preview_login(output_dir=None):
    for scale in (1, 1.25, 1.5):
        root = module.tk.Tk()
        root.geometry("1000x760+20+20")
        root.tk.call("tk", "scaling", 96 / 72 * scale)
        app = module.CleanupApp.__new__(module.CleanupApp)
        app.root = root
        app._configure_styles()
        root.update()
        for kind, value in (("phone", "+86 138-0000-0000"), ("code", "123456"), ("password", " demo password ")):
            dialog = module.LoginDialog(app, kind)
            dialog.submit()
            root.update()
            assert dialog.error.get() and not dialog.closed
            dialog.value.set(value)
            if kind == "password":
                assert dialog.entry["show"] == "•"
                dialog.visible.set(True)
                dialog.toggle_password()
                assert dialog.entry["show"] == ""
                dialog.visible.set(False)
                dialog.toggle_password()
            root.update()
            assert dialog.submit_button.winfo_rooty() + dialog.submit_button.winfo_height() <= dialog.window.winfo_rooty() + dialog.window.winfo_height()
            if output_dir:
                time.sleep(0.15)
                capture_window(dialog.window, output_dir / f"login-{kind}-{scale:g}.png")
            dialog.window.focus_force()
            root.update()
            dialog.window.event_generate("<Return>")
            root.update()
            assert dialog.closed
            assert dialog.result == module.LoginDialog.validate(kind, value)
            assert dialog.value.get() == ""
            dialog.submit()  # Repeated submission cannot change the result.
        dialog = module.LoginDialog(app, "phone")
        dialog.window.focus_force()
        root.update()
        dialog.window.event_generate("<Escape>")
        root.update()
        assert dialog.closed and dialog.result is None
        app.closing = False
        dialog = module.LoginDialog(app, "code")
        app.login_dialog = dialog
        app.busy = False
        app.status_text = module.tk.StringVar(root)
        app.loop = module.asyncio.new_event_loop()
        app._shutdown_background = Mock()
        # Exercise the closing hook only until it starts background shutdown.
        from unittest.mock import patch
        with patch.object(module.asyncio, "run_coroutine_threadsafe", side_effect=RuntimeError("offline stop")):
            try:
                app._on_close()
            except RuntimeError:
                pass
        assert dialog.closed and dialog.result is None
        app.loop.close()
        root.destroy()
        print(f"Login dialogs verified: scaling {scale:g}")


def preview(output_dir=None):
    screenshots = []
    for scale, width, height in ((1, 1000, 760), (1, 860, 640), (1, 1250, 950), (1.25, 1000, 760), (1.5, 1000, 760)):
        root = module.tk.Tk()
        root.title("离线界面预览 · 模拟数据")
        root.geometry(f"{width}x{height}+40+40")
        root.minsize(860, 640)
        root.tk.call("tk", "scaling", 96 / 72 * scale)
        app = module.CleanupApp.__new__(module.CleanupApp)
        app.root = root
        app.accounts = []
        app.session_issues = {}
        app.busy = False
        app.connected = False
        app.preview_loaded = False
        app.current_account_id = "demo"
        app.selected_items = set()
        app.selection_rows = {}
        app.groups = [(SimpleNamespace(entity=Chat(id=i, photo=None, participants_count=0, date=None, version=0, title="演示群组 " + str(i) + " 很长的群组名称" * 3)), "群组") for i in range(1, 25)]
        app.private_users = [SimpleNamespace(entity=User(id=i, first_name="同名演示用户", username="demo_user_" + str(i))) for i in range(1, 187)]
        app.private_users.append(SimpleNamespace(entity=User(id=9999, first_name="Saved Messages", is_self=True)))
        app.contacts = [User(id=i, first_name="演示联系人") for i in range(1, 1251)]
        for name in ("api_id", "api_hash", "selected_account", "account_text", "group_count", "dialog_count", "contact_count", "status_text"):
            setattr(app, name, module.tk.StringVar(root, value=""))
        # Override every business callback before constructing the widgets.
        for name in ("_login_selected", "_add_account", "_logout_selected", "_confirm_cleanup", "_stop_cleanup", "_credentials_changed", "_account_selection_changed"):
            setattr(app, name, Mock())
        app._build_ui()
        app.api_id.set("123456")
        app.api_hash.set("DEMO_ONLY_NOT_REAL_CREDENTIALS")
        root.update()
        time.sleep(0.1)
        root.update()
        for state in ("empty", "connected", "running", "flood", "failed", "stopped", "long_text"):
            app.accounts = [{"id": "demo", "label": "演示账号 @demo_account", "user_id": "123456789"}] if state != "empty" else []
            displays = [app._account_display(account) for account in app.accounts]
            app.account_combo.configure(values=displays)
            app.selected_account.set(displays[0] if displays else "")
            app.connected = state in ("connected", "running", "flood")
            app.account_text.set("尚未连接" if state == "empty" else "演示账号 (@demo_account) [ID: 123456789]")
            for variable, value in ((app.group_count, "24"), (app.dialog_count, "186"), (app.contact_count, "1250")):
                variable.set("—" if state == "empty" else value)
            app.busy = False
            app.preview_loaded = app.connected
            if app.preview_loaded:
                app._populate_selection()
                assert not app.selected_items
                assert "9999" not in app.selection_trees["private"].get_children()
                app._select_category("private", True)
                assert len(app.selected_items) == 186
                app._select_category("contacts", True)
                app._select_category("private", False)
                assert len(app.selected_items) == 1250
                app._toggle_selection(("private", 1))
                app._select_category("groups", True)
                assert app.dialog_count.get() == "1 / 186"
                root.update()
                app.selection_notebook.select(1)
                root.update()
                tree = app.selection_trees["private"]
                box = tree.bbox("1", "checked")
                assert box
                assert box[1] + box[3] <= tree.winfo_height()
                x, y, w, h = box
                app._selection_click("private", SimpleNamespace(x=x + w // 2, y=y + h // 2))
                assert ("private", 1) not in app.selected_items
                app._selection_space("private")
                assert ("private", 1) in app.selected_items

                all_tree = app.selection_trees["all"]
                assert len(all_tree.get_children()) == 186 + 24 + 1250
                assert all_tree.set("private:1", "checked") == "☑"
                assert all_tree.set("contacts:1", "checked") == "☑"
                app.selection_notebook.select(0)
                root.update()
                root.update()
                box = all_tree.bbox("private:1", "checked")
                assert box and box[1] + box[3] <= all_tree.winfo_height()
                x, y, w, h = box
                app._selection_click("all", SimpleNamespace(x=x + w // 2, y=y + h // 2))
                assert app.selection_trees["private"].set("1", "checked") == "☐"
                app._selection_space("all")
                assert app.selection_trees["private"].set("1", "checked") == "☑"
                all_tree.focus("private:1")
                app._selection_space("all")
                assert app.selection_trees["private"].set("1", "checked") == "☐"
                app._selection_space("all")
                for index in range(4):
                    app.selection_notebook.select(index)
                    root.update()
                    assert app.selection_trees[("all", "private", "groups", "contacts")[index]].winfo_ismapped()
                app.selection_notebook.select(0)
                saved = set(app.selected_items)
                button = app.selection_toggle_buttons["all"]
                assert button["text"] == "全选"
                button.invoke()
                assert button["text"] == "取消全选"
                assert len(app.selected_items) == 1460
                button.invoke()
                assert not app.selected_items and button["text"] == "全选"
                app.selected_items.update(saved)
                app._refresh_selection()
                app._invert_category_selection("all")
                assert len(app.selected_items) == 1460 - len(saved)
                app._invert_category_selection("all")
                assert app.selected_items == saved
                for tree in app.selection_trees.values():
                    tree.yview_moveto(1)
                    tree.yview_moveto(0)
            else:
                app._reset_selection()
            app._update_execute_state()
            app.stop_button.configure(state="normal" if state in ("running", "flood") else "disabled")
            app._set_busy(state in ("running", "flood"), {"empty": "请选择账号并登录。", "connected": "账号已连接，预览已加载", "running": "正在处理 18/24：退出群组：演示群组", "flood": "限流等待：120 秒；删除私聊及双方记录：演示联系人", "failed": "清理结束：成功 20，失败 2，结果未知 1，未执行 3。请重新连接。", "stopped": "清理已停止，请重新连接", "long_text": "网络请求超时，结果未知。" * 10}[state])
            if app.busy:
                previous = set(app.selected_items)
                app._selection_space("private")
                app._select_category("contacts", False)
                assert previous == app.selected_items
                assert all("disabled" in tree.state() for tree in app.selection_trees.values())
            expected = "disabled" if state in ("running", "flood") else "normal"
            assert str(app.add_button["state"]) == expected
            assert str(app.logout_button["state"]) == expected
            if state == "long_text":
                app.account_text.set("这是用于检查换行的很长的账号名称" * 5 + " (@demo_account) [ID: 123456789]")
            app.log.configure(state="normal")
            app.log.delete("1.0", "end")
            app.log.insert("end", "这是一份模拟日志，不涉及真实账号。\n已连接演示账号，清理范围已加载。\n完成：退出群组：演示群组\n失败：删除私聊及双方记录：示例请求被拒绝\n")
            app.log.configure(state="disabled")
            root.update()
            if state == "connected":
                app.account_combo.event_generate("<Enter>")
                deadline = time.monotonic() + 0.65
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                tips = [widget for widget in root.winfo_children() if isinstance(widget, module.tk.Toplevel)]
                assert len(tips) == 1
                assert tips[0].winfo_children()[0]["text"] == displays[0]
                app.account_combo.event_generate("<Leave>")
                root.update()
                assert not tips[0].winfo_exists()
                # Verify focus and disabled colors are explicit and readable.
                style = module.ttk.Style(root)
                assert style.lookup("DangerOutline.TButton", "foreground", ("disabled",)) == "#748397"
                assert style.lookup("DangerOutline.TButton", "bordercolor", ("focus",)) == app.colors["red_pressed"]
            root.update_idletasks()
            root.update()
            if output_dir and state in ("connected", "long_text"):
                root.lift()
                root.update()
                time.sleep(0.15)
                path = output_dir / f"ui-{width}x{height}-{scale:g}-{state}.png"
                capture_window(root, path)
                screenshots.append(path)
            assert app.execute_button.winfo_ismapped()
            assert app.stop_button.winfo_ismapped()
            assert app.log.winfo_height() >= 80
            for target in (app.execute_button, app.stop_button, app.safety_note, app.scope_note, app.log):
                parent = target.master
                while parent is not root:
                    assert target.winfo_rooty() >= parent.winfo_rooty()
                    assert target.winfo_rooty() + target.winfo_height() <= parent.winfo_rooty() + parent.winfo_height()
                    parent = parent.master
                assert target.winfo_rooty() >= root.winfo_rooty()
                assert target.winfo_rooty() + target.winfo_height() <= root.winfo_rooty() + root.winfo_height()
            for tree in app.selection_trees.values():
                if tree.winfo_ismapped():
                    assert tree.winfo_height() >= int(28 * app.ui_scale) + 24
            tree = app.selection_trees["all"]
            assert app.log.winfo_rootx() < tree.winfo_rootx()
            assert tree.winfo_height() > app.log.winfo_height() if height <= 760 else tree.winfo_height() > 200
            assert abs((app.log.master.master.winfo_rooty() + app.log.master.master.winfo_height()) - (app.scope_note.master.winfo_rooty() + app.scope_note.master.winfo_height())) <= 2
            log_view = app.log.yview()
            tree.yview_moveto(1)
            root.update()
            tree.yview_moveto(0)
            assert app.log.yview() == log_view
            tree_view = tree.yview()
            app.log.yview_moveto(1)
            root.update()
            assert tree.yview() == tree_view
            app.log.yview_moveto(0)
            # Scroll each panel and confirm that its last controls can actually
            # be reached within the viewport, not merely exist off-screen.
            for target in (app.logout_button,):
                parent = target.master
                while parent is not None and not isinstance(parent, module.tk.Canvas):
                    parent = getattr(parent, "master", None)
                assert parent is not None
                parent.yview_moveto(1)
                root.update()
                top = max(target.winfo_rooty(), parent.winfo_rooty())
                bottom = min(target.winfo_rooty() + target.winfo_height(), parent.winfo_rooty() + parent.winfo_height())
                assert bottom - top >= min(target.winfo_height(), 80)
                parent.yview_moveto(0)
                root.update()
        print(f"Preview rendered: {width}x{height}, scaling {scale:g}; log height {app.log.winfo_height()}")
        if scale == 1 and width == 1000:
            app._show_details("清理结果", "成功 20，失败 2，结果未知 1，未执行 3", {"success": ["退出群组：演示群组"], "failed": [("演示私聊", "示例请求被拒绝")], "unknown": [("演示联系人", "请求超时")], "unexecuted": ["剩余联系人"], "stop_reason": "用户停止清理"})
            root.update()
            if output_dir:
                window = next(widget for widget in root.winfo_children() if isinstance(widget, module.tk.Toplevel))
                window.lift()
                root.update()
                time.sleep(0.15)
                path = output_dir / "ui-result.png"
                capture_window(window, path)
                screenshots.append(path)
        root.destroy()
    for path in screenshots:
        print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_qr(args.output_dir)
    preview_login(args.output_dir)
    preview(args.output_dir)
