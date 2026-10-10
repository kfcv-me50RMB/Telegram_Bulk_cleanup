import time
import tkinter as tk
from tkinter import ttk
import qrcode


class LoginDialog:
    """UI-only modal input; never reads credentials or constructs a client."""

    @staticmethod
    def validate(kind, value):
        if kind == "password":
            if not value:
                raise ValueError("请输入两步验证密码。")
            return value
        value = value.strip()
        if kind == "phone":
            value = "".join(c for c in value if not c.isspace() and c != "-")
            if not value.startswith("+") or not value[1:] or not all("0" <= c <= "9" for c in value[1:]):
                raise ValueError("请输入带国家区号的手机号，例如 +8613800000000。")
        elif not value or not all("0" <= c <= "9" for c in value):
            raise ValueError("请输入数字验证码。")
        return value

    def __init__(self, app, kind):
        self.app, self.kind, self.result = app, kind, None
        self.closed = False
        title, step, label, help_text, action = {
            "phone": ("账号登录", "第 1 步 · 手机号", "手机号", "请输入含国家区号的手机号，例如 +8613800000000。", "发送验证码"),
            "code": ("登录验证码", "第 2 步 · 验证码", "Telegram 验证码", "请到 Telegram 中查看登录验证码，并在下方输入。", "提交验证码"),
            "password": ("两步验证", "第 3 步 · 两步验证", "两步验证密码", "请输入此账号设置的两步验证密码，不是登录验证码。", "验证密码"),
        }[kind]
        window = self.window = tk.Toplevel(app.root)
        window.withdraw()
        window.title(title)
        window.transient(app.root)
        window.configure(background=app.colors["background"])
        window.resizable(False, False)
        width = min(int(440 * app.ui_scale), window.winfo_screenwidth() - 40)
        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text=step, style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(content, text=title, style="Title.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 12))
        card = ttk.Frame(content, style="Card.TFrame", padding=12)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=help_text, style="Muted.TLabel", wraplength=width - 72, justify="left").grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(card, text=label).grid(row=1, column=0, sticky="w", pady=(0, 5))
        self.value = tk.StringVar(window)
        self.entry = ttk.Entry(card, textvariable=self.value, show="•" if kind == "password" else "", width=1)
        self.entry.grid(row=2, column=0, sticky="ew")
        self.visible = tk.BooleanVar(window, value=False)
        if kind == "password":
            ttk.Checkbutton(card, style="Login.TCheckbutton", text="显示密码", variable=self.visible, command=self.toggle_password).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.error = tk.StringVar(window)
        tk.Label(card, textvariable=self.error, background=app.colors["surface"], foreground=app.colors["red"], font=app.fonts["small"], height=2, width=1, anchor="w", wraplength=width - 72, justify="left").grid(row=4, column=0, sticky="ew", pady=(8, 0))
        actions = ttk.Frame(content)
        actions.grid(row=3, column=0, sticky="e", pady=(12, 0))
        ttk.Button(actions, text="取消", command=self.cancel).pack(side="left", padx=(0, 8))
        self.submit_button = ttk.Button(actions, text=action, style="Primary.TButton", command=self.submit)
        self.submit_button.pack(side="left")
        window.bind("<Return>", self.submit)
        window.bind("<Escape>", self.cancel)
        window.protocol("WM_DELETE_WINDOW", self.cancel)
        window.update_idletasks()
        height = window.winfo_reqheight()
        x = max(0, min(app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2, window.winfo_screenwidth() - width))
        y = max(0, min(app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2, window.winfo_screenheight() - height))
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.wait_visibility()
        window.grab_set()
        self.entry.focus_set()

    def toggle_password(self):
        self.entry.configure(show="" if self.visible.get() else "•")

    def submit(self, _event=None):
        if self.closed:
            return "break"
        try:
            result = self.validate(self.kind, self.value.get())
        except ValueError as exc:
            self.error.set(str(exc))
            self.entry.focus_set()
            return "break"
        self.submit_button.configure(state="disabled")
        self.result = result
        self.finish()
        return "break"

    def cancel(self, _event=None):
        if not self.closed:
            self.result = None
            self.finish()
        return "break"

    def finish(self):
        self.closed = True
        self.value.set("")
        self.window.grab_release()
        self.window.destroy()


