"""Offline reliability checks; never instantiate a real Telegram client or GUI."""

import asyncio
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The bundled verification interpreter can reuse the project's pure-Python deps.
sys.path.append(str(ROOT / ".venv" / "Lib" / "site-packages"))
import tg_cleanup as app_module
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.functions.auth import LogOutRequest
from telethon.tl.types import Chat, User


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, outcomes=(), disconnect_error=None, dialogs=()):
        self.outcomes = list(outcomes)
        self.requests = []
        self.session = FakeSession()
        self.connected = True
        self.disconnect_error = disconnect_error
        self.dialogs = list(dialogs)

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return User(id=1, first_name="Test", is_self=True)

    async def get_dialogs(self):
        return self.dialogs

    async def __call__(self, request):
        self.requests.append(request)
        value = self.outcomes.pop(0) if self.outcomes else SimpleNamespace(offset=0, users=[])
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return await value()
        return value

    async def delete_dialog(self, entity):
        return await self(entity)

    async def disconnect(self):
        if self.disconnect_error:
            raise self.disconnect_error
        self.connected = False

    async def log_out(self):
        if self.outcomes:
            return await self("logout")
        self.requests.append("logout")
        return True


def make_app(client=None):
    app = app_module.CleanupApp.__new__(app_module.CleanupApp)
    app.client = client
    app.clients = {client} if client else set()
    app.disconnect_tasks = set()
    app.logout_clients = {}
    app.revoked_accounts = set()
    app.groups = []
    app.private_users = []
    app.contacts = []
    app.selected_items = set()
    app.selection_rows = {}
    app.selection_trees = {}
    app.accounts = []
    app.current_account_id = "account"
    app.cleanup_task = None
    app.cleanup_running = True
    app.closing = False
    app._log = Mock()
    app._progress = Mock()
    app._refresh_account_list = Mock()
    app._save_config = Mock()
    app._delete_session_files = AsyncMock()

    async def ui(function):
        return function()

    app._request_ui = ui
    return app


