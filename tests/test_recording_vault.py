"""Capture ownership and durable handoff, without screen/network authority."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lume.recording import (MAX_CHUNK_BYTES, RecordingError, RecordingSession,
                            get_recording, ready_recordings)


class RecordingVaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def session(self, **kwargs):
        return RecordingSession(self.root, mime=kwargs.get('mime', 'video/webm;codecs=vp9,opus'),
                                title=kwargs.get('title', 'Gravação pública'), audio=kwargs.get('audio', True))

    def send(self, session, content):
        return session.chunk(session.id, session.sequence, base64.b64encode(content).decode('ascii'))

    def finish(self, session):
        return session.finish(session.id, session.sequence, session.bytes)

    def test_ready_receipt_is_only_handoff_and_hashes_exact_ordered_bytes(self):
        session = self.session()
        content = b'\x1aE\xdf\xa3' + bytes(range(256)) * 300
        self.assertEqual(ready_recordings(self.root), ())
        for offset in range(0, len(content), MAX_CHUNK_BYTES):
            reply = self.send(session, content[offset:offset + MAX_CHUNK_BYTES])
            self.assertEqual(reply['bytes'], min(offset + MAX_CHUNK_BYTES, len(content)))
        facts = self.finish(session)
        self.assertEqual(facts.path.read_bytes(), content)
        self.assertEqual(facts.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(facts.ext, 'webm')
        self.assertEqual(ready_recordings(self.root), (facts,))
        self.assertEqual(get_recording(self.root, session.id), facts)
        self.assertEqual(facts.to_dict()['path'], str(facts.path))
        self.assertFalse((session.folder / 'capture.webm.part').exists())

    def test_abort_preserves_partial_and_unknown_files(self):
        unknown = self.root / 'recordings' / 'unknown'
        unknown.mkdir(parents=True)
        (unknown / 'keep.txt').write_text('user-owned')
        session = self.session()
        self.send(session, b'partial capture')
        reply = session.abort(session.id)
        self.assertEqual(reply['status'], 'interrupted')
        self.assertEqual((session.folder / 'capture.webm.part').read_bytes(), b'partial capture')
        self.assertEqual(json.loads((session.folder / 'receipt.json').read_text())['status'], 'interrupted')
        self.assertEqual(ready_recordings(self.root), ())
        self.assertEqual((unknown / 'keep.txt').read_text(), 'user-owned')
        with self.assertRaises(RecordingError):
            self.finish(session)

    def test_strict_mime_title_audio_and_no_caller_path(self):
        invalid = [dict(mime='application/x-msdownload'), dict(mime='video/webm;codecs=../../run'),
                   dict(mime='video/webm;codecs=vp9;path=foo'), dict(mime='video/webm\n'),
                   dict(title='bad\ntitle'), dict(title='x' * 257), dict(audio=1), dict(title='\ud800')]
        for values in invalid:
            with self.subTest(values=repr(values)), self.assertRaises(RecordingError):
                self.session(**values)
        self.assertFalse((self.root / 'recordings').exists())
        for mime in ('video/mp4', 'video/mp4;codecs="avc1.42E01E, mp4a.40.2"', 'video/webm;codecs=vp8,opus'):
            session = self.session(mime=mime)
            session.interrupt()

    def test_invalid_base64_replay_size_and_identity_are_rejected_before_write(self):
        session = self.session()
        for identity, sequence, data in [(session.id, 0, 'a==='), (session.id, True, 'YWJj'),
                (session.id, 1, 'YWJj'), ('../anything', 0, 'YWJj'),
                (session.id, 0, 'YWJj\n'), (session.id, 0, ''),
                (session.id, 0, base64.b64encode(b'x' * (MAX_CHUNK_BYTES + 1)).decode()),
                (session.id, 0, 'YR==')]:
            with self.subTest(data=data[:20]), self.assertRaises(RecordingError):
                session.chunk(identity, sequence, data)
            self.assertEqual(session.bytes, 0)
        self.send(session, b'abc')
        with self.assertRaises(RecordingError):
            session.chunk(session.id, 0, 'YWJj')
        session.interrupt()

    def test_total_sequence_empty_and_late_finish_fail_closed(self):
        session = self.session()
        with self.assertRaises(RecordingError):
            self.finish(session)
        self.send(session, b'abc')
        for sequence, total in [(0, 3), (1, 4), (True, 3), (1, True)]:
            with self.assertRaises(RecordingError):
                session.finish(session.id, sequence, total)
        facts = self.finish(session)
        with self.assertRaises(RecordingError):
            self.finish(session)
        session.interrupt()
        self.assertEqual(get_recording(self.root, session.id), facts)

    def test_capacity_duration_and_free_space_bounds_preserve_partial(self):
        session = self.session()
        self.send(session, b'abc')
        with patch('lume.recording.MAX_RECORDING_BYTES', 4), self.assertRaises(RecordingError):
            self.send(session, b'de')
        with patch('lume.recording.time.monotonic', return_value=session._started + 86401), self.assertRaises(RecordingError):
            self.finish(session)
        session._next_disk_check = 0
        usage = type('Usage', (), {'free': 0})()
        with patch('lume.recording.shutil.disk_usage', return_value=usage), self.assertRaises(RecordingError):
            self.send(session, b'd')
        session.interrupt()
        self.assertEqual((session.folder / 'capture.webm.part').read_bytes(), b'abc')

    def test_disk_failure_on_start_keeps_owned_interrupted_receipt(self):
        usage = type('Usage', (), {'free': 0})()
        with patch('lume.recording.shutil.disk_usage', return_value=usage), self.assertRaises(RecordingError):
            self.session()
        folders = list((self.root / 'recordings').iterdir())
        self.assertEqual(len(folders), 1)
        self.assertEqual(json.loads((folders[0] / 'receipt.json').read_text())['status'], 'interrupted')
        self.assertEqual(ready_recordings(self.root), ())

    def test_metadata_tamper_and_file_substitution_are_not_silently_accepted(self):
        session = self.session()
        self.send(session, b'abc')
        path = session.folder / 'capture.webm.part'
        session._file.close()
        session._file = None
        path.rename(session.folder / 'retained-original.part')
        path.write_bytes(b'foreign')
        with self.assertRaises(RecordingError):
            self.finish(session)
        session.interrupt()
        self.assertEqual(path.read_bytes(), b'foreign')
        owner_path = session.folder / 'owner.json'
        owner = json.loads(owner_path.read_text())
        owner['other'] = 'not authorized'
        owner_path.write_text(json.dumps(owner))
        with self.assertRaises(RecordingError):
            ready_recordings(self.root)

    def test_receipt_closed_schema_bad_types_and_duplicate_keys_rejected(self):
        session = self.session()
        self.send(session, b'abc')
        facts = self.finish(session)
        path = session.folder / 'receipt.json'
        original = path.read_text(encoding='utf-8')
        for value in [dict(json.loads(original), path='C:/external'),
                      dict(json.loads(original), status=[]), dict(json.loads(original), bytes=True),
                      dict(json.loads(original), sha256='bad')]:
            path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(RecordingError):
                get_recording(self.root, facts.id)
        path.write_text(original[:-1] + ',"id":"' + facts.id + '"}', encoding='utf-8')
        with self.assertRaises(RecordingError):
            get_recording(self.root, facts.id)

    def test_changed_ready_file_size_rejected_and_scan_capacity_explicit(self):
        session = self.session()
        self.send(session, b'abc')
        facts = self.finish(session)
        facts.path.write_bytes(b'changed length')
        with self.assertRaises(RecordingError):
            get_recording(self.root, facts.id)
        facts.path.write_bytes(b'abc')
        (self.root / 'recordings' / 'foreign').mkdir()
        with patch('lume.recording.MAX_RECORDINGS', 1), self.assertRaisesRegex(RecordingError, 'incompleta'):
            ready_recordings(self.root)
        with patch('lume.recording.MAX_RECORDINGS', 2), self.assertRaisesRegex(RecordingError, 'limite'):
            self.session()

    def test_ready_receipt_publication_failure_retains_unimportable_capture(self):
        session = self.session()
        self.send(session, b'abc')
        with patch('lume.recording.os.replace', side_effect=OSError('disk write failure')):
            with self.assertRaises(OSError):
                self.finish(session)
        session.interrupt()
        self.assertEqual(ready_recordings(self.root), ())
        self.assertEqual((session.folder / 'capture.webm').read_bytes(), b'abc')
        self.assertTrue(list(session.folder.glob('.receipt-*.tmp')))

    def test_reparse_root_is_rejected_without_touching_external_files(self):
        outside = self.root / 'external'
        outside.mkdir()
        marker = outside / 'keep.txt'
        marker.write_text('preserve')
        try:
            (self.root / 'recordings').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires an unavailable privilege.')
        with self.assertRaises(RecordingError):
            self.session()
        self.assertEqual(list(outside.iterdir()), [marker])


if __name__ == '__main__':
    unittest.main()
