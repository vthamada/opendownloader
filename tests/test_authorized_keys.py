"""Authorized key input, queue lifetime and secret-free persistence contracts."""
from __future__ import annotations

import contextlib
import gc
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lume.__main__ import main
from lume.core import Manager, SourceFacts
from lume.crypto_keys import AuthorizedKeys, parse_authorized_keys
from lume.network import Control, DownloadError

KID = "0123456789abcdef0123456789abcdef"
KEY = "fedcba9876543210fedcba9876543210"
PAIR = KID + ":" + KEY


class AuthorizedKeyParserTests(unittest.TestCase):
    def test_immutable_normalized_copy_and_order_independent_proof(self):
        keys = parse_authorized_keys(" " + PAIR.upper() + ";\n" + "0" * 32 + ":" + "1" * 32)
        self.assertEqual(keys.by_kid()[KID], bytes.fromhex(KEY))
        changed = keys.by_kid()
        changed.clear()
        self.assertEqual(len(keys.by_kid()), 2)
        self.assertEqual(keys.fingerprint(), parse_authorized_keys("0" * 32 + ":" + "1" * 32 + "," + PAIR).fingerprint())
        self.assertNotIn(KEY, repr(keys))
        self.assertNotIn(KID, repr(keys))
        self.assertTrue(keys)
        self.assertFalse(parse_authorized_keys(" \n"))

    def test_rejects_malformed_duplicate_and_excess_without_echo(self):
        invalid = [PAIR + ";" + PAIR.upper(), "secret" + KEY, "*:" + KEY,
                   KID + ":" + KEY[:-1], "x" * 8193,
                   ";".join(f"{n:032x}:{KEY}" for n in range(17))]
        for value in invalid:
            with self.subTest(case=len(value)), self.assertRaises(DownloadError) as error:
                parse_authorized_keys(value)
            self.assertNotIn(KEY, str(error.exception))
        with self.assertRaises(DownloadError):
            AuthorizedKeys(((KID, KEY),))


class AuthorizedKeyQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="od-authorized-keys-")
        self.root = Path(self.temp.name)
        self.manager = Manager(self.root / "data", self.root / "files")
        self.keys = parse_authorized_keys(PAIR)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def add(self, **options):
        return self.manager.add("https://example.org/index.mpd", self.root / "files", start=False, **options)

    def assert_no_keys_on_disk(self):
        for path in self.root.rglob("*"):
            if path.is_file() and path.name != "desktop.lock":
                data = path.read_bytes()
                self.assertNotIn(KEY.encode(), data)
                self.assertNotIn(KID.encode(), data)
                self.assertNotIn(bytes.fromhex(KEY), data)
        self.assertNotIn(KEY, json.dumps(self.manager.list_tasks()))
        self.assertNotIn(KEY, json.dumps(self.manager.get_options()))

    def test_held_restart_reentry_forgets_keys_and_persists_only_requirement(self):
        task_id = self.add(authorized_keys=self.keys)
        task = self.manager.list_tasks()[0]
        self.assertEqual(task["status"], "held")
        self.assertTrue(task["requires_keys"])
        self.assertFalse(self.manager.needs_authorized_keys(task_id))
        self.assert_no_keys_on_disk()
        self.manager.close()
        self.assertEqual(self.manager._authorized_keys, {})
        self.manager = Manager(self.root / "data", self.root / "files")
        self.assertTrue(self.manager.needs_authorized_keys(task_id))
        for operation in (self.manager.resume, self.manager.restart):
            with self.assertRaises(DownloadError) as error:
                operation(task_id)
            self.assertEqual(error.exception.code, "authorized_keys_required")
            self.assertEqual(self.manager.list_tasks()[0]["status"], "held")
        self.manager.set_authorized_keys(task_id, self.keys)
        self.assertFalse(self.manager.needs_authorized_keys(task_id))
        self.manager.remove(task_id)
        self.assertEqual(self.manager._authorized_keys, {})

    def test_scheduled_restart_cannot_autostart_without_reentry(self):
        task_id = self.add(authorized_keys=self.keys, scheduled_at=time.time() + 1000)
        self.manager.close()
        self.manager = Manager(self.root / "data", self.root / "files")
        self.assertEqual(self.manager.list_tasks()[0]["status"], "paused")
        self.assertTrue(self.manager.needs_authorized_keys(task_id))
        self.assertEqual(self.manager.workers, {})

    def test_invalid_boundary_is_rejected_before_file_or_request_mutation(self):
        with self.assertRaises(DownloadError):
            self.manager.add("https://example.org/index.mpd", self.root / "not-created", authorized_keys={KID: KEY})
        self.assertFalse((self.root / "not-created").exists())
        with self.assertRaises(DownloadError):
            self.manager.confirm_request("missing", self.root / "not-created", authorized_keys={})
        self.assertEqual(self.manager.list_tasks(), [])
        task_id = self.add()
        with self.assertRaises(DownloadError):
            self.manager.set_authorized_keys(task_id, AuthorizedKeys())
        self.manager._change(self.manager.tasks[task_id], status="queued")
        with self.manager.lock, self.assertRaises(DownloadError):
            self.manager.set_authorized_keys(task_id, self.keys)
        self.manager.pause(task_id)

    def test_scheduler_observes_keys_on_first_admitted_start(self):
        reached = threading.Event()
        observed = []
        def run(task, control):
            try:
                with self.manager.lock:
                    observed.append(self.manager._authorized_keys.get(task["id"]))
                    self.manager._change(task, status="held")
            finally:
                with self.manager.lock:
                    self.manager.workers.pop(task["id"], None)
                    self.manager.controls.pop(task["id"], None)
                reached.set()
        with patch.object(self.manager, "_run", side_effect=run):
            self.manager.add("https://example.org/index.mpd", self.root / "files", authorized_keys=self.keys)
            self.assertTrue(reached.wait(3))
        self.assertEqual(observed, [self.keys])

    def test_browser_confirmation_keys_remain_ephemeral(self):
        from lume.browser import enqueue_request
        identity = enqueue_request(self.root / "data", {"version": 3, "url": "https://example.org/index.mpd",
                                   "media": True, "source": "dash"}).replace("-", "")
        self.manager._consume_inbox()
        self.manager.confirm_request(identity, self.root / "files", start=False, authorized_keys=self.keys)
        self.assertEqual(self.manager.list_requests(), [])
        self.assertFalse(self.manager.needs_authorized_keys(identity))
        self.assert_no_keys_on_disk()

    def test_only_download_stage_receives_ephemeral_key_object(self):
        task_id = self.add(authorized_keys=self.keys)
        task = self.manager.tasks[task_id]
        plan = SimpleNamespace(filename="fixture.mp4", fingerprint="0" * 64, title="Fixture",
                               selection="video", audio_only=False, ext="mp4", protocol="dash")
        sentinel = DownloadError("fixture", "Stopped before transfer")
        with patch("lume.core.resolve_stream", return_value=plan) as resolve, patch("lume.core.download_stream", side_effect=sentinel) as download:
            with self.assertRaises(DownloadError):
                self.manager._download_manifest(task, Control(), SourceFacts(task["url"], {}, False, [], manifest=True))
        self.assertNotIn("authorized_keys", resolve.call_args.kwargs)
        self.assertIs(download.call_args.kwargs["authorized_keys"], self.keys)
        self.assert_no_keys_on_disk()

    def test_missing_download_key_promotes_reentry_requirement_without_secret(self):
        identity = self.add()
        with patch.object(self.manager, "_prepare_source", side_effect=DownloadError("stream_keys", "Informe as chaves autorizadas.")):
            self.manager._run(self.manager.tasks[identity], Control())
        self.assertEqual(self.manager.list_tasks()[0]["status"], "failed")
        self.assertTrue(self.manager.needs_authorized_keys(identity))
        self.assert_no_keys_on_disk()

    def test_direct_file_with_keys_fails_before_http_and_without_publishing(self):
        from tests.test_streaming import Fixture
        data = b"A direct file cannot consume a CENC key map."
        server = Fixture({"/direct.mp4": data})
        try:
            identity = self.manager.add(server.url + "/direct.mp4", self.root / "files", authorized_keys=self.keys)
            task = self.manager.wait(identity, 5)
            self.assertEqual(task["status"], "failed")
            self.assertIn("Nenhum arquivo foi baixado", task["error"])
            self.assertEqual(server.requests, [])
            self.assertFalse((self.root / "files" / "direct.mp4").exists())
            self.assertTrue(task["requires_keys"])
            self.assert_no_keys_on_disk()
            # The same actual endpoint is healthy for an ordinary keyless file
            # download; refusal above must be caused by the explicit key input.
            plain_id = self.manager.add(server.url + "/direct.mp4", self.root / "files")
            plain = self.manager.wait(plain_id, 5)
            self.assertEqual(plain["status"], "completed", plain["error"])
            self.assertEqual((self.root / "files" / plain["filename"]).read_bytes(), data)
        finally:
            server.close()


