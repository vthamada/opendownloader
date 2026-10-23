"""Native recording bytes to confirmed desktop queue; no remote media access."""
import base64
import hashlib
import io
import os
import json
from pathlib import Path
import struct
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from lume.browser import run_native_host
from lume.core import Manager
from lume.mux import find_media_tools
from lume.network import DownloadError
from lume.recording import RecordingSession


def frame(value):
    raw = json.dumps(value).encode()
    return struct.pack('<I', len(raw)) + raw


class RecordingQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = tempfile.TemporaryDirectory(prefix='od-record-source-')
        ffmpeg, _ = find_media_tools()
        cls.media = {}
        for ext, codec in [('mp4', 'libx264'), ('webm', 'libvpx-vp9')]:
            path = Path(cls.generated.name) / ('fixture.' + ext)
            subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-n', '-f', 'lavfi',
                            '-i', 'testsrc2=size=128x72:rate=10', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000', '-t', '1', '-c:v', codec,
                            '-c:a', 'aac' if ext == 'mp4' else 'libopus', str(path)],
                           check=True, capture_output=True, timeout=25,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            cls.media[ext] = path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='od-record-queue-')
        self.root = Path(self.temp.name)
        self.files = self.root / 'files'
        self.manager = Manager(self.root / 'data', self.files)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def receive(self, ext='mp4', data=None):
        session = RecordingSession(self.manager.data_dir, mime='video/' + ext, title='Teste', audio=True)
        data = self.media[ext] if data is None else data
        for offset in range(0, len(data), 36864):
            session.chunk(session.id, session.sequence, base64.b64encode(data[offset:offset+36864]).decode())
        facts = session.finish(session.id, session.sequence, session.bytes)
        self.manager._consume_recordings()
        return facts

    def test_native_to_confirmation_held_restart_copy_and_no_network(self):
        # Exercise the public framing as well as the persistent handoff. A
        # completed native session is deliberately closed before desktop import.
        data = self.media['mp4']
        output = io.BytesIO()
        start = io.BytesIO(frame({'version': 1, 'command': 'recording.start',
                                 'mime': 'video/mp4', 'title': 'Teste nativo', 'audio': True}))
        # Identity belongs to the host; exchange dynamically through a pipe-like
        # input rather than injecting a caller-selected vault identity.
        class Conversation:
            def __init__(inner): inner.phase = 0; inner.pending = b''
            def read(inner, size):
                if not inner.pending:
                    if inner.phase == 0: inner.pending = start.read()
                    elif inner.phase == 1:
                        value = output.getvalue(); length = struct.unpack('<I', value[:4])[0]
                        identity = json.loads(value[4:4+length])['recording_id']
                        inner.identity = identity
                        inner.pending = frame({'version': 1, 'command': 'recording.chunk', 'recording_id': identity,
                                               'sequence': 0, 'data': base64.b64encode(data).decode()})
                    elif inner.phase == 2:
                        inner.pending = frame({'version': 1, 'command': 'recording.finish', 'recording_id': inner.identity,
                                               'sequence': 1, 'bytes': len(data)})
                    else: return b''
                    inner.phase += 1
                value, inner.pending = inner.pending[:size], inner.pending[size:]
                return value
        conversation = Conversation()
        with patch('socket.socket', side_effect=AssertionError('Recording cannot fetch network media')):
            self.assertEqual(run_native_host(self.manager.data_dir, stdin=conversation, stdout=output,
                                             on_queued=lambda _: None), 0)
            self.manager._consume_recordings()
            identity = conversation.identity
            self.assertEqual(self.manager.list_tasks(), [])
            self.assertEqual(len(self.manager.list_requests()), 1)
            self.manager.close()
            self.manager = Manager(self.root / 'data', self.files)
            self.manager.confirm_request(identity, self.files, 'captura.mp4', start=False)
            self.assertEqual(self.manager.wait(identity)['status'], 'held')
            self.assertFalse((self.files / 'captura.mp4').exists())
            self.manager.resume(identity)
            result = self.manager.wait(identity, 20)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertTrue(result['recorded_media']['audio'])
        self.assertEqual((self.files / 'captura.mp4').read_bytes(), data)
        self.assertFalse(self.manager._work(result).exists())
        self.manager.remove(identity)
        self.manager._consume_recordings()
        self.assertEqual(self.manager.list_requests(), [])

    def test_webm_duplicate_copy_preserves_both_original_and_vault(self):
        facts = self.receive('webm')
        self.files.mkdir(exist_ok=True)
        (self.files / 'captura.webm').write_bytes(b'previous')
        self.manager.save_recording(facts.id, self.files, 'captura.webm')
        result = self.manager.wait(facts.id, 20)
        self.assertEqual(result['status'], 'completed', result['error'])
        self.assertEqual(result['filename'], 'captura (1).webm')
        self.assertEqual((self.files / 'captura.webm').read_bytes(), b'previous')
        self.assertEqual(facts.path.read_bytes(), self.media['webm'])

    def test_cancel_receipt_and_bad_extension_are_not_resurrected(self):
        facts = self.receive()
        with self.assertRaises(DownloadError):
            self.manager.confirm_request(facts.id, self.files, 'video.webm')
        self.assertIn('.mp4', self.manager.list_requests()[0]['error'])
        self.manager.dismiss_request(facts.id)
        self.manager._consume_recordings()
        self.assertEqual(self.manager.list_requests(), [])
        self.assertTrue(facts.path.exists())

    def test_mutation_or_invalid_media_never_publishes(self):
        for mutate in (False, True):
            with self.subTest(mutate=mutate):
                facts = self.receive(data=None if mutate else b'not a video')
                if mutate:
                    value = bytearray(facts.path.read_bytes()); value[-1] ^= 1
                    facts.path.write_bytes(value)
                self.manager.confirm_request(facts.id, self.files)
                result = self.manager.wait(facts.id, 20)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse((self.files / result['filename']).exists())
                self.assertTrue(facts.path.exists())

    def test_scan_error_does_not_stop_network_scheduler(self):
        with patch('lume.core.ready_recordings', side_effect=ValueError('recibo alterado')):
            self.manager._consume_recordings()
        self.assertIn('recibo alterado', self.manager.recording_error)
        self.assertTrue(self.manager.scheduler.is_alive())

    def test_uncertain_admission_is_reconciled_without_second_task(self):
        facts = self.receive()
        admit = self.manager.store.admit
        def committed(task, request):
            admit(task, request)
            raise OSError('late acknowledgement')
        with patch.object(self.manager.store, 'admit', side_effect=committed):
            self.manager.confirm_request(facts.id, self.files, start=False)
        self.assertEqual(self.manager.wait(facts.id)['status'], 'held')
        self.assertEqual(self.manager.confirm_request(facts.id, self.files), facts.id)
        self.assertEqual(len(self.manager.list_tasks()), 1)

    def test_scan_reconciles_committed_receipt_after_lost_acknowledgement(self):
        # Prevent the background scan from winning this deliberate handoff race.
        self.manager._next_recording_scan = time.monotonic() + 60
        original = self.manager.store.save_request
        def committed(request):
            original(request)
            raise OSError('late receipt acknowledgement')
        with patch.object(self.manager.store, 'save_request', side_effect=committed):
            facts = self.receive()
        self.assertEqual(self.manager.list_requests(), [])
        self.manager._consume_recordings()
        self.assertEqual(self.manager.list_requests()[0]['id'], facts.id)

    def test_transient_sqlite_failure_keeps_scheduler_and_ready_capture(self):
        facts = self.receive()
        with patch.object(self.manager.store, 'request', side_effect=sqlite3.OperationalError('database busy')):
            self.manager._consume_recordings()
        self.assertIn('database busy', self.manager.recording_error)
        self.assertTrue(self.manager.scheduler.is_alive())
        self.manager._consume_recordings()
        self.assertEqual(self.manager.recording_error, '')
        self.assertTrue(facts.path.exists())

    def test_replaced_part_is_rejected_before_truncating_foreign_bytes(self):
        facts = self.receive()
        self.manager.confirm_request(facts.id, self.files, start=False)
        task = self.manager.tasks[facts.id]
        part = self.manager._part_path(task, 0)
        part.write_bytes(b'owned partial')
        foreign = self.root / 'foreign.bin'
        foreign.write_bytes(b'preserve foreign data')
        original = os.open
        def replace(path, flags, *args, **kwargs):
            if Path(path) == part:
                part.unlink()
                os.link(foreign, part)
            return original(path, flags, *args, **kwargs)
        with patch('lume.core.os.open', side_effect=replace):
            self.manager.resume(facts.id)
            result = self.manager.wait(facts.id, 20)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(foreign.read_bytes(), b'preserve foreign data')
        self.assertTrue(facts.path.exists())
        self.assertFalse((self.files / result['filename']).exists())

    def test_cli_recording_conflicts_fail_before_creating_profile(self):
        from lume.__main__ import main
        for arguments in (['--download', 'http://example.org/file'], ['--add', 'http://example.org/file'],
                          ['--integration-center'], ['--self-test'], ['--sha256', 'a'*64]):
            with self.subTest(arguments=arguments), patch('lume.__main__.Manager') as manager, patch('sys.stderr', io.StringIO()):
                self.assertEqual(main(['--save-recording', 'a'*32, *arguments]), 1)
                manager.assert_not_called()
