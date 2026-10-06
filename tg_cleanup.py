"""Tkinter GUI for bulk Telegram cleanup using personal accounts."""

import asyncio
import ctypes
import json
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from telethon import TelegramClient
from telethon.utils import get_peer_id
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.auth import LogOutRequest
from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.types import Channel, Chat, User


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / ".tg_cleanup_config.json"
LEGACY_SESSION_PATH = BASE_DIR / "tg_cleanup_session"
SESSIONS_DIR = BASE_DIR / "sessions"
INSTANCE_MUTEX_NAME = "Local\\TelegramCleanupGUI_8D634395"
CLEANUP_TIMEOUT = 60
MAX_FLOOD_WAIT = 300


@dataclass(frozen=True)
class CleanupSelection:
    account_id: str
    groups: tuple
    private_users: tuple
    contacts: tuple


class CleanupStopped(RuntimeError):
    def __init__(self, reason, unknown=False):
        super().__init__(reason)
        self.unknown = unknown


class StoredSessionInvalidError(RuntimeError):
    """The registry entry does not contain a reusable authorized session."""


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


class CleanupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram 批量清理")
        width = max(860, min(1000, self.root.winfo_screenwidth() - 80))
        height = max(640, min(760, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(860, 640)

        self.ui_requests = queue.Queue()
        self.loop = asyncio.new_event_loop()
        self.loop_ready = threading.Event()
        self.loop_closed = threading.Event()
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()
        self.loop_ready.wait()

        self.client = None
        self.current_account_id = None
        self.groups = []
        self.private_users = []
        self.contacts = []
        self.selected_items = set()
        self.selection_rows = {}
        self.selection_trees = {}
        self.selection_buttons = []
        self.accounts = []
        self.session_issues = {}
        self.busy = False
        self.closing = False
        self.active_futures = set()
        self.async_tasks = set()
        self.preview_loaded = False
        self.connected = False
        self.cleanup_task = None
        self.cleanup_running = False
        self.clients = set()
        self.disconnect_tasks = set()
        self.logout_clients = {}
        self.revoked_accounts = set()

        self.api_id = tk.StringVar()
        self.api_hash = tk.StringVar()
        self.selected_account = tk.StringVar()
        self.account_text = tk.StringVar(value="尚未连接")
        self.group_count = tk.StringVar(value="—")
        self.dialog_count = tk.StringVar(value="—")
        self.contact_count = tk.StringVar(value="—")
        self.status_text = tk.StringVar(value="请选择账号并登录。")

        self._build_ui()
        self._load_config()
        self.root.after(50, self._process_ui_requests)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self):
        self.colors = {
            "background": "#F3F6FA", "surface": "#FFFFFF", "text": "#172B4D",
            "muted": "#64748B", "border": "#DFE7F0", "blue": "#229ED9",
            "blue_hover": "#1688C1", "blue_pressed": "#1076AC", "red": "#D64545",
            "red_hover": "#BF3636", "red_pressed": "#A52D2D",
        }
        families = set(tkfont.families(self.root))
        family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        self.fonts = {
            "body": (family, 10), "small": (family, 9), "heading": (family, 15, "bold"),
            "section": (family, 12, "bold"), "metric": (family, 16, "bold"),
            "log": ("Consolas" if "Consolas" in families else family, 10),
        }
        self.ui_scale = max(1, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        c = self.colors
        self.root.configure(background=c["background"])
        # A wildcard *Font overrides ttk's title/metric fonts; configure only
        # the named defaults so native dialogs still get the body typography.
        for name in ("TkDefaultFont", "TkTextFont"):
            tkfont.nametofont(name).configure(family=family, size=10)
        self.root.option_add("*TCombobox*Listbox.font", self.fonts["body"])
        self.root.option_add("*TCombobox*Listbox.background", c["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["blue"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=c["background"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("Card.TFrame", background=c["surface"], bordercolor=c["border"], borderwidth=1, relief="solid")
        style.configure("TLabel", background=c["surface"], foreground=c["text"], font=self.fonts["body"])
        style.configure("Title.TLabel", background=c["background"], font=self.fonts["heading"])
        style.configure("Subtitle.TLabel", background=c["background"], foreground=c["muted"], font=self.fonts["small"])
        style.configure("Section.TLabel", font=self.fonts["section"])
        style.configure("Muted.TLabel", foreground=c["muted"], font=self.fonts["small"])
        style.configure("Metric.TLabel", foreground=c["blue"], font=self.fonts["metric"])
        style.configure("Brand.TLabel", background=c["blue"], foreground="white", font=(family, 14, "bold"), padding=(8, 5))
        style.configure("Notice.TLabel", foreground="#9D3C3C", font=self.fonts["small"])
        style.configure("TEntry", padding=(10, 6), fieldbackground=c["surface"], foreground=c["text"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        style.map("TEntry", bordercolor=[("focus", c["blue"])], fieldbackground=[("disabled", "#EDF2F7")], foreground=[("disabled", c["muted"])])
        style.configure("TCombobox", padding=(8, 6), foreground=c["text"], fieldbackground=c["surface"], background=c["surface"], bordercolor=c["border"], arrowcolor=c["muted"])
        style.map("TCombobox", fieldbackground=[("disabled", "#EDF2F7"), ("readonly", c["surface"])], foreground=[("disabled", c["muted"])], bordercolor=[("focus", c["blue"])], selectbackground=[("readonly", c["surface"])], selectforeground=[("readonly", c["text"])])
        for name, color, hover, pressed, foreground in (
            ("TButton", "#F5F8FC", "#EAF0F7", "#DEE7F1", c["text"]),
            ("Primary.TButton", c["blue"], c["blue_hover"], c["blue_pressed"], "white"),
            ("Danger.TButton", c["red"], c["red_hover"], c["red_pressed"], "white"),
        ):
            style.configure(name, font=self.fonts["body"], padding=(10, 5), background=color, foreground=foreground, bordercolor=c["border"] if name == "TButton" else color, borderwidth=1, focusthickness=2, focuscolor=c["blue"])
            style.map(name, background=[("disabled", "#E9EEF4"), ("pressed", pressed), ("active", hover)], foreground=[("disabled", "#748397")], bordercolor=[("disabled", c["border"]), ("focus", c["blue"])])
        style.configure("TNotebook", background=c["surface"], borderwidth=0, tabmargins=(0, 0, 0, 6))
        style.configure("TNotebook.Tab", background="#EDF2F8", foreground=c["muted"], padding=(10, 3), font=self.fonts["small"], borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#E3F2FC"), ("active", "#EAF0F7")], foreground=[("selected", "#1379AD"), ("active", c["text"])])
        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"], font=self.fonts["body"], rowheight=int(28 * self.ui_scale), borderwidth=0)
        style.configure("Treeview.Heading", background="#EDF3F9", foreground=c["muted"], font=(*self.fonts["small"], "bold"), padding=(8, 3), relief="flat")
        style.map("Treeview", background=[("selected", "#DCEFFC")], foreground=[("disabled", c["muted"]), ("selected", c["text"])])
        style.map("Treeview.Heading", background=[("active", "#E3EDF7")])
        style.configure("Horizontal.TScrollbar", background="#D7E1ED", troughcolor=c["surface"], bordercolor=c["surface"], arrowcolor=c["muted"])
        style.configure("Secondary.TButton", font=self.fonts["small"], padding=(10, 4))
        style.configure("Vertical.TScrollbar", background="#D7E1ED", troughcolor=c["surface"], bordercolor=c["surface"], arrowcolor=c["muted"])
        style.configure("DangerOutline.TButton", background=c["surface"], foreground=c["red"], bordercolor=c["red"], focuscolor=c["red"])
        style.map("DangerOutline.TButton", background=[("disabled", "#E9EEF4"), ("pressed", "#FADDDD"), ("active", "#FFF0F0")], foreground=[("disabled", "#748397"), ("!disabled", c["red"])], bordercolor=[("disabled", c["border"]), ("focus", c["red_pressed"]), ("!disabled", c["red"])])

    def _account_tooltip(self):
        """Show the full selected identity without opening or reading sessions."""
        state = {"timer": None, "window": None}

        def hide(_event=None):
            if state["timer"] is not None:
                self.root.after_cancel(state["timer"])
                state["timer"] = None
            if state["window"] is not None:
                state["window"].destroy()
                state["window"] = None

        def show():
            state["timer"] = None
            account = self._selected_account()
            if not account:
                return
            window = tk.Toplevel(self.root)
            state["window"] = window
            window.overrideredirect(True)
            label = ttk.Label(window, text=self._account_display(account), padding=(10, 6), wraplength=420, justify="left", relief="solid", borderwidth=1)
            label.pack()
            window.update_idletasks()
            x = min(self.account_combo.winfo_rootx(), max(0, window.winfo_screenwidth() - window.winfo_reqwidth() - 8))
            y = self.account_combo.winfo_rooty() + self.account_combo.winfo_height() + 4
            if y + window.winfo_reqheight() > window.winfo_screenheight():
                y = max(0, self.account_combo.winfo_rooty() - window.winfo_reqheight() - 4)
            window.geometry(f"+{x}+{y}")

        def schedule(_event=None):
            hide()
            state["timer"] = self.root.after(500, show)

        self.account_combo.bind("<Enter>", schedule, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<KeyPress>", "<<ComboboxSelected>>", "<Destroy>"):
            self.account_combo.bind(event, hide, add="+")

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=8)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        return card

    def _text_options(self):
        return dict(background=self.colors["surface"], foreground=self.colors["text"],
                    font=self.fonts["log"], relief="flat", borderwidth=0,
                    highlightthickness=1, highlightbackground=self.colors["border"],
                    highlightcolor=self.colors["blue"], selectbackground="#DCEFFC",
                    selectforeground=self.colors["text"], padx=12, pady=10, spacing3=4)

    def _wrap_to_width(self, label, parent, inset=0):
        def resize(event):
            if event.width > inset:
                label.configure(wraplength=max(80, event.width - inset))
        parent.bind("<Configure>", resize, add="+")

    def _scrollable_panel(self, parent, width):
        shell = ttk.Frame(parent)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(shell, width=width, height=1, background=self.colors["background"], highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=content, anchor="nw")

        def resize(event=None):
            height = max(content.winfo_reqheight(), canvas.winfo_height())
            canvas.itemconfigure(window, width=canvas.winfo_width(), height=height)
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), height))
            canvas.xview_moveto(0)
            if content.winfo_reqheight() > canvas.winfo_height():
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
            else:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)

        def scroll(event):
            widget = event.widget
            if widget.winfo_class() in ("TCombobox", "Text", "Treeview"):
                return
            while widget is not None:
                if widget is shell:
                    if content.winfo_reqheight() > canvas.winfo_height():
                        canvas.yview_scroll(-int(event.delta / 120), "units")
                        return "break"
                    return
                widget = getattr(widget, "master", None)

        canvas.bind("<Configure>", resize)
        content.bind("<Configure>", resize)
        shell.bind("<Configure>", lambda _event: canvas.after_idle(resize))
        self.root.bind("<MouseWheel>", scroll, add="+")
        return shell, content

    def _build_ui(self):
        self._configure_styles()
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="TG", style="Brand.TLabel").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ttk.Label(header, text="Telegram 批量清理", style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="管理账号 · 预览范围 · 确认后清理", style="Subtitle.TLabel").grid(row=1, column=1, sticky="w", pady=(4, 0))

        body = ttk.Frame(container)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, minsize=int(240 * min(self.ui_scale, 1.15)) + 12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar_column = ttk.Frame(body)
        sidebar_column.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        sidebar_column.columnconfigure(0, weight=1)
        sidebar_column.rowconfigure(1, weight=1)
        sidebar_shell, sidebar = self._scrollable_panel(sidebar_column, int(240 * min(self.ui_scale, 1.15)))
        sidebar_shell.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        sidebar.columnconfigure(0, weight=1)

        credentials = self._card(sidebar, "API 凭据")
        credentials.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(credentials, text="API ID", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 5))
        self.api_id_entry = ttk.Entry(credentials, textvariable=self.api_id, width=1)
        self.api_id_entry.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(credentials, text="API Hash", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 5))
        self.api_hash_entry = ttk.Entry(credentials, textvariable=self.api_hash, show="•", width=1)
        self.api_hash_entry.grid(row=4, column=0, sticky="ew")
        credentials_note = ttk.Label(credentials, text="凭据明文保存在本机，请勿分享。", style="Muted.TLabel", justify="left", wraplength=250)
        credentials_note.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self._wrap_to_width(credentials_note, credentials, 24)
        for entry in (self.api_id_entry, self.api_hash_entry):
            entry.bind("<KeyRelease>", self._credentials_changed)

        accounts_frame = self._card(sidebar, "账号管理")
        accounts_frame.grid(row=1, column=0, sticky="ew")
        self.account_combo = ttk.Combobox(accounts_frame, textvariable=self.selected_account, state="readonly", width=1)
        self.account_combo.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.account_combo.bind("<<ComboboxSelected>>", self._account_selection_changed)
        self._account_tooltip()
        account_actions = ttk.Frame(accounts_frame, style="Surface.TFrame")
        account_actions.grid(row=2, column=0, sticky="ew")
        for column in (0, 1):
            account_actions.columnconfigure(column, weight=1, uniform="account_buttons")
        self.login_button = ttk.Button(account_actions, text="登录 / 连接", style="Primary.TButton", command=self._login_selected)
        self.login_button.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 8))
        self.add_button = ttk.Button(account_actions, text="添加账号", command=self._add_account)
        self.add_button.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 8))
        self.logout_button = ttk.Button(account_actions, text="退出登录", style="DangerOutline.TButton", command=self._logout_selected)
        self.logout_button.grid(row=1, column=0, columnspan=2, sticky="ew")

        workspace = ttk.Frame(body)
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1, minsize=int(166 + 60 * (self.ui_scale - 1)))
        summary = ttk.Frame(workspace, style="Card.TFrame", padding=6)
        summary.columnconfigure(0, weight=1)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        account_label = ttk.Label(summary, textvariable=self.account_text, width=1, anchor="w")
        account_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        metrics = ttk.Frame(summary, style="Surface.TFrame")
        metrics.grid(row=1, column=0, sticky="ew")
        for column, (label, variable) in enumerate((("群组 / 频道", self.group_count), ("用户私聊", self.dialog_count), ("联系人", self.contact_count))):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            tile = ttk.Frame(metrics, style="Surface.TFrame", padding=(4, 0))
            tile.grid(row=0, column=column, sticky="ew")
            ttk.Label(tile, text=label + " · 已选 / 总数", style="Muted.TLabel").pack(anchor="w")
            metric_font = tkfont.Font(root=self.root, font=self.fonts["metric"])
            reference_font = tkfont.Font(root=self.root, font=self.fonts["metric"])
            ttk.Label(tile, textvariable=variable, style="Metric.TLabel", font=metric_font).pack(anchor="w")
            def fit_metric(event=None, tile=tile, variable=variable, font=metric_font, reference=reference_font):
                available = max(1, tile.winfo_width() - 8)
                measured = max(1, reference.measure(variable.get()))
                font.configure(size=max(10, min(16, int(16 * available / measured))))
            tile.bind("<Configure>", fit_metric, add="+")
            variable.trace_add("write", lambda *_args, fit=fit_metric: fit())

        selection = ttk.Frame(workspace, style="Card.TFrame", padding=8)
        selection.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        selection.columnconfigure(0, weight=1)
        selection.rowconfigure(0, weight=1)
        self.selection_notebook = notebook = ttk.Notebook(selection)
        notebook.grid(row=0, column=0, sticky="nsew")
        self.selection_trees = {}
        self.selection_buttons = []
        self.selection_counts = {}
        for category, caption in (("all", "全部"), ("private", "用户私聊"), ("groups", "群组 / 频道"), ("contacts", "联系人")):
            page = ttk.Frame(notebook, style="Surface.TFrame")
            notebook.add(page, text=caption)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(1, weight=1)
            toolbar = ttk.Frame(page, style="Surface.TFrame")
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            for label, checked in (("全选全部分类" if category == "all" else "全选本分类", True), ("取消全选", False)):
                button = ttk.Button(toolbar, text=label, style="Secondary.TButton", command=lambda c=category, v=checked: self._select_category(c, v))
                button.pack(side="left", padx=3, pady=2)
                self.selection_buttons.append(button)
            count = tk.StringVar(value="已选 0 / 共 0 项")
            self.selection_counts[category] = count
            ttk.Label(toolbar, textvariable=count, style="Muted.TLabel").pack(side="right", padx=4)
            columns = ("checked", "type", "name", "username", "id") if category == "all" else ("checked", "name", "username", "id")
            tree = ttk.Treeview(page, columns=columns, show="headings", height=1, selectmode="browse")
            definitions = (("checked", "勾选", 48), ("type", "类型", 90), ("name", "名称", 190), ("username", "用户名", 120), ("id", "Telegram ID", 120))
            for column, title, width in definitions:
                if column not in columns:
                    continue
                anchor = "center" if column in ("checked", "type") else "e" if column == "id" else "w"
                tree.heading(column, text=title, anchor=anchor)
                tree.column(column, width=width, minwidth=width, stretch=column == "name", anchor=anchor)
            tree.tag_configure("even", background="#FFFFFF")
            tree.tag_configure("odd", background="#F5F8FC")
            tree.grid(row=1, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=tree.yview)
            scrollbar.grid(row=1, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(page, orient="horizontal", command=tree.xview)
            horizontal.grid(row=2, column=0, sticky="ew")
            tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
            tree.bind("<Button-1>", lambda event, c=category: self._selection_click(c, event))
            tree.bind("<space>", lambda event, c=category: self._selection_space(c))
            self.selection_trees[category] = tree

        log_frame = ttk.Frame(sidebar_column, style="Card.TFrame", padding=6)
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="运行日志", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1, minsize=80)
        self.log = ScrolledText(log_frame, height=1, width=1, state="disabled", wrap="word", **self._text_options())
        self.log.grid(row=1, column=0, sticky="nsew")

        def fit_sidebar(_event=None):
            # Reserve a usable log while allowing account controls to scroll.
            log_minimum = max(108, log_frame.winfo_reqheight())
            available = sidebar_column.winfo_height()
            controls = min(sidebar.winfo_reqheight() + 8, max(100, available - log_minimum))
            sidebar_column.rowconfigure(0, minsize=controls)
            sidebar_column.rowconfigure(1, minsize=log_minimum)

        sidebar_column.bind("<Configure>", fit_sidebar)
        sidebar.bind("<Configure>", lambda _event: sidebar.after_idle(fit_sidebar), add="+")

        footer = ttk.Frame(workspace, style="Card.TFrame", padding=6)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer, style="Surface.TFrame")
        actions.grid(row=0, column=0, sticky="ew")
        self.execute_button = ttk.Button(actions, text="清理已选项目", style="Danger.TButton", command=self._confirm_cleanup, state="disabled")
        self.execute_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止清理", command=self._stop_cleanup, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.safety_note = safety_note = ttk.Label(footer, text="清理需两次确认。将双向删除私聊记录（包括对方记录），并从当前账号列表移除对话。", style="Notice.TLabel", wraplength=480, justify="left")
        safety_note.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._wrap_to_width(safety_note, footer, 24)
        self.scope_note = scope_note = ttk.Label(footer, text="Saved Messages 不参与清理；新消息可能使对话重新出现。", style="Muted.TLabel", justify="left")
        scope_note.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._wrap_to_width(scope_note, footer, 24)

        status = ttk.Frame(container, style="Card.TFrame", padding=(10, 5))
        status.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status.columnconfigure(1, weight=1)
        ttk.Label(status, text="任务状态", style="Muted.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 14))
        status_label = ttk.Label(status, textvariable=self.status_text, width=1, anchor="w")
        status_label.grid(row=0, column=1, sticky="ew")

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop_ready.set()
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()
            self.loop_closed.set()

    def _process_ui_requests(self):
        self._drain_ui_requests()
        self.root.after(50, self._process_ui_requests)

    def _drain_ui_requests(self):
        try:
            while True:
                function, result = self.ui_requests.get_nowait()
                if result.cancelled():
                    continue
                try:
                    value = function()
                    if not result.done():
                        result.set_result(value)
                except BaseException as exc:
                    if not result.done():
                        result.set_exception(exc)
        except queue.Empty:
            pass

    async def _request_ui(self, function):
        result = Future()
        self.ui_requests.put((function, result))
        return await asyncio.wrap_future(result)

    def _log(self, text):
        if self.closing:
            return
        def append():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.ui_requests.put((append, Future()))

    def _load_config(self):
        config = {}
        if CONFIG_PATH.exists():
            try:
                config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                backup = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.corrupt-{int(time.time())}")
                try:
                    CONFIG_PATH.replace(backup)
                    backup_note = f"\n原文件已保留为：{backup.name}"
                except OSError:
                    backup_note = ""
                messagebox.showwarning("配置读取失败", f"无法读取本地配置：\n{exc}{backup_note}")
        if not isinstance(config, dict):
            config = {}
        self.api_id.set(str(config.get("api_id", "")))
        self.api_hash.set(str(config.get("api_hash", "")))
        raw_accounts = config.get("accounts", [])
        registry_changed = False
        if isinstance(raw_accounts, list):
            seen_ids = set()
            seen_sessions = set()
            for item in raw_accounts:
                if not self._valid_account(item):
                    registry_changed = True
                    continue
                if item["id"] in seen_ids or item["session"] in seen_sessions:
                    registry_changed = True
                    continue
                seen_ids.add(item["id"])
                seen_sessions.add(item["session"])
                self.accounts.append(item)

        # The registry is only metadata. Never show or recreate entries whose
        # authentication file was manually removed.
        existing_accounts = []
        for account in self.accounts:
            try:
                session_status = self._session_status(account)
            except RuntimeError:
                session_status = "invalid"
            if session_status in ("valid", "busy", "corrupt", "unreadable"):
                existing_accounts.append(account)
                if session_status in ("corrupt", "unreadable"):
                    self._log(f"会话不可用，已保留账号记录：{account['label']}")
                if session_status != "valid":
                    self.session_issues[account["id"]] = session_status
            else:
                registry_changed = True
                if session_status == "empty":
                    try:
                        self._remove_empty_session(self._session_path(account))
                    except (OSError, RuntimeError):
                        pass
        self.accounts = existing_accounts

        legacy_file = LEGACY_SESSION_PATH.with_suffix(".session")
        if legacy_file.exists() and not any(item["session"] == "tg_cleanup_session" for item in self.accounts):
            legacy_account = {
                "id": "legacy",
                "session": "tg_cleanup_session",
                "label": "现有账号（连接后识别）",
                "user_id": "",
            }
            legacy_status = self._session_status(legacy_account)
            if legacy_status in ("valid", "busy", "corrupt", "unreadable"):
                self.accounts.append(legacy_account)
                if legacy_status != "valid":
                    self.session_issues["legacy"] = legacy_status
                try:
                    self._save_config()
                except OSError as exc:
                    messagebox.showwarning("配置保存失败", f"无法登记现有账号：\n{exc}")
            elif legacy_status == "empty":
                try:
                    self._remove_empty_session(LEGACY_SESSION_PATH)
                except (OSError, RuntimeError):
                    pass
        self._refresh_account_list(config.get("selected_account"))
        if registry_changed:
            try:
                self._save_config()
            except OSError as exc:
                messagebox.showwarning("配置保存失败", f"无法清理已失效的账号记录：\n{exc}")

    @staticmethod
    def _valid_account(item):
        return (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("session"), str)
            and isinstance(item.get("label"), str)
        )

    def _save_config(self, selected_id=None):
        selected = selected_id or self._selected_account_id() or self.current_account_id
        config = {
            "api_id": self.api_id.get().strip(),
            "api_hash": self.api_hash.get().strip(),
            "selected_account": selected,
            "accounts": self.accounts,
        }
        temporary = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(CONFIG_PATH)

    def _refresh_account_list(self, select_id=None):
        values = [self._account_display(account) for account in self.accounts]
        self.account_combo.configure(values=values)
        if not values:
            self.selected_account.set("")
            self._update_account_buttons()
            return
        index = next((i for i, account in enumerate(self.accounts) if account["id"] == select_id), 0)
        self.account_combo.current(index)
        self._update_account_buttons()

    @staticmethod
    def _account_display(account):
        user_id = account.get("user_id")
        return f"{account['label']}  [ID: {user_id}]" if user_id else account["label"]

    def _selected_account(self):
        index = self.account_combo.current()
        return self.accounts[index] if 0 <= index < len(self.accounts) else None

    def _selected_account_id(self):
        account = self._selected_account()
        return account["id"] if account else None

    def _session_path(self, account):
        path = (BASE_DIR / account["session"]).resolve()
        sessions_root = SESSIONS_DIR.resolve()
        if path != LEGACY_SESSION_PATH.resolve() and path.parent != sessions_root:
            raise RuntimeError("账号会话路径无效，已拒绝访问。")
        return path

    def _session_file(self, account):
        session_path = self._session_path(account)
        return session_path if session_path.suffix == ".session" else session_path.with_suffix(".session")

    def _session_status(self, account):
        async def inspect():
            return self._read_session_status(account)

        return asyncio.run_coroutine_threadsafe(inspect(), self.loop).result()

    def _read_session_status(self, account):
        """Inspect a session read-only without letting Telethon create or upgrade it."""
        session_file = self._session_file(account)
        if not session_file.exists():
            return "missing"
        connection = None
        try:
            uri = session_file.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=0.2)
            row = connection.execute("SELECT auth_key FROM sessions LIMIT 1").fetchone()
            return "valid" if row and row[0] else "empty"
        except sqlite3.OperationalError as exc:
            detail = str(exc).lower()
            if "locked" in detail or "busy" in detail:
                return "busy"
            return "corrupt" if "no such table" in detail else "unreadable"
        except sqlite3.DatabaseError:
            return "corrupt"
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _session_files(session_path):
        session_file = session_path if session_path.suffix == ".session" else session_path.with_suffix(".session")
        return (
            session_file,
            Path(f"{session_file}-journal"),
            Path(f"{session_file}-wal"),
            Path(f"{session_file}-shm"),
        )

    async def _delete_session_files(self, session_path):
        """Delete the SQLite session and sidecars after Telethon releases them."""
        last_errors = []
        for _attempt in range(5):
            last_errors = []
            for candidate in self._session_files(session_path):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:
                    last_errors.append(f"{candidate.name}: {exc}")
            remaining = [candidate.name for candidate in self._session_files(session_path) if candidate.exists()]
            if not remaining:
                return
            await asyncio.sleep(0.2)
        details = "; ".join(last_errors) or ", ".join(remaining)
        raise RuntimeError(f"本地会话文件删除失败：{details}")

    def _remove_empty_session(self, session_path):
        asyncio.run_coroutine_threadsafe(self._delete_session_files(session_path), self.loop).result()

    def _credentials(self, required=True):
        api_id = self.api_id.get().strip()
        api_hash = self.api_hash.get().strip()
        if not api_id.isdigit() or not api_hash:
            if required:
                messagebox.showerror("凭据无效", "请输入有效的 API ID 和 API Hash。")
            return None
        try:
            self._save_config()
        except OSError as exc:
            messagebox.showerror("配置保存失败", f"无法保存本地配置：\n{exc}")
            return None
        return int(api_id), api_hash

    def _set_busy(self, busy, status):
        self.busy = busy
        self.status_text.set(status)
        widget_state = "disabled" if busy else "normal"
        readonly_state = "disabled" if busy else "readonly"
        for widget in (
            self.login_button,
            self.add_button,
            self.logout_button,
        ):
            widget.configure(state=widget_state)
        credentials_state = "disabled" if busy or self.connected else "normal"
        self.api_id_entry.configure(state=credentials_state)
        self.api_hash_entry.configure(state=credentials_state)
        self.account_combo.configure(state=readonly_state)
        self._update_account_buttons()
        for button in getattr(self, "selection_buttons", []):
            button.configure(state="normal" if self._selection_available() else "disabled")
        for tree in getattr(self, "selection_trees", {}).values():
            tree.state(["!disabled"] if self._selection_available() else ["disabled"])
        if busy:
            self.execute_button.configure(state="disabled")

    def _selection_available(self):
        account = self._selected_account()
        return bool(not self.busy and self.preview_loaded and self.connected
                    and account and account["id"] == self.current_account_id)

    def _reset_selection(self):
        self.selected_items = set()
        self.selection_rows = {}
        for variable in getattr(self, "selection_counts", {}).values():
            variable.set("已选 0 / 共 0 项")
        for tree in getattr(self, "selection_trees", {}).values():
            tree.delete(*tree.get_children())

    def _populate_selection(self):
        self._reset_selection()
        categories = (("private", self.private_users), ("groups", self.groups), ("contacts", self.contacts))
        for category, items in categories:
            for item in items:
                entity = item[0].entity if category == "groups" else item.entity if category == "private" else item
                if isinstance(entity, User) and entity.is_self:
                    continue
                peer_id = get_peer_id(entity)
                key = (category, peer_id)
                if key in self.selection_rows:
                    continue
                self.selection_rows[key] = item
                name = getattr(entity, "title", None) or " ".join(v for v in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if v) or str(entity.id)
                tree = self.selection_trees[category]
                values = ("☐", name, "@" + entity.username if getattr(entity, "username", None) else "—", str(peer_id))
                tree.insert("", "end", iid=str(peer_id), tags=("odd" if len(tree.get_children()) % 2 else "even",), values=values)
                all_tree = self.selection_trees.get("all")
                if all_tree is not None:
                    kind = {"private": "用户私聊", "groups": "群组/频道", "contacts": "联系人"}[category]
                    all_tree.insert("", "end", iid=f"{category}:{peer_id}", tags=("odd" if len(all_tree.get_children()) % 2 else "even",), values=(values[0], kind, *values[1:]))
        self._refresh_selection()

    def _refresh_selection(self):
        for category, variable in (("groups", self.group_count), ("private", self.dialog_count), ("contacts", self.contact_count)):
            keys = [key for key in self.selection_rows if key[0] == category]
            variable.set(f"{sum(key in self.selected_items for key in keys)} / {len(keys)}")
            tree = self.selection_trees.get(category)
            if tree:
                for key in keys:
                    tree.set(str(key[1]), "checked", "☑" if key in self.selected_items else "☐")
        all_tree = self.selection_trees.get("all")
        if all_tree is not None:
            for key in self.selection_rows:
                all_tree.set(f"{key[0]}:{key[1]}", "checked", "☑" if key in self.selected_items else "☐")
        for category, variable in getattr(self, "selection_counts", {}).items():
            keys = {key for key in self.selection_rows if category == "all" or key[0] == category}
            variable.set(f"已选 {len(keys & self.selected_items)} / 共 {len(keys)} 项")
        self._update_execute_state()

    @staticmethod
    def _selection_row_key(category, row):
        if category == "all":
            kind, peer_id = row.split(":", 1)
            return kind, int(peer_id)
        return category, int(row)

    def _toggle_selection(self, key):
        if not self._selection_available() or key not in self.selection_rows:
            return
        if key in self.selected_items:
            self.selected_items.remove(key)
        else:
            self.selected_items.add(key)
        self._refresh_selection()

    def _select_category(self, category, checked):
        if not self._selection_available():
            return
        keys = {key for key in self.selection_rows if category == "all" or key[0] == category}
        if checked:
            self.selected_items.update(keys)
        else:
            self.selected_items.difference_update(keys)
        self._refresh_selection()

    def _selection_click(self, category, event):
        tree = self.selection_trees[category]
        row = tree.identify_row(event.y)
        if row and tree.identify_region(event.x, event.y) == "cell" and tree.identify_column(event.x) == "#1":
            tree.focus(row)
            tree.selection_set(row)
            self._toggle_selection(self._selection_row_key(category, row))
            return "break"

    def _selection_space(self, category):
        row = self.selection_trees[category].focus()
        if row:
            self._toggle_selection(self._selection_row_key(category, row))
        return "break"

    def _selection_snapshot(self):
        def items(category):
            return tuple(item for key, item in self.selection_rows.items() if key[0] == category and key in self.selected_items)
        return CleanupSelection(self.current_account_id, items("groups"), items("private"), items("contacts"))

    def _clear_preview(self, account_text="尚未连接"):
        self.preview_loaded = False
        self._reset_selection()
        self.account_text.set(account_text)
        self.group_count.set("—")
        self.dialog_count.set("—")
        self.contact_count.set("—")
        self._update_execute_state()

    def _credentials_changed(self, _event=None):
        self._clear_preview("连接凭据已更改")
        self.status_text.set("请重新登录账号。")

    def _account_selection_changed(self, _event=None):
        if self.busy:
            return
        selected = self._selected_account()
        self._update_account_buttons()
        if not selected:
            self._clear_preview()
            return
        if selected["id"] != self.current_account_id or not self.connected:
            self._clear_preview("正在切换账号")
        if selected and selected["id"] in self.session_issues:
            self.status_text.set("会话不可用，账号已保留；请修复文件或权限后重启程序。")
            return
        if selected["id"] != self.current_account_id or not self.connected:
            self._connect_account(selected, "切换账号")
        elif selected and self.current_account_id == selected["id"]:
            self.status_text.set("当前连接账号" if self.preview_loaded else "当前账号预览尚未加载")
            self._update_execute_state()

    def _update_account_buttons(self):
        selected = self._selected_account()
        issue = self.session_issues.get(selected["id"]) if selected else None
        state = "disabled" if self.busy or issue else "normal"
        self.login_button.configure(state=state)
        if issue and not self.busy:
            self.status_text.set("授权已撤销，请再次点击“退出登录”完成本地清理。" if issue == "revoked" else "会话不可用，账号已保留；请修复文件或权限后重启程序。")

    def _update_execute_state(self):
        selected = self._selected_account()
        total = len(self.selected_items)
        enabled = bool(
            not self.busy
            and self.preview_loaded
            and total
            and selected
            and selected["id"] == self.current_account_id
            and self.connected
        )
        self.execute_button.configure(state="normal" if enabled else "disabled")
        for button in getattr(self, "selection_buttons", []):
            button.configure(state="normal" if self._selection_available() else "disabled")
        for tree in getattr(self, "selection_trees", {}).values():
            tree.state(["!disabled"] if self._selection_available() else ["disabled"])

    def _submit(self, coroutine, on_success, operation_name, on_error=None):
        async def tracked_operation():
            task = asyncio.current_task()
            self.async_tasks.add(task)
            try:
                return await coroutine
            finally:
                self.async_tasks.discard(task)

        future = asyncio.run_coroutine_threadsafe(tracked_operation(), self.loop)
        self.active_futures.add(future)

        def check_result():
            if self.closing:
                self.active_futures.discard(future)
                return
            if not future.done():
                self.root.after(100, check_result)
                return
            self.active_futures.discard(future)
            self._set_busy(False, "就绪")
            try:
                result = future.result()
            except Exception as exc:
                if operation_name in ("登录账号", "切换账号", "添加账号", "断开账号"):
                    self.connected = False
                    self._clear_preview()
                if on_error and on_error(exc):
                    return
                self.status_text.set(f"{operation_name}失败")
                self._log(f"{operation_name}失败：{exc}")
                messagebox.showerror(f"{operation_name}失败", str(exc))
            else:
                on_success(result)

        self.root.after(100, check_result)

    def _login_selected(self):
        account = self._selected_account()
        if not account:
            messagebox.showinfo("没有账号", "请先点击“添加账号”。")
            return
        self._connect_account(account, "登录账号")

    def _connect_account(self, account, operation_name):
        credentials = self._credentials()
        if not credentials:
            return
        try:
            session_path = self._session_path(account)
        except RuntimeError as exc:
            messagebox.showerror("会话无效", str(exc))
            return
        if not self._session_file(account).exists():
            self._remove_stale_account(account, "该账号的 session 文件不存在，已从列表移除。")
            return
        status = self._session_status(account)
        if status in ("corrupt", "unreadable", "busy"):
            self.session_issues[account["id"]] = status
            self._update_account_buttons()
            messagebox.showwarning("会话暂不可用", "会话损坏、不可读取或正在使用。账号记录已保留，请检查文件权限或恢复会话后重试。")
            return
        self._clear_preview()
        self.connected = False
        self.current_account_id = None
        self._set_busy(True, "正在连接并加载预览……")
        self._log(f"正在连接账号：{account['label']}")
        api_id, api_hash = credentials
        self._submit(
            self._prepare(api_id, api_hash, session_path, allow_authentication=False),
            lambda result: self._account_connected(account, result),
            operation_name,
            lambda exc: self._handle_connect_error(account, exc),
        )

    def _add_account(self):
        credentials = self._credentials()
        if not credentials:
            return
        account_id = uuid.uuid4().hex
        account = {
            "id": account_id,
            "session": f"sessions/account_{account_id}",
            "label": "新账号",
            "user_id": "",
        }
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        session_path = self._session_path(account)
        self._clear_preview()
        self.connected = False
        self.current_account_id = None
        self._set_busy(True, "正在登录新账号……")
        self._log("正在添加新账号，等待登录验证……")
        api_id, api_hash = credentials
        self._submit(
            self._prepare(
                api_id,
                api_hash,
                session_path,
                delete_on_auth_failure=True,
                allow_authentication=True,
                new_account=account,
            ),
            lambda result: self._new_account_connected(account, result),
            "添加账号",
        )

    def _handle_connect_error(self, account, error):
        if not isinstance(error, StoredSessionInvalidError):
            return False
        self._remove_stale_account(account, str(error))
        return True

    def _remove_stale_account(self, account, reason):
        self.accounts = [item for item in self.accounts if item["id"] != account["id"]]
        if self.current_account_id == account["id"]:
            self.current_account_id = None
        self._clear_preview()
        self._refresh_account_list()
        try:
            self._save_config()
        except OSError as exc:
            messagebox.showwarning("配置保存失败", f"无法移除失效账号记录：\n{exc}")
        self.status_text.set("失效账号已移除")
        self._log(f"已移除失效账号：{account['label']}（{reason}）")
        messagebox.showwarning("账号已失效", reason)

    def _new_account_connected(self, account, result):
        user_id = str(result["user_id"]) if result["user_id"] else ""
        duplicate = next((item for item in self.accounts if item["id"] != account["id"] and user_id and item.get("user_id") == user_id), None)
        if duplicate:
            session_path = self._session_path(account)
            self._set_busy(True, "正在清理重复登录……")
            self._submit(
                self._discard_new_session(session_path),
                lambda error: self._duplicate_discarded(duplicate, account, error),
                "清理重复账号",
            )
            return
        if not any(item["id"] == account["id"] for item in self.accounts):
            self.accounts.append(account)
        self._account_connected(account, result)

    def _register_authorized_account(self, account):
        account["label"] = "已授权账号（连接后识别）"
        self.accounts.append(account)
        try:
            self._save_config(account["id"])
        except OSError as exc:
            messagebox.showwarning("账号登记失败", f"授权会话已保留：{account['session']}\n修复配置写入权限后，点击“登录 / 连接”即可重试保存。\n若已退出，可用以下信息恢复配置中的账号登记：\n{json.dumps(account, ensure_ascii=False)}\n\n{exc}")
        if not self.closing:
            self._refresh_account_list(account["id"])

    async def _discard_new_session(self, session_path):
        client = self.client
        error = None
        try:
            if client and client.is_connected() and await self._timed(client.is_user_authorized(), 15, "验证重复账号"):
                await self._timed(client.log_out(), 30, "退出重复账号")
        except Exception as exc:
            error = str(exc)
        finally:
            release_error = await self._release_client(client) if client else None
            self.client = None
            if release_error:
                raise RuntimeError(release_error)
            await self._delete_session_files(session_path)
        return error

    def _duplicate_discarded(self, existing_account, new_account, remote_error):
        self.accounts = [item for item in self.accounts if item["id"] != new_account["id"]]
        self.connected = False
        self.current_account_id = None
        self._clear_preview()
        self._refresh_account_list(existing_account["id"])
        try:
            self._save_config(existing_account["id"])
        except OSError as exc:
            messagebox.showwarning("配置保存失败", f"重复会话已删除，但账号列表保存失败：\n{exc}")
        self._set_busy(False, "该账号已存在，请直接连接列表中的账号")
        self.status_text.set("该账号已存在，请直接连接列表中的账号")
        self._log(f"重复账号登录已撤销：{existing_account['label']}")
        messagebox.showinfo("账号已存在", "该 Telegram 账号已经在列表中，新创建的重复登录会话已删除。")
        if remote_error:
            messagebox.showwarning("远程退出失败", f"本地重复会话已删除，请在 Telegram 的设备页面检查活动会话。\n\n{remote_error}")

    def _account_connected(self, account, result):
        self.connected = True
        account["label"] = result["label"]
        account["user_id"] = str(result["user_id"]) if result["user_id"] else account.get("user_id", "")
        self.current_account_id = account["id"]
        try:
            self._save_config(account["id"])
        except OSError as exc:
            messagebox.showwarning("配置保存失败", f"账号已连接，但无法保存账号列表：\n{exc}")
        self._refresh_account_list(account["id"])
        self.account_text.set(result["label"])
        preview_error = result.get("preview_error")
        self.preview_loaded = not preview_error
        self.group_count.set(str(result["groups"]) if self.preview_loaded else "—")
        self.dialog_count.set(str(result["dialogs"]) if self.preview_loaded else "—")
        self.contact_count.set(str(result["contacts"]) if self.preview_loaded else "—")
        if self.preview_loaded:
            self._populate_selection()
        else:
            self._reset_selection()
        total = result["groups"] + result["dialogs"] + result["contacts"]
        self.status_text.set("账号已连接，预览已加载" if self.preview_loaded else "账号已保存，但预览加载失败")
        self._set_busy(False, self.status_text.get())
        self._update_execute_state()
        self._log(
            f"已连接 {result['label']}：群组/频道 {result['groups']}，"
            f"用户私聊 {result['dialogs']}，联系人 {result['contacts']}。"
        )
        if preview_error:
            messagebox.showwarning(
                "账号已保存",
                f"账号登录状态已保留，但清理预览加载失败。请稍后点击“登录 / 连接”重试。\n\n{preview_error}",
            )
        elif not total:
            messagebox.showinfo("无需清理", "没有发现可处理的项目。")

    def _logout_selected(self):
        account = self._selected_account()
        if not account:
            messagebox.showinfo("没有账号", "账号列表为空。")
            return
        if not messagebox.askyesno(
            "退出登录",
            f"确定退出“{self._account_display(account)}”吗？\n\n"
            "将撤销此账号的 Telegram 登录授权，断开连接，并删除本地会话及账号记录。再次添加需要重新验证。\n\n"
            "远端退出失败时会保留账号和会话，以便重试。",
            icon="warning",
        ):
            return
        try:
            session_path = self._session_path(account)
        except RuntimeError as exc:
            messagebox.showerror("会话无效", str(exc))
            return
        # Credentials are only needed when a new client must be constructed.
        credentials = self._credentials(required=False)
        if account["id"] == self.current_account_id:
            self._clear_preview("正在退出登录")
        self._set_busy(True, "正在退出登录并删除本地会话……")
        self._submit(
            self._logout_account(account, session_path, credentials),
            lambda result: self._account_logged_out(account, result),
            "退出登录",
        )

    async def _logout_account(self, account, session_path, credentials):
        """Keep remote, release and local results independent; never reauthenticate."""
        account_id = account["id"]
        result = dict(remote_error=None, disconnect_error=None, delete_error=None, deleted=False)
        client = self.logout_clients.get(account_id)
        owns_current = self.current_account_id == account_id
        # A failed release may have closed SQLite while leaving the transport
        # alive. Finish releasing that client before constructing a fresh one.
        if client is not None and account_id not in self.revoked_accounts:
            result["disconnect_error"] = await self._release_client(client)
            if result["disconnect_error"]:
                return result
            self.logout_clients.pop(account_id, None)
            if self.client is client:
                self.client = None
            client = None
        if client is None and owns_current:
            client = self.client
        try:
            if account_id not in self.revoked_accounts:
                status = "valid" if client else self._read_session_status(account)
                if status in ("missing", "empty"):
                    self.revoked_accounts.add(account_id)
                elif status != "valid":
                    raise RuntimeError("会话暂不可读取或已损坏；账号和会话已保留，请修复后重试。")
                else:
                    if client is None:
                        if not credentials:
                            raise RuntimeError("请输入有效的 API 凭据后重试退出登录。")
                        client = TelegramClient(str(session_path), *credentials, flood_sleep_threshold=0)
                        self.clients.add(client)
                    self.logout_clients[account_id] = client
                    if not client.is_connected():
                        await self._timed(client.connect(), 30, "连接退出账号")
                    if await self._timed(client.is_user_authorized(), 30, "验证退出账号"):
                        # Telethon.log_out() also disconnects and deletes files.
                        # Use the RPC directly so those phases stay independent.
                        if not await self._timed(client(LogOutRequest()), 30, "退出账号"):
                            raise RuntimeError("Telegram 未确认退出登录，请重试。")
                    self.revoked_accounts.add(account_id)
        except Exception as exc:
            result["remote_error"] = str(exc)
        finally:
            if client:
                result["disconnect_error"] = await self._release_client(client)
                if not result["disconnect_error"]:
                    self.logout_clients.pop(account_id, None)
                    if self.client is client:
                        self.client = None
        if account_id in self.revoked_accounts and not result["disconnect_error"]:
            try:
                await self._delete_session_files(session_path)
                result["deleted"] = True
            except Exception as exc:
                result["delete_error"] = str(exc)
        return result

    async def _release_client(self, client):
        """Shield session release, retain ownership until the release really ends."""
        async def release():
            error = None
            try:
                await client.disconnect()
            except Exception as exc:
                error = f"断开连接失败：{exc}"
            finally:
                try:
                    session = getattr(client, "session", None)
                    if session is not None:
                        session.close()
                except Exception as exc:
                    error = f"{error or ''} 会话关闭失败：{exc}".strip()
                if not error:
                    self.clients.discard(client)
            return error

        task = asyncio.create_task(release())
        self.disconnect_tasks.add(task)
        task.add_done_callback(self.disconnect_tasks.discard)
        return await asyncio.shield(task)

    def _account_logged_out(self, account, result):
        if self.current_account_id == account["id"]:
            self.connected = False
            self.current_account_id = None
            self._clear_preview()
        if not result["deleted"]:
            errors = [result[key] for key in ("remote_error", "disconnect_error", "delete_error") if result[key]]
            detail = "\n".join(errors)
            if account["id"] in self.revoked_accounts:
                self.session_issues[account["id"]] = "revoked"
                detail = "远端授权已撤销；请再次点击“退出登录”完成本地清理。\n\n" + detail
            self._set_busy(False, "退出未完成，账号已保留，可重试")
            self._log(f"退出未完成：{account['label']}\n{detail}")
            messagebox.showwarning("退出未完成", detail)
            return
        self.revoked_accounts.discard(account["id"])
        self.session_issues.pop(account["id"], None)
        self.accounts = [item for item in self.accounts if item["id"] != account["id"]]
        self._refresh_account_list()
        try:
            self._save_config()
        except OSError as exc:
            messagebox.showwarning("配置保存失败", f"账号授权已退出、会话已删除，但账号列表保存失败：\n{exc}")
        self._set_busy(False, "已退出登录并删除账号；请选择账号或点击“登录 / 连接”" if self.accounts else "已退出登录，账号列表为空")
        self._update_execute_state()
        self._log(f"已退出登录并删除本地会话及账号：{account['label']}")

    def _ask_required(self, title, prompt, secret=False):
        if self.closing:
            raise RuntimeError("程序正在退出，登录已取消。")
        value = simpledialog.askstring(title, prompt, show="*" if secret else None, parent=self.root)
        if value is None or not value.strip():
            raise RuntimeError(f"已取消{title}。")
        return value.strip()

    async def _authenticate(self, client):
        if await self._timed(client.is_user_authorized(), 30, "验证账号会话"):
            return
        phone = await self._request_ui(
            lambda: self._ask_required("账号登录", "请输入手机号（含国家区号，例如 +8613800000000）：")
        )
        sent_code = await self._timed(client.send_code_request(phone), 30, "发送登录验证码")
        for attempt in range(3):
            code = await self._request_ui(lambda: self._ask_required("登录验证码", "请输入 Telegram 登录验证码："))
            try:
                await self._timed(
                    client.sign_in(phone, code, phone_code_hash=sent_code.phone_code_hash),
                    30,
                    "提交登录验证码",
                )
                return
            except PhoneCodeInvalidError:
                if attempt == 2:
                    raise RuntimeError("登录验证码连续三次错误。")
                await self._request_ui(lambda: messagebox.showwarning("验证码错误", "验证码无效，请重试。"))
            except PhoneCodeExpiredError as exc:
                raise RuntimeError("登录验证码已过期，请重新添加账号并获取新验证码。") from exc
            except SessionPasswordNeededError:
                break
        for attempt in range(3):
            password = await self._request_ui(
                lambda: self._ask_required("两步验证", "请输入 Telegram 两步验证密码：", secret=True)
            )
            try:
                await self._timed(client.sign_in(password=password), 30, "提交两步验证密码")
                return
            except PasswordHashInvalidError:
                if attempt == 2:
                    raise RuntimeError("两步验证密码连续三次错误。")
                await self._request_ui(lambda: messagebox.showwarning("密码错误", "两步验证密码错误，请重试。"))

    @staticmethod
    async def _timed(awaitable, seconds, label):
        try:
            return await asyncio.wait_for(awaitable, timeout=seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{label}超时，请检查网络后重试。") from exc

    async def _prepare(
        self,
        api_id,
        api_hash,
        session_path,
        delete_on_auth_failure=False,
        allow_authentication=False,
        new_account=None,
    ):
        if self.client:
            error = await self._release_client(self.client)
            if error:
                raise RuntimeError(error)
            self.client = None
        if self.clients:
            raise RuntimeError("仍有会话未安全释放，请退出程序后重试。")
        client = None
        auth_completed = False
        try:
            client = TelegramClient(str(session_path), api_id, api_hash, flood_sleep_threshold=0)
            self.clients.add(client)
            await self._timed(client.connect(), 30, "连接 Telegram")
            authorized = await self._timed(client.is_user_authorized(), 30, "验证账号会话")
            if not authorized and not allow_authentication:
                error = await self._release_client(client)
                if error:
                    raise RuntimeError(error)
                await self._delete_session_files(session_path)
                raise StoredSessionInvalidError("该账号的登录会话已失效，已删除空 session 和账号记录；请重新添加账号。")
            if not authorized:
                await self._authenticate(client)
            auth_completed = True
            if new_account is not None:
                # Persist authorization independently of preview loading/cancellation.
                registration = asyncio.create_task(self._request_ui(lambda: self._register_authorized_account(new_account)))
                try:
                    await asyncio.shield(registration)
                except asyncio.CancelledError:
                    await registration
                    raise
        except BaseException:
            release_error = await self._release_client(client) if client else None
            if delete_on_auth_failure and not auth_completed:
                if not release_error:
                    await self._delete_session_files(session_path)
            raise

        label = "已登录账号（信息待刷新）"
        user_id = ""
        try:
            me = await self._timed(client.get_me(), 30, "读取账号信息")
            name = " ".join(value for value in (me.first_name, me.last_name) if value)
            identity = f"@{me.username}" if me.username else (f"+{me.phone}" if me.phone else str(me.id))
            label = f"{name or '未命名账号'} ({identity})"
            user_id = me.id
            dialogs = await self._timed(client.get_dialogs(), 90, "读取会话列表")
            groups = []
            private_users = []
            for dialog in dialogs:
                entity = dialog.entity
                if isinstance(entity, Chat):
                    groups.append((dialog, "群组"))
                elif isinstance(entity, Channel):
                    groups.append((dialog, "频道/群组"))
                elif isinstance(entity, User) and not entity.is_self:
                    private_users.append(dialog)
            contacts_result = await self._timed(client(GetContactsRequest(hash=0)), 30, "读取联系人")
            contacts = [user for user in contacts_result.users if isinstance(user, User)]
            preview_error = None
        except asyncio.CancelledError:
            await self._release_client(client)
            raise
        except Exception as exc:
            groups = []
            private_users = []
            contacts = []
            preview_error = str(exc)

        self.client = client
        self.groups = groups
        self.private_users = private_users
        self.contacts = contacts
        return {
            "label": label,
            "user_id": user_id,
            "groups": len(groups),
            "dialogs": len(private_users),
            "contacts": len(contacts),
            "preview_error": preview_error,
        }

    def _confirm_cleanup(self):
        if self.busy:
            return
        selected = self._selected_account()
        if (
            not self.preview_loaded
            or not selected
            or selected["id"] != self.current_account_id
            or not self.connected
        ):
            messagebox.showwarning("预览已失效", "请重新连接目标账号并加载清理预览。")
            self._update_execute_state()
            return
        snapshot = self._selection_snapshot()
        if not (snapshot.groups or snapshot.private_users or snapshot.contacts):
            messagebox.showwarning("未选择项目", "请勾选要清理的项目。")
            return
        counts = f"将退出 {len(snapshot.groups)} 个群组/频道、删除 {len(snapshot.private_users)} 个私聊及双方记录，并移除 {len(snapshot.contacts)} 个联系人。"
        first = messagebox.askyesno(
            "确认批量清理",
            f"目标账号：{self._account_display(selected)}\n\n"
            + counts + "\n\n" +
            "私聊删除将同时删除对方聊天记录，并从当前账号列表移除对话。\n不会处理 Saved Messages。\n此操作无法撤销。\n\n是否继续？",
            icon="warning",
        )
        if not first:
            return
        if not messagebox.askyesno("最终确认", f"目标账号：{self._account_display(selected)}\n\n{counts}\n\n将删除私聊及双方记录，包括对方聊天记录，并从当前账号列表移除对话。\n此操作无法撤销。确定立即清理已选项目吗？", icon="warning"):
            return
        self.preview_loaded = False
        self.cleanup_running = True
        self.stop_button.configure(state="normal")
        self._set_busy(True, "正在执行清理……")
        self._log("已完成两次确认，开始执行批量清理。")
        self._submit(self._cleanup(snapshot), self._cleanup_finished, "批量清理")

    def _stop_cleanup(self):
        if not self.cleanup_running:
            return
        self.stop_button.configure(state="disabled")
        self.status_text.set("正在停止清理并释放会话……")
        self.loop.call_soon_threadsafe(self._cancel_cleanup)

    def _cancel_cleanup(self):
        if self.cleanup_task and not self.cleanup_task.cancelling():
            self.cleanup_task.cancel()

    def _progress(self, text):
        if not self.closing:
            self.ui_requests.put((lambda: self.status_text.set(text), Future()))

    async def _perform(self, label, operation_factory, flood_state):
        while True:
            try:
                return await asyncio.wait_for(operation_factory(), CLEANUP_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise CleanupStopped(f"请求超过 {CLEANUP_TIMEOUT} 秒，结果未知；请重新加载预览。", unknown=True) from exc
            except FloodWaitError as exc:
                if flood_state["waited"] or exc.seconds > MAX_FLOOD_WAIT:
                    raise CleanupStopped(f"限流等待 {exc.seconds} 秒，超过等待策略；剩余清理已停止。") from exc
                flood_state["waited"] = True
                self._log(f"Telegram 限流 {exc.seconds} 秒：{label}")
                deadline = time.monotonic() + exc.seconds
                while time.monotonic() < deadline:
                    remaining = max(1, int(deadline - time.monotonic() + 0.999))
                    self._progress(f"限流等待：{remaining} 秒；{label}")
                    await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

    async def _clear_private_history(self, client, entity, label, flood_state):
        while True:
            result = await self._perform(
                label,
                lambda: client(DeleteHistoryRequest(peer=entity, max_id=0, just_clear=False, revoke=True)),
                flood_state,
            )
            if result.offset == 0:
                return
            flood_state["partial"] = True

    async def _cleanup(self, snapshot):
        client = self.client
        result = {"success": [], "failed": [], "unknown": [], "unexecuted": [], "stop_reason": None, "disconnect_error": None}
        operations = []
        for dialog, kind in snapshot.groups:
            entity = dialog.entity
            title = getattr(entity, "title", str(entity.id))
            factory = ((lambda entity=entity: client(LeaveChannelRequest(entity)))
                       if isinstance(entity, Channel)
                       else (lambda entity=entity: client.delete_dialog(entity)))
            operations.append((f"退出{kind}：{title}", factory, None))
        for dialog in snapshot.private_users:
            entity = dialog.entity
            if entity.is_self:
                continue
            name = " ".join(value for value in (entity.first_name, entity.last_name) if value) or str(entity.id)
            operations.append((f"删除私聊及双方记录：{name}", None, entity))
        contacts = snapshot.contacts
        if contacts:
            operations.append((f"移除联系人（{len(contacts)} 人）", lambda: client(DeleteContactsRequest(id=list(contacts))), None))
        self.cleanup_task = asyncio.current_task()
        try:
            if snapshot.account_id != self.current_account_id or not client or not client.is_connected():
                result["stop_reason"] = "Telegram 连接已断开，请重新加载预览。"
                result["unexecuted"] = [label for label, _, _ in operations]
            else:
                for index, (label, factory, entity) in enumerate(operations):
                    self._progress(f"正在处理 {index + 1}/{len(operations)}：{label}")
                    flood_state = {"waited": False, "partial": False}
                    try:
                        if entity is not None:
                            await self._clear_private_history(client, entity, label, flood_state)
                        else:
                            await self._perform(label, factory, flood_state)
                    except CleanupStopped as exc:
                        result["stop_reason"] = str(exc)
                        if exc.unknown:
                            result["unknown"].append((label, str(exc)))
                        elif flood_state["partial"]:
                            result["failed"].append((label, f"部分双方记录已删除，其余未完成：{exc}"))
                        else:
                            result["unexecuted"].append(label)
                        result["unexecuted"].extend(item[0] for item in operations[index + 1:])
                        break
                    except asyncio.CancelledError:
                        result["unknown"].append((label, "已取消；当前项目可能已经部分或全部执行，请重新加载预览。"))
                        result["unexecuted"].extend(item[0] for item in operations[index + 1:])
                        result["stop_reason"] = "用户停止清理"
                        break
                    except Exception as exc:
                        result["failed"].append((label, str(exc)))
                        self._log(f"失败：{label}：{exc}")
                    else:
                        result["success"].append(label)
                        self._log(f"完成：{label}")
        finally:
            self.cleanup_task = None
            if client:
                result["disconnect_error"] = await self._release_client(client)
            if self.client is client:
                self.client = None
        return result

    def _cleanup_finished(self, result):
        self._reset_selection()
        self.cleanup_running = False
        self.connected = False
        self.stop_button.configure(state="disabled")
        self.preview_loaded = False
        self.execute_button.configure(state="disabled")
        self.group_count.set("—")
        self.dialog_count.set("—")
        self.contact_count.set("—")
        summary = (f"成功 {len(result['success'])}，失败 {len(result['failed'])}，"
                   f"结果未知 {len(result['unknown'])}，未执行 {len(result['unexecuted'])}")
        self.status_text.set("清理已停止，请重新连接" if result["stop_reason"] else "清理结束，请重新连接")
        self._set_busy(False, self.status_text.get())
        self._log(summary)
        self._show_details("清理结果", summary, result)

    def _show_details(self, title, summary, result):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("720x480")
        window.minsize(680, 380)
        window.transient(self.root)
        window.configure(background=self.colors["background"])
        container = ttk.Frame(window, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)
        ttk.Label(container, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        summary_label = ttk.Label(container, text=summary, style="Subtitle.TLabel", wraplength=640, justify="left")
        summary_label.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self._wrap_to_width(summary_label, container)
        details = ScrolledText(container, width=1, wrap="word", **self._text_options())
        details.grid(row=2, column=0, sticky="nsew")
        details.tag_configure("section", foreground=self.colors["blue"], font=self.fonts["section"], spacing1=6, spacing3=4)
        details.tag_configure("notice", foreground=self.colors["red"])
        for key, heading in (("stop_reason", "停止原因"), ("disconnect_error", "断开连接")):
            if result.get(key):
                details.insert("end", f"{heading}：{result[key]}\n\n", "notice")
        for key, heading in (("success", "成功"), ("failed", "失败"), ("unknown", "结果未知"), ("unexecuted", "未执行")):
            items = result.get(key, [])
            if items:
                details.insert("end", f"{heading}（{len(items)}）：\n", "section")
                for item in items:
                    text = f"{item[0]}：{item[1]}" if isinstance(item, tuple) else item
                    details.insert("end", f"- {text}\n")
                details.insert("end", "\n")
        details.configure(state="disabled")
        ttk.Button(container, text="关闭", command=window.destroy).grid(row=3, column=0, sticky="e", pady=(12, 0))

    async def _shutdown_background(self):
        tasks = [task for task in self.async_tasks if task is not asyncio.current_task()]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.disconnect_tasks:
            await asyncio.gather(*tuple(self.disconnect_tasks), return_exceptions=True)
        errors = []
        for client in tuple(self.clients):
            error = await self._release_client(client)
            if error:
                errors.append(error)
        if errors:
            raise RuntimeError("；".join(errors))
        self.client = None

    def _on_close(self):
        if self.closing:
            return
        if self.busy and not messagebox.askyesno(
            "任务正在执行",
            "当前有任务正在执行。是否立即停止任务并退出程序？\n\n"
            "已经完成的操作无法撤销。",
            icon="warning",
        ):
            return

        self.closing = True
        self.status_text.set("正在停止任务并退出……" if self.busy else "正在退出……")

        shutdown_future = asyncio.run_coroutine_threadsafe(self._shutdown_background(), self.loop)
        loop_stop_requested = False

        def finish_close():
            nonlocal loop_stop_requested
            if not loop_stop_requested and not shutdown_future.done():
                self.root.after(50, finish_close)
                return
            if not loop_stop_requested:
                error = shutdown_future.exception()
                if error:
                    self.closing = False
                    self.connected = False
                    self.cleanup_running = False
                    self.stop_button.configure(state="disabled")
                    self._clear_preview()
                    self._set_busy(False, "退出未完成，请重试关闭窗口")
                    messagebox.showwarning("会话尚未安全释放", str(error))
                    return
                self.loop.call_soon_threadsafe(self.loop.stop)
                loop_stop_requested = True
            if not self.loop_closed.is_set():
                self.root.after(50, finish_close)
                return
            self.loop_thread.join()
            self.root.destroy()

        finish_close()


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
