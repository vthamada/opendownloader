"""Actual native framing and durable recordings without a browser simulator."""
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from lume.browser import MAX_MESSAGE_BYTES, run_native_host
from lume.recording import get_recording, ready_recordings
from tests.test_browser import frame, responses


def start(**values):
    return dict(version=1, command='recording.start', mime='video/webm;codecs=vp9,opus', title='Test capture', audio=True, **values)


class InteractiveInput:
    """Produce the next real LE32 frame only after reading the host ACK."""
    def __init__(self, output, commands):
        self.output, self.commands, self.pending = output, iter(commands), b''

    def read(self, count):
        if not self.pending:
            command = next(self.commands, None)
            if command is None:
                return b''
            item = command(responses(self.output.getvalue())) if callable(command) else command
            self.pending = item if isinstance(item, bytes) else frame(item)
        result, self.pending = self.pending[:min(count, 3)], self.pending[min(count, 3):]
        return result


def chunk(replies, *, sequence=0, data=b'\x1aE\xdf\xa3capture'):
    return {'version': 1, 'command': 'recording.chunk', 'recording_id': replies[0]['recording_id'],
            'sequence': sequence, 'data': base64.b64encode(data).decode('ascii')}


class RecordingNativeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, commands, *, wake=None):
        output = io.BytesIO()
        with patch('socket.socket', side_effect=AssertionError('No network authority')), \
             patch('subprocess.Popen', side_effect=AssertionError('No child authority')):
            code = run_native_host(self.root, stdin=InteractiveInput(output, commands), stdout=output, on_queued=wake)
        return code, responses(output.getvalue())

    def test_complete_ordered_capture_ack_and_desktop_after_durable_ready_only(self):
        payload = b'\x1aE\xdf\xa3capture'
        def wake(path):
            facts = ready_recordings(path)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].path.read_bytes(), payload)
        code, replies = self.invoke([start(), lambda r: chunk(r),
            lambda r: {'version': 1, 'command': 'recording.finish', 'recording_id': r[0]['recording_id'],
                       'sequence': 1, 'bytes': len(payload)}], wake=wake)
        self.assertEqual(code, 0)
        self.assertTrue(all(reply['ok'] for reply in replies))
        self.assertTrue(all(reply['version'] == 1 for reply in replies))
        self.assertEqual(replies[1]['sequence'], 0)
        self.assertEqual(replies[1]['bytes'], len(payload))
        self.assertEqual(replies[2]['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(replies[2]['desktop'], 'requested')
        self.assertNotIn('path', replies[2])
        self.assertFalse((self.root / 'inbox').exists())

    def test_eof_truncated_and_overlarge_frames_preserve_interrupted_part(self):
        endings = [[], [b'\x01'], [struct.pack('<I', 10) + b'{}'],
                   [struct.pack('<I', MAX_MESSAGE_BYTES + 1)]]
        for index, ending in enumerate(endings):
            with self.subTest(ending=index):
                self.root = Path(self.temporary.name) / str(index)
                code, replies = self.invoke([start(), lambda r: chunk(r), *ending], wake=lambda _: self.fail('Cannot wake unfinished recording'))
                self.assertEqual(code, 0 if index == 0 else 1)
                identity = replies[0]['recording_id']
                folder = self.root / 'recordings' / identity
                self.assertEqual(json.loads((folder / 'receipt.json').read_text())['status'], 'interrupted')
                self.assertTrue((folder / 'capture.webm.part').is_file())
                self.assertEqual(ready_recordings(self.root), ())

    def test_replay_wrong_finish_invalid_base64_interrupt_without_ready(self):
        invalids = [lambda r: chunk(r, sequence=0),
                    lambda r: dict(chunk(r, sequence=1), data='not base64'),
                    lambda r: {'version': 1, 'command': 'recording.finish', 'recording_id': r[0]['recording_id'],
                               'sequence': 1, 'bytes': 1},
                    lambda r: dict(chunk(r, sequence=1), path='C:/outside')]
        for index, invalid in enumerate(invalids):
            self.root = Path(self.temporary.name) / str(index)
            code, replies = self.invoke([start(), lambda r: chunk(r), invalid,
                lambda r: {'version': 1, 'command': 'recording.finish', 'recording_id': r[0]['recording_id'],
                           'sequence': 1, 'bytes': len(b'\x1aE\xdf\xa3capture')}])
            self.assertEqual(code, 0)
            self.assertFalse(replies[2]['ok'])
            self.assertFalse(replies[3]['ok'])
            self.assertEqual(ready_recordings(self.root), ())

    def test_abort_and_late_finish_keep_original_and_old_url_status_still_work(self):
        code, replies = self.invoke([start(), lambda r: chunk(r),
            lambda r: {'version': 1, 'command': 'recording.abort', 'recording_id': r[0]['recording_id']},
            lambda r: {'version': 1, 'command': 'recording.finish', 'recording_id': r[0]['recording_id'], 'sequence': 1, 'bytes': 11},
            {'version': 1, 'command': 'status'}, {'url': 'https://example.org/file'}])
        self.assertEqual(code, 0)
        self.assertEqual([item['ok'] for item in replies], [True, True, True, False, True, True])
        self.assertEqual(replies[2]['status'], 'interrupted')
        self.assertEqual(len(list((self.root / 'inbox').glob('*.json'))), 1)

    def test_start_key_closed_and_second_start_does_not_destroy_active_session(self):
        bad = [dict(start(), version=True), dict(start(), path='C:/caller'), dict(start(), mime='video/webm;path=foo')]
        code, replies = self.invoke([*bad, start(), start()])
        self.assertEqual(code, 0)
        self.assertEqual([reply['ok'] for reply in replies], [False, False, False, True, False])
        self.assertEqual(len(list((self.root / 'recordings').iterdir())), 1)

    def test_finish_wakeup_failure_preserves_ready_capture_and_warns(self):
        code, replies = self.invoke([start(), lambda r: chunk(r),
            lambda r: {'version': 1, 'command': 'recording.finish', 'recording_id': r[0]['recording_id'],
                       'sequence': 1, 'bytes': len(b'\x1aE\xdf\xa3capture')}],
            wake=lambda _: (_ for _ in ()).throw(OSError('desktop unavailable')))
        self.assertEqual(code, 0)
        self.assertEqual(replies[-1]['desktop'], 'unavailable')
        self.assertEqual(replies[-1]['status'], 'ready')
        self.assertEqual(len(ready_recordings(self.root)), 1)
        self.assertEqual(get_recording(self.root, replies[0]['recording_id']).bytes, replies[-1]['bytes'])

    def test_lost_ack_output_always_interrupts_owned_recording(self):
        class FailingOutput(io.BytesIO):
            writes = 0
            def write(inner, data):
                if inner.writes >= 2:
                    raise OSError('browser disconnected before chunk acknowledgement')
                inner.writes += 1
                return super().write(data)
        output = FailingOutput()
        with self.assertRaises(OSError):
            run_native_host(self.root, stdin=InteractiveInput(output, [start(), lambda r: chunk(r)]), stdout=output)
        identity = responses(output.getvalue())[0]['recording_id']
        folder = self.root / 'recordings' / identity
        self.assertEqual(json.loads((folder / 'receipt.json').read_text())['status'], 'interrupted')
        self.assertEqual((folder / 'capture.webm.part').read_bytes(), b'\x1aE\xdf\xa3capture')
        self.assertEqual(ready_recordings(self.root), ())

    def test_disk_write_failure_never_wakes_and_keeps_original_partial(self):
        with patch('lume.recording.RecordingSession.chunk', side_effect=OSError('disk full')):
            code, replies = self.invoke([start(), lambda r: chunk(r)], wake=lambda _: self.fail('Cannot wake on failed recording'))
        self.assertEqual(code, 0)
        self.assertFalse(replies[-1]['ok'])
        self.assertIn('gravação', replies[-1]['error'])
        folder = self.root / 'recordings' / replies[0]['recording_id']
        self.assertEqual(json.loads((folder / 'receipt.json').read_text())['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
