import asyncio
import queue
from concurrent.futures import Future
from tkinter import messagebox
from .models import QRLoginCancelled


class BackgroundMixin:
    """Async worker, UI dispatch and orderly shutdown."""

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
            except QRLoginCancelled:
                self.connected = False
                self._clear_preview()
                self.status_text.set("扫码登录已取消；已授权或状态待核对的账号会保留，请重新连接核对。")
                self._log("扫码登录已取消，连接已释放。")
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

    @staticmethod
    async def _timed(awaitable, seconds, label):
        try:
            return await asyncio.wait_for(awaitable, timeout=seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{label}超时，请检查网络后重试。") from exc

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
        dialog = getattr(self, "login_dialog", None)
        if dialog and not dialog.closed:
            dialog.cancel()
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

