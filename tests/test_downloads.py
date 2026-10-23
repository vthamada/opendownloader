from __future__ import annotations

import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lume.core import Manager, safe_filename
from lume.network import DownloadError, Transport, validate_url

DATA = bytes(range(256)) * 8192
DIGEST = hashlib.sha256(DATA).hexdigest()


class FixtureServer:
    def __init__(self, directory: Path, tls=False):
        self.requests = []
        self.etag = '"fixture-v1"'
        self.content_type = None
        self.resources = {}
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                with outer.lock:
                    outer.requests.append((self.path, dict(self.headers)))
                    outer.active += 1
                    outer.peak = max(outer.peak, outer.active)
                try:
                    self.serve()
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
                    pass
                finally:
                    with outer.lock:
                        outer.active -= 1

            def serve(self):
                if self.path in ("/redirect", "/loop"):
                    self.send_response(302)
                    self.send_header("Location", "/file" if self.path == "/redirect" else "/loop")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/downgrade":
                    self.send_response(302)
                    self.send_header("Location", "http://localhost:1/file")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                resource = outer.resources.get(self.path)
                content = resource[1] if resource else (b"" if self.path == "/empty" else DATA)
                range_header = self.headers.get("Range")
                use_range = range_header and self.path not in ("/no-range", "/unknown")
                if self.path == "/lying" and range_header != "bytes=0-0":
                    use_range = False
                if use_range and not content:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                first, last = 0, len(content) - 1
                if use_range:
                    first, last = map(int, range_header.removeprefix("bytes=").split("-"))
                    last = min(last, len(content) - 1)
                    if first > last:
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    header_last = last + 1 if self.path == "/bad-range" and range_header != "bytes=0-0" else last
                    self.send_header("Content-Range", f"bytes {first}-{header_last}/{len(content)}")
                else:
                    self.send_response(200)
                body = content[first:last + 1]
                if resource or outer.content_type:
                    self.send_header("Content-Type", resource[0] if resource else outer.content_type)
                if self.path != "/unknown":
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", outer.etag)
                self.send_header("Connection", "close")
                self.end_headers()
                if self.path == "/drop" and range_header != "bytes=0-0":
                    self.wfile.write(body[:65536])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                for index in range(0, len(body), 8192):
                    self.wfile.write(body[index:index + 8192])
                    self.wfile.flush()
                    if self.path in ("/slow", "/changed"):
                        time.sleep(.008)
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.certificate = None
        if tls:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
            key = rsa.generate_private_key(65537, 2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(minutes=1))
                    .not_valid_after(now + datetime.timedelta(days=1))
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
                    .sign(key, hashes.SHA256()))
            cert_path = directory / "localhost.pem"
            key_path = directory / "localhost.key"
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                 serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
            self.certificate = cert_path
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.url = f"https://localhost:{self.server.server_port}" if tls else f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lume-test-")
        self.root = Path(self.temp.name)
        self.server = FixtureServer(self.root)
        self.manager = Manager(self.root / "data", self.root / "files")

    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.temp.cleanup()

    def add(self, path="/file", **options):
        return self.manager.add(self.server.url + path, self.root / "files", filename="fixture.bin", **options)

    def completed(self, task_id):
        result = self.manager.wait(task_id, 20)
        self.assertEqual(result["status"], "completed", result["error"])
        self.assertEqual((self.root / "files" / "fixture.bin").read_bytes(), DATA)
        self.assertEqual(result["sha256"], DIGEST)
        return result

    def progress(self, task_id):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            row = next(x for x in self.manager.list_tasks() if x["id"] == task_id)
            if row["downloaded"] >= 65536:
                return
            if row["status"] == "failed":
                self.fail(row["error"])
            time.sleep(.01)
        self.fail("Nenhum progresso dentro do prazo")

    def test_parallel_download_exact_content_and_sha(self):
        self.completed(self.add(parts=4, sha256=DIGEST))
        ranges = [h.get("Range") for _, h in self.server.requests if h.get("Range") != "bytes=0-0"]
        self.assertEqual(len(ranges), 4)
        self.assertTrue(all(ranges))
        self.assertFalse(list((self.root / "files").glob(".lume-*")))

    def test_no_range_falls_back_to_one_connection(self):
        result = self.completed(self.add("/no-range", parts=8))
        self.assertEqual(len(result["parts_state"]), 1)

    def test_lying_range_server_safe_full_fallback(self):
        self.completed(self.add("/lying", parts=4))
        self.assertTrue(any("Range" not in h for _, h in self.server.requests))

    def test_bad_content_range_never_publishes(self):
        result = self.manager.wait(self.add("/bad-range"), 10)
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.root / "files" / "fixture.bin").exists())

    def test_resume_after_real_manager_restart(self):
        task_id = self.add("/slow", parts=4)
        self.progress(task_id)
        self.manager.pause(task_id)
        paused = self.manager.wait(task_id)
        saved = paused["downloaded"]
        self.assertGreater(saved, 0)
        self.manager.close()
        self.manager = Manager(self.root / "data", self.root / "files")
        self.manager.resume(task_id)
        self.completed(task_id)
        self.assertTrue(any(h.get("If-Range") == '"fixture-v1"' for _, h in self.server.requests))

    def test_resource_change_refuses_resume(self):
        task_id = self.add("/changed", parts=2)
        self.progress(task_id)
        self.manager.pause(task_id)
        self.manager.wait(task_id)
        self.server.etag = '"fixture-v2"'
        self.manager.resume(task_id)
        row = self.manager.wait(task_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("mudou", row["error"])
        self.assertFalse((self.root / "files" / "fixture.bin").exists())
        self.manager.restart(task_id)
        self.completed(task_id)

    def test_wrong_expected_hash_never_publishes(self):
        row = self.manager.wait(self.add(sha256="0" * 64), 15)
        self.assertEqual(row["status"], "failed")
        self.assertIn("SHA-256", row["error"])
        self.assertFalse((self.root / "files" / "fixture.bin").exists())

    def test_preexisting_destination_preserved(self):
        directory = self.root / "files"
        directory.mkdir()
        (directory / "fixture.bin").write_bytes(b"existing")
        with self.assertRaises(DownloadError):
            self.add()
        self.assertEqual((directory / "fixture.bin").read_bytes(), b"existing")

    def test_destination_created_during_download_not_overwritten(self):
        task_id = self.add("/slow", parts=2)
        self.progress(task_id)
        (self.root / "files" / "fixture.bin").write_bytes(b"external")
        self.assertEqual(self.manager.wait(task_id)["status"], "failed")
        self.assertEqual((self.root / "files" / "fixture.bin").read_bytes(), b"external")

    def test_cancel_waits_cleanup_and_removes_only_owned_parts(self):
        task_id = self.add("/slow")
        self.progress(task_id)
        self.manager.cancel(task_id)
        self.assertEqual(self.manager.wait(task_id)["status"], "cancelled")
        self.manager.remove(task_id)
        self.assertFalse(self.manager.list_tasks())
        self.assertFalse(list((self.root / "files").glob(".lume-*")))

    def test_unowned_file_prevents_recursive_cleanup(self):
        task_id = self.add("/slow")
        self.progress(task_id)
        self.manager.cancel(task_id)
        self.manager.wait(task_id)
        work = self.root / "files" / (".lume-" + task_id)
        (work / "external.txt").write_text("keep")
        with self.assertRaises(DownloadError):
            self.manager.remove(task_id)
        self.assertTrue((work / "owner.json").exists())
        self.assertEqual((work / "external.txt").read_text(), "keep")

    def test_scheduler_does_not_start_early(self):
        task_id = self.add(scheduled_at=time.time() + .7)
        time.sleep(.2)
        self.assertFalse(self.server.requests)
        self.completed(task_id)

    def test_redirect_and_loop_limit(self):
        self.completed(self.add("/redirect"))
        loop_id = self.manager.add(self.server.url + "/loop", self.root / "files", "loop.bin")
        self.assertEqual(self.manager.wait(loop_id)["status"], "failed")

    def test_truncated_response_preserves_only_partial(self):
        result = self.manager.wait(self.add("/drop"))
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.root / "files" / "fixture.bin").exists())

    def test_unknown_length_and_empty(self):
        self.completed(self.add("/unknown", parts=1))
        empty = self.manager.add(self.server.url + "/empty", self.root / "files", "empty.bin")
        self.assertEqual(self.manager.wait(empty)["status"], "completed")
        self.assertEqual((self.root / "files" / "empty.bin").stat().st_size, 0)

    def test_disk_capacity_failure_preserves_no_final(self):
        with patch("lume.core.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
            row = self.manager.wait(self.add())
        self.assertEqual(row["status"], "failed")
        self.assertIn("Espaço", row["error"])

    def test_speed_limit_and_close_during_transfer(self):
        self.manager.set_options(max_active=1, speed_limit_kib=128)
        task_id = self.add("/slow", parts=1)
        time.sleep(1.2)
        rows = self.manager.list_tasks()
        self.assertLessEqual(rows[0]["downloaded"], 196608)
        self.manager.close()
        self.manager = Manager(self.root / "data", self.root / "files")
        self.assertEqual(self.manager.list_tasks()[0]["status"], "paused")
        self.assertEqual(self.manager.get_options()["speed_limit_kib"], 128)

    def test_browser_inbox_consumed_once(self):
        from lume.browser import enqueue_link
        request_id = enqueue_link(self.root / "data", self.server.url + "/file")
        inbox = self.root / "data" / "inbox" / (request_id + ".json")
        until = time.monotonic() + 3
        while not self.manager.list_requests() and time.monotonic() < until:
            time.sleep(.02)
        self.assertEqual(self.server.requests, [])
        self.manager.confirm_request(request_id.replace('-', ''), self.root / 'files')
        row = self.manager.list_tasks()[0]
        self.assertEqual(self.manager.wait(row["id"])["status"], "completed")
        self.assertFalse(inbox.exists())
        # Simulate a crash after import/commit and before acknowledging the claimed file.
        inbox.with_suffix(".processing").write_text(json.dumps({"url": self.server.url + "/file"}))
        time.sleep(.4)
        self.assertEqual(len(self.manager.list_tasks()), 1)
        self.assertFalse(inbox.with_suffix(".processing").exists())

    def test_single_instance_lock(self):
        with self.assertRaises(RuntimeError):
            Manager(self.root / "data", self.root / "files")

    def test_invalid_inbox_does_not_stop_following_download(self):
        inbox = self.root / "data" / "inbox"
        (inbox / ("c" * 32 + ".json")).write_text('{"url":17}')
        time.sleep(.3)
        from lume.browser import enqueue_link
        identity = enqueue_link(self.root / "data", self.server.url + "/file").replace('-', '')
        until = time.monotonic() + 5
        while not any(r['id'] == identity for r in self.manager.list_requests()) and time.monotonic() < until:
            time.sleep(.02)
        self.assertTrue(any(r['error'] for r in self.manager.list_requests()))
        self.manager.confirm_request(identity, self.root / 'files')
        task = self.manager.list_tasks()[0]
        self.assertEqual(self.manager.wait(task["id"])["status"], "completed")
        self.assertTrue(list(inbox.glob("*.rejected-*")))

    def test_precommit_inbox_claim_recovers_owner_only_directory(self):
        request_id = "d" * 32
        work = self.root / "files" / (".lume-" + request_id)
        work.mkdir(parents=True)
        (work / "owner.json").write_text(json.dumps({"app": "lume-downloads", "id": request_id}))
        claim = self.root / "data" / "inbox" / (request_id + ".processing")
        claim.write_text(json.dumps({"url": self.server.url + "/file"}))
        until = time.monotonic() + 5
        while not self.manager.list_requests() and time.monotonic() < until:
            time.sleep(.02)
        self.manager.confirm_request(request_id, self.root / 'files')
        task = self.manager.list_tasks()[0]
        self.assertEqual(task["id"], request_id)
        self.assertEqual(self.manager.wait(task["id"])["status"], "completed")
        self.assertFalse(claim.exists())

    def test_paths_and_urls_rejected(self):
        for name in ("../escape", "C:\\bad", "NUL.txt", "name.", "a:b", "x/evil"):
            with self.subTest(name=name), self.assertRaises(DownloadError):
                safe_filename(name)
        for url in ("file:///C:/data", "javascript:alert(1)", "https://user:pass@example.com/", "http://example.com/\r\nx"):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)


