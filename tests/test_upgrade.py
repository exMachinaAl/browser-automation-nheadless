"""Regression test: in-place upgrade must protect config and session data."""
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.upgrade import upgrade, read_version, UpgradeError

SRC = Path(__file__).resolve().parents[1]
# A synthetic v0.6.0 tree makes these tests self-contained in FULL releases.


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'new'
        self.target = self.root / 'old'
        shutil.copytree(SRC, self.source, ignore=shutil.ignore_patterns('data', 'config.json', '_backups','__pycache__','*.pyc'))
        shutil.copytree(self.source, self.target, ignore=shutil.ignore_patterns('data', '__pycache__','*.pyc'))
        (self.target/'gateway'/'__init__.py').write_text("__version__ = '0.6.0'\n", encoding='utf-8')
        (self.target/'gateway'/'core.py').write_text('# simulated older implementation\n',encoding='utf-8')
        self.settings = self.target/'config.json'
        self.settings.write_text('{"port": 44839, "my_custom_setting": true}', encoding='utf-8')
        (self.target/'data').mkdir(exist_ok=True)
        self.sentinel = self.target/'data'/'gateway.sqlite3'
        self.sentinel.write_bytes(b'IMPORTANT-USER-DB-NOT-MODIFIED')

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_does_not_modify_target(self):
        result = upgrade(self.target,self.source,apply=False)
        self.assertEqual(result['mode'],'dry_run')
        self.assertEqual(read_version(self.target),'0.6.0')
        self.assertFalse((self.source/'_backups').exists())

    def test_upgrade_preserves_data_and_config_and_backups_code(self):
        config_original = self.settings.read_bytes()
        sqlite_original = self.sentinel.read_bytes()
        result = upgrade(self.target,self.source,apply=True)
        self.assertEqual(result['status'],'applied')
        self.assertEqual(read_version(self.target),'0.6.1')
        self.assertEqual(self.settings.read_bytes(), config_original)
        self.assertEqual(self.sentinel.read_bytes(), sqlite_original)
        backup = Path(result['backup_path'])
        self.assertTrue((backup/'gateway'/'core.py').is_file())
        self.assertEqual(read_version(backup),'0.6.0')
        with self.assertRaises(UpgradeError):
            upgrade(self.target,self.source,apply=True)

    def test_refuse_unrecognized_target(self):
        (self.target/'gateway'/'__init__.py').write_text("__version__ = '99.0'\n")
        with self.assertRaises(UpgradeError):
            upgrade(self.target,self.source,apply=True)


if __name__ == '__main__':
    unittest.main()
