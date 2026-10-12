import asyncio
import json
import sqlite3
from pathlib import Path
from tkinter import messagebox
from .constants import (BASE_DIR, CONFIG_PATH, LEGACY_CONFIG_PATH, LEGACY_SESSION_PATH,
                        RELEASE_ATTEMPTS, RELEASE_RETRY_DELAY, SESSIONS_DIR)


def _optional_text(value):
    """SQLite stores NULL for missing text; the UI wants an empty string."""
    return "" if value is None else str(value)


def read_config(path):
    """Read existing metadata only; never create a database during reading."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        settings = dict(connection.execute("SELECT key, value FROM settings"))
        accounts = [dict(zip(("id", "session", "label", "user_id"), row)) for row in
                    connection.execute("SELECT id, session, label, user_id FROM accounts ORDER BY position")]
        return {**settings, "accounts": accounts}
    finally:
        connection.close()


def write_config(path, config):
    """Replace metadata atomically with a short-lived, thread-local connection."""
    # Credentials are never optional, so a missing value is stored as an empty
    # string instead of NULL (which would read back as the text "None").
    settings = (
        ("api_id", _optional_text(config.get("api_id"))),
        ("api_hash", _optional_text(config.get("api_hash"))),
        ("selected_account", config.get("selected_account")),
    )
    new_database = not path.exists()
    target = path.with_name(path.name + ".tmp") if new_database else path
    connection = sqlite3.connect(target, timeout=5)
    succeeded = False
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, session TEXT NOT NULL UNIQUE, label TEXT NOT NULL, user_id TEXT NOT NULL, position INTEGER NOT NULL)")
            connection.execute("DELETE FROM settings")
            connection.executemany("INSERT INTO settings VALUES (?, ?)", settings)
            connection.execute("DELETE FROM accounts")
            connection.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                                   [(item["id"], item["session"], item["label"], _optional_text(item.get("user_id")), index)
                                    for index, item in enumerate(config["accounts"])])
        succeeded = True
    finally:
        connection.close()
        if new_database and not succeeded:
            target.unlink(missing_ok=True)
    if new_database:
        target.replace(path)


class StorageMixin:
    """Configuration and local session lifecycle."""

    def _load_config(self):
        config = {}
        migrating = False
        self._config_read_failed = False
        try:
            if CONFIG_PATH.exists():
                config = read_config(CONFIG_PATH)
            elif LEGACY_CONFIG_PATH.exists():
                config = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(config, dict):
                    raise ValueError("旧配置格式无效")
                migrating = True
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self._config_read_failed = True
            messagebox.showwarning("配置读取失败", f"无法读取本地配置，原文件已保留；修复后请重新启动。\n{exc}")
        if not isinstance(config, dict):
            config = {}
        self.api_id.set(_optional_text(config.get("api_id")))
        self.api_hash.set(_optional_text(config.get("api_hash")))
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
        if registry_changed or migrating:
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
        if getattr(self, "_config_read_failed", False):
            raise OSError("配置读取失败，已阻止覆盖原数据；请修复后重新启动。")
        try:
            write_config(CONFIG_PATH, config)
        except sqlite3.Error as exc:
            raise OSError(f"无法写入本地配置数据库：{exc}") from exc

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
        for _attempt in range(RELEASE_ATTEMPTS):
            last_errors = []
            for candidate in self._session_files(session_path):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:
                    last_errors.append(f"{candidate.name}: {exc}")
            remaining = [candidate.name for candidate in self._session_files(session_path) if candidate.exists()]
            if not remaining:
                return
            await asyncio.sleep(RELEASE_RETRY_DELAY)
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

