"""Public Native Messaging protocol and file handoff, without a browser/network."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import uuid

from lume.browser import MAX_MESSAGE_BYTES, run_native_host, validate_url


def frame(value):
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def responses(data):
    result = []
    stream = io.BytesIO(data)
    while header := stream.read(4):
        length = struct.unpack("<I", header)[0]
        result.append(json.loads(stream.read(length).decode("utf-8")))
    return result


class ShortReads(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 2) if size >= 0 else 2)


class NativeHostTests(unittest.TestCase):
    def invoke(self, data, directory, stream_type=io.BytesIO):
        output = io.BytesIO()
        status = run_native_host(Path(directory), stdin=stream_type(data), stdout=output)
        return status, responses(output.getvalue())

    def test_status_is_read_only_and_never_wakes_desktop_or_creates_profile(self):
        from lume import VERSION
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'not-created'
            output = io.BytesIO()
            with patch('lume.browser.enqueue_request', side_effect=AssertionError('Status cannot enqueue')), \
                 patch('socket.socket', side_effect=AssertionError('Status cannot fetch')), \
                 patch('subprocess.Popen', side_effect=AssertionError('Status cannot start desktop')):
                status = run_native_host(profile, stdin=ShortReads(frame({'version': 1, 'command': 'status'})),
                                         stdout=output, on_queued=lambda _: self.fail('Status cannot wake desktop'))
            self.assertEqual(status, 0)
            self.assertEqual(responses(output.getvalue()), [{'ok': True, 'version': 1, 'app_version': VERSION,
                                                           'status': 'ready'}])
            self.assertFalse(profile.exists())

    def test_status_schema_is_strict_and_is_not_a_durable_download_request(self):
        from lume.browser import decode_request
        status = {'version': 1, 'command': 'status'}
        invalid = [dict(status, version=True), dict(status, version=2), dict(status, command='start'),
                   dict(status, url='https://example.org/file'), dict(status, data_dir='C:/other')]
        with self.assertRaises(ValueError):
            decode_request(json.dumps(status).encode())
        with tempfile.TemporaryDirectory() as directory:
            code, replies = self.invoke(b''.join(frame(value) for value in invalid)
                                        + frame(status) + frame({'url': 'https://example.org/file'}) + frame(status), directory)
            self.assertEqual(code, 0)
            self.assertEqual([reply['ok'] for reply in replies], [False] * len(invalid) + [True, True, True])
            self.assertEqual(len(list((Path(directory) / 'inbox').glob('*.json'))), 1)

    def test_multiple_links_are_atomic_handoffs_and_never_downloaded(self):
        urls = ["https://example.org/ação.zip?token=caller-value#fragment", "http://127.0.0.1:8012/file"]
        with tempfile.TemporaryDirectory() as directory:
            with patch("socket.socket", side_effect=AssertionError("No network in native host")), patch("subprocess.Popen", side_effect=AssertionError("No child process in native host")), patch("webbrowser.open", side_effect=AssertionError("No GUI in native host")):
                status, replies = self.invoke(b"".join(frame({"url": url}) for url in urls), directory, ShortReads)
            self.assertEqual(status, 0)
            self.assertEqual(len(replies), 2)
            self.assertNotEqual(replies[0]["id"], replies[1]["id"])
            for url, reply in zip(urls, replies):
                self.assertEqual(set(reply), {"ok", "id"})
                self.assertIs(reply["ok"], True)
                self.assertEqual(str(uuid.UUID(reply["id"])), reply["id"])
                content = json.loads((Path(directory) / "inbox" / (reply["id"] + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(content, {"url": url})
            self.assertEqual(len(list((Path(directory) / "inbox").iterdir())), 2)

    def test_versioned_media_hint_roundtrips_without_accepting_commands_or_headers(self):
        request = {"version": 2, "url": "https://example.org/video.mp4", "title": "Vídeo público", "media": True}
        with tempfile.TemporaryDirectory() as directory:
            status, replies = self.invoke(frame(request), directory)
            self.assertEqual(status, 0)
            persisted = json.loads((Path(directory)/'inbox'/(replies[0]['id']+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(persisted, request)
            invalid = [dict(request, headers={"Cookie": "private"}), dict(request, command="run"),
                       dict(request, version=True), dict(request, version=3), dict(request, media=1),
                       dict(request, title='x'*257), dict(request, title='bad\nTitle')]
            _, replies = self.invoke(b''.join(frame(value) for value in invalid), directory)
            self.assertTrue(all(reply['ok'] is False for reply in replies))

    def test_manifest_source_hint_is_versioned_strict_and_never_fetches_metadata(self):
        values = [{'version':3, 'url':'https://example.org/manifest?selected=caller',
                   'title':'Streaming', 'media':True, 'source':source} for source in ('hls','dash')]
        with tempfile.TemporaryDirectory() as directory:
            with patch('socket.socket', side_effect=AssertionError('Native ingress must not fetch manifests')):
                status, replies = self.invoke(b''.join(frame(value) for value in values), directory)
            self.assertEqual(status, 0)
            self.assertTrue(all(reply['ok'] for reply in replies))
            for value, reply in zip(values,replies):
                persisted=json.loads((Path(directory)/'inbox'/(reply['id']+'.json')).read_text(encoding='utf-8'))
                self.assertEqual(persisted,value)
            invalid=[dict(values[0],source='file'),dict(values[0],media=False),dict(values[0],headers={'Cookie':'private'}),
                     dict(values[0],source=None),dict(values[0],version=4),dict(values[0],version=2)]
            _,replies=self.invoke(b''.join(frame(value) for value in invalid),directory)
            self.assertTrue(all(not reply['ok'] for reply in replies))

    def test_invalid_requests_return_error_then_next_frame_can_succeed(self):
        invalid = [[], {}, {"url": 7}, {"url": "https://example.org", "filename": "other"}]
        with tempfile.TemporaryDirectory() as directory:
            status, replies = self.invoke(b"".join(frame(value) for value in invalid) + frame({"url": "https://example.org"}), directory)
            self.assertEqual(status, 0)
            self.assertEqual([reply["ok"] for reply in replies], [False] * len(invalid) + [True])
            self.assertEqual(len(list((Path(directory) / "inbox").glob("*.json"))), 1)

    def test_bad_json_and_invalid_utf8_are_rejected_without_losing_framing(self):
        bodies = [b"not-json", b'\xff', b'{"url":"https://a.test","url":"https://b.test"}', b'{"url":NaN}']
        with tempfile.TemporaryDirectory() as directory:
            status, replies = self.invoke(b"".join(struct.pack("<I", len(body)) + body for body in bodies), directory)
            self.assertEqual(status, 0)
            self.assertEqual(len(replies), len(bodies))
            self.assertTrue(all(reply["ok"] is False for reply in replies))
            self.assertFalse((Path(directory) / "inbox").exists())

    def test_truncated_header_or_body_is_fatal_and_never_acknowledged(self):
        for data in [b"\x03", struct.pack("<I", 40) + b"{}"]:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                status, replies = self.invoke(data, directory)
                self.assertEqual(status, 1)
                self.assertEqual(len(replies), 1)
                self.assertIs(replies[0]["ok"], False)
                self.assertFalse((Path(directory) / "inbox").exists())

    def test_oversized_header_does_not_attempt_body_allocation(self):
        class HeaderOnly(io.BytesIO):
            def read(self, size=-1):
                if self.tell() >= 4:
                    raise AssertionError("An oversized body must never be read")
                return super().read(size)
        with tempfile.TemporaryDirectory() as directory:
            status, replies = self.invoke(struct.pack("<I", MAX_MESSAGE_BYTES + 1), directory, HeaderOnly)
            self.assertEqual(status, 1)
            self.assertIs(replies[0]["ok"], False)

    def test_persistence_failure_is_not_reported_as_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("lume.browser.os.replace", side_effect=PermissionError("blocked")):
                status, replies = self.invoke(frame({"url": "https://example.org/file"}), directory)
            self.assertEqual(status, 0)
            self.assertIs(replies[0]["ok"], False)
            self.assertEqual(list((Path(directory) / "inbox").iterdir()), [])

    def test_empty_input_does_not_create_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(b"", directory), (0, []))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_desktop_wakeup_follows_persistence_and_is_not_called_for_rejected_input(self):
        with tempfile.TemporaryDirectory() as directory:
            called=[]
            def wake(data_dir):
                paths=list((data_dir/'inbox').glob('*.json'))
                self.assertEqual(len(paths),1)
                self.assertEqual(json.loads(paths[0].read_text()),{'url':'https://example.org/file'})
                called.append(data_dir)
            output=io.BytesIO()
            run_native_host(Path(directory),stdin=io.BytesIO(frame({'url':'file:///bad'})+frame({'url':'https://example.org/file'})),
                            stdout=output,on_queued=wake)
            replies=responses(output.getvalue())
            self.assertEqual([item['ok'] for item in replies],[False,True])
            self.assertEqual(replies[-1]['desktop'],'requested')
            self.assertEqual(called,[Path(directory)])

    def test_desktop_start_failure_preserves_accepted_request_and_warns_without_retrying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            output=io.BytesIO()
            def denied(_data):
                raise OSError('Cannot start fixed companion')
            run_native_host(Path(directory),stdin=io.BytesIO(frame({'url':'https://example.org/file'})),
                            stdout=output,on_queued=denied)
            reply=responses(output.getvalue())[0]
            self.assertTrue(reply['ok'])
            self.assertEqual(reply['desktop'],'unavailable')
            self.assertIn('Link salvo',reply['warning'])
            self.assertTrue((Path(directory)/'inbox'/(reply['id']+'.json')).is_file())

    def test_unowned_temporary_is_preserved_when_exclusive_create_fails(self):
        identity = uuid.UUID("6f4bdcb1-4cbb-428d-908e-94f5fa90f694")
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox"
            inbox.mkdir()
            existing = inbox / f".{identity}.tmp"
            existing.write_text("preserve caller evidence", encoding="utf-8")
            with patch("lume.browser.uuid.uuid4", return_value=identity):
                _, replies = self.invoke(frame({"url": "https://example.org/a"}), directory)
            self.assertIs(replies[0]["ok"], False)
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve caller evidence")

    def test_zero_frame_rejects_then_maximum_sized_valid_frame_is_accepted(self):
        body = b'{"url":"https://example.org/a"}'
        body += b" " * (MAX_MESSAGE_BYTES - len(body))
        with tempfile.TemporaryDirectory() as directory:
            status, replies = self.invoke(struct.pack("<I", 0) + struct.pack("<I", len(body)) + body, directory)
            self.assertEqual(status, 0)
            self.assertEqual([item["ok"] for item in replies], [False, True])

    def test_url_validation_preserves_allowed_input(self):
        for url in ["https://example.org/a?x=1", "HTTP://localhost:8080/a", "https://[::1]/x", "https://example.org/ação"]:
            self.assertEqual(validate_url(url), url)

    def test_url_validation_rejects_unsafe_or_ambiguous_values(self):
        for url in ["file:///C:/secret", "javascript:alert(1)", "data:text/plain,a", "ftp://example.org/x", "https://name:pass@example.org", "https://name@example.org", "https://example.org\r\nHeader:x", "\thttps://example.org", "https://example.org/\x7f", "https://example.org/\x85", "https://example.org/a b", "https://example.org\\@other.org", "https:///path", "http://[bad", "https://example.org:99999", "https://example.org:" , "https://example.org/" + "a" * 8192, "https://example.org/\ud800"]:
            with self.subTest(url=repr(url)), self.assertRaises(ValueError):
                validate_url(url)


if __name__ == "__main__":
    unittest.main()
