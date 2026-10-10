import asyncio
import time
from concurrent.futures import Future
from tkinter import messagebox
from telethon.utils import get_peer_id
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.contacts import DeleteContactsRequest
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.types import Channel, User
from .constants import CLEANUP_TIMEOUT, MAX_FLOOD_WAIT
from .models import CleanupSelection, CleanupStopped


class CleanupMixin:
    """Preview selection and confirmed cleanup operations."""

    def _selection_available(self):
        account = self._selected_account()
        return bool(not self.busy and self.preview_loaded and self.connected
                    and account and account["id"] == self.current_account_id)

    def _reset_selection(self):
        self.selected_items = set()
        self.selection_rows = {}
        for button in getattr(self, "selection_toggle_buttons", {}).values():
            button.configure(text="全选")
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
        for category, button in getattr(self, "selection_toggle_buttons", {}).items():
            keys = self._category_keys(category)
            button.configure(text="取消全选" if keys and keys <= self.selected_items else "全选")
        self._update_execute_state()

    def _category_keys(self, category):
        return {key for key in self.selection_rows if category == "all" or key[0] == category}

    def _toggle_category_selection(self, category):
        keys = self._category_keys(category)
        self._select_category(category, not (keys and keys <= self.selected_items))

    def _invert_category_selection(self, category):
        if not self._selection_available():
            return
        self.selected_items.symmetric_difference_update(self._category_keys(category))
        self._refresh_selection()

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

