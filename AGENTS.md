# Repository notes

- This is a Tkinter/Telethon GUI; `tg_cleanup.py` is the compatibility entrypoint. The `cleanup_app/` package separates responsibilities through Mixins composed by `CleanupApp`. Dependencies are pinned in `requirements.txt`; offline tests and previews are in `tests/`; `build_exe.bat` packages with PyInstaller. There is no linter.
- Launch the window with `start.bat`; it uses `pythonw` so no terminal remains open. For debugging, use `.\.venv\Scripts\python.exe tg_cleanup.py`. A fresh environment needs `python -m pip install -r requirements.txt`.
- API credentials and the account registry are stored as plaintext in ignored file `.tg_cleanup_config.json`; Telegram login/2FA is handled by GUI dialogs.
- Each added account has a separate ignored session under `sessions/`; the pre-GUI `tg_cleanup_session.session` is migrated into the account list as a legacy session. These are live authentication secrets: do not inspect, share, replace, or commit them. Explicit logout revokes the selected account's authorization, releases the connection, then deletes local session files and its registry entry. Remote logout failure retains the account and session for retry. Selecting another account automatically switches connections while preserving the previous account's authorization and session.
- The GUI enforces a single running Windows process because Telethon session files are SQLite databases; do not remove that guard without replacing its lock-safety role.

## Safety and verification

- Running the script connects to the real Telegram account. It previews counts, then mutates data only after two GUI confirmations; never accept those confirmations during automated verification.
- The destructive flow leaves every `Chat`/`Channel`, deletes all non-self user dialogs and both sides' histories with `just_clear=False, revoke=True`, and removes all contacts. Saved Messages are excluded. Both confirmations must explicitly mention deletion of the other person's history. Preserve these boundaries unless the requested behavior explicitly changes them.
- Closing the window during a task asks whether to cancel; cancellation stops remaining work and disconnects, but cannot undo items already processed.
- Use `.\.venv\Scripts\python.exe -m py_compile tg_cleanup.py` for a side-effect-free syntax check. Also compile `cleanup_app/` with `python -m compileall -q cleanup_app` and run offline tests with `python -m unittest discover -s tests -v`. `tests/preview_ui.py` uses synthetic data without accessing real configuration or sessions. Mock dependencies in their owning module, not the compatibility entrypoint. Meaningful end-to-end testing requires a disposable Telegram account.
