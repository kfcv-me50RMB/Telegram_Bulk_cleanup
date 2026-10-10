import asyncio
import datetime
import json
import time
import uuid
from tkinter import messagebox
from telethon import TelegramClient, events
from telethon.errors import PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.tl.functions.auth import LogOutRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import Channel, Chat, User, UpdateLoginToken
from .constants import SESSIONS_DIR, QR_LOGIN_TIMEOUT
from .models import StoredSessionInvalidError, QRLoginCancelled
from .dialogs import LoginDialog, LoginMethodDialog, QRLoginDialog


class AccountsMixin:
    """Account selection, authentication and connections."""

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
        if self.busy or self.closing:
            return
        credentials = self._credentials()
        if not credentials:
            return
        method = self._choose_login_method()
        if method is None or self.closing:
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
                authentication_method=method,
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

    def _register_authorized_account(self, account, authorized=True):
        account["label"] = "已授权账号（连接后识别）" if authorized else "扫码账号（授权状态待核对）"
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
        kind = "password" if secret else "code" if title == "登录验证码" else "phone"
        dialog = LoginDialog(self, kind)
        self.login_dialog = dialog
        try:
            self.root.wait_window(dialog.window)
            if dialog.result is None or self.closing:
                raise RuntimeError(f"已取消{title}。")
            return dialog.result
        finally:
            dialog.result = None
            self.login_dialog = None

    def _choose_login_method(self):
        dialog = LoginMethodDialog(self)
        self.login_dialog = dialog
        try:
            self.root.wait_window(dialog.window)
            return dialog.result
        finally:
            self.login_dialog = None

    def _open_qr_dialog(self, cancelled):
        if self.closing:
            raise asyncio.CancelledError()
        dialog = QRLoginDialog(self, lambda: self.loop.call_soon_threadsafe(cancelled.set))
        self.login_dialog = dialog
        return dialog

    def _finish_qr_dialog(self, dialog):
        dialog.finish()
        if getattr(self, "login_dialog", None) is dialog:
            self.login_dialog = None

    async def _authenticate_qr(self, client):
        cancelled = asyncio.Event()
        deadline = time.monotonic() + QR_LOGIN_TIMEOUT
        dialog = await self._request_ui(lambda: self._open_qr_dialog(cancelled))
        wait_task = None
        scan_task = None
        scanned = asyncio.Event()
        async def scan_received(_update):
            scanned.set()
        client.add_event_handler(scan_received, events.Raw(UpdateLoginToken))
        cancel_task = asyncio.create_task(cancelled.wait())
        async def request(awaitable, label):
            task = asyncio.create_task(self._timed(awaitable, 30, label))
            try:
                done, _ = await asyncio.wait((task, cancel_task), timeout=max(0, deadline - time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    return await task
                if cancel_task in done:
                    raise QRLoginCancelled("已取消扫码登录。")
                raise RuntimeError("扫码登录等待超过 5 分钟，请重新添加账号。")
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        try:
            qr = await request(client.qr_login(), "获取登录二维码")
            while True:
                if cancelled.is_set():
                    raise QRLoginCancelled("已取消扫码登录。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("扫码登录等待超过 5 分钟，请重新添加账号。")
                lifetime = max(0, (qr.expires - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                lifetime = min(lifetime, remaining)
                # Start Telethon's event listener before exposing the token.
                scanned.clear()
                if scan_task and not scan_task.done():
                    scan_task.cancel()
                    await asyncio.gather(scan_task, return_exceptions=True)
                scan_task = asyncio.create_task(scanned.wait())
                wait_task = asyncio.create_task(qr.wait(timeout=lifetime))
                await asyncio.sleep(0)
                await self._request_ui(lambda: dialog.show_code(qr.url, lifetime))
                done, _ = await asyncio.wait((wait_task, cancel_task, scan_task), timeout=min(remaining, lifetime + 30), return_when=asyncio.FIRST_COMPLETED)
                if scan_task in done and wait_task not in done and cancel_task not in done:
                    # Once scanned, bound Telethon's token exchange/DC migration.
                    done, _ = await asyncio.wait((wait_task, cancel_task), timeout=min(30, max(0, deadline - time.monotonic())), return_when=asyncio.FIRST_COMPLETED)
                if wait_task in done:
                    try:
                        await wait_task
                        return
                    except asyncio.TimeoutError:
                        await self._request_ui(dialog.loading)
                        if cancelled.is_set():
                            raise QRLoginCancelled("已取消扫码登录。")
                        if time.monotonic() >= deadline:
                            raise RuntimeError("扫码登录等待超过 5 分钟，请重新添加账号。")
                        await request(qr.recreate(), "刷新登录二维码")
                        continue
                if cancel_task in done:
                    raise QRLoginCancelled("已取消扫码登录。")
                if time.monotonic() >= deadline:
                    raise RuntimeError("扫码登录等待超过 5 分钟，请重新添加账号。")
                raise RuntimeError("扫码登录请求超时，请检查网络后重试。")
        except (SessionPasswordNeededError, asyncio.CancelledError, RuntimeError):
            raise
        except Exception:
            # Library exceptions may embed login-token responses; never expose them.
            raise RuntimeError("扫码登录失败，请检查网络和 API 凭据后重新添加账号。") from None
        finally:
            client.remove_event_handler(scan_received)
            for task in (wait_task, cancel_task, scan_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (wait_task, cancel_task, scan_task) if t), return_exceptions=True)
            await self._request_ui(lambda: self._finish_qr_dialog(dialog))

    async def _authenticate(self, client, method="phone"):
        if await self._timed(client.is_user_authorized(), 30, "验证账号会话"):
            return
        if method == "qr":
            try:
                await self._authenticate_qr(client)
                return
            except SessionPasswordNeededError:
                await self._authenticate_password(client)
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
        await self._authenticate_password(client)

    async def _authenticate_password(self, client):
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

    async def _prepare(
        self,
        api_id,
        api_hash,
        session_path,
        delete_on_auth_failure=False,
        allow_authentication=False,
        new_account=None,
        authentication_method="phone",
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
                if authentication_method == "phone":
                    await self._authenticate(client)
                else:
                    await self._authenticate(client, authentication_method)
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
            if authentication_method == "qr" and client and not auth_completed and new_account is not None:
                uncertain = False
                try:
                    auth_completed = bool(await asyncio.shield(self._timed(client.get_me(), 30, "核对扫码授权状态")))
                except Exception:
                    # Keep a retryable registry entry when authorization is unknown.
                    uncertain = True
                    auth_completed = True
                if auth_completed:
                    callback = (lambda: self._register_authorized_account(new_account, authorized=False)) if uncertain else (lambda: self._register_authorized_account(new_account))
                    registration = asyncio.create_task(self._request_ui(callback))
                    try:
                        await asyncio.shield(registration)
                    except asyncio.CancelledError:
                        await registration
                    self._log("扫码账号的会话已保留登记，请稍后重新连接核对状态。")
            release_error = await self._release_client(client) if client else None
            if delete_on_auth_failure and not auth_completed:
                if not release_error:
                    await self._delete_session_files(session_path)
            if authentication_method == "qr" and release_error:
                raise RuntimeError("扫码登录已停止，但连接未安全释放，会话文件已保留。请重试关闭程序后再添加账号。") from None
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

