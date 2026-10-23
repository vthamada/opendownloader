"""Public browser-to-queue acceptance: consent, persistence and safe copies."""
import json
from pathlib import Path
import tempfile
import threading
import sqlite3
from contextlib import closing
import time
import unittest

from lume.browser import enqueue_link, run_native_host
from lume.core import Manager
from lume.network import DownloadError
from test_downloads import FixtureServer, DATA, DIGEST


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lume-confirm-')
        self.root = Path(self.temp.name)
        self.files = self.root / 'files'
        self.files.mkdir()
        self.server = FixtureServer(self.root)
        self.manager = Manager(self.root / 'data', self.files)

    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.temp.cleanup()

    def request(self, path='/file'):
        identity = enqueue_link(self.root / 'data', self.server.url + path).replace('-', '')
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if any(row['id'] == identity for row in self.manager.list_requests()):
                return identity
            time.sleep(.02)
        self.fail('Browser request was not exposed for confirmation')

    def test_browser_never_transfers_before_confirmation(self):
        identity = self.request()
        time.sleep(.25)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.manager.list_tasks(), [])
        self.assertEqual(self.manager.list_requests()[0]['status'], 'awaiting_confirmation')
        self.assertFalse((self.files / 'file').exists())
        task = self.manager.confirm_request(identity, self.files)
        self.assertEqual(task, identity)
        self.assertEqual(self.manager.wait(task, 10)['sha256'], DIGEST)
        self.assertEqual((self.files / 'file').read_bytes(), DATA)
        self.assertEqual(self.manager.list_requests(), [])

    def test_pending_request_survives_close_and_confirm_later_does_not_transfer(self):
        identity = self.request()
        self.manager.close()
        self.manager = Manager(self.root / 'data', self.files)
        self.assertEqual(self.manager.list_requests()[0]['id'], identity)
        task = self.manager.confirm_request(identity, self.files, start=False)
        time.sleep(.25)
        self.assertEqual(self.manager.wait(task)['status'], 'held')
        self.assertEqual(self.server.requests, [])
        self.manager.resume(task)
        self.assertEqual(self.manager.wait(task, 10)['status'], 'completed')

    def test_duplicate_browser_download_creates_another_copy_and_preserves_original(self):
        old = self.files / 'file'
        old.write_bytes(b'previous user file')
        identity = self.request()
        task = self.manager.confirm_request(identity, self.files, duplicate_policy='copy')
        result = self.manager.wait(task, 10)
        self.assertEqual(result['filename'], 'file (1)')
        self.assertEqual(old.read_bytes(), b'previous user file')
        self.assertEqual((self.files / 'file (1)').read_bytes(), DATA)

    def test_collision_error_remains_visible_and_can_be_corrected(self):
        (self.files / 'file').write_bytes(b'preserve')
        identity = self.request()
        with self.assertRaises(DownloadError):
            self.manager.confirm_request(identity, self.files, duplicate_policy='error')
        self.assertIn('existe', self.manager.list_requests()[0]['error'])
        self.manager.confirm_request(identity, self.files, 'different.bin')
        self.assertEqual(self.manager.wait(identity, 10)['status'], 'completed')

    def test_cancel_is_durable_even_if_input_claim_is_replayed(self):
        identity = self.request()
        self.manager.dismiss_request(identity)
        claim = self.root / 'data/inbox' / (identity + '.processing')
        claim.write_text(json.dumps({'url': self.server.url + '/file'}), encoding='utf-8')
        time.sleep(.4)
        self.manager.close()
        self.manager = Manager(self.root / 'data', self.files)
        time.sleep(.2)
        self.assertEqual(self.manager.list_requests(), [])
        self.assertEqual(self.manager.list_tasks(), [])
        self.assertEqual(self.server.requests, [])

    def test_confirmation_replay_does_not_download_twice(self):
        identity = self.request()
        self.manager.confirm_request(identity, self.files)
        self.assertEqual(self.manager.wait(identity, 10)['status'], 'completed')
        count = len(self.server.requests)
        self.manager.confirm_request(identity, self.files)
        claim = self.root / 'data/inbox' / (identity + '.processing')
        claim.write_text(json.dumps({'url': self.server.url + '/file'}), encoding='utf-8')
        time.sleep(.3)
        self.assertEqual(len(self.manager.list_tasks()), 1)
        self.assertEqual(self.server.requests[count:], [])

    def test_uncertain_admission_commit_is_reconciled_before_cancellation(self):
        identity = self.request()
        original = self.manager.store.admit
        def commit_then_report_error(*args):
            original(*args)
            raise OSError('Commit completed but acknowledgement failed')
        self.manager.store.admit = commit_then_report_error
        task = self.manager.confirm_request(identity, self.files, start=False)
        self.assertEqual(task, identity)
        self.assertEqual(self.manager.list_requests(), [])
        self.assertEqual(self.manager.store.request(identity)['status'], 'confirmed')
        self.manager.cancel(task)
        self.manager.close()
        self.manager = Manager(self.root/'data', self.files)
        time.sleep(.2)
        self.assertEqual(self.manager.wait(task)['status'], 'cancelled')
        self.assertEqual(self.server.requests, [])

    def test_two_held_copies_reserve_different_names_and_preserve_suffix(self):
        one = self.manager.add(self.server.url + '/file', self.files, 'archive.tar.gz', start=False, duplicate_policy='copy')
        two = self.manager.add(self.server.url + '/file', self.files, 'archive.tar.gz', start=False, duplicate_policy='copy')
        names = {row['filename'] for row in self.manager.list_tasks()}
        self.assertEqual(names, {'archive.tar.gz', 'archive.tar (1).gz'})
        self.assertNotEqual(one, two)
        self.assertEqual(self.server.requests, [])

    def test_legacy_database_upgrade_has_a_consistent_backup_and_is_idempotent(self):
        from lume.storage import Store
        folder=self.root/'legacy'; folder.mkdir(); path=folder/'queue.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
                db.execute('CREATE TABLE settings (id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
                db.execute('INSERT INTO settings VALUES (?,?)',('options',json.dumps({'default_directory':str(self.files)})))
        store=Store(path); store.close()
        backups=list((folder/'backups').glob('*.sqlite3'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM settings').fetchone()[0])['default_directory'],str(self.files))
            self.assertNotIn('requests',{row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        store=Store(path); store.close()
        self.assertEqual(list((folder/'backups').glob('*.sqlite3')),backups)

    def test_automatic_browser_download_is_explicit_persisted_opt_in(self):
        self.assertIs(self.manager.get_options()['browser_auto_start'], False)
        self.manager.set_options(browser_auto_start=True)
        self.manager.close()
        self.manager = Manager(self.root / 'data', self.files)
        identity = enqueue_link(self.root / 'data', self.server.url + '/file').replace('-', '')
        end = time.monotonic() + 3
        while not self.manager.list_tasks() and time.monotonic() < end:
            time.sleep(.02)
        self.assertEqual(self.manager.wait(identity, 10)['status'], 'completed')

    def test_detected_media_rejects_html_but_generic_file_still_downloads_html(self):
        from lume.browser import enqueue_request
        self.server.content_type = 'text/html'
        identity = enqueue_request(self.root / 'data', {'version': 2, 'url': self.server.url + '/file',
                                                        'media': True, 'title': 'Page video'}).replace('-', '')
        end = time.monotonic() + 3
        while not self.manager.list_requests() and time.monotonic() < end:
            time.sleep(.02)
        self.manager.confirm_request(identity, self.files, 'video.mp4')
        result = self.manager.wait(identity, 10)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse((self.files / 'video.mp4').exists())
        self.assertIn('mídia', result['error'])


if __name__ == '__main__':
    unittest.main()
