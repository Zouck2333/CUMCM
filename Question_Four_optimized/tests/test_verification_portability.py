"""An optional archived-source check must remain strict after relocation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from Question_Four_optimized.code.verify_optimized import verify_original_sources


class OriginalSourceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'renamed_archive'
        self.file = self.archive / 'code' / 'model.py'
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'original model\n')
        self.manifest = self.root / 'source_hashes.json'
        self.manifest.write_text(json.dumps([{
            'Path': r'C:\previous_computer\CUMCM\Question_Four\code\model.py',
            'Hash': hashlib.sha256(self.file.read_bytes()).hexdigest().upper(),
        }]), encoding='utf-8')

    def test_renamed_archive_uses_relative_paths_and_preserves_manifest(self):
        before = self.manifest.read_bytes()
        report = verify_original_sources(self.manifest, self.archive)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['checked_files'], 1)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_requested_check_rejects_changed_source(self):
        self.file.write_bytes(b'changed model\n')
        with self.assertRaisesRegex(AssertionError, 'hash mismatch'):
            verify_original_sources(self.manifest, self.archive)

    def test_requested_check_rejects_missing_source(self):
        with self.assertRaisesRegex(FileNotFoundError, 'missing'):
            verify_original_sources(self.manifest, self.root / 'missing_archive')
