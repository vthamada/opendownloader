"""Recovery through real HTTP boundaries, without replaying delivered bodies."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import socket
import threading
import time
import unittest
from unittest.mock import patch

from lume.network import (Control, DownloadError, Interrupted, RetryPolicy, Transport,
                          content_disposition_filename)


class RecoveryServer:
    def __init__(self, statuses=(503, 200), *, retry_after=None, disconnect=False,
                 truncate=False):
        self.requests = []
        self.started = threading.Event()
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                with outer.lock:
                    attempt = len(outer.requests)
                    outer.requests.append((time.monotonic(), dict(self.headers)))
                    outer.started.set()
                if disconnect and attempt == 0:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status = statuses[min(attempt, len(statuses) - 1)]
                self.send_response(status)
                self.send_header("Content-Length", "4")
                self.send_header("Connection", "close")
                if retry_after is not None:
                    self.send_header("Retry-After", retry_after)
                self.end_headers()
                try:
                    self.wfile.write(b"ok" if truncate else b"okay")
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/resource"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.servers = []
        self.transports = []

    def tearDown(self):
        for transport in self.transports:
            transport.close()
        for server in self.servers:
            server.close()

    def server(self, *args, **kwargs):
        server = RecoveryServer(*args, **kwargs)
        self.servers.append(server)
        return server

    def transport(self, policy=None):
        transport = Transport(timeout=1, retry_policy=policy or RetryPolicy(base_delay=0, max_delay=0))
        self.transports.append(transport)
        return transport

    def get(self, server, transport, control=None):
        with transport.open(server.url, {"Range": "bytes=0-3", "If-Range": '"v1"'}, control or Control()) as owned:
            return owned.response.status, owned.response.read()

    def test_transient_http_preserves_range_and_validator_before_delivery(self):
        server = self.server((503, 429, 200), retry_after="0")
        transport = self.transport()
        self.assertEqual(self.get(server, transport), (200, b"okay"))
        self.assertEqual(len(server.requests), 3)
        self.assertTrue(all(headers["Range"] == "bytes=0-3" and headers["If-Range"] == '"v1"'
                            for _, headers in server.requests))
        self.assertFalse(transport.host_slots)

    def test_budget_exhaustion_delivers_original_http_error(self):
        server = self.server((503,))
        self.assertEqual(self.get(server, self.transport()), (503, b"okay"))
        self.assertEqual(len(server.requests), 3)

    def test_permanent_http_failure_is_not_retried(self):
        server = self.server((403,))
        self.assertEqual(self.get(server, self.transport()), (403, b"okay"))
        self.assertEqual(len(server.requests), 1)

    def test_disconnected_before_headers_can_recover(self):
        server = self.server((200,), disconnect=True)
        self.assertEqual(self.get(server, self.transport()), (200, b"okay"))
        self.assertEqual(len(server.requests), 2)

    def test_delivered_truncated_body_never_replays(self):
        server = self.server((200,), truncate=True)
        with self.assertRaises(http.client.IncompleteRead):
            self.get(server, self.transport())
        self.assertEqual(len(server.requests), 1)

    def test_retry_after_is_minimum_wait(self):
        server = self.server((429, 200), retry_after="1")
        self.assertEqual(self.get(server, self.transport()), (200, b"okay"))
        self.assertGreaterEqual(server.requests[1][0] - server.requests[0][0], .99)

    def test_large_retry_after_does_not_retry_early(self):
        server = self.server((429, 200), retry_after="3600")
        self.assertEqual(self.get(server, self.transport()), (429, b"okay"))
        self.assertEqual(len(server.requests), 1)

    def test_cancel_during_backoff_releases_lease_without_new_request(self):
        server = self.server((503, 200), retry_after="10")
        transport = self.transport()
        control = Control()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.get, server, transport, control)
            self.assertTrue(server.started.wait(2))
            control.interrupt()
            with self.assertRaises(Interrupted):
                job.result(1)
        self.assertEqual(len(server.requests), 1)
        self.assertFalse(transport.host_slots)
        self.assertFalse(control.connections)

    def test_transport_close_during_backoff_finishes_promptly(self):
        server = self.server((503, 200), retry_after="10")
        transport = self.transport()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.get, server, transport)
            self.assertTrue(server.started.wait(2))
            transport.close()
            with self.assertRaises(DownloadError) as caught:
                job.result(1)
        self.assertEqual(caught.exception.code, "transport_closed")
        self.assertEqual(len(server.requests), 1)
        self.assertFalse(transport.host_slots)

    def test_single_attempt_policy_disables_retry(self):
        server = self.server((503, 200))
        self.assertEqual(self.get(server, self.transport(RetryPolicy(max_attempts=1))), (503, b"okay"))
        self.assertEqual(len(server.requests), 1)

    def test_retry_after_http_date_and_jitter(self):
        policy = RetryPolicy()
        with patch("lume.network.time.time", return_value=1000), patch("lume.network.random.uniform", return_value=.2):
            self.assertEqual(policy.delay(0, formatdate(1005, usegmt=True)), 5)
            self.assertEqual(policy.delay(0, "invalid"), .2)
            self.assertEqual(policy.delay(0, "0" * 10000), .2)
            self.assertIsNone(policy.delay(0, "9" * 10000))


class DispositionTests(unittest.TestCase):
    def test_quoted_delimiters_and_escapes(self):
        self.assertEqual(content_disposition_filename('attachment; filename="episode; final.mp4"'), "episode; final.mp4")
        self.assertEqual(content_disposition_filename(r'attachment; filename="a\"b.mp4"'), 'a"b.mp4')

    def test_extended_utf8_takes_precedence_in_either_order(self):
        for value in ("attachment; filename=legacy.mp4; filename*=UTF-8'pt'epis%C3%B3dio.mp4",
                      "attachment; filename*=UTF-8''epis%C3%B3dio.mp4; filename=legacy.mp4"):
            self.assertEqual(content_disposition_filename(value), "episódio.mp4")
        self.assertEqual(content_disposition_filename("attachment; filename*=ISO-8859-1''caf%E9.mp4"), "café.mp4")

    def test_invalid_extended_uses_legacy_fallback(self):
        for extended in ("UTF-8''%GG.mp4", "UTF-8''%FF.mp4", "unknown''file.mp4"):
            self.assertEqual(content_disposition_filename(f'attachment; filename=legacy.mp4; filename*={extended}'), "legacy.mp4")

    def test_malformed_or_ambiguous_headers_have_no_name(self):
        for value in (None, "", 'attachment; filename="unclosed', "attachment; filename=a; FILENAME=b",
                      "attachment; filename=a\r\nInjected: true", "attachment; filename=a; invalid",
                      "attachment; filename*=UTF-8''%GG", "attachment; filename*=UTF-8''%FF"):
            self.assertIsNone(content_disposition_filename(value), value)

    def test_parser_does_not_sanitize_or_reinterpret_a_path(self):
        self.assertEqual(content_disposition_filename('attachment; filename="../episode.mp4"'), "../episode.mp4")


if __name__ == "__main__":
    unittest.main()