class LoginMethodDialog:
    def __init__(self, app):
        self.result = None
        self.closed = False
        self.window = tk.Toplevel(app.root)
        self.window.title("添加账号 · 选择登录方式")
        self.window.transient(app.root)
        self.window.resizable(False, False)
        content = ttk.Frame(self.window, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="选择登录方式", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Label(content, text="两种方式均需 API 凭据；启用两步验证的账号仍需输入密码。", style="Subtitle.TLabel", wraplength=int(350 * app.ui_scale)).pack(anchor="w", pady=(0, 16))
        ttk.Button(content, text="扫码登录", style="Primary.TButton", command=lambda: self.finish("qr")).pack(fill="x", pady=(0, 8))
        ttk.Button(content, text="手机号登录", command=lambda: self.finish("phone")).pack(fill="x", pady=(0, 8))
        ttk.Button(content, text="取消", command=self.cancel).pack(anchor="e")
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.window.bind("<Escape>", self.cancel)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, min(app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2, self.window.winfo_screenwidth() - width))
        y = max(0, min(app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2, self.window.winfo_screenheight() - height))
        self.window.geometry(f"+{x}+{y}")
        self.window.wait_visibility()
        self.window.grab_set()

    def finish(self, result=None):
        if self.closed:
            return
        self.result = result
        self.closed = True
        self.window.grab_release()
        self.window.destroy()

    def cancel(self, _event=None):
        self.finish()


class QRLoginDialog:
    """QR image lives only in memory; callbacks never perform network I/O."""
    def __init__(self, app, on_cancel, demo=False):
        self.closed = False
        self.image = None
        self.timer = None
        self.on_cancel = on_cancel
        self.expires_at = None
        self.window = tk.Toplevel(app.root)
        self.window.title("扫码登录" + (" · 离线演示" if demo else ""))
        self.window.transient(app.root)
        self.window.resizable(False, False)
        content = ttk.Frame(self.window, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="扫码登录", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(content, text=("演示二维码，不能用于登录。" if demo else "请使用已登录的 Telegram 手机客户端，在设置中的设备页面扫描并确认登录。"), style="Subtitle.TLabel", wraplength=int(330 * app.ui_scale), justify="left").pack(anchor="w", pady=(0, 12))
        self.image_label = tk.Label(content, background="white", text="正在获取二维码……", width=30, height=15)
        self.image_label.pack(fill="both", padx=4)
        self.status = tk.StringVar(self.window, value="正在获取二维码……")
        ttk.Label(content, textvariable=self.status, style="Subtitle.TLabel").pack(anchor="w", pady=10)
        ttk.Button(content, text="取消", command=self.cancel).pack(anchor="e")
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.window.bind("<Escape>", self.cancel)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, min(app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2, self.window.winfo_screenwidth() - width))
        y = max(0, min(app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2, self.window.winfo_screenheight() - height))
        self.window.geometry(f"+{x}+{y}")
        self.window.wait_visibility()
        self.window.grab_set()
        self.scale = app.ui_scale

    def loading(self):
        if self.closed:
            return
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        self.expires_at = None
        self.image_label.configure(image="", text="正在刷新二维码……", width=30, height=15)
        self.image = None
        self.status.set("正在刷新二维码……")

    def show_code(self, url, lifetime):
        if self.closed:
            return
        qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        image = tk.PhotoImage(master=self.window, width=size, height=size)
        image.put(" ".join("{" + " ".join("#000000" if cell else "#ffffff" for cell in row) + "}" for row in matrix))
        self.image = image.zoom(max(1, int(240 * self.scale) // size))
        self.image_label.configure(image=self.image, text="", width=0, height=0)
        self.expires_at = time.monotonic() + lifetime
        self.tick()

    def tick(self):
        if self.closed or self.expires_at is None:
            return
        remaining = max(0, int(self.expires_at - time.monotonic()))
        if remaining == 0:
            self.loading()
            return
        self.status.set(f"二维码剩余 {remaining} 秒，过期后自动刷新。")
        self.timer = self.window.after(1000, self.tick)

    def finish(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        self.image_label.configure(image="")
        self.image = None
        self.expires_at = None
        self.window.grab_release()
        self.window.destroy()

    def cancel(self, _event=None):
        if not self.closed:
            self.on_cancel()
            self.finish()