def dialog(user_id, **kwargs):
    return SimpleNamespace(entity=User(id=user_id, first_name="Test", **kwargs))


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    def selected_app(self):
        app = make_app()
        app.busy = False
        app.preview_loaded = True
        app.connected = True
        app._selected_account = lambda: {"id": "account"}
        app.execute_button = Mock()
        for name in ("group_count", "dialog_count", "contact_count"):
            setattr(app, name, Mock())
        app.selection_rows = {("private", 2): dialog(2), ("private", 3): dialog(3),
                              ("contacts", 2): User(id=2, first_name="Test")}
        return app

    async def test_default_empty_and_category_selection_independent(self):
        app = self.selected_app()
        app._update_execute_state()
        app.execute_button.configure.assert_called_with(state="disabled")
        app._toggle_selection(("private", 2))
        app.execute_button.configure.assert_called_with(state="normal")
        self.assertEqual(app.selected_items, {("private", 2)})
        app._select_category("private", True)
        self.assertEqual(app.selected_items, {("private", 2), ("private", 3)})
        app._select_category("contacts", True)
        app._select_category("private", False)
        self.assertEqual(app.selected_items, {("contacts", 2)})
        app._toggle_selection(("contacts", 2))
        self.assertFalse(app.selected_items)

    async def test_all_selection_shared_and_snapshot_has_no_duplicates(self):
        app = self.selected_app()
        app._select_category("all", True)
        self.assertEqual(app.selected_items, set(app.selection_rows))
        snapshot = app._selection_snapshot()
        self.assertEqual(len(snapshot.private_users), 2)
        self.assertEqual(len(snapshot.contacts), 1)
        app._toggle_selection(app._selection_row_key("all", "private:2"))
        self.assertNotIn(("private", 2), app.selected_items)
        self.assertIn(("contacts", 2), app.selected_items)
        app._select_category("private", True)
        self.assertIn(("private", 2), app.selected_items)
        app._select_category("all", False)
        self.assertFalse(app.selected_items)

    async def test_all_snapshot_executes_each_operation_once(self):
        from telethon.tl.functions.contacts import DeleteContactsRequest
        app = self.selected_app()
        client = FakeClient()
        app.client = client
        app.clients = {client}
        app._select_category("all", True)
        app._select_category("private", True)
        snapshot = app._selection_snapshot()
        result = await app._cleanup(snapshot)
        self.assertEqual(len(result["success"]), 3)
        self.assertEqual([r.peer.id for r in client.requests if isinstance(r, DeleteHistoryRequest)], [2, 3])
        self.assertEqual(len([r for r in client.requests if isinstance(r, DeleteContactsRequest)]), 1)

    async def test_invalid_preview_busy_or_other_account_cannot_select(self):
        app = self.selected_app()
        for name in ("busy", "preview_loaded", "connected"):
            old = getattr(app, name)
            setattr(app, name, name == "busy")
            app._select_category("private", True)
            app._toggle_selection(("private", 2))
            self.assertFalse(app.selected_items)
            setattr(app, name, old)
        app._selected_account = lambda: {"id": "other"}
        app._select_category("private", True)
        self.assertFalse(app.selected_items)

    async def test_snapshot_is_fixed_and_executes_only_selected_requests(self):
        from telethon.tl.functions.contacts import DeleteContactsRequest
        app = self.selected_app()
        client = FakeClient()
        app.client = client
        app.clients = {client}
        app._toggle_selection(("private", 3))
        app._select_category("contacts", True)
        snapshot = app._selection_snapshot()
        app._select_category("private", True)
        app._select_category("contacts", False)
        app.private_users = [dialog(99)]
        result = await app._cleanup(snapshot)
        self.assertEqual(len(result["success"]), 2)
        self.assertEqual([r.peer.id for r in client.requests if isinstance(r, DeleteHistoryRequest)], [3])
        self.assertEqual([u.id for r in client.requests if isinstance(r, DeleteContactsRequest) for u in r.id], [2])
        self.assertFalse(result["unexecuted"])
        with self.assertRaises(AttributeError):
            snapshot.account_id = "other"

    async def test_populate_distinguishes_group_peer_types_and_excludes_self(self):
        from telethon.tl.types import Channel
        app = self.selected_app()
        app.groups = [(SimpleNamespace(entity=Chat(id=7, title="Same", photo=None, participants_count=0, date=None, version=0)), "群组"),
                      (SimpleNamespace(entity=Channel(id=7, title="Same", photo=None, date=None)), "频道")]
        app.private_users = [dialog(2), dialog(3), dialog(1, is_self=True)]
        app.contacts = [User(id=2, first_name="Test")]
        app.selection_trees = {category: Mock() for category in ("all", "groups", "private", "contacts")}
        for tree in app.selection_trees.values():
            tree.get_children.return_value = ()
        app._populate_selection()
        self.assertFalse(app.selected_items)
        self.assertEqual(len([key for key in app.selection_rows if key[0] == "groups"]), 2)
        self.assertNotIn(("private", 1), app.selection_rows)
        self.assertIn(("private", 2), app.selection_rows)
        self.assertIn(("private", 3), app.selection_rows)
        self.assertIn(("contacts", 2), app.selection_rows)

    async def test_confirmation_submits_original_snapshot(self):
        app = self.selected_app()
        app._account_display = lambda account: "Demo"
        app.stop_button = Mock()
        app._set_busy = Mock()
        app._toggle_selection(("private", 2))
        captured = []
        def submit(coroutine, callback, name):
            captured.append(coroutine.cr_frame.f_locals["snapshot"])
            coroutine.close()
        app._submit = submit
        def confirm(*args, **kwargs):
            app._select_category("private", True)
            return True
        with patch.object(app_module.messagebox, "askyesno", side_effect=confirm):
            app._confirm_cleanup()
        self.assertEqual([d.entity.id for d in captured[0].private_users], [2])
        self.assertFalse(app.preview_loaded)

    async def test_snapshot_account_mismatch_executes_nothing(self):
        client = FakeClient()
        app = make_app(client)
        result = await app._cleanup(app_module.CleanupSelection("other", (), (dialog(2),), ()))
        self.assertFalse(client.requests)
        self.assertEqual(len(result["unexecuted"]), 1)

    async def test_clear_preview_discards_selection_and_rows(self):
        app = self.selected_app()
        app.account_text = Mock()
        app._select_category("private", True)
        app._clear_preview()
        self.assertFalse(app.selected_items)
        self.assertFalse(app.selection_rows)
        self.assertFalse(app.preview_loaded)

    async def test_confirm_empty_or_cancel_never_submits(self):
        app = self.selected_app()
        app._account_display = lambda account: "Demo"
        app._submit = Mock()
        with patch.object(app_module.messagebox, "showwarning") as warning, patch.object(app_module.messagebox, "askyesno") as ask:
            app._confirm_cleanup()
            warning.assert_called_once()
            ask.assert_not_called()
        app._toggle_selection(("private", 2))
        for responses in ([False], [True, False]):
            with patch.object(app_module.messagebox, "askyesno", side_effect=responses) as ask:
                app._confirm_cleanup()
                for call in ask.call_args_list:
                    self.assertIn("对方聊天记录", call.args[1])
                    self.assertIn("删除 1 个私聊", call.args[1])
        app._submit.assert_not_called()


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_continues_until_zero_and_preserves_boundaries(self):
        client = FakeClient([SimpleNamespace(offset=2), SimpleNamespace(offset=1), SimpleNamespace(offset=0)])
        app = make_app(client)
        app.private_users = [dialog(1, is_self=True), dialog(2, deleted=True)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(client.requests), 3)
        for request in client.requests:
            self.assertIsInstance(request, DeleteHistoryRequest)
            self.assertEqual(request.peer.id, 2)
            self.assertFalse(request.just_clear)
            self.assertTrue(request.revoke)
            self.assertEqual(request.max_id, 0)
        self.assertEqual(len(result["success"]), 1)
        self.assertFalse(result["unknown"])
        self.assertTrue(client.session.closed)

    async def test_timeout_unknown_stops_remaining_without_retry(self):
        client = FakeClient([asyncio.TimeoutError()])
        app = make_app(client)
        app.private_users = [dialog(2), dialog(3)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(result["unknown"]), 1)
        self.assertEqual(len(result["unexecuted"]), 1)
        self.assertFalse(result["success"])

    async def test_real_wait_for_timeout_cancels_request(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hang():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        app = make_app(FakeClient([hang]))
        app.private_users = [dialog(2)]
        with patch.object(app_module, "CLEANUP_TIMEOUT", 0.01):
            result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertTrue(started.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(result["unknown"]), 1)

    async def test_long_flood_stops_without_sleeping(self):
        client = FakeClient([FloodWaitError(request=None, capture=301)])
        app = make_app(client)
        app.private_users = [dialog(2), dialog(3)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(result["unexecuted"]), 2)
        self.assertEqual(len(client.requests), 1)

    async def test_flood_wait_countdown_and_single_retry(self):
        client = FakeClient([FloodWaitError(request=None, capture=2), SimpleNamespace(offset=0)])
        app = make_app(client)
        app.private_users = [dialog(2)]
        clock = SimpleNamespace(monotonic=Mock(side_effect=[10, 10, 10, 10, 13]))
        with patch.object(app_module, "time", clock), patch.object(app_module.asyncio, "sleep", new=AsyncMock()) as sleep:
            result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        sleep.assert_awaited_once()
        self.assertEqual(len(result["success"]), 1)
        self.assertTrue(any("限流等待" in call.args[0] for call in app._progress.call_args_list))

    async def test_repeat_flood_across_history_chunks_stops(self):
        client = FakeClient([FloodWaitError(request=None, capture=0), SimpleNamespace(offset=1), FloodWaitError(request=None, capture=0)])
        app = make_app(client)
        app.private_users = [dialog(2), dialog(3)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(len(result["unexecuted"]), 1)
        self.assertTrue(result["stop_reason"])

    async def test_cancel_preserves_finished_and_marks_inflight_unknown(self):
        started = asyncio.Event()

        async def hang():
            started.set()
            await asyncio.Event().wait()

        client = FakeClient([SimpleNamespace(offset=0), hang])
        app = make_app(client)
        app.private_users = [dialog(2), dialog(3), dialog(4)]
        task = asyncio.create_task(app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts))))
        await started.wait()
        app._cancel_cleanup()
        result = await task
        self.assertEqual(len(result["success"]), 1)
        self.assertEqual(len(result["unknown"]), 1)
        self.assertEqual(len(result["unexecuted"]), 1)
        self.assertTrue(client.session.closed)
        self.assertIsNone(app.cleanup_task)

    async def test_disconnect_failure_does_not_override_success(self):
        client = FakeClient(disconnect_error=RuntimeError("disconnect failed"))
        app = make_app(client)
        app.private_users = [dialog(2)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(result["success"]), 1)
        self.assertIn("disconnect failed", result["disconnect_error"])
        self.assertTrue(client.session.closed)
        self.assertIn(client, app.clients)

    async def test_rpc_failure_continues_to_next_item(self):
        app = make_app(FakeClient([RuntimeError("denied"), SimpleNamespace(offset=0)]))
        app.private_users = [dialog(2), dialog(3)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(len(result["success"]), 1)

    async def test_bilateral_deletion_failure_never_falls_back_to_local_clear(self):
        client = FakeClient([RuntimeError("bilateral deletion denied")])
        app = make_app(client)
        app.private_users = [dialog(2)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertFalse(request.just_clear)
        self.assertTrue(request.revoke)
        self.assertEqual(len(result["failed"]), 1)
        self.assertFalse(result["success"])
        self.assertIn("删除私聊及双方记录", result["failed"][0][0])

    async def test_disconnected_client_all_unexecuted(self):
        client = FakeClient()
        client.connected = False
        app = make_app(client)
        app.private_users = [dialog(2)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(result["unexecuted"]), 1)
        self.assertFalse(client.requests)
        self.assertTrue(client.session.closed)

    async def test_groups_and_contacts_use_the_confirmed_snapshot(self):
        client = FakeClient()
        app = make_app(client)
        app.groups = [(SimpleNamespace(entity=Chat(id=10, title="Group", photo=None, participants_count=1, date=None, version=1)), "群组")]
        app.contacts = [User(id=20)]
        result = await app._cleanup(app_module.CleanupSelection(app.current_account_id, tuple(app.groups), tuple(app.private_users), tuple(app.contacts)))
        self.assertEqual(len(result["success"]), 2)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[1].id[0].id, 20)

    async def test_prepare_registers_before_preview_and_includes_deleted(self):
        client = FakeClient(dialogs=[dialog(1, is_self=True), dialog(2, deleted=True)])
        app = make_app()
        account = {"id": "new", "session": "sessions/new", "label": "New"}
        with patch.object(app_module, "TelegramClient", return_value=client) as constructor:
            result = await app._prepare(1, "hash", Path("unused"), allow_authentication=True, new_account=account)
        self.assertIn(account, app.accounts)
        app._save_config.assert_called_once_with("new")
        self.assertEqual(result["dialogs"], 1)
        self.assertEqual(constructor.call_args.kwargs["flood_sleep_threshold"], 0)
        await app._release_client(client)

    async def test_cancel_during_registration_still_persists_account(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        app = make_app()
        client = FakeClient()
        account = {"id": "new", "session": "sessions/new", "label": "New"}

        async def ui(function):
            entered.set()
            await release.wait()
            return function()

        app._request_ui = ui
        with patch.object(app_module, "TelegramClient", return_value=client):
            task = asyncio.create_task(app._prepare(1, "hash", Path("unused"), allow_authentication=True, new_account=account))
            await entered.wait()
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn(account, app.accounts)
        app._save_config.assert_called_once()
        app._delete_session_files.assert_not_awaited()
        self.assertTrue(client.session.closed)

    async def test_registration_save_failure_preserves_session(self):
        app = make_app()
        app._save_config.side_effect = OSError("read only")
        client = FakeClient()
        account = {"id": "new", "session": "sessions/new", "label": "New"}
        with patch.object(app_module, "TelegramClient", return_value=client), patch.object(app_module.messagebox, "showwarning") as warning:
            await app._prepare(1, "hash", Path("unused"), allow_authentication=True, new_account=account)
        self.assertIn(account, app.accounts)
        warning.assert_called_once()
        app._delete_session_files.assert_not_awaited()
        await app._release_client(client)

    async def test_logout_revokes_releases_then_deletes(self):
        client = FakeClient()
        app = make_app(client)
        account = {"id": "account", "label": "Test"}
        app.accounts = [account]
        async def delete(path):
            self.assertTrue(client.session.closed)
            self.assertNotIn(client, app.clients)
        app._delete_session_files.side_effect = delete
        result = await app._logout_account(account, Path("unused"), None)
        self.assertFalse(client.connected)
        self.assertTrue(client.session.closed)
        self.assertEqual(app.accounts, [account])
        self.assertEqual(len(client.requests), 1)
        self.assertIsInstance(client.requests[0], LogOutRequest)
        self.assertTrue(result["deleted"])
        app._delete_session_files.assert_awaited_once()
        self.assertIsNone(app.client)

    async def test_logout_disconnect_failure_preserves_session_files_and_retries(self):
        client = FakeClient(disconnect_error=RuntimeError("disconnect failed"))
        app = make_app(client)
        account = {"id": "account", "label": "Test"}
        result = await app._logout_account(account, Path("unused"), None)
        self.assertIn("disconnect failed", result["disconnect_error"])
        app._delete_session_files.assert_not_awaited()
        self.assertIn(client, app.clients)
        client.disconnect_error = None
        result = await app._logout_account(account, Path("unused"), None)
        self.assertTrue(result["deleted"])
        self.assertEqual(len(client.requests), 1)
        self.assertIsInstance(client.requests[0], LogOutRequest)

    async def test_logout_unconnected_account_does_not_disconnect_current_client(self):
        client = FakeClient()
        app = make_app(client)
        other = FakeClient()
        app._read_session_status = Mock(return_value="valid")
        with patch.object(app_module, "TelegramClient", return_value=other):
            result = await app._logout_account({"id": "other"}, Path("unused"), (1, "hash"))
        self.assertTrue(result["deleted"])
        self.assertTrue(client.connected)
        self.assertIs(app.client, client)
        self.assertFalse(client.session.closed)
        self.assertEqual(len(other.requests), 1)
        self.assertIsInstance(other.requests[0], LogOutRequest)

    async def test_logout_remote_failure_retains_registry_and_files(self):
        for outcome in (RuntimeError("offline"), False, asyncio.TimeoutError()):
            with self.subTest(outcome=outcome):
                client = FakeClient([outcome])
                app = make_app(client)
                account = {"id": "account"}
                app.accounts = [account]
                result = await app._logout_account(account, Path("unused"), None)
                self.assertIsNotNone(result["remote_error"])
                self.assertFalse(result["deleted"])
                self.assertEqual(app.accounts, [account])
                app._delete_session_files.assert_not_awaited()
                self.assertTrue(client.session.closed)

    async def test_logout_local_failure_retries_without_remote_request(self):
        client = FakeClient()
        app = make_app(client)
        account = {"id": "account"}
        app._delete_session_files.side_effect = OSError("locked")
        result = await app._logout_account(account, Path("unused"), None)
        self.assertEqual(result["delete_error"], "locked")
        self.assertIsNone(result["remote_error"])
        app._delete_session_files.side_effect = None
        result = await app._logout_account(account, Path("unused"), None)
        self.assertTrue(result["deleted"])
        self.assertEqual(len(client.requests), 1)
        self.assertIsInstance(client.requests[0], LogOutRequest)

    async def test_logout_missing_empty_and_unreadable_sessions(self):
        for status in ("missing", "empty", "corrupt", "unreadable", "busy"):
            app = make_app()
            app._read_session_status = Mock(return_value=status)
            with patch.object(app_module, "TelegramClient") as factory:
                result = await app._logout_account({"id": "other"}, Path("unused"), None)
            factory.assert_not_called()
            self.assertEqual(result["deleted"], status in ("missing", "empty"))

    async def test_logout_expired_authorization_does_not_reauthenticate(self):
        client = FakeClient()
        client.is_user_authorized = AsyncMock(return_value=False)
        app = make_app(client)
        result = await app._logout_account({"id": "account"}, Path("unused"), None)
        self.assertTrue(result["deleted"])
        self.assertEqual(client.requests, [])

    async def test_logout_cancellation_releases_session_without_local_deletion(self):
        started = asyncio.Event()
        async def hang():
            started.set()
            await asyncio.Event().wait()
        client = FakeClient([hang])
        app = make_app(client)
        task = asyncio.create_task(app._logout_account({"id": "account"}, Path("unused"), None))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client.session.closed)
        app._delete_session_files.assert_not_awaited()

    async def test_switch_releases_previous_without_revoking_or_deleting(self):
        previous = FakeClient()
        replacement = FakeClient()
        app = make_app(previous)
        with patch.object(app_module, "TelegramClient", return_value=replacement):
            await app._prepare(1, "hash", Path("unused"), allow_authentication=False)
        self.assertTrue(previous.session.closed)
        self.assertEqual(previous.requests, [])
        self.assertIs(app.client, replacement)
        app._delete_session_files.assert_not_awaited()
        await app._release_client(replacement)

    async def test_remote_failure_and_release_failure_are_both_reported(self):
        client = FakeClient([RuntimeError("remote failed")], disconnect_error=RuntimeError("release failed"))
        app = make_app(client)
        result = await app._logout_account({"id": "account"}, Path("unused"), None)
        self.assertEqual(result["remote_error"], "remote failed")
        self.assertIn("release failed", result["disconnect_error"])
        app._delete_session_files.assert_not_awaited()
        client.disconnect_error = None
        replacement = FakeClient()
        app._read_session_status = Mock(return_value="valid")
        with patch.object(app_module, "TelegramClient", return_value=replacement):
            result = await app._logout_account({"id": "account"}, Path("unused"), (1, "hash"))
        self.assertTrue(result["deleted"])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(replacement.requests), 1)


