"""Runtime paths remain beside the script or frozen executable."""

import sys
from pathlib import Path


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / ".tg_cleanup_config.json"
LEGACY_SESSION_PATH = BASE_DIR / "tg_cleanup_session"
SESSIONS_DIR = BASE_DIR / "sessions"
INSTANCE_MUTEX_NAME = "Local\\TelegramCleanupGUI_8D634395"
CLEANUP_TIMEOUT = 60
MAX_FLOOD_WAIT = 300
QR_LOGIN_TIMEOUT = 300

