"""Runtime paths remain beside the script or frozen executable."""

import sys
from pathlib import Path


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / ".tg_cleanup_config.sqlite3"
LEGACY_CONFIG_PATH = BASE_DIR / ".tg_cleanup_config.json"
LEGACY_SESSION_PATH = BASE_DIR / "tg_cleanup_session"
SESSIONS_DIR = BASE_DIR / "sessions"
INSTANCE_MUTEX_NAME = "Local\\TelegramCleanupGUI_8D634395"
CLEANUP_TIMEOUT = 60
MAX_FLOOD_WAIT = 300
QR_LOGIN_TIMEOUT = 300
# Single request budget for connection, authorization and logout calls.
RPC_TIMEOUT = 30
# Reading a large dialog list needs a wider budget than one RPC.
DIALOG_LOAD_TIMEOUT = 90
# Waiting for the event loop to finish session release before process exit.
SHUTDOWN_TIMEOUT = 30
# UI thread polling cadence for background results and queued callbacks.
UI_POLL_INTERVAL_MS = 100
# Local session file deletion is retried while Telethon releases SQLite.
RELEASE_ATTEMPTS = 5
RELEASE_RETRY_DELAY = 0.2