class AuthorizedKeyCliTests(unittest.TestCase):
    def test_misplaced_argv_secret_is_never_echoed(self):
        for extra in (["--keys-stdin=" + PAIR], ["--keys-stdin", PAIR]):
            output = io.StringIO()
            with contextlib.redirect_stderr(output), self.assertRaises(SystemExit):
                main(["--download", "https://example.org/index.mpd", *extra])
            self.assertNotIn(KEY, output.getvalue())
            self.assertNotIn(KID, output.getvalue())

    def test_cli_rejects_missing_wrong_mode_and_oversize_before_manager(self):
        for argv, text in [(["--keys-stdin"], PAIR), (["--add", "https://example.org/a", "--keys-stdin"], PAIR),
                           (["--download", "https://example.org/index.mpd", "--keys-stdin"], KEY * 300),
                           (["--download", "https://example.org/index.mpd", "--keys-stdin"], "invalid" + KEY)]:
            output = io.StringIO()
            with patch("sys.stdin", io.StringIO(text)), contextlib.redirect_stderr(output), patch("lume.__main__.Manager") as manager:
                self.assertEqual(main(argv), 1)
                manager.assert_not_called()
            self.assertNotIn(KEY, output.getvalue())

    def test_cli_stdin_only_passes_typed_keys_to_add(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(PAIR)), contextlib.redirect_stdout(output), patch("lume.__main__.Manager") as factory:
            manager = factory.return_value
            manager.add.return_value = "fixture"
            manager.wait.return_value = dict(id="fixture", filename="a.mp4", status="completed", total=1, sha256="0" * 64, error="")
            self.assertEqual(main(["--download", "https://example.org/index.mpd", "--keys-stdin"]), 0)
            self.assertEqual(manager.add.call_args.kwargs["authorized_keys"].by_kid()[KID], bytes.fromhex(KEY))
            manager.close.assert_called_once()
        self.assertNotIn(KEY, output.getvalue())


class AuthorizedKeyUiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from lume.ui import App
        from tests.test_ui import FixtureManager
        self.temp = tempfile.TemporaryDirectory(prefix="od-key-ui-")
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.temp.cleanup()
            self.skipTest(str(error))
        self.root.withdraw()
        self.manager = FixtureManager(Path(self.temp.name))
        self.app = App(self.root, self.manager)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def test_dialog_masks_input_and_clears_widget_before_admission(self):
        from lume.ui import AddDialog
        dialog = AddDialog(self.app)
        dialog.urls.insert("1.0", "https://example.org/index.mpd")
        dialog.authorized_keys.set(PAIR)
        variable = dialog.authorized_keys
        self.assertEqual(dialog.authorized_keys_entry.cget("show"), "●")
        dialog.submit(start=False)
        self.assertEqual(variable.get(), "")
        self.assertEqual(self.manager.tasks[0]["authorized_keys"].by_kid()[KID], bytes.fromhex(KEY))

    def test_multiple_urls_refuse_keys_without_admitting_any(self):
        from lume.ui import AddDialog
        dialog = AddDialog(self.app)
        dialog.urls.insert("1.0", "https://example.org/a.mpd\nhttps://example.org/b.mpd")
        dialog.authorized_keys.set(PAIR)
        with patch("lume.ui.messagebox.showwarning") as warning:
            dialog.submit(start=False)
        warning.assert_called_once()
        self.assertEqual(self.manager.tasks, [])
        self.assertEqual(dialog.authorized_keys.get(), "")
        dialog.destroy()

    def test_resume_reentry_prompt_is_masked_and_cancel_keeps_task_blocked(self):
        from unittest.mock import Mock
        self.manager.needs_authorized_keys = Mock(return_value=True)
        self.manager.set_authorized_keys = Mock()
        task = {"id": "fixture", "requires_keys": True}
        with patch("lume.ui.simpledialog.askstring", return_value=None) as prompt:
            self.assertFalse(self.app._ensure_authorized_keys(task))
        self.assertEqual(prompt.call_args.kwargs["show"], "●")
        self.manager.set_authorized_keys.assert_not_called()
        with patch("lume.ui.simpledialog.askstring", return_value=PAIR):
            self.assertTrue(self.app._ensure_authorized_keys(task))
        self.assertEqual(self.manager.set_authorized_keys.call_args.args[1].by_kid()[KID], bytes.fromhex(KEY))


if __name__ == "__main__":
    unittest.main()
