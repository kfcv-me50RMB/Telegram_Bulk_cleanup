import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText


class UiMixin:
    """Widget layout, styling and display state."""

    @staticmethod
    def _ui_scale(root):
        """Single source of truth for DPI scaling used by layout and geometry."""
        return max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))

    def _configure_styles(self):
        self.colors = {
            "background": "#EEF2F7", "surface": "#FFFFFF", "surface_alt": "#F7F9FC",
            "text": "#16233A", "muted": "#5B6B84", "faint": "#93A1B5",
            "border": "#E1E8F1", "border_strong": "#CBD6E4",
            "blue": "#229ED9", "blue_hover": "#1C8FC7", "blue_pressed": "#1477A8",
            "blue_soft": "#E6F3FB",
            "red": "#D64545", "red_hover": "#BF3636", "red_pressed": "#A52D2D",
            "red_soft": "#FDECEC", "notice": "#9D3C3C",
            "green": "#2E9E6B", "amber": "#C98A0B",
            "disabled_bg": "#EDF1F6", "disabled_fg": "#748397",
        }
        families = set(tkfont.families(self.root))
        family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        self.fonts = {
            "body": (family, 10), "small": (family, 9), "heading": (family, 17, "bold"),
            "section": (family, 11, "bold"), "metric": (family, 18, "bold"),
            "button": (family, 10, "bold"),
            "log": ("Consolas" if "Consolas" in families else family, 10),
        }
        self.ui_scale = self._ui_scale(self.root)
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
        style.configure("SurfaceAlt.TFrame", background=c["surface_alt"])
        style.configure("Rule.TFrame", background=c["border"])
        style.configure("Accent.TFrame", background=c["blue"])
        style.configure("Card.TFrame", background=c["surface"], bordercolor=c["border"], borderwidth=1, relief="solid")
        style.configure("TLabel", background=c["surface"], foreground=c["text"], font=self.fonts["body"])
        style.configure("Title.TLabel", background=c["background"], foreground=c["text"], font=self.fonts["heading"])
        style.configure("Subtitle.TLabel", background=c["background"], foreground=c["muted"], font=self.fonts["small"])
        style.configure("Section.TLabel", background=c["surface"], foreground=c["text"], font=self.fonts["section"])
        style.configure("Muted.TLabel", background=c["surface"], foreground=c["muted"], font=self.fonts["small"])
        style.configure("Status.TLabel", background=c["surface"], foreground=c["text"], font=self.fonts["body"])
        style.configure("Metric.TLabel", background=c["surface_alt"], foreground=c["blue"], font=self.fonts["metric"])
        style.configure("MetricCaption.TLabel", background=c["surface_alt"], foreground=c["muted"], font=self.fonts["small"])
        style.configure("Brand.TLabel", background=c["blue"], foreground="white", font=(family, 15, "bold"), padding=(11, 7))
        style.configure("Pill.TLabel", background=c["blue_soft"], foreground=c["blue"], font=self.fonts["small"], padding=(8, 3))
        style.configure("Notice.TLabel", background=c["surface"], foreground=c["notice"], font=self.fonts["small"])
        style.configure("TEntry", padding=(12, 6), fieldbackground=c["surface"], foreground=c["text"], insertcolor=c["text"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        style.map("TEntry", bordercolor=[("focus", c["blue"])], fieldbackground=[("disabled", c["disabled_bg"])], foreground=[("disabled", c["muted"])])
        style.configure("TCombobox", padding=(10, 7), foreground=c["text"], fieldbackground=c["surface"], background=c["surface"], bordercolor=c["border"], arrowcolor=c["muted"], lightcolor=c["border"], darkcolor=c["border"])
        style.map("TCombobox", fieldbackground=[("disabled", c["disabled_bg"]), ("readonly", c["surface"])], foreground=[("disabled", c["muted"])], bordercolor=[("focus", c["blue"])], arrowcolor=[("disabled", c["faint"])], selectbackground=[("readonly", c["surface"])], selectforeground=[("readonly", c["text"])])
        for name, color, hover, pressed, foreground, font, padding in (
            ("TButton", c["surface"], c["surface_alt"], "#E8EEF6", c["text"], self.fonts["body"], (14, 7)),
            ("Primary.TButton", c["blue"], c["blue_hover"], c["blue_pressed"], "white", self.fonts["button"], (16, 8)),
            ("Danger.TButton", c["red"], c["red_hover"], c["red_pressed"], "white", self.fonts["button"], (16, 8)),
        ):
            style.configure(name, font=font, padding=padding, background=color, foreground=foreground, bordercolor=c["border_strong"] if name == "TButton" else color, borderwidth=1, focusthickness=2, focuscolor=c["blue"])
            style.map(name, background=[("disabled", c["disabled_bg"]), ("pressed", pressed), ("active", hover)], foreground=[("disabled", c["disabled_fg"])], bordercolor=[("disabled", c["border"]), ("focus", c["blue"])])
        style.configure("Ghost.TButton", font=self.fonts["body"], padding=(12, 6), background=c["background"], foreground=c["muted"], bordercolor=c["background"], borderwidth=0, focusthickness=0)
        style.map("Ghost.TButton", background=[("disabled", c["background"]), ("pressed", c["border"]), ("active", c["border"])], foreground=[("disabled", c["faint"]), ("!disabled", c["text"])], bordercolor=[("!disabled", c["background"])])
        style.configure("Login.TCheckbutton", background=c["surface"], foreground=c["text"], font=self.fonts["small"])
        style.map("Login.TCheckbutton", background=[("active", c["surface"])], foreground=[("disabled", c["muted"])])
        style.configure("TNotebook", background=c["surface"], borderwidth=0, tabmargins=(2, 2, 2, 0))
        style.configure("TNotebook.Tab", background=c["surface_alt"], foreground=c["muted"], padding=(14, 6), font=self.fonts["body"], borderwidth=0, bordercolor=c["border"], lightcolor=c["surface_alt"], darkcolor=c["surface_alt"])
        style.map("TNotebook.Tab", background=[("selected", c["surface"]), ("active", c["blue_soft"])], foreground=[("selected", c["blue"]), ("active", c["text"])], bordercolor=[("selected", c["blue"])], lightcolor=[("selected", c["surface"])], darkcolor=[("selected", c["surface"])])
        # Replace the default layout to drop the dotted focus ring inside the label.
        style.layout("TNotebook.Tab", [("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [("Notebook.label", {"side": "top", "sticky": ""})]})])
        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"], font=self.fonts["body"], rowheight=int(30 * self.ui_scale), borderwidth=0)
        style.configure("Treeview.Heading", background=c["surface_alt"], foreground=c["muted"], font=(*self.fonts["small"], "bold"), padding=(10, 6), relief="flat", borderwidth=0)
        style.map("Treeview", background=[("selected", c["blue_soft"])], foreground=[("disabled", c["muted"]), ("selected", c["text"])])
        style.map("Treeview.Heading", background=[("active", c["blue_soft"])], foreground=[("active", c["blue"])])
        for name in ("Horizontal.TScrollbar", "Vertical.TScrollbar"):
            style.configure(name, background=c["border_strong"], troughcolor=c["surface_alt"], bordercolor=c["surface_alt"], arrowcolor=c["muted"], lightcolor=c["border_strong"], darkcolor=c["border_strong"], arrowsize=int(14 * self.ui_scale))
            style.map(name, background=[("active", c["faint"]), ("pressed", c["muted"])], arrowcolor=[("disabled", c["border"])])
        style.configure("Secondary.TButton", font=self.fonts["small"], padding=(12, 5))
        style.configure("DangerOutline.TButton", font=self.fonts["body"], padding=(14, 7), background=c["surface"], foreground=c["red"], bordercolor=c["red"], focuscolor=c["red"])
        style.map("DangerOutline.TButton", background=[("disabled", c["disabled_bg"]), ("pressed", c["red_soft"]), ("active", "#FFF4F4")], foreground=[("disabled", c["disabled_fg"]), ("!disabled", c["red"])], bordercolor=[("disabled", c["border"]), ("focus", c["red_pressed"]), ("!disabled", c["red"])])

    # Status bar tone keywords, most urgent first. Kept in one place so every
    # producer of status text (accounts, cleanup, background) is styled alike.
    STATUS_TONES = (
        ("error", ("失败", "错误", "超时", "无效", "未完成", "未释放", "请重试")),
        ("busy", ("正在", "限流", "等待", "停止", "取消", "刷新", "处理中")),
        ("ok", ("已连接", "已加载", "已完成", "已保存")),
    )

    def _status_tone(self, text):
        for tone, keywords in self.STATUS_TONES:
            if any(keyword in text for keyword in keywords):
                return tone
        return "idle"

    def _refresh_status_indicator(self):
        indicator = getattr(self, "status_indicator", None)
        if indicator is None:
            return
        tone = self._status_tone(self.status_text.get())
        indicator.itemconfigure(self.status_dot, fill={
            "error": self.colors["red"], "busy": self.colors["amber"],
            "ok": self.colors["green"],
        }.get(tone, self.colors["faint"]))

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
            if getattr(self, "closing", False) or not self.root.winfo_exists():
                return
            account = self._selected_account()
            if not account:
                return
            window = tk.Toplevel(self.root)
            state["window"] = window
            window.overrideredirect(True)
            label = ttk.Label(window, text=self._account_display(account), padding=(12, 8), wraplength=int(420 * self.ui_scale), justify="left", relief="solid", borderwidth=1)
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
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.columnconfigure(0, weight=1)
        header = ttk.Frame(card, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Frame(header, style="Accent.TFrame", width=3, height=14).pack(side="left", padx=(0, 8), pady=1)
        ttk.Label(header, text=title, style="Section.TLabel").pack(side="left")
        return card

    def _text_options(self):
        return dict(background=self.colors["surface"], foreground=self.colors["text"],
                    font=self.fonts["log"], relief="flat", borderwidth=0,
                    highlightthickness=1, highlightbackground=self.colors["border"],
                    highlightcolor=self.colors["blue"], selectbackground=self.colors["blue_soft"],
                    selectforeground=self.colors["text"], insertbackground=self.colors["text"],
                    padx=12, pady=10, spacing1=2, spacing3=4)

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
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="TG", style="Brand.TLabel").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ttk.Label(header, text="Telegram 批量清理", style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="管理账号 · 预览范围 · 确认后清理", style="Subtitle.TLabel").grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Frame(header, style="Rule.TFrame", height=1).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

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
        sidebar_shell.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        sidebar.columnconfigure(0, weight=1)

        credentials = self._card(sidebar, "API 凭据")
        credentials.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(credentials, text="API ID", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 5))
        self.api_id_entry = ttk.Entry(credentials, textvariable=self.api_id, width=1)
        self.api_id_entry.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(credentials, text="API Hash", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(0, 5))
        self.api_hash_entry = ttk.Entry(credentials, textvariable=self.api_hash, show="•", width=1)
        self.api_hash_entry.grid(row=4, column=0, sticky="ew")
        credentials_note = ttk.Label(credentials, text="凭据明文保存在本机，请勿分享。", style="Muted.TLabel", justify="left", wraplength=250)
        credentials_note.grid(row=5, column=0, sticky="ew", pady=(10, 0))
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
        self.login_button.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 8))
        self.add_button = ttk.Button(account_actions, text="添加账号", command=self._add_account)
        self.add_button.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 8))
        self.logout_button = ttk.Button(account_actions, text="退出登录", style="DangerOutline.TButton", command=self._logout_selected)
        self.logout_button.grid(row=1, column=0, columnspan=2, sticky="ew")

        workspace = ttk.Frame(body)
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1, minsize=int(166 + 60 * (self.ui_scale - 1)))
        summary = ttk.Frame(workspace, style="Card.TFrame", padding=10)
        summary.columnconfigure(0, weight=1)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        account_label = ttk.Label(summary, textvariable=self.account_text, width=1, anchor="w", justify="left", style="Status.TLabel")
        account_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        metrics = ttk.Frame(summary, style="Surface.TFrame")
        metrics.grid(row=1, column=0, sticky="ew")
        for column, (label, variable) in enumerate((("群组 / 频道", self.group_count), ("用户私聊", self.dialog_count), ("联系人", self.contact_count))):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            tile = ttk.Frame(metrics, style="SurfaceAlt.TFrame", padding=(10, 4))
            tile.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            ttk.Label(tile, text=label + " · 已选 / 总数", style="MetricCaption.TLabel").pack(anchor="w")
            metric_font = tkfont.Font(root=self.root, font=self.fonts["metric"])
            reference_font = tkfont.Font(root=self.root, font=self.fonts["metric"])
            ttk.Label(tile, textvariable=variable, style="Metric.TLabel", font=metric_font).pack(anchor="w")
            def fit_metric(event=None, tile=tile, variable=variable, font=metric_font, reference=reference_font):
                available = max(1, tile.winfo_width() - 20)
                measured = max(1, reference.measure(variable.get()))
                font.configure(size=max(12, min(18, int(18 * available / measured))))
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
        self.selection_toggle_buttons = {}
        for category, caption in (("all", "全部"), ("private", "用户私聊"), ("groups", "群组 / 频道"), ("contacts", "联系人")):
            page = ttk.Frame(notebook, style="Surface.TFrame")
            notebook.add(page, text=caption)
            page.columnconfigure(0, weight=1)
            page.rowconfigure(1, weight=1)
            toolbar = ttk.Frame(page, style="Surface.TFrame")
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            button = ttk.Button(toolbar, text="全选", style="Secondary.TButton", command=lambda c=category: self._toggle_category_selection(c))
            button.pack(side="left", padx=3, pady=1)
            self.selection_buttons.append(button)
            self.selection_toggle_buttons[category] = button
            inverse = ttk.Button(toolbar, text="反选", style="Secondary.TButton", command=lambda c=category: self._invert_category_selection(c))
            inverse.pack(side="left", padx=3, pady=1)
            self.selection_buttons.append(inverse)
            count = tk.StringVar(value="已选 0 / 共 0 项")
            self.selection_counts[category] = count
            ttk.Label(toolbar, textvariable=count, style="Muted.TLabel").pack(side="right", padx=4)
            columns = ("checked", "type", "name", "username", "id") if category == "all" else ("checked", "name", "username", "id")
            tree = ttk.Treeview(page, columns=columns, show="headings", height=1, selectmode="browse")
            definitions = (("checked", "勾选", 44), ("type", "类型", 84), ("name", "名称", 170), ("username", "用户名", 112), ("id", "Telegram ID", 112))
            for column, title, width in definitions:
                if column not in columns:
                    continue
                width = int(width * self.ui_scale)
                anchor = "center" if column in ("checked", "type") else "e" if column == "id" else "w"
                tree.heading(column, text=title, anchor=anchor)
                tree.column(column, width=width, minwidth=width, stretch=column == "name", anchor=anchor)
            tree.tag_configure("even", background=self.colors["surface"])
            tree.tag_configure("odd", background=self.colors["surface_alt"])
            tree.grid(row=1, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=tree.yview)
            scrollbar.grid(row=1, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(page, orient="horizontal", command=tree.xview)
            horizontal.grid(row=2, column=0, sticky="ew")
            tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
            tree.bind("<Button-1>", lambda event, c=category: self._selection_click(c, event))
            tree.bind("<space>", lambda event, c=category: self._selection_space(c))
            self.selection_trees[category] = tree

        log_frame = ttk.Frame(sidebar_column, style="Card.TFrame", padding=10)
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="运行日志", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1, minsize=80)
        self.log = ScrolledText(log_frame, height=1, width=1, state="disabled", wrap="word", **self._text_options())
        self.log.grid(row=1, column=0, sticky="nsew")
        for tag, color in (("ok", self.colors["green"]), ("warn", self.colors["amber"]), ("error", self.colors["red"])):
            self.log.tag_configure(tag, foreground=color)

        def fit_sidebar(_event=None):
            # Reserve a usable log while allowing account controls to scroll.
            log_minimum = max(108, log_frame.winfo_reqheight())
            available = sidebar_column.winfo_height()
            controls = min(sidebar.winfo_reqheight() + 10, max(100, available - log_minimum))
            sidebar_column.rowconfigure(0, minsize=controls)
            sidebar_column.rowconfigure(1, minsize=log_minimum)

        sidebar_column.bind("<Configure>", fit_sidebar)
        sidebar.bind("<Configure>", lambda _event: sidebar.after_idle(fit_sidebar), add="+")

        footer = ttk.Frame(workspace, style="Card.TFrame", padding=8)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer, style="Surface.TFrame")
        actions.grid(row=0, column=0, sticky="ew")
        self.execute_button = ttk.Button(actions, text="清理已选项目", style="Danger.TButton", command=self._confirm_cleanup, state="disabled")
        self.execute_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止清理", command=self._stop_cleanup, state="disabled")
        self.stop_button.pack(side="left", padx=(10, 0))
        self.safety_note = safety_note = ttk.Label(footer, text="清理需两次确认。将双向删除私聊记录（包括对方记录），并从当前账号列表移除对话。", style="Notice.TLabel", wraplength=480, justify="left")
        safety_note.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._wrap_to_width(safety_note, footer, 24)
        self.scope_note = scope_note = ttk.Label(footer, text="Saved Messages 不参与清理；新消息可能使对话重新出现。", style="Muted.TLabel", justify="left")
        scope_note.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._wrap_to_width(scope_note, footer, 24)

        status = ttk.Frame(container, style="Card.TFrame", padding=(12, 6))
        status.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status.columnconfigure(2, weight=1)
        dot_size = max(8, int(10 * self.ui_scale))
        self.status_indicator = tk.Canvas(status, width=dot_size, height=dot_size, background=self.colors["surface"], highlightthickness=0, borderwidth=0)
        self.status_indicator.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(3, 0))
        self.status_dot = self.status_indicator.create_oval(0, 0, dot_size, dot_size, fill=self.colors["faint"], outline="")
        ttk.Label(status, text="任务状态", style="Muted.TLabel").grid(row=0, column=1, sticky="nw", padx=(0, 14))
        status_label = ttk.Label(status, textvariable=self.status_text, width=1, anchor="w", style="Status.TLabel")
        status_label.grid(row=0, column=2, sticky="ew")
        self.status_text.trace_add("write", lambda *_args: self._refresh_status_indicator())
        self._refresh_status_indicator()

    def _set_busy(self, busy, status=None):
        self.busy = busy
        if status is not None:
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
        if busy:
            # Selection must stay frozen while a task runs.
            self.execute_button.configure(state="disabled")
            for button in getattr(self, "selection_buttons", []):
                button.configure(state="disabled")
            for tree in getattr(self, "selection_trees", {}).values():
                tree.state(["disabled"])
        else:
            self._update_execute_state()

    def _show_details(self, title, summary, result):
        scale = getattr(self, "ui_scale", 1.0)
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry(f"{int(720 * scale)}x{int(480 * scale)}")
        window.minsize(int(680 * scale), int(380 * scale))
        window.transient(self.root)
        window.configure(background=self.colors["background"])
        container = ttk.Frame(window, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)
        ttk.Label(container, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        summary_label = ttk.Label(container, text=summary, style="Subtitle.TLabel", wraplength=int(640 * scale), justify="left")
        summary_label.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self._wrap_to_width(summary_label, container)
        ttk.Frame(container, style="Rule.TFrame", height=1).grid(row=2, column=0, sticky="ew", pady=(0, 10))
        details = ScrolledText(container, width=1, wrap="word", **self._text_options())
        details.grid(row=3, column=0, sticky="nsew")
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
        ttk.Button(container, text="关闭", style="Ghost.TButton", command=window.destroy).grid(row=4, column=0, sticky="e", pady=(12, 0))

