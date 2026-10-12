"""SQLite configuration tests use temporary metadata; no real sessions."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from cleanup_app import storage
from cleanup_app.storage import StorageMixin, read_config, write_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.sqlite3'
        self.legacy = self.path.with_suffix('.json')
        self.config = {'api_id': '123', 'api_hash': 'demo', 'selected_account': 'a',
                       'accounts': [{'id': 'a', 'session': 'sessions/demo', 'label': '演示', 'user_id': '42'}]}

    def app(self):
        app = StorageMixin()
        app.accounts = []
        app.session_issues = {}
        app.current_account_id = None
        app.api_id = Mock(get=Mock(return_value='123'))
        app.api_hash = Mock(get=Mock(return_value='demo'))
        app._selected_account_id = Mock(return_value='a')
        app._session_status = Mock(return_value='valid')
        app._refresh_account_list = Mock()
        app._log = Mock()
        return app

    def load(self, app):
        with patch.object(storage, 'CONFIG_PATH', self.path), patch.object(storage, 'LEGACY_CONFIG_PATH', self.legacy), patch.object(storage, 'LEGACY_SESSION_PATH') as legacy:
            legacy.with_suffix.return_value.exists.return_value = False
            app._load_config()

    def test_roundtrip_and_replacement(self):
        write_config(self.path, self.config)
        self.assertEqual(read_config(self.path), self.config)
        self.config['accounts'] = []
        self.config['selected_account'] = None
        write_config(self.path, self.config)
        self.assertEqual(read_config(self.path), self.config)

    def test_transaction_rolls_back_duplicate_accounts(self):
        write_config(self.path, self.config)
        bad = {**self.config, 'api_hash': 'changed', 'accounts': self.config['accounts'] * 2}
        with self.assertRaises(sqlite3.IntegrityError):
            write_config(self.path, bad)
        self.assertEqual(read_config(self.path), self.config)

    def test_first_write_failure_does_not_leave_database(self):
        with self.assertRaises(sqlite3.IntegrityError):
            write_config(self.path, {**self.config, 'accounts': self.config['accounts'] * 2})
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + '.tmp').exists())

    def test_legacy_migration_preserves_backup_and_selection(self):
        original = json.dumps(self.config)
        self.legacy.write_text(original, encoding='utf-8')
        app = self.app()
        self.load(app)
        self.assertEqual(read_config(self.path), self.config)
        self.assertEqual(self.legacy.read_text(encoding='utf-8'), original)
        app._refresh_account_list.assert_called_once_with('a')

    def test_database_wins_over_legacy(self):
        write_config(self.path, self.config)
        self.legacy.write_text('invalid json', encoding='utf-8')
        app = self.app()
        self.load(app)
        self.assertEqual(app.accounts, self.config['accounts'])
        self.assertFalse(app._config_read_failed)

    def test_corrupt_database_is_preserved_and_save_blocked(self):
        self.path.write_bytes(b'not a database')
        app = self.app()
        with patch.object(storage.messagebox, 'showwarning') as warning:
            self.load(app)
        warning.assert_called_once()
        with self.assertRaises(OSError), patch.object(storage, 'CONFIG_PATH', self.path):
            app._save_config()
        self.assertEqual(self.path.read_bytes(), b'not a database')

    def test_failed_migration_keeps_legacy_and_allows_next_start(self):
        self.legacy.write_text(json.dumps(self.config), encoding='utf-8')
        with patch.object(storage, 'write_config', side_effect=sqlite3.OperationalError('readonly')), patch.object(storage.messagebox, 'showwarning'):
            self.load(self.app())
        self.assertFalse(self.path.exists())
        self.load(self.app())
        self.assertEqual(read_config(self.path), self.config)

    def test_invalid_legacy_does_not_create_database(self):
        self.legacy.write_text('{', encoding='utf-8')
        with patch.object(storage.messagebox, 'showwarning'):
            self.load(self.app())
        self.assertFalse(self.path.exists())

    def test_missing_keys_fill_placeholders_without_literal_none(self):
        write_config(self.path, {'accounts': []})
        stored = read_config(self.path)
        self.assertEqual(stored['api_id'], '')
        self.assertEqual(stored['api_hash'], '')
        self.assertIsNone(stored['selected_account'])
        app = self.app()
        app._selected_account_id = Mock(return_value=None)
        with patch.object(app.api_id, 'set') as set_api_id:
            self.load(app)
        set_api_id.assert_called_once_with('')

    def test_stored_none_credentials_never_become_text_none(self):
        write_config(self.path, {'api_id': None, 'api_hash': None, 'accounts': []})
        self.assertEqual(read_config(self.path)['api_id'], '')
        self.assertEqual(read_config(self.path)['api_hash'], '')
        app = self.app()
        with patch.object(app.api_id, 'set') as set_api_id, patch.object(app.api_hash, 'set') as set_api_hash:
            self.load(app)
        set_api_id.assert_called_once_with('')
        set_api_hash.assert_called_once_with('')