class SessionStatusTests(unittest.TestCase):
    def test_session_errors_are_not_misclassified_as_missing(self):
        app = app_module.CleanupApp.__new__(app_module.CleanupApp)
        session_path = Mock()
        session_path.exists.return_value = True
        session_path.resolve.return_value.as_uri.return_value = "file:///unused.session"
        app._session_file = Mock(return_value=session_path)
        for error, expected in [(sqlite3.OperationalError("unable to open database file"), "unreadable"),
                                (sqlite3.OperationalError("database is locked"), "busy"),
                                (sqlite3.DatabaseError("file is not a database"), "corrupt")]:
            with self.subTest(expected=expected), patch.object(app_module.sqlite3, "connect", side_effect=error):
                self.assertEqual(app._read_session_status({}), expected)

    def test_unreadable_and_corrupt_registry_entries_survive_loading(self):
        app = app_module.CleanupApp.__new__(app_module.CleanupApp)
        app.accounts = []
        app.session_issues = {}
        for name in ("api_id", "api_hash"):
            setattr(app, name, Mock())
        app._log = Mock()
        app._refresh_account_list = Mock()
        app._save_config = Mock()
        accounts = [{"id": "bad", "session": "sessions/bad", "label": "Bad"},
                    {"id": "busy", "session": "sessions/busy", "label": "Busy"}]
        config = Mock()
        config.exists.return_value = True
        app._session_status = Mock(side_effect=["corrupt", "unreadable"])
        with patch.object(app_module, "CONFIG_PATH", config), patch.object(app_module.json, "loads", return_value={"accounts": accounts, "keep_account_on_logout": False}), patch.object(app_module, "LEGACY_SESSION_PATH") as legacy:
            legacy.with_suffix.return_value.exists.return_value = False
            app._load_config()
        self.assertEqual(app.accounts, accounts)
        self.assertEqual(app.session_issues, {"bad": "corrupt", "busy": "unreadable"})
        app._save_config.assert_not_called()


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeWidget:
    def __init__(self):
        self.options = {}
        self.index = -1

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def __getitem__(self, key):
        return self.options[key]

    def current(self, index=None):
        if index is not None:
            self.index = index
        return self.index


