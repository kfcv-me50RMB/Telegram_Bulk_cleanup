"""Offline icon resources and Windows integration checks."""

import ctypes
import io
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cleanup_app import icons


class IconTests(unittest.TestCase):
    def test_source_resource_exists(self):
        self.assertEqual(icons.icon_path(), ROOT / "assets" / "app_icon.ico")
        self.assertTrue(icons.icon_path().is_file())

    def test_frozen_resource_uses_unpack_directory(self):
        with patch.object(sys, "_MEIPASS", "C:/temporary/unpacked", create=True):
            self.assertEqual(icons.icon_path(), Path("C:/temporary/unpacked/assets/app_icon.ico"))

    def test_default_icon_is_inherited_by_new_windows(self):
        root = Mock()
        icons.apply_window_icon(root)
        root.iconbitmap.assert_called_once_with(default=str(icons.icon_path()))

    def test_icon_failure_does_not_abort_startup(self):
        root = Mock()
        root.iconbitmap.side_effect = tk.TclError("missing icon")
        with patch.object(sys, "stderr", new_callable=io.StringIO) as output:
            icons.apply_window_icon(root)
            self.assertIn("missing icon", output.getvalue())
        with patch.object(sys, "stderr", None):
            icons.apply_window_icon(root)

    def test_windows_identity_and_hresult_failure(self):
        setter = Mock(return_value=0)
        windll = Mock()
        windll.shell32.SetCurrentProcessExplicitAppUserModelID = setter
        with patch.object(sys, "platform", "win32"), patch.object(ctypes, "windll", windll, create=True):
            icons.set_taskbar_identity()
            setter.assert_called_once_with(icons.APP_USER_MODEL_ID)
            setter.return_value = -1
            with patch.object(sys, "stderr", new_callable=io.StringIO) as output:
                icons.set_taskbar_identity()
                self.assertIn("HRESULT", output.getvalue())

    def test_unsupported_platform_is_noop(self):
        with patch.object(sys, "platform", "linux"), patch.object(ctypes, "windll", Mock(), create=True) as windll:
            icons.set_taskbar_identity()
            self.assertFalse(windll.mock_calls)


if __name__ == "__main__":
    unittest.main()
