"""Real HTTP framing, pooling ownership and cancellation regressions."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import socket
import ssl
import tempfile
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from lume.network import Control, DownloadError, Interrupted, Transport


BODY = b"pool-fixture" * 1024


class PersistentServer:
    def __init__(self, *, connect_delay=0, body=BODY, tls_context=None):
        self.requests = []
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.connect_delay = connect_delay
        self.body = body
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                # Optional controlled server-side per-connection setup cost.
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if outer.connect_delay:
                    time.sleep(outer.connect_delay)

            def log_message(self, *_):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, BrokenPipeError):
                    # Discarding an unread response intentionally resets TCP.
                    pass

            def finish(self):
                try:
                    super().finish()
                finally:
                    outer.finished.set()

            def do_GET(self):
                body = outer.body
                with outer.lock:
                    outer.requests.append((self.path, self.client_address[1]))
                    outer.active += 1
                    outer.peak = max(outer.peak, outer.active)
                    outer.started.set()
                try:
                    if self.path == "/hold":
                        outer.release.wait(5)
                    self.send_response(200)
                    if self.path in ("/chunked", "/bad-chunk"):
                        self.send_header("Transfer-Encoding", "chunked")
                    else:
                        self.send_header("Content-Length", str(len(body)))
                    if self.path in ("/close", "/truncated"):
                        self.send_header("Connection", "close")
                    self.end_headers()
                    if self.path == "/chunked":
                        self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
                    elif self.path == "/bad-chunk":
                        self.wfile.write(b"oops\r\n")
                        self.close_connection = True
                    elif self.path == "/truncated":
                        self.wfile.write(body[:20])
                        self.close_connection = True
                    else:
                        self.wfile.write(body)
                    self.wfile.flush()
                    if self.path == "/idle-close":
                        self.close_connection = True
                except (OSError, BrokenPipeError):
                    self.close_connection = True
                finally:
                    with outer.lock:
                        outer.active -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        if tls_context:
            self.server.socket = tls_context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"https://localhost:{self.server.server_port}" if tls_context else f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class ConnectionPoolTests(unittest.TestCase):
    def setUp(self):
        self.server = PersistentServer()
        self.transport = Transport(timeout=2)

    def tearDown(self):
        self.transport.close()
        self.server.close()

    def get(self, path="/file", *, control=None, transport=None):
        with (transport or self.transport).open(self.server.url + path, {}, control or Control()) as owned:
            return owned.response.read()

    def test_complete_bodies_reuse_tcp_connection(self):
        for _ in range(3):
            self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 1)

    def test_partial_body_is_not_reused(self):
        with self.transport.open(self.server.url + "/file", {}, Control()) as owned:
            self.assertEqual(owned.response.read(5), BODY[:5])
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_consumer_validation_failure_discards_complete_body(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            with self.transport.open(self.server.url + "/file", {}, Control()) as owned:
                self.assertEqual(owned.response.read(), BODY)
                raise ValueError("identity changed")
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_chunked_completion_reuses_connection(self):
        self.assertEqual(self.get("/chunked"), BODY)
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 1)

    def test_malformed_chunk_never_reenters_pool(self):
        with self.assertRaises(http.client.IncompleteRead):
            self.get("/bad-chunk")
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_truncated_body_never_reenters_pool(self):
        with self.assertRaises(http.client.IncompleteRead):
            self.get("/truncated")
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_server_close_header_disables_reuse(self):
        self.assertEqual(self.get("/close"), BODY)
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_server_closed_idle_socket_is_not_reused(self):
        self.assertEqual(self.get("/idle-close"), BODY)
        self.assertTrue(self.server.finished.wait(2))
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_verified_tls_complete_responses_reuse_connection(self):
        from test_downloads import FixtureServer
        with tempfile.TemporaryDirectory(prefix="lume-pool-tls-") as directory:
            # Reuse the existing TLS fixture's local certificate producer.
            certificate = FixtureServer(Path(directory), tls=True)
            try:
                secure = PersistentServer(tls_context=certificate.server.socket.context)
                transport = Transport(ssl.create_default_context(cafile=str(certificate.certificate)))
                try:
                    for _ in range(3):
                        with transport.open(secure.url + "/file", {}, Control()) as owned:
                            self.assertEqual(owned.response.read(), BODY)
                    self.assertEqual(len({port for _, port in secure.requests}), 1)
                finally:
                    transport.close()
                    secure.close()
            finally:
                certificate.close()

    def test_readinto_completion_reuses_connection(self):
        with self.transport.open(self.server.url + "/file", {}, Control()) as owned:
            buffer = bytearray(len(BODY))
            self.assertEqual(owned.response.readinto(buffer), len(BODY))
            self.assertEqual(buffer, BODY)
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 1)

    def test_manually_closed_incomplete_response_is_not_reused(self):
        with self.transport.open(self.server.url + "/file", {}, Control()) as owned:
            owned.response.close()
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_old_control_cannot_cancel_reassigned_connection(self):
        old = Control()
        self.assertEqual(self.get(control=old), BODY)
        current = Control()
        with self.transport.open(self.server.url + "/file", {}, current) as owned:
            old.interrupt()
            self.assertEqual(owned.response.read(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 1)

    def test_cancelled_complete_body_is_not_reused(self):
        control = Control()
        with self.transport.open(self.server.url + "/file", {}, control) as owned:
            self.assertEqual(owned.response.read(), BODY)
            control.interrupt()
        self.assertEqual(self.get(), BODY)
        self.assertEqual(len({port for _, port in self.server.requests}), 2)

    def test_idle_expiration_discards_connection(self):
        transport = Transport(idle_timeout=.01)
        try:
            self.assertEqual(self.get(transport=transport), BODY)
            time.sleep(.02)
            self.assertEqual(self.get(transport=transport), BODY)
            self.assertEqual(len({port for _, port in self.server.requests}), 2)
        finally:
            transport.close()

    def test_connection_origins_are_separate(self):
        other = PersistentServer()
        try:
            self.assertEqual(self.get(), BODY)
            with self.transport.open(other.url + "/file", {}, Control()) as owned:
                self.assertEqual(owned.response.read(), BODY)
            self.assertEqual(len(other.requests), 1)
            self.assertEqual(self.get(), BODY)
            self.assertEqual(len({port for _, port in self.server.requests}), 1)
        finally:
            other.close()

    def test_keep_alive_can_be_disabled_for_comparison(self):
        transport = Transport(keep_alive=False)
        try:
            self.assertEqual(self.get(transport=transport), BODY)
            self.assertEqual(self.get(transport=transport), BODY)
            self.assertEqual(len({port for _, port in self.server.requests}), 2)
        finally:
            transport.close()

    def test_close_while_active_releases_but_does_not_repool(self):
        with self.transport.open(self.server.url + "/file", {}, Control()) as owned:
            self.transport.close()
            self.assertEqual(owned.response.read(), BODY)
        self.transport.close()
        with self.assertRaises(DownloadError) as caught:
            self.get()
        self.assertEqual(caught.exception.code, "transport_closed")
        self.assertFalse(self.transport.idle)
        self.assertFalse(self.transport.host_slots)

    def test_eight_connections_allowed_and_ninth_waits_until_release(self):
        transport = Transport(max_connections_per_host=8)
        try:
            with ThreadPoolExecutor(max_workers=9) as pool:
                jobs = [pool.submit(self.get, "/hold", transport=transport) for _ in range(8)]
                until = time.monotonic() + 3
                while time.monotonic() < until and self.server.peak < 8:
                    time.sleep(.005)
                self.assertEqual(self.server.peak, 8)
                ninth = pool.submit(self.get, transport=transport)
                time.sleep(.03)
                self.assertFalse(ninth.done())
                self.server.release.set()
                self.assertTrue(all(job.result(3) == BODY for job in jobs))
                self.assertEqual(ninth.result(3), BODY)
                self.assertLessEqual(self.server.peak, 8)
        finally:
            self.server.release.set()
            transport.close()

    def test_waiting_cancel_drops_host_capacity(self):
        transport = Transport(max_connections_per_host=1)
        waiting = Control()
        try:
            with self.transport.open(self.server.url + "/file", {}, Control()) as first:
                # Use a separate transport's one occupied lease.
                first.response.read()
            with transport.open(self.server.url + "/file", {}, Control()) as owned:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    job = pool.submit(self.get, control=waiting, transport=transport)
                    waiting.interrupt()
                    with self.assertRaises(Interrupted):
                        job.result(2)
                owned.response.read()
            self.assertFalse(transport.host_slots)
        finally:
            transport.close()

    def test_late_connect_after_cancellation_is_closed(self):
        control = Control()
        connected = threading.Event()
        finish = threading.Event()
        original = socket.create_connection

        def delayed(*args, **kwargs):
            sock = original(*args, **kwargs)
            connected.set()
            finish.wait(2)
            return sock

        with patch("socket.create_connection", delayed):
            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(self.get, control=control)
                self.assertTrue(connected.wait(2))
                control.interrupt()
                finish.set()
                with self.assertRaises(Interrupted):
                    job.result(3)
        self.assertFalse(self.server.requests)
        self.assertFalse(control.connections)
        self.assertFalse(self.transport.host_slots)


if __name__ == "__main__":
    unittest.main()
