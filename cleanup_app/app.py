import asyncio
import queue
import threading
import tkinter as tk


from .ui import UiMixin
from .storage import StorageMixin
from .accounts import AccountsMixin
from .cleanup import CleanupMixin
from .background import BackgroundMixin


class CleanupApp(UiMixin, StorageMixin, AccountsMixin, CleanupMixin, BackgroundMixin):
    """Compose application behavior while owning all shared state."""

    def __init__(self, root):
        self.root = root
        self.root.title("Telegram 批量清理")
        # Window sizes are in real pixels; scale them so a DPI-aware window
        # occupies the same apparent area it did before, and never exceed the
        # screen on small displays.
        scale = self._ui_scale(root)
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(max(int(860 * scale), min(int(1000 * scale), screen_width - int(80 * scale))), screen_width - 20)
        height = min(max(int(640 * scale), min(int(760 * scale), screen_height - int(100 * scale))), screen_height - 20)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(int(860 * scale), width), min(int(640 * scale), height))

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
        self.pending_release = {}
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

