"""Deterministic late cancellation/publication and HTTP validator boundaries.

The transport returns protocol-shaped responses without network or GUI. A
finalization barrier marks a stable lifecycle seam after every part is durable;
it does not duplicate transfer/checkpoint implementation inside the test.
"""
from __future__ import annotations

from email.message import Message
import hashlib
import io
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lume.core import Manager
from lume.network import validator


PAYLOAD = b"Lume lifecycle fixture\n" * 4096


class HeaderResponse:
    def __init__(self, fields):
        self.headers = Message()
        for key, value in fields.items():
            self.headers[key] = value

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class FixtureResponse(HeaderResponse):
    def __init__(self, url, first, last):
        super().__init__({"Content-Range": f"bytes {first}-{last}/{len(PAYLOAD)}",
                          "Content-Length": str(last - first + 1), "ETag": '"lifecycle-v1"'})
        self.url = url
        self.response = self
        self.status = 206
        self.body = io.BytesIO(PAYLOAD[first:last + 1])

    def read(self, size):
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.body.close()


class FixtureTransport:
    def open(self, url, headers, control):
        control.check()
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", headers["Range"])
        if not match:
            raise AssertionError("Fixture expected an explicit byte range")
        return FixtureResponse(url, *map(int, match.groups()))


class NoNetworkTransport:
    def __init__(self):
        self.calls = 0

    def open(self, *_):
        self.calls += 1
        raise AssertionError("Publication recovery must not download the file again")


class FinalizationGateManager(Manager):
    def __init__(self, *args, **kwargs):
        self.finalization_entered = threading.Event()
        self.allow_finalization = threading.Event()
        self.gate_once = True
        super().__init__(*args, **kwargs)

    def _finalize(self, task, control):
        if self.gate_once:
            self.gate_once = False
            self.finalization_entered.set()
            if not self.allow_finalization.wait(5):
                raise AssertionError("Finalization test did not release its barrier")
        return super()._finalize(task, control)


class LateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lume-lifecycle-")
        self.root = Path(self.temp.name)
        self.manager = FinalizationGateManager(self.root / "data", self.root / "files", transport=FixtureTransport())

    def tearDown(self):
        self.manager.allow_finalization.set()
        self.manager.close()
        self.temp.cleanup()

    def enter_finalization(self):
        identity = self.manager.add("https://fixture.invalid/file", self.root / "files", "file.bin", parts=1)
        self.assertTrue(self.manager.finalization_entered.wait(5), "Transfer did not reach finalization")
        self.assertEqual(self.manager.list_tasks()[0]["status"], "verifying")
        return identity

    def test_pause_at_finalization_preserves_state_and_resume_publishes_exact_file(self):
        identity = self.enter_finalization()
        self.manager.pause(identity)
        self.manager.allow_finalization.set()
        settled = self.manager.wait(identity, 5)
        self.assertEqual(settled["status"], "paused")
        self.assertFalse((self.root / "files" / "file.bin").exists())
        self.manager.resume(identity)
        completed = self.manager.wait(identity, 5)
        self.assertEqual(completed["status"], "completed", completed["error"])
        self.assertEqual((self.root / "files" / "file.bin").read_bytes(), PAYLOAD)
        self.assertEqual(completed["sha256"], hashlib.sha256(PAYLOAD).hexdigest())

    def test_cancel_at_finalization_is_retained_after_cleanup_and_restart(self):
        identity = self.enter_finalization()
        self.manager.cancel(identity)
        self.manager.allow_finalization.set()
        self.assertEqual(self.manager.wait(identity, 5)["status"], "cancelled")
        self.manager.close()
        self.manager = FinalizationGateManager(self.root / "data", self.root / "files", transport=FixtureTransport())
        self.assertEqual(self.manager.list_tasks()[0]["status"], "cancelled")
        self.assertFalse((self.root / "files" / "file.bin").exists())

    def test_file_created_after_transfer_is_never_overwritten_at_publication(self):
        identity = self.enter_finalization()
        destination = self.root / "files" / "file.bin"
        destination.write_bytes(b"User-owned file created during transfer")
        self.manager.allow_finalization.set()
        settled = self.manager.wait(identity, 5)
        self.assertEqual(settled["status"], "failed")
        self.assertEqual(destination.read_bytes(), b"User-owned file created during transfer")

    def test_published_file_is_reconciled_after_final_sqlite_commit_failure(self):
        self.manager.allow_finalization.set()
        original_save = self.manager.store.save
        failed = threading.Event()

        def fail_final_commit_once(task):
            if task["status"] == "completed" and not failed.is_set():
                failed.set()
                raise OSError("Simulated failure before final SQLite completion commit")
            return original_save(task)

        self.manager.store.save = fail_final_commit_once
        identity = self.manager.add("https://fixture.invalid/file", self.root / "files", "file.bin", parts=1)
        self.manager.wait(identity, 5)
        self.assertTrue(failed.is_set())
        persisted = self.manager.store.load()[0]
        self.assertNotEqual(persisted["status"], "completed")
        self.assertEqual(persisted["publication"], {"size": len(PAYLOAD), "sha256": hashlib.sha256(PAYLOAD).hexdigest()})
        destination = self.root / "files" / "file.bin"
        before = destination.stat().st_mtime_ns
        self.manager.close()
        network = NoNetworkTransport()
        self.manager = FinalizationGateManager(self.root / "data", self.root / "files", transport=network)
        self.manager.allow_finalization.set()
        self.manager.resume(identity)
        completed = self.manager.wait(identity, 5)
        self.assertEqual(completed["status"], "completed", completed["error"])
        self.assertEqual(network.calls, 0)
        self.assertEqual(destination.stat().st_mtime_ns, before)
        self.assertEqual(destination.read_bytes(), PAYLOAD)
        self.assertEqual(self.manager.store.load()[0]["status"], "completed")

    def test_directory_cleanup_failure_keeps_ownership_for_a_later_retry(self):
        identity = self.manager.add("https://fixture.invalid/later", self.root / "files", "later.bin", scheduled_at=time.time() + 3600)
        original_rmdir = Path.rmdir

        def deny_work_directory(path):
            if path.parent == self.root / "files" and path.name.startswith(".lume-"):
                raise PermissionError("Temporary directory is locked by another application")
            return original_rmdir(path)

        with patch.object(Path, "rmdir", deny_work_directory), self.assertRaises(OSError):
            self.manager.remove(identity)
        self.assertEqual([item["id"] for item in self.manager.list_tasks()], [identity])
        self.manager.remove(identity)
        self.assertEqual(self.manager.list_tasks(), [])
        self.assertEqual(list((self.root / "files").glob(".lume-*")), [])


class HTTPDateValidatorTests(unittest.TestCase):
    MODIFIED = "Tue, 20 Oct 2026 12:00:00 GMT"

    def test_recent_or_undated_last_modified_does_not_enable_resume(self):
        for date in [None, "Tue, 20 Oct 2026 12:00:00 GMT", "Tue, 20 Oct 2026 12:00:59 GMT", "bad-date"]:
            fields = {"Last-Modified": self.MODIFIED}
            if date is not None:
                fields["Date"] = date
            with self.subTest(date=date):
                self.assertIsNone(validator(HeaderResponse(fields)))

    def test_qualified_last_modified_or_strong_etag_can_identify_resume(self):
        self.assertEqual(validator(HeaderResponse({"Last-Modified": self.MODIFIED, "Date": "Tue, 20 Oct 2026 12:01:00 GMT"})), self.MODIFIED)
        self.assertEqual(validator(HeaderResponse({"ETag": '"strong-v1"', "Last-Modified": self.MODIFIED})), '"strong-v1"')

    def test_weak_etag_must_not_be_replaced_with_an_http_date_for_if_range(self):
        self.assertIsNone(validator(HeaderResponse({"ETag": 'W/"weak-v1"', "Last-Modified": self.MODIFIED, "Date": "Tue, 20 Oct 2026 12:05:00 GMT"})))


if __name__ == "__main__":
    unittest.main()