class TLSTests(unittest.TestCase):
    def test_tls_trust_hostname_and_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="lume-tls-") as temp:
            root = Path(temp)
            server = FixtureServer(root, tls=True)
            managers = []
            try:
                trusted = ssl.create_default_context(cafile=str(server.certificate))
                manager = Manager(root / "trusted", root / "files", transport=Transport(trusted))
                managers.append(manager)
                task_id = manager.add(server.url + "/file", root / "files", "valid.bin")
                self.assertEqual(manager.wait(task_id, 20)["status"], "completed")
                self.assertEqual(hashlib.sha256((root / "files" / "valid.bin").read_bytes()).hexdigest(), DIGEST)
                bad_name = manager.add(server.url.replace("localhost", "127.0.0.1") + "/file", root / "files", "bad-name.bin")
                result = manager.wait(bad_name)
                self.assertEqual(result["status"], "failed")
                self.assertIn("TLS", result["error"])
                default = Manager(root / "untrusted", root / "files")
                managers.append(default)
                bad_chain = default.add(server.url + "/file", root / "files", "bad-chain.bin")
                self.assertEqual(default.wait(bad_chain)["status"], "failed")
                downgrade = manager.add(server.url + "/downgrade", root / "files", "downgrade.bin")
                self.assertEqual(manager.wait(downgrade)["status"], "failed")
                self.assertFalse((root / "files" / "bad-chain.bin").exists())
                self.assertFalse((root / "files" / "bad-name.bin").exists())
            finally:
                for manager in managers:
                    manager.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
