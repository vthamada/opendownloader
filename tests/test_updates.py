"""Exercise signed updates through the public offline verification/staging API."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from lume.updates import UpdateError, stage_update, verify_update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / "Lume-1.0.1.zip"
        self.manifest = self.root / "release.json"
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(
            self.key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode("ascii")

    def make_package(self, entries=None):
        if entries is None:
            entries = [("lume/app.py", b"print('fixture, never executed')\n")]
        with zipfile.ZipFile(self.package, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries:
                archive.writestr(name, value)
        return self.write_manifest()

    def write_manifest(self, **changes):
        data = self.package.read_bytes()
        payload = {
            "app": "lume-downloads",
            "version": "1.0.1",
            "filename": self.package.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        payload.update(changes)
        signed = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        self.manifest.write_text(
            json.dumps(
                {
                    "payload": payload,
                    "signature": base64.b64encode(self.key.sign(signed)).decode("ascii"),
                }
            ),
            encoding="utf-8",
        )
        return payload

    def verify(self, **kwargs):
        return verify_update(self.manifest, self.package, self.public, **kwargs)

    def assert_stage_rejected(self):
        verified = self.verify()
        staging = self.root / "staging"
        with self.assertRaises(UpdateError):
            stage_update(verified, staging)
        self.assertFalse(staging.exists() and list(staging.iterdir()))

    def test_signed_package_is_staged_without_execution(self):
        self.make_package()
        verified = self.verify()
        self.assertEqual(verified.version, "1.0.1")
        staged = stage_update(verified, self.root / "staging")
        self.assertEqual(
            (staged / "payload" / "lume" / "app.py").read_bytes(),
            b"print('fixture, never executed')\n",
        )
        self.assertEqual((staged / "release.zip").read_bytes(), self.package.read_bytes())
        self.assertTrue((staged / "manifest.json").is_file())
        other = stage_update(verified, self.root / "staging")
        self.assertNotEqual(staged, other)

    def test_changed_signed_metadata_is_rejected(self):
        self.make_package()
        obj = json.loads(self.manifest.read_text(encoding="utf-8"))
        obj["payload"]["version"] = "9.0.0"
        self.manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaises(UpdateError):
            self.verify()

    def test_wrong_key_missing_key_and_invalid_signature(self):
        self.make_package()
        for key in ("", "not base64", base64.b64encode(b"x" * 32).decode("ascii")):
            with self.subTest(key=key):
                with self.assertRaises(UpdateError):
                    verify_update(self.manifest, self.package, key)
        obj = json.loads(self.manifest.read_text(encoding="utf-8"))
        obj["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        self.manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaises(UpdateError):
            self.verify()

    def test_modified_package_and_size_are_rejected(self):
        self.make_package()
        original = self.package.read_bytes()
        self.package.write_bytes(original + b"x")
        with self.assertRaises(UpdateError):
            self.verify()
        self.package.write_bytes(b"X" + original[1:])
        with self.assertRaises(UpdateError):
            self.verify()
        self.package.write_bytes(original)
        self.write_manifest(size=len(original) + 1)
        with self.assertRaises(UpdateError):
            self.verify()

    def test_replay_downgrade_and_noncanonical_versions(self):
        self.make_package()
        for version in ("1.0.0", "0.9.9", "01.2.3", "1.2", "1.0.1-beta"):
            with self.subTest(version=version):
                self.write_manifest(version=version)
                with self.assertRaises(UpdateError):
                    self.verify()

    def test_schema_app_name_and_manifest_size_are_strict(self):
        self.make_package()
        for changes in (
            {"app": "other-app"},
            {"extra": "unexpected"},
            {"size": True},
            {"size": 1024**3 + 1},
            {"filename": "../release.zip"},
            {"filename": "other.zip"},
        ):
            with self.subTest(changes=changes):
                self.write_manifest(**changes)
                with self.assertRaises(UpdateError):
                    self.verify()
        self.manifest.write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaises(UpdateError):
            self.verify()
        self.manifest.write_text('{"payload":{},"payload":{}}', encoding="utf-8")
        with self.assertRaises(UpdateError):
            self.verify()

    def test_changed_source_after_verification_fails_before_extraction(self):
        self.make_package()
        verified = self.verify()
        with zipfile.ZipFile(self.package, "w") as archive:
            archive.writestr("replacement.txt", b"changed package")
        with self.assertRaises(UpdateError):
            stage_update(verified, self.root / "staging")
        self.assertFalse((self.root / "staging").exists() and list((self.root / "staging").iterdir()))

    def test_traversal_absolute_windows_names_and_case_collisions(self):
        bad_entries = [
            [("../outside.txt", b"bad")],
            [("/absolute.txt", b"bad")],
            [("C:/windows.txt", b"bad")],
            [(r"dir\..\outside.txt", b"bad")],
            [("NUL.txt", b"bad")],
            [("dir/a. ", b"bad")],
            [("app.py", b"a"), ("APP.PY", b"b")],
            [("Dir/a", b"a"), ("dir/b", b"b")],
            [("lume", b"a"), ("lume/app.py", b"b")],
        ]
        for entries in bad_entries:
            with self.subTest(entries=entries):
                self.make_package(entries)
                self.assert_stage_rejected()
        self.assertFalse((self.root / "outside.txt").exists())

    def test_symlink_is_rejected(self):
        symlink = zipfile.ZipInfo("escape")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_package([(symlink, b"../outside")])
        self.assert_stage_rejected()

    def test_archive_quota_checked_before_extraction(self):
        self.make_package([("a", b"a"), ("b", b"b")])
        # Test the public configurable quota without creating a large archive.
        verified = self.verify()
        with self.assertRaises(UpdateError):
            stage_update(verified, self.root / "staging", max_entries=1)
        with self.assertRaises(UpdateError):
            stage_update(verified, self.root / "staging", max_expanded_bytes=1)

    def test_empty_zip_can_be_staged(self):
        self.make_package([])
        staged = stage_update(self.verify(), self.root / "staging")
        self.assertEqual(list((staged / "payload").iterdir()), [])

    def test_forged_zip_directory_count_is_rejected(self):
        self.make_package([("a", b"a"), ("b", b"b")])
        raw = bytearray(self.package.read_bytes())
        end = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", raw, end + 8, 0, 0)
        self.package.write_bytes(raw)
        self.write_manifest()
        self.assert_stage_rejected()

    def test_missing_crypto_dependency_fails_closed(self):
        self.make_package()
        script = (
            "from lume.updates import verify_update, UpdateError; import sys\n"
            "try:\n verify_update(sys.argv[1],sys.argv[2],sys.argv[3])\n"
            "except UpdateError as error:\n"
            " print(str(error)); sys.exit(0 if 'cryptography' in str(error) else 2)\n"
            "sys.exit(3)\n"
        )
        result = subprocess.run(
            [sys.executable, "-S", "-c", script, str(self.manifest), str(self.package), self.public],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_release_signing_tool_round_trip_and_no_overwrite(self):
        self.make_package()
        tools = Path(__file__).resolve().parents[1] / "tools" / "sign_release.py"
        key_dir = self.root / "release-keys"
        generated = subprocess.run(
            [sys.executable, str(tools), "--generate-key", str(key_dir)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        public = (key_dir / "public-key.txt").read_text(encoding="ascii").strip()
        output = self.root / "signed-release.json"
        command = [sys.executable, str(tools), "--package", str(self.package),
                   "--version", "1.0.1", "--private-key", str(key_dir / "private-key.pem"),
                   "--output", str(output)]
        signed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(signed.returncode, 0, signed.stderr)
        verified = verify_update(output, self.package, public)
        self.assertEqual(verified.version, "1.0.1")
        before = output.read_bytes()
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

