"""Module boundary and path compatibility checks using temporary data only."""

import ast
import importlib
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".venv" / "Lib" / "site-packages"))

import tg_cleanup
from cleanup_app import constants, storage
from cleanup_app.app import CleanupApp


class ModuleTests(unittest.TestCase):
    def test_source_paths_remain_at_project_root(self):
        self.assertEqual(constants.BASE_DIR, ROOT)
        self.assertEqual(constants.CONFIG_PATH, ROOT / ".tg_cleanup_config.json")
        self.assertEqual(constants.SESSIONS_DIR, ROOT / "sessions")
        self.assertEqual(constants.LEGACY_SESSION_PATH, ROOT / "tg_cleanup_session")

    def test_frozen_paths_follow_executable_not_unpack_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "TelegramCleanup.exe"
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
                values = runpy.run_path(str(ROOT / "cleanup_app" / "constants.py"))
            self.assertEqual(values["BASE_DIR"], executable.parent)
            self.assertEqual(values["CONFIG_PATH"], executable.parent / ".tg_cleanup_config.json")
            self.assertEqual(values["SESSIONS_DIR"], executable.parent / "sessions")

    def test_session_path_boundary_with_temporary_directory(self):
        app = CleanupApp.__new__(CleanupApp)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(storage, "BASE_DIR", root), patch.object(storage, "SESSIONS_DIR", root / "sessions"), patch.object(storage, "LEGACY_SESSION_PATH", root / "tg_cleanup_session"):
                self.assertEqual(app._session_path({"session": "sessions/account"}), root / "sessions" / "account")
                self.assertEqual(app._session_path({"session": "tg_cleanup_session"}), root / "tg_cleanup_session")
                with self.assertRaises(RuntimeError):
                    app._session_path({"session": "../outside"})

    def test_entrypoint_import_has_no_runtime_side_effects(self):
        with patch.object(tg_cleanup.tk, "Tk", side_effect=AssertionError("Unexpected GUI")), patch.object(tg_cleanup, "SingleInstance", side_effect=AssertionError("Unexpected lock")), patch.object(CleanupApp, "__init__", side_effect=AssertionError("Unexpected application startup")):
            importlib.reload(tg_cleanup)
        self.assertIs(tg_cleanup.CleanupApp, CleanupApp)

    def test_mixins_have_unique_methods_and_no_entrypoint_imports(self):
        methods = []
        for cls in CleanupApp.__mro__[:-1]:
            methods.extend(name for name, value in vars(cls).items() if callable(value) or isinstance(value, (staticmethod, classmethod)))
        self.assertEqual(len(methods), len(set(methods)))
        for path in (ROOT / "cleanup_app").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertNotIn("tg_cleanup", [alias.name for alias in node.names])
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "tg_cleanup")


if __name__ == "__main__":
    unittest.main()
