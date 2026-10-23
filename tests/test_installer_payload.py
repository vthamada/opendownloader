"""Build boundary checks bind copied bytes to the exact reviewed inventory."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.build_installer import copy_tree, runtime_inventory
from lume.deployment import bundle_inventory
from tools.package_release import copy_extension


class InstallerPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'candidate'
        target = self.source / 'runtimes/node/node.exe'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'fixture, not an executable')
        self.lock = {'files': [{'path': 'runtimes/node/node.exe', 'bytes': target.stat().st_size,
                              'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}]}

    def test_unlisted_runtime_is_rejected_before_copy(self):
        runtime_inventory(self.source, self.lock)
        (self.source / 'runtimes/extra.dll').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'exatamente'):
            runtime_inventory(self.source, self.lock)

    def test_changed_runtime_is_rejected(self):
        (self.source / 'runtimes/node/node.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            runtime_inventory(self.source, self.lock)

    def test_source_change_between_inventory_and_copy_is_rejected_in_staging(self):
        import shutil
        original = shutil.copy2
        def racing_copy(source, target):
            Path(source).write_bytes(b'changed during copy')
            return original(source, target)
        expected = bundle_inventory(self.source)
        with patch('tools.build_installer.shutil.copy2', side_effect=racing_copy), \
             self.assertRaisesRegex(ValueError, 'staging'):
            copy_tree(self.source, self.root / 'output', expected)

    def test_verified_copy_covers_dlls_and_data(self):
        expected = bundle_inventory(self.source)
        copy_tree(self.source, self.root / 'output', expected)
        self.assertEqual(bundle_inventory(self.root / 'output'), expected)
        (self.source / 'runtimes/node/data.dll').write_bytes(b'unverified')
        with self.assertRaises(ValueError):
            copy_tree(self.source, self.root / 'other', expected)

    def test_extension_asset_closure_includes_nested_icons_and_excludes_tests(self):
        from tools.package_extension import collect
        source=Path(__file__).resolve().parents[1]/'browser-extension'
        manifest, expected=collect(source)
        target=self.root/'extension'
        self.assertEqual(copy_extension(source,target),manifest)
        for name,body in expected.items():self.assertEqual((target/name).read_bytes(),body)
        self.assertTrue((target/'icons/icon-128.png').is_file())
        self.assertFalse((target/'tests').exists())
        before=bundle_inventory(target)
        with self.assertRaises(ValueError):copy_extension(source,target)
        self.assertEqual(bundle_inventory(target),before)
