"""Persistent queue editing, source recovery and server-named real downloads."""
import hashlib
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lume.core import Manager
from lume.network import DownloadError
from lume.storage import Store
from test_acceleration import BalancedServer
from test_lifecycle import NoNetworkTransport


class QueueEditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lume-queue-edit-")
        self.root = Path(self.temporary.name)
        self.transport = NoNetworkTransport()
        self.manager = Manager(self.root / "data", self.root / "files", transport=self.transport)

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def add(self, filename="first.bin"):
        return self.manager.add("https://example.test/" + filename, self.root / "files", filename,
                                start=False, duplicate_policy="copy")

    def test_move_persists_order_without_starting_held_tasks(self):
        first, second, third = self.add(), self.add("second.bin"), self.add("third.bin")
        self.manager.move(third, -1)
        self.manager.move(third, -1)
        self.manager.move(third, -1)  # Already first: unchanged.
        self.manager.move(first, 1)
        expected = [third, second, first]
        self.assertEqual([row["id"] for row in self.manager.list_tasks()], expected)
        self.assertTrue(all(row["status"] == "held" for row in self.manager.list_tasks()))
        self.manager.close()
        self.manager = Manager(self.root / "data", self.root / "files", transport=self.transport)
        self.assertEqual([row["id"] for row in self.manager.list_tasks()], expected)
        self.assertEqual(self.transport.calls, 0)

    def test_move_failed_transaction_preserves_in_memory_and_stored_order(self):
        first, second = self.add(), self.add("second.bin")
        with patch.object(self.manager.store, "save_many", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.manager.move(second, -1)
        self.assertEqual([row["id"] for row in self.manager.list_tasks()], [first, second])
        self.assertEqual(self.manager.store.task(first)["queue_order"], 0)
        for value in (0, 2, True):
            with self.assertRaises(DownloadError):
                self.manager.move(first, value)

    def test_legacy_order_is_upgraded_in_original_insertion_order(self):
        first, second = self.add(), self.add("second.bin")
        self.manager.close()
        store = Store(self.root / "data" / "queue.sqlite3")
        rows = store.load()
        for row in rows:
            row.pop("queue_order")
        store.save_many(rows)
        store.close()
        self.manager = Manager(self.root / "data", self.root / "files", transport=self.transport)
        self.assertEqual([row["id"] for row in self.manager.list_tasks()], [first, second])
        self.assertEqual([row["queue_order"] for row in self.manager.list_tasks()], [0, 1])

    def test_replace_source_cleans_owned_partials_and_preserves_final_and_name(self):
        identity = self.add()
        task = self.manager.tasks[identity]
        work = self.root / "files" / (".lume-" + identity)
        (work / "part-0.bin").write_bytes(b"old partial")
        (self.root / "files" / "first.bin").write_bytes(b"existing final")
        self.manager.replace_source(identity, "https://example.test/new-source.bin")
        changed = self.manager.wait(identity)
        self.assertEqual(changed["status"], "held")
        self.assertEqual(changed["url"], "https://example.test/new-source.bin")
        self.assertEqual(changed["filename"], "first (1).bin")
        self.assertTrue(changed["filename_selected"])
        self.assertEqual((self.root / "files" / "first.bin").read_bytes(), b"existing final")
        self.assertEqual({path.name for path in work.iterdir()}, {"owner.json"})
        self.assertEqual(changed["parts_state"], [])
        self.assertIsNone(changed["identity"])
        self.assertIsNone(changed["media"])
        self.assertEqual(self.transport.calls, 0)

    def test_replace_source_validates_before_cleanup_and_refuses_publication(self):
        identity = self.add()
        work = self.root / "files" / (".lume-" + identity)
        partial = work / "part-0.bin"
        partial.write_bytes(b"preserve")
        with self.assertRaises(DownloadError):
            self.manager.replace_source(identity, "file:///invalid")
        self.assertEqual(partial.read_bytes(), b"preserve")
        self.manager._change(self.manager.tasks[identity], publication={"size": 8, "sha256": "a" * 64})
        with self.assertRaises(DownloadError):
            self.manager.replace_source(identity, "https://example.test/new")
        self.assertEqual(partial.read_bytes(), b"preserve")

    def test_replace_source_refuses_foreign_partial_and_keeps_original_url(self):
        identity = self.add()
        work = self.root / "files" / (".lume-" + identity)
        (work / "user-note.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(DownloadError):
            self.manager.replace_source(identity, "https://example.test/new")
        self.assertEqual(self.manager.tasks[identity]["url"], "https://example.test/first.bin")
        self.assertEqual((work / "user-note.txt").read_text(encoding="utf-8"), "preserve")

    def test_save_many_rolls_back_every_row_on_a_database_failure(self):
        store = self.manager.store
        first, second = self.add(), self.add("second.bin")
        store.connection.execute(f"CREATE TRIGGER fail_second BEFORE UPDATE ON tasks WHEN new.id='{second}' BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_many([{**store.task(first), "queue_order": 1}, {**store.task(second), "queue_order": 0}])
        self.assertEqual(store.task(first)["queue_order"], 0)
        self.assertEqual(store.task(second)["queue_order"], 1)


class NamedServer:
    def __init__(self, disposition):
        self.data = b"server-named-content" * 65536
        self.disposition = disposition
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                first, last = map(int, self.headers["Range"][6:].split("-"))
                outer.requests.append((first, last))
                self.send_response(206)
                self.send_header("ETag", '"named-v1"')
                self.send_header("Content-Range", f"bytes {first}-{last}/{len(outer.data)}")
                self.send_header("Content-Length", str(last - first + 1))
                self.send_header("Content-Disposition", outer.disposition)
                self.end_headers()
                try:
                    self.wfile.write(outer.data[first:last + 1])
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/opaque"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class CoreDownloadEvolutionTests(unittest.TestCase):
    def named_download(self, disposition, *, selected=None, existing=None):
        with tempfile.TemporaryDirectory(prefix="lume-header-name-") as directory:
            root = Path(directory)
            server = NamedServer(disposition)
            manager = Manager(root / "data", root / "files")
            try:
                (root / "files").mkdir(exist_ok=True)
                if existing:
                    (root / "files" / existing).write_bytes(b"original")
                identity = manager.add(server.url, root / "files", selected, duplicate_policy="copy")
                result = manager.wait(identity, 10)
                self.assertEqual(result["status"], "completed", result["error"])
                self.assertEqual((root / "files" / result["filename"]).read_bytes(), server.data)
                self.assertEqual(result["sha256"], hashlib.sha256(server.data).hexdigest())
                self.assertFalse(list((root / "files").glob(".lume-*")))
                if existing:
                    self.assertEqual((root / "files" / existing).read_bytes(), b"original")
                return result
            finally:
                manager.close()
                server.close()

    def test_server_extended_name_respects_safe_basename_and_copy_conflict(self):
        result = self.named_download("attachment; filename=legacy.bin; filename*=UTF-8''..%2Fcaf%C3%A9.bin", existing="café.bin")
        self.assertEqual(result["filename"], "café (1).bin")
        self.assertEqual(result["http_filename"], "../café.bin")

    def test_selected_name_has_priority_over_server_name(self):
        result = self.named_download('attachment; filename="server.bin"', selected="chosen.bin")
        self.assertEqual(result["filename"], "chosen.bin")

    def test_reserved_server_name_keeps_url_fallback(self):
        self.assertEqual(self.named_download('attachment; filename="CON.bin"')["filename"], "opaque")

    def test_completed_direct_download_has_stage_timings(self):
        result = self.named_download('attachment; filename="timed.bin"')
        for stage in ("resolve_s", "transfer_s", "aggregate_hash_s", "publish_s", "cleanup_s"):
            self.assertGreaterEqual(result["timings"][stage], 0)

    def test_large_checkpoint_pause_restart_preserves_durable_map_and_hash(self):
        data = bytes(range(256)) * (48 * 1024 * 1024 // 256)
        with tempfile.TemporaryDirectory(prefix="lume-large-checkpoint-") as directory:
            root = Path(directory)
            server = BalancedServer(data, gate=True)
            manager = Manager(root / "data", root / "files")
            try:
                identity = manager.add(server.url, root / "files", parts=4)
                self.assertTrue(server.started.wait(5))
                self.assertTrue(server.reassigned.wait(5))
                manager.pause(identity)
                server.release.set()
                paused = manager.wait(identity, 15)
                self.assertEqual(paused["status"], "paused")
                original_map = [(part["start"], part["end"]) for part in paused["parts_state"]]
                self.assertGreater(len(original_map), 4)
                manager.close()
                manager = Manager(root / "data", root / "files")
                manager.resume(identity)
                result = manager.wait(identity, 15)
                self.assertEqual(result["status"], "completed", result["error"])
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual([(part["start"], part["end"]) for part in result["parts_state"]], original_map)
            finally:
                server.release.set()
                manager.close()
                server.close()

    def test_large_download_reduces_fsync_without_losing_content(self):
        data = b"checkpoint-cost-" * (32 * 1024 * 1024 // 16)
        with tempfile.TemporaryDirectory(prefix="lume-checkpoint-cost-") as directory:
            root = Path(directory)
            server = BalancedServer(data)
            manager = Manager(root / "data", root / "files")
            try:
                with patch("lume.core.os.fsync", wraps=os.fsync) as sync:
                    identity = manager.add(server.url, root / "files", parts=4)
                    result = manager.wait(identity, 15)
                self.assertEqual(result["status"], "completed", result["error"])
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                # 32 MiB formerly required at least 32 data fsync calls. The
                # budget includes durable final assembly as well as all parts.
                self.assertLessEqual(sync.call_count, 12)
            finally:
                manager.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
