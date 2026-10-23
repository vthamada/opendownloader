"""Authorized AES-128 HLS through the public streaming and real HTTP boundaries."""
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

from lume.mux import find_media_tools, MuxCleanupPending, pending_work, retry_pending_cleanup
from lume.network import Control, DownloadError, Interrupted, Transport
from lume.streaming import discover_stream, download_stream, owned_stream_files, resolve_stream
from tests.test_streaming import Fixture, generate_media


KEY = b'authorized-key01'
OTHER_KEY = b'authorized-key02'
IV = bytes.fromhex('000000000000000000000000000000a5')


def encrypt(data, key=KEY, iv=IV):
    """Independent producer: production decrypts through cryptography, not this library."""
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(data, AES.block_size))


def decoded_hashes(path):
    ffmpeg, _ = find_media_tools()
    result = []
    for stream in ('v:0', 'a:0'):
        process = subprocess.run(
            [str(ffmpeg), '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:'+stream,
             '-f', 'hash', '-hash', 'sha256', '-'],
            capture_output=True, check=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result.append(process.stdout)
    return tuple(result)


class HlsCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = tempfile.TemporaryDirectory(prefix='opendownloader-aes-source-')
        cls.resources = generate_media(Path(cls.generated.name))
        server = Fixture(cls.resources)
        try:
            baseline = Path(cls.generated.name)/'baseline'
            baseline.mkdir()
            plan = resolve_stream(server.url+'/hls/index.m3u8', Control())
            cls.expected_hashes = decoded_hashes(download_stream(plan, baseline, Control())['path'])
            fragmented = Path(cls.generated.name)/'baseline-fmp4'
            fragmented.mkdir()
            fragmented_plan = resolve_stream(server.url+'/fmp4/index.m3u8', Control())
            cls.expected_fragmented_hashes = decoded_hashes(download_stream(fragmented_plan, fragmented, Control())['path'])
        finally:
            server.close()

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='opendownloader-aes-work-')
        self.work = Path(self.temp.name)
        self.server = Fixture(self.resources)
        self.server.resources['/aes/key'] = KEY
        self.server.resources['/aes/key2'] = OTHER_KEY

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def playlist(self, *, implicit=False, rotation=False, clear_last=False, keyformat=False):
        lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:1', '#EXT-X-MEDIA-SEQUENCE:73']
        keyline = '#EXT-X-KEY:METHOD=AES-128,URI="key"'
        if not implicit:
            keyline += ',IV=0x'+IV.hex()
        if keyformat:
            keyline += ',KEYFORMAT="identity",KEYFORMATVERSIONS="1"'
        lines.append(keyline)
        for index in range(3):
            key = KEY
            if rotation and index == 1:
                lines.append('#EXT-X-KEY:METHOD=AES-128,URI="key2",IV=0x'+IV.hex())
            if rotation and index >= 1:
                key = OTHER_KEY
            if clear_last and index == 2:
                lines.append('#EXT-X-KEY:METHOD=NONE')
            plain = self.resources[f'/hls/seg-{index:02d}.ts']
            iv = (73+index).to_bytes(16, 'big') if implicit else IV
            data = plain if clear_last and index == 2 else encrypt(plain, key, iv)
            self.server.resources[f'/aes/seg-{index}.ts'] = data
            lines += ['#EXTINF:1,', f'seg-{index}.ts']
        lines.append('#EXT-X-ENDLIST')
        self.server.resources['/aes/index.m3u8'] = '\n'.join(lines)+'\n'
        return self.server.url+'/aes/index.m3u8'

    def assert_no_clear_temporaries_or_keys(self):
        for path in owned_stream_files(self.work):
            self.assertFalse(path.name.endswith(('.clear', '.clear.part')), path.name)
            self.assertNotIn(KEY, path.read_bytes(), path.name)
            self.assertNotIn(OTHER_KEY, path.read_bytes(), path.name)

    def assert_valid_media(self, result):
        self.assertTrue(result['probe']['video'])
        self.assertTrue(result['probe']['audio'])
        self.assertGreater(float(result['probe']['duration']), 2.9)
        self.assertEqual(decoded_hashes(result['path']), self.expected_hashes)

    def test_discovery_and_resolution_never_request_keys_or_segments(self):
        url = self.playlist(keyformat=True)
        catalogue = discover_stream(url, threading.Event())
        self.assertEqual(len(catalogue['formats']), 1)
        plan = resolve_stream(url, Control())
        self.assertEqual(len(plan.tracks[0].segments), 3)
        self.assertEqual(self.server.requests, [('/aes/index.m3u8', None)]*2)

    def test_explicit_iv_download_is_exact_decoded_media_and_retains_only_ciphertext(self):
        plan = resolve_stream(self.playlist(keyformat=True), Control())
        self.assert_valid_media(download_stream(plan, self.work, Control(), concurrency=3))
        for index in range(3):
            self.assertEqual((self.work/f'streaming-seg-0-{index}.bin').read_bytes(),
                             self.server.resources[f'/aes/seg-{index}.ts'])
        self.assert_no_clear_temporaries_or_keys()

    def test_implicit_iv_uses_media_sequence_not_segment_array_position(self):
        plan = resolve_stream(self.playlist(implicit=True), Control())
        self.assert_valid_media(download_stream(plan, self.work, Control()))
        self.assert_no_clear_temporaries_or_keys()

    def test_encrypted_byte_ranges_are_decrypted_as_independent_segments(self):
        self.playlist(implicit=True)
        ciphertexts = [self.server.resources[f'/aes/seg-{index}.ts'] for index in range(3)]
        self.server.resources['/aes/all.ts'] = b''.join(ciphertexts)
        lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:1', '#EXT-X-MEDIA-SEQUENCE:73',
                 '#EXT-X-KEY:METHOD=AES-128,URI="key"']
        for index, ciphertext in enumerate(ciphertexts):
            lines += ['#EXTINF:1,', '#EXT-X-BYTERANGE:'+str(len(ciphertext))+('@0' if index == 0 else ''), 'all.ts']
        self.server.resources['/aes/index.m3u8'] = '\n'.join(lines+['#EXT-X-ENDLIST'])+'\n'
        plan = resolve_stream(self.server.url+'/aes/index.m3u8', Control())
        self.assert_valid_media(download_stream(plan, self.work, Control(), concurrency=3))
        self.assert_no_clear_temporaries_or_keys()

    def test_key_rotation_and_method_none_have_segment_local_scope(self):
        plan = resolve_stream(self.playlist(rotation=True, clear_last=True), Control())
        self.assert_valid_media(download_stream(plan, self.work, Control(), concurrency=3))
        requested = [path for path, _ in self.server.requests]
        self.assertIn('/aes/key', requested)
        self.assertIn('/aes/key2', requested)
        self.assert_no_clear_temporaries_or_keys()

    def test_concurrent_segments_overlap_without_corrupting_order(self):
        plan = resolve_stream(self.playlist(), Control())
        together = threading.Barrier(3)
        def overlap(name, interval):
            if name.startswith('/aes/seg-') and interval is None:
                together.wait(5)
        self.server.on_get = overlap
        self.assert_valid_media(download_stream(plan, self.work, Control(), concurrency=3))
        self.assertFalse(any(t.name.startswith('lume-stream-segment') for t in threading.enumerate()))
        self.assert_no_clear_temporaries_or_keys()

    def test_encrypted_fragmented_mp4_map_requires_and_accepts_explicit_iv(self):
        original = self.resources['/fmp4/index.m3u8'].decode()
        self.server.resources['/aes/init.mp4'] = encrypt(self.resources['/fmp4/init.mp4'])
        for index in range(3):
            self.server.resources[f'/aes/seg-{index:02d}.m4s'] = encrypt(self.resources[f'/fmp4/seg-{index:02d}.m4s'])
        body = original.replace('#EXT-X-MAP:', '#EXT-X-KEY:METHOD=AES-128,URI="key",IV=0x'+IV.hex()+'\n#EXT-X-MAP:')
        self.server.resources['/aes/index.m3u8'] = body
        result = download_stream(resolve_stream(self.server.url+'/aes/index.m3u8', Control()), self.work, Control())
        self.assertTrue(result['probe']['video'])
        self.assertTrue(result['probe']['audio'])
        self.assertGreater(float(result['probe']['duration']), 2.9)
        self.assertEqual(decoded_hashes(result['path']), self.expected_fragmented_hashes)
        self.assert_no_clear_temporaries_or_keys()
        self.server.requests.clear()
        self.server.resources['/aes/index.m3u8'] = body.replace(',IV=0x'+IV.hex(), '')
        with self.assertRaises(DownloadError):
            resolve_stream(self.server.url+'/aes/index.m3u8', Control())
        self.assertEqual(self.server.requests, [('/aes/index.m3u8', None)])

    def test_unsupported_methods_and_drm_keyformats_fail_before_key_requests(self):
        cases = ('METHOD=SAMPLE-AES-CTR,URI="key"',
                 'METHOD=AES-256,URI="key"', 'METHOD=AES-128,URI="key",KEYFORMAT="com.apple.streamingkeydelivery"',
                 'METHOD=AES-128,URI="key",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"')
        for attrs in cases:
            with self.subTest(attrs=attrs):
                self.server.requests.clear()
                self.server.resources['/bad.m3u8'] = '#EXTM3U\n#EXT-X-KEY:'+attrs+'\n#EXTINF:1,\n/aes/seg-0.ts\n#EXT-X-ENDLIST\n'
                with self.assertRaises(DownloadError) as error:
                    resolve_stream(self.server.url+'/bad.m3u8', Control())
                self.assertEqual(error.exception.code, 'stream_protected')
                self.assertEqual(self.server.requests, [('/bad.m3u8', None)])

    def test_master_session_key_metadata_is_validated_without_eager_key_fetch(self):
        self.playlist()
        master = ('#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=AES-128,URI="/aes/key",IV=0x'+IV.hex()+
                  '\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=96x64\n/aes/index.m3u8\n')
        self.server.resources['/master.m3u8'] = master
        url = self.server.url+'/master.m3u8'
        self.assertEqual(len(discover_stream(url, threading.Event())['formats']), 1)
        self.assertEqual(self.server.requests, [('/master.m3u8', None)])
        plan = resolve_stream(url, Control())
        self.assertNotIn(('/aes/key', None), self.server.requests)
        self.assert_valid_media(download_stream(plan, self.work, Control()))
        self.assert_no_clear_temporaries_or_keys()
        self.server.requests.clear()
        self.server.resources['/master.m3u8'] = master.replace('METHOD=AES-128', 'METHOD=SAMPLE-AES-CTR')
        with self.assertRaises(DownloadError) as error:
            discover_stream(url, threading.Event())
        self.assertEqual(error.exception.code, 'stream_protected')
        self.assertEqual(self.server.requests, [('/master.m3u8', None)])

    def test_https_effective_manifest_cannot_downgrade_key_to_http(self):
        """Simulated HTTPS final-URL metadata, with a real loopback manifest body."""
        self.playlist()
        self.server.resources['/aes/index.m3u8'] = self.server.resources['/aes/index.m3u8'].replace(
            'URI="key"', 'URI="'+self.server.url+'/aes/key"')
        server_url = self.server.url
        class EffectiveHttpsTransport(Transport):
            def open(self, url, headers, control):
                result = super().open(server_url+'/aes/index.m3u8', headers, control)
                result.url = url
                return result
        transport = EffectiveHttpsTransport()
        self.addCleanup(transport.close)
        with self.assertRaises(DownloadError) as error:
            resolve_stream('https://fixture.invalid/aes/index.m3u8', Control(), transport=transport)
        self.assertEqual(error.exception.code, 'tls_downgrade')
        self.assertEqual(self.server.requests, [('/aes/index.m3u8', None)])

    def test_malformed_iv_and_non_http_key_addresses_fail_before_download(self):
        cases = ('METHOD=AES-128,URI="key",IV=not-hex',
                 'METHOD=AES-128,URI="key",IV=0x'+'f'*33,
                 'METHOD=AES-128,URI="file:///C:/private.key"',
                 'METHOD=AES-128,URI="data:text/plain,secret"',
                 'METHOD=AES-128,URI="https://user:secret@example.org/key"')
        for attrs in cases:
            with self.subTest(attrs=attrs):
                self.server.requests.clear()
                self.server.resources['/bad.m3u8'] = '#EXTM3U\n#EXT-X-KEY:'+attrs+'\n#EXTINF:1,\n/aes/seg-0.ts\n#EXT-X-ENDLIST\n'
                with self.assertRaises(DownloadError):
                    resolve_stream(self.server.url+'/bad.m3u8', Control())
                self.assertEqual(self.server.requests, [('/bad.m3u8', None)])

    def test_key_response_must_be_exactly_sixteen_bytes(self):
        plan = resolve_stream(self.playlist(), Control())
        for length in (0, 15, 17, 4096):
            with self.subTest(length=length):
                work = self.work/str(length)
                work.mkdir()
                self.server.resources['/aes/key'] = b'x'*length
                with self.assertRaises(DownloadError) as error:
                    download_stream(plan, work, Control())
                self.assertEqual(error.exception.code, 'stream_crypto')
                self.assertFalse((work/'streaming-output.mp4').exists())
                self.assertFalse(any(path.name.endswith(('.clear', '.clear.part')) for path in owned_stream_files(work)))

    def test_missing_key_reports_access_failure_without_publishing_media(self):
        plan = resolve_stream(self.playlist(), Control())
        del self.server.resources['/aes/key']
        with self.assertRaises(DownloadError) as error:
            download_stream(plan, self.work, Control())
        self.assertEqual(error.exception.code, 'stream_key_access')
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assert_no_clear_temporaries_or_keys()

    def test_wrong_key_and_truncated_or_bad_padding_ciphertext_never_publish(self):
        url = self.playlist()
        original = self.server.resources['/aes/seg-0.ts']
        plain = self.resources['/hls/seg-00.ts']
        invalid_padded = plain + b'\0'*(16-len(plain)%16)
        bad_padding = AES.new(KEY, AES.MODE_CBC, IV).encrypt(invalid_padded)
        for name, key, data in (('wrong-key', OTHER_KEY, original),
                                ('truncated', KEY, original[:-1]),
                                ('padding', KEY, bad_padding)):
            with self.subTest(case=name):
                work = self.work/name
                work.mkdir()
                self.server.resources['/aes/key'] = key
                self.server.resources['/aes/seg-0.ts'] = data
                with self.assertRaises(DownloadError) as error:
                    download_stream(resolve_stream(url, Control()), work, Control(), concurrency=1)
                self.assertEqual(error.exception.code, 'stream_crypto')
                self.assertFalse((work/'streaming-output.mp4').exists())
                self.assertFalse(any(path.name.endswith(('.clear', '.clear.part')) for path in owned_stream_files(work)))

    def test_cancelled_transfer_keeps_ciphertext_receipts_and_resumes_without_full_refetch(self):
        plan = resolve_stream(self.playlist(), Control())
        control = Control()
        first_size = len(self.server.resources['/aes/seg-0.ts'])
        def progress(value):
            if value > first_size:
                control.interrupt()
        with self.assertRaises(Interrupted):
            download_stream(plan, self.work, control, on_progress=progress, concurrency=1)
        self.assertTrue((self.work/'streaming-seg-0-0.json').exists())
        self.assert_no_clear_temporaries_or_keys()
        before = len(self.server.requests)
        self.assert_valid_media(download_stream(plan, self.work, Control()))
        resumed = self.server.requests[before:]
        self.assertIn(('/aes/key', None), resumed)
        self.assertIn(('/aes/seg-0.ts', 'bytes=0-0'), resumed)
        self.assertNotIn(('/aes/seg-0.ts', None), resumed)
        self.assert_no_clear_temporaries_or_keys()

    def test_changed_key_cannot_be_combined_with_previous_ciphertext_cache(self):
        plan = resolve_stream(self.playlist(), Control())
        download_stream(plan, self.work, Control())
        self.server.resources['/aes/key'] = OTHER_KEY
        with self.assertRaises(DownloadError) as error:
            download_stream(plan, self.work, Control())
        self.assertEqual(error.exception.code, 'stream_changed')
        self.assertEqual((self.work/'streaming-seg-0-0.bin').read_bytes(), self.server.resources['/aes/seg-0.ts'])
        self.assert_no_clear_temporaries_or_keys()

    def test_redeclared_key_uri_with_changed_response_is_not_silently_reused(self):
        url = self.playlist(rotation=True)
        self.server.resources['/aes/index.m3u8'] = self.server.resources['/aes/index.m3u8'].replace('URI="key2"', 'URI="key"')
        key_requests = 0
        def rotate_same_uri(name, interval):
            nonlocal key_requests
            if name == '/aes/key':
                key_requests += 1
                if key_requests > 1:
                    self.server.resources['/aes/key'] = OTHER_KEY
        self.server.on_get = rotate_same_uri
        with self.assertRaises(DownloadError) as error:
            download_stream(resolve_stream(url, Control()), self.work, Control(), concurrency=1)
        self.assertEqual(error.exception.code, 'stream_changed')
        self.assertEqual(key_requests, 2)
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assert_no_clear_temporaries_or_keys()

    def test_cancelled_decryption_removes_plaintext_and_preserves_resumable_ciphertext(self):
        plan = resolve_stream(self.playlist(), Control())
        work = self.work
        class DecryptionCancellation(Control):
            def check(self):
                if any(work.glob('streaming-*.clear.part')):
                    self.interrupt()
                super().check()
        with self.assertRaises(Interrupted):
            download_stream(plan, self.work, DecryptionCancellation())
        self.assertTrue((self.work/'streaming-seg-0-0.json').exists())
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assert_no_clear_temporaries_or_keys()
        self.assert_valid_media(download_stream(plan, self.work, Control()))
        self.assert_no_clear_temporaries_or_keys()

    def test_pending_mux_child_retains_plaintext_until_cleanup_and_safe_resume(self):
        plan = resolve_stream(self.playlist(), Control())
        control = Control()
        popen = subprocess.Popen
        children = []
        def spawn(command, **kwargs):
            process = popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            terminate = process.terminate
            def blocked():
                raise OSError('fixture cleanup blocked')
            process.terminate = blocked
            children.append((process, terminate))
            control.stop.set()
            return process
        def cleanup():
            for process, terminate in children:
                process.terminate = terminate
            retry_pending_cleanup(self.work)
        try:
            with patch('lume.mux.subprocess.Popen', side_effect=spawn):
                with self.assertRaises(MuxCleanupPending):
                    download_stream(plan, self.work, control)
            self.assertTrue(pending_work(self.work))
            self.assertTrue((self.work/'streaming-owner.json').exists())
            self.assertIsNone(children[0][0].poll())
            clear_inputs = [path for path in owned_stream_files(self.work) if path.name.endswith('.clear')]
            self.assertEqual(len(clear_inputs), 3)
            for index in range(3):
                self.assertEqual((self.work/f'streaming-seg-0-{index}.clear').read_bytes(),
                                 self.resources[f'/hls/seg-{index:02d}.ts'])
            cleanup()
            self.assertFalse(pending_work(self.work))
            self.assertIsNotNone(children[0][0].poll())
            self.assert_valid_media(download_stream(plan, self.work, Control()))
            self.assert_no_clear_temporaries_or_keys()
        finally:
            cleanup()

    def test_cancelled_before_download_fetches_no_key_or_segments(self):
        plan = resolve_stream(self.playlist(), Control())
        before = list(self.server.requests)
        control = Control()
        control.interrupt()
        with self.assertRaises(Interrupted):
            download_stream(plan, self.work, control)
        self.assertEqual(self.server.requests, before)
        self.assertEqual(list(self.work.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
