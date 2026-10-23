"""External announcements: pure fakes and isolated HKCU keys, never browsers."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from lume.distribution import PublishedExtension
from lume.extension_install import (UPDATE_URLS, RegistryState, RegistryTarget,
    WindowsRegistry, ExtensionInstallError, plan_extension_install,
    apply_extension_install, rollback_extension_install, remove_owned_extensions)

CHROME = PublishedExtension("chrome", "a" * 32, "https://chromewebstore.google.com/detail/" + "a" * 32,
                            UPDATE_URLS["chrome"])
EDGE = PublishedExtension("edge", "b" * 32, "https://microsoftedge.microsoft.com/addons/detail/" + "b" * 32,
                          UPDATE_URLS["edge"])


class FakeRegistry:
    def __init__(self):
        self.states, self.writes = {}, 0
        self.fail_at = None
        self.after_write = None
        self.fail_remove = False

    def target(self, browser, extension_id):
        return RegistryTarget(browser, extension_id, "Private/" + browser + "/" + extension_id)

    def read(self, target):
        return self.states.get(target, RegistryState())

    def write(self, target, update_url):
        self.writes += 1
        self.states[target] = RegistryState(True, (("update_url", update_url, 1),))
        if self.after_write:
            self.after_write(target)
        if self.writes == self.fail_at:
            raise OSError("uncertain write")

    def remove(self, target):
        if self.fail_remove:
            raise OSError("cleanup denied")
        self.states.pop(target, None)


class ExtensionInstallTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".cache" / "extension-install-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        self.file = self.root / "owner" / "extension-announcements.json"
        self.backend = FakeRegistry()

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, entries=(CHROME, EDGE)):
        return plan_extension_install(entries, self.file, _backend=self.backend)

    def apply(self, entries=(CHROME, EDGE)):
        return apply_extension_install(self.plan(entries))

    def test_unpublished_is_read_only_and_empty_apply_creates_nothing(self):
        plan = self.plan(())
        self.assertFalse(plan.changed)
        self.assertFalse(apply_extension_install(plan).applied)
        self.assertFalse(self.file.parent.exists())
        self.assertEqual(self.backend.writes, 0)

    def test_real_announcements_round_trip_is_idempotent_with_backup(self):
        plan = self.plan()
        self.assertFalse(self.file.exists())
        receipt = apply_extension_install(plan)
        self.assertEqual(receipt.status, "ready")
        self.assertTrue(receipt.backup_directory.is_dir())
        self.assertEqual(self.backend.writes, 2)
        self.assertFalse(self.apply().applied)
        self.assertEqual(self.backend.writes, 2)
        removed = remove_owned_extensions(self.file, _backend=self.backend)
        self.assertEqual(removed.status, "removed")
        self.assertFalse(self.backend.states)

    def test_same_url_existing_foreign_entry_is_never_adopted(self):
        target = self.backend.target(CHROME.browser, CHROME.extension_id)
        state = RegistryState(True, (("update_url", CHROME.update_url, 1),))
        self.backend.states[target] = state
        result = self.apply((CHROME,))
        self.assertEqual(result.preserved, ("chrome:" + CHROME.extension_id,))
        self.assertEqual(self.backend.read(target), state)
        self.assertFalse(self.file.exists())

    def test_mixed_foreign_and_new_preserves_foreign_and_owns_only_new(self):
        target = self.backend.target(CHROME.browser, CHROME.extension_id)
        self.backend.states[target] = RegistryState(True, (("unknown", "data", 1),))
        self.apply()
        remove_owned_extensions(self.file, _backend=self.backend)
        self.assertEqual(len(self.backend.states), 1)
        self.assertIn(target, self.backend.states)

    def test_switching_unpublished_removes_previous_owned_announcements(self):
        self.apply()
        receipt = self.apply(())
        self.assertTrue(receipt.applied)
        self.assertFalse(self.backend.states)
        self.assertEqual(json.loads(self.file.read_text())["owned"], [])
        rollback_extension_install(receipt)
        self.assertEqual(len(self.backend.states), 2)

    def test_rollback_of_added_channel_does_not_remove_previous_channel(self):
        self.apply((CHROME,))
        receipt = self.apply()
        rollback_extension_install(receipt)
        self.assertEqual(len(self.backend.states), 1)
        self.assertIn(self.backend.target(CHROME.browser, CHROME.extension_id), self.backend.states)

    def test_stale_plan_is_rejected_before_registry_or_filesystem_write(self):
        plan = self.plan()
        target = self.backend.target(CHROME.browser, CHROME.extension_id)
        self.backend.states[target] = RegistryState(True)
        with self.assertRaises(ExtensionInstallError):
            apply_extension_install(plan)
        self.assertFalse(self.file.parent.exists())
        self.assertEqual(self.backend.writes, 0)

    def test_uncertain_write_rolls_back_both_attempted_channels(self):
        self.backend.fail_at = 2
        with self.assertRaises(ExtensionInstallError) as caught:
            self.apply()
        self.assertEqual(caught.exception.receipt.status, "rolled_back")
        self.assertFalse(self.backend.states)

    def test_failed_cleanup_retains_ownership_and_retry_completes(self):
        self.backend.fail_at = 2
        self.backend.fail_remove = True
        with self.assertRaises(ExtensionInstallError) as caught:
            self.apply()
        receipt = caught.exception.receipt
        self.assertEqual(receipt.status, "recovery_pending")
        self.assertEqual(len(json.loads(self.file.read_text())["owned"]), 2)
        with self.assertRaises(ExtensionInstallError):
            self.plan()
        self.backend.fail_remove = False
        self.backend.fail_at = None
        result = remove_owned_extensions(self.file, _backend=self.backend)
        self.assertFalse(result.pending)
        self.assertFalse(self.backend.states)

    def test_mutated_owned_entry_is_preserved_and_cleanup_retries(self):
        self.apply()
        target = self.backend.target(CHROME.browser, CHROME.extension_id)
        owned = self.backend.read(target)
        self.backend.states[target] = replace(owned, subkeys=("foreign",))
        result = remove_owned_extensions(self.file, _backend=self.backend)
        self.assertEqual(len(result.pending), 1)
        self.assertEqual(len(self.backend.states), 1)
        self.backend.states[target] = owned
        self.assertEqual(remove_owned_extensions(self.file, _backend=self.backend).status, "removed")

    def test_late_handshake_change_is_preserved_and_reported(self):
        first = self.backend.target(CHROME.browser, CHROME.extension_id)
        def late(target):
            if target.browser == "edge":
                self.backend.states[first] = RegistryState(True, (("unknown", "late", 1),))
        self.backend.after_write = late
        with self.assertRaises(ExtensionInstallError) as caught:
            self.apply()
        self.assertEqual(caught.exception.receipt.pending, ("chrome:" + CHROME.extension_id,))
        self.assertEqual(len(self.backend.states), 1)

    def test_receipt_cannot_authorize_arbitrary_registry_path(self):
        self.apply()
        raw = json.loads(self.file.read_text())
        raw["owned"][0]["target"]["path"] = r"Software\Policies\Google\Chrome"
        self.file.write_text(json.dumps(raw))
        with self.assertRaises(ExtensionInstallError):
            remove_owned_extensions(self.file, _backend=self.backend)
        self.assertEqual(len(self.backend.states), 2)

    def test_duplicate_json_keys_are_rejected(self):
        self.file.parent.mkdir()
        self.file.write_text('{"owner":"first","owner":"second"}')
        with self.assertRaises(ExtensionInstallError):
            self.plan()

    def test_unknown_update_url_or_identity_cannot_be_written(self):
        for entry in (replace(CHROME, update_url="https://example.com/update"),
                      replace(CHROME, extension_id="../policies"),
                      replace(CHROME, browser="brave")):
            with self.subTest(entry=entry), self.assertRaises(ExtensionInstallError):
                self.plan((entry,))
        self.assertEqual(self.backend.writes, 0)

    def test_stale_receipt_cannot_rollback_new_operation(self):
        old = self.apply((CHROME,))
        self.apply()
        with self.assertRaises(ExtensionInstallError):
            rollback_extension_install(old)
        self.assertEqual(len(self.backend.states), 2)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("OPENDOWNLOADER_TEST_PRIVATE_REGISTRY") == "1",
                         "private registry integration requires explicit test opt-in")
    def test_actual_private_hkcu_round_trip(self):
        namespace = "Software\\OpenDownloaderExtensionTests\\" + uuid.uuid4().hex
        backend = WindowsRegistry(namespace=namespace)
        try:
            plan = plan_extension_install((CHROME, EDGE), self.file, _backend=backend)
            apply_extension_install(plan)
            self.assertTrue(backend.read(backend.target(CHROME.browser, CHROME.extension_id)).key_exists)
            self.assertEqual(remove_owned_extensions(self.file, _backend=backend).status, "removed")
            for entry in (CHROME, EDGE):
                self.assertFalse(backend.read(backend.target(entry.browser, entry.extension_id)).key_exists)
        finally:
            # Remove only verified empty private test parents, never production keys.
            import winreg
            for path in (namespace + r"\chrome", namespace + r"\edge", namespace):
                try:
                    winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, path, winreg.KEY_WOW64_64KEY, 0)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    unittest.main()