class FakeRoot:
    def __init__(self):
        self.callbacks = []
        self.destroyed = False

    def after(self, delay, callback):
        self.callbacks.append((time.monotonic() + delay / 1000, callback))

    def update(self):
        ready = [item for item in self.callbacks if item[0] <= time.monotonic()]
        self.callbacks = [item for item in self.callbacks if item not in ready]
        for _, callback in ready:
            callback()

    def destroy(self):
        self.destroyed = True

    def winfo_exists(self):
        return not self.destroyed


class InterfaceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.app = make_app()
        self.app.root = self.root
        self.app.ui_requests = app_module.queue.Queue()
        self.app.busy = False
        self.app.connected = False
        self.app.preview_loaded = False
        self.app.session_issues = {}
        self.app.colors = {"background": "#F3F6FA", "surface": "#FFFFFF", "text": "#172B4D", "border": "#DFE7F0", "blue": "#229ED9", "red": "#D64545"}
        self.app.fonts = {"section": ("Arial", 12), "log": ("Arial", 10)}
        for name in ("status_text", "account_text", "group_count", "dialog_count", "contact_count", "selected_account"):
            setattr(self.app, name, FakeVariable())
        for name in ("login_button", "add_button", "logout_button", "api_id_entry", "api_hash_entry", "account_combo", "execute_button", "stop_button"):
            setattr(self.app, name, FakeWidget())
        self.app.loop = asyncio.new_event_loop()
        self.app.loop_ready = threading.Event()
        self.app.loop_closed = threading.Event()
        self.app.async_tasks = set()
        self.app.loop_thread = threading.Thread(target=self.app._run_event_loop, daemon=True)
        self.app.loop_thread.start()
        self.app.loop_ready.wait()

    def pump_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("GUI/background shutdown did not finish")
            self.root.update()
            time.sleep(0.005)

    def tearDown(self):
        if not self.app.loop_closed.is_set():
            self.app.busy = False
            self.app._on_close()
            self.pump_until(self.app.loop_closed.is_set)
        self.pump_until(lambda: self.root.destroyed)

    def test_unavailable_session_disables_connect_buttons(self):
        self.app.accounts = [{"id": "bad", "label": "Bad", "session": "sessions/bad"}]
        self.app.session_issues = {"bad": "unreadable"}
        self.app.account_combo.current(0)
        self.app._account_selection_changed()
        self.assertEqual(str(self.app.login_button["state"]), "disabled")
        self.assertIn("账号已保留", self.app.status_text.get())

    def test_dropdown_selection_invalidates_preview_and_connects(self):
        account = {"id": "other", "label": "Other"}
        self.app.accounts = [account]
        self.app.account_combo.current(0)
        self.app.connected = True
        self.app.preview_loaded = True
        with patch.object(self.app, "_connect_account") as connect:
            self.app._account_selection_changed()
        connect.assert_called_once_with(account, "切换账号")
        self.assertFalse(self.app.preview_loaded)
        self.assertEqual(self.app.execute_button["state"], "disabled")

    def test_busy_selection_does_not_switch(self):
        self.app.busy = True
        with patch.object(self.app, "_connect_account") as connect:
            self.app._account_selection_changed()
        connect.assert_not_called()
        self.app.busy = False

    def test_logout_removes_registry_and_selects_remaining_without_connection(self):
        account = {"id": "account", "label": "Current"}
        other = {"id": "other", "label": "Other"}
        self.app.accounts = [account, other]
        self.app.connected = True
        result = dict(deleted=True, remote_error=None, disconnect_error=None, delete_error=None)
        # Use the actual refresh method, not the default test stub.
        self.app._refresh_account_list = app_module.CleanupApp._refresh_account_list.__get__(self.app)
        with patch.object(self.app, "_connect_account") as connect:
            self.app._account_logged_out(account, result)
        self.assertEqual(self.app.accounts, [other])
        self.assertEqual(self.app._selected_account(), other)
        self.assertFalse(self.app.connected)
        connect.assert_not_called()
        self.app._save_config.assert_called_once()

    def test_last_logout_shows_empty_list(self):
        account = {"id": "account", "label": "Current"}
        self.app.accounts = [account]
        self.app._refresh_account_list = app_module.CleanupApp._refresh_account_list.__get__(self.app)
        self.app._account_logged_out(account, dict(deleted=True))
        self.assertEqual(self.app.accounts, [])
        self.assertIsNone(self.app._selected_account())
        self.assertIn("账号列表为空", self.app.status_text.get())

    def test_logout_failure_retains_registry_and_reports_all_errors(self):
        account = {"id": "account", "label": "Current"}
        self.app.accounts = [account]
        result = dict(deleted=False, remote_error="remote failed", disconnect_error="release failed", delete_error=None)
        with patch.object(app_module.messagebox, "showwarning") as warning:
            self.app._account_logged_out(account, result)
        self.assertEqual(self.app.accounts, [account])
        self.assertIn("remote failed", warning.call_args.args[1])
        self.assertIn("release failed", warning.call_args.args[1])
        self.app._save_config.assert_not_called()

    def test_cleanup_result_restores_controls_and_opens_scrollable_details(self):
        self.app.connected = True
        self.app._set_busy(True, "Running")
        result = {"success": ["Done"], "failed": [], "unknown": [], "unexecuted": [], "stop_reason": None, "disconnect_error": None}
        with patch.object(app_module.tk, "Toplevel"), patch.object(app_module.ttk, "Label"), patch.object(app_module.ttk, "Button"), patch.object(app_module, "ScrolledText") as details:
            self.app._cleanup_finished(result)
            self.assertTrue(any("Done" in call.args[1] for call in details.return_value.insert.call_args_list))
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        self.assertEqual(str(self.app.api_id_entry["state"]), "normal")
        self.assertFalse(self.app.preview_loaded)

    def test_close_waits_for_session_release_and_background_thread(self):
        release = threading.Event()
        started = threading.Event()
        client = FakeClient()

        async def disconnect():
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            client.connected = False

        client.disconnect = disconnect

        async def install():
            self.app.client = client
            self.app.clients.add(client)

        asyncio.run_coroutine_threadsafe(install(), self.app.loop).result()
        self.app._on_close()
        self.assertTrue(started.wait(1))
        self.root.update()
        self.assertFalse(self.app.loop_closed.is_set())
        self.assertTrue(self.root.winfo_exists())
        release.set()
        self.pump_until(self.app.loop_closed.is_set)
        self.assertTrue(client.session.closed)
        self.app.loop_thread.join(1)
        self.assertFalse(self.app.loop_thread.is_alive())
        self.pump_until(lambda: self.root.destroyed)

    def test_close_failure_retains_window_and_allows_retry(self):
        client = FakeClient(disconnect_error=RuntimeError("disconnect failed"))

        async def install():
            self.app.client = client
            self.app.clients.add(client)

        asyncio.run_coroutine_threadsafe(install(), self.app.loop).result()
        with patch.object(app_module.messagebox, "showwarning") as warning:
            self.app._on_close()
            self.pump_until(lambda: not self.app.closing)
        warning.assert_called_once()
        self.assertFalse(self.root.destroyed)
        self.assertFalse(self.app.loop_closed.is_set())
        client.disconnect_error = None

    def test_main_releases_instance_guard_after_unexpected_mainloop_exit(self):
        self.root.mainloop = Mock()
        instance = Mock(acquired=True)

        def release_guard():
            self.assertTrue(self.app.loop_closed.is_set())
            self.assertFalse(self.app.loop_thread.is_alive())

        instance.close.side_effect = release_guard
        with patch.object(app_module, "SingleInstance", return_value=instance), patch.object(app_module.tk, "Tk", return_value=self.root), patch.object(app_module, "CleanupApp", return_value=self.app):
            app_module.main()
        instance.close.assert_called_once()
        self.root.destroy()


if __name__ == "__main__":
    unittest.main()
