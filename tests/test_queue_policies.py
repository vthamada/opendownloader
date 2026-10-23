"""Named queues and explicit folder suggestions through the real Manager seam."""
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from lume.core import Manager
from lume.network import DownloadError
from test_lifecycle import NoNetworkTransport
from test_core_evolution import NamedServer

class QueuePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.transport = NoNetworkTransport()
        self.manager = Manager(self.root/'data', self.root/'files', transport=self.transport)
    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()
    def test_named_queue_suspension_and_assignment_persist_without_http(self):
        queue = self.manager.create_queue('Vídeos')
        self.manager.set_queue_enabled(queue, False)
        task = self.manager.add('https://example.test/a.mp4', self.root/'files', queue_id=queue)
        time.sleep(.35)
        self.assertEqual(self.manager.tasks[task]['status'], 'queued')
        self.assertEqual(self.transport.calls, 0)
        self.manager.pause(task)
        self.manager.assign_queue(task, 'default')
        self.manager.close()
        self.manager = Manager(self.root/'data', self.root/'files', transport=self.transport)
        self.assertEqual(self.manager.tasks[task]['queue_id'], 'default')
        self.assertFalse(next(q for q in self.manager.list_queues() if q['id']==queue)['enabled'])
    def test_folder_rule_is_only_suggestion_and_explicit_path_wins(self):
        folder = self.root/'category-videos'
        self.manager.set_category_directory('Vídeo', folder)
        self.assertEqual(self.manager.suggest_directory('sample.MP4'), str(folder.resolve()))
        self.assertIsNone(self.manager.suggest_directory('opaque'))
        self.assertFalse(folder.exists())
        task=self.manager.add('https://example.test/a.mp4', self.root/'chosen', start=False)
        self.assertEqual(self.manager.tasks[task]['directory'], str((self.root/'chosen').resolve()))
        self.manager.close()
        self.manager = Manager(self.root/'data', self.root/'files', transport=self.transport)
        self.assertEqual(self.manager.suggest_directory('sample.MP4'), str(folder.resolve()))
    def test_failed_option_write_keeps_old_memory_and_catalog(self):
        before=self.manager.get_options()
        with patch.object(self.manager.store,'save_options',side_effect=sqlite3.OperationalError('full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.manager.create_queue('Not saved')
        self.assertEqual(self.manager.get_options(), before)
    def test_move_stays_inside_queue_and_preserves_legacy_default(self):
        second=self.manager.create_queue('Second')
        a=self.manager.add('https://example.test/a',self.root/'files',start=False)
        b=self.manager.add('https://example.test/b',self.root/'files',start=False,queue_id=second)
        c=self.manager.add('https://example.test/c',self.root/'files',start=False)
        self.manager.move(c,-1)
        self.assertEqual([t['id'] for t in self.manager.list_tasks()], [c,b,a])
        self.manager.tasks[a].pop('queue_id')
        self.manager.store.save(self.manager.tasks[a])
        self.manager.close()
        self.manager = Manager(self.root/'data',self.root/'files',transport=self.transport)
        self.assertEqual(self.manager.tasks[a].get('queue_id','default'),'default')
    def test_invalid_queue_or_rule_never_admits_or_creates_folder(self):
        with self.assertRaises(DownloadError):
            self.manager.add('https://example.test/a',self.root/'absent',start=False,queue_id='missing')
        self.assertFalse((self.root/'absent').exists())
        with self.assertRaises(DownloadError):self.manager.set_category_directory('Unknown',self.root/'absent')
        self.assertEqual(self.manager.list_tasks(),[])

    def test_rejects_duplicate_names_and_busy_assignment_without_changing_policy(self):
        queue=self.manager.create_queue('Video')
        for name in (' video ', 'Todas as filas', '', 'a\nb', 'a\x7fb', 'a\x85b'):
            with self.assertRaises(DownloadError):self.manager.create_queue(name)
        task=self.manager.add('https://example.test/a',self.root/'files',start=False)
        self.manager.tasks[task]['publication']={'size':1}
        with self.assertRaises(DownloadError):self.manager.assign_queue(task,queue)
        self.assertEqual(self.manager.tasks[task]['queue_id'],'default')
        with self.assertRaises(DownloadError):self.manager.set_queue_enabled(queue,1)
        self.assertTrue(next(row for row in self.manager.list_queues() if row['id']==queue)['enabled'])

    def test_invalid_rules_and_failed_preferences_are_atomic(self):
        before=self.manager.get_options()
        for rules in ({'Unknown':str(self.root)}, {'Vídeo':''}, ['invalid']):
            with self.assertRaises(DownloadError):self.manager.set_options(category_directories=rules)
        self.assertEqual(self.manager.get_options(),before)
        with patch.object(self.manager.store,'save_options',side_effect=sqlite3.OperationalError('full')):
            with self.assertRaises(sqlite3.OperationalError):self.manager.set_options(max_active=3)
        self.assertEqual(self.manager.get_options(),before)

    def test_invalid_persisted_catalog_is_reported_and_left_intact(self):
        from lume.storage import Store
        before=self.manager.get_options()
        self.manager.close()
        for invalid in ([], {'bad':'catalog'}, [{'id':'default','name':'Main','enabled':'yes'}],
                        [{'id':'default','name':'Bad\x7fname','enabled':True}],
                        [{'id':'not-opaque','name':'Main','enabled':True}]):
            store=Store(self.root/'data'/'queue.sqlite3')
            store.save_options({**before,'queues':invalid})
            store.close()
            opened=[]
            def open_store(path):
                instance=Store(path)
                opened.append(instance)
                return instance
            with patch('lume.core.Store',side_effect=open_store):
                with self.assertRaises(DownloadError):
                    Manager(self.root/'data',self.root/'files',transport=self.transport)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].connection.execute('SELECT 1')
            store=Store(self.root/'data'/'queue.sqlite3')
            self.assertEqual(store.options()['queues'],invalid)
            store.save_options(before)
            store.close()
        self.manager=Manager(self.root/'data',self.root/'files',transport=self.transport)

    def test_unknown_queue_reference_remains_blocked_without_killing_scheduler(self):
        task=self.manager.add('https://example.test/a',self.root/'files',start=False)
        for unknown in ('missing', ['malformed']):
            with self.manager.lock:
                self.manager._change(self.manager.tasks[task],queue_id=unknown,status='queued')
            time.sleep(.35)
            row=next(row for row in self.manager.list_tasks() if row['id']==task)
            self.assertFalse(row['queue_known'])
            self.assertFalse(row['queue_enabled'])
            self.assertTrue(self.manager.scheduler.is_alive())
            self.assertEqual(self.transport.calls,0)
        self.manager.pause(task)
        self.manager.assign_queue(task,'default')

class RealQueueDispatchTests(unittest.TestCase):
    def test_late_content_disposition_does_not_move_to_category_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            server=NamedServer('attachment; filename="surprise.mp4"')
            manager=Manager(root/'data',root/'files')
            try:
                manager.set_category_directory('Vídeo',root/'rule-not-selected')
                identity=manager.add(server.url,root/'files')
                result=manager.wait(identity,10)
                self.assertEqual(result['status'],'completed',result['error'])
                self.assertEqual(result['filename'],'surprise.mp4')
                self.assertEqual(result['directory'],str((root/'files').resolve()))
                self.assertFalse((root/'rule-not-selected').exists())
                self.assertEqual((root/'files'/'surprise.mp4').read_bytes(),server.data)
            finally:
                manager.close()
                server.close()

    def test_resume_after_queue_assignment_keeps_owned_ranges_and_respects_suspension(self):
        import hashlib
        from test_acceleration import BalancedServer
        data=b'queue-resume-123'*(12*1024*1024//16)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            server=BalancedServer(data,gate=True)
            manager=Manager(root/'data',root/'files')
            try:
                queue=manager.create_queue('Paused dispatch')
                manager.set_queue_enabled(queue,False)
                identity=manager.add(server.url,root/'files',parts=4)
                self.assertTrue(server.started.wait(5))
                manager.pause(identity)
                server.release.set()
                paused=manager.wait(identity,10)
                ranges=[(p['start'],p['end']) for p in paused['parts_state']]
                original_directory=paused['directory']
                manager.assign_queue(identity,queue)
                manager.resume(identity)
                request_count=len(server.requests)
                time.sleep(.35)
                self.assertEqual(len(server.requests),request_count)
                manager.close()
                manager=Manager(root/'data',root/'files')
                time.sleep(.35)
                self.assertEqual(len(server.requests),request_count)
                manager.set_queue_enabled(queue,True)
                result=manager.wait(identity,15)
                self.assertEqual(result['status'],'completed',result['error'])
                self.assertEqual(result['directory'],original_directory)
                self.assertEqual([(p['start'],p['end']) for p in result['parts_state']],ranges)
                self.assertEqual(result['sha256'],hashlib.sha256(data).hexdigest())
            finally:
                server.release.set()
                manager.close()
                server.close()

    def test_two_queues_persist_suspension_and_complete_identical_hashes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            first=NamedServer('attachment; filename="a.bin"')
            second=NamedServer('attachment; filename="b.bin"')
            manager=Manager(root/'data',root/'files')
            try:
                queue=manager.create_queue('Later')
                manager.set_queue_enabled(queue,False)
                held=manager.add(first.url,root/'files',filename='a.bin',queue_id=queue)
                running=manager.add(second.url,root/'files',filename='b.bin')
                self.assertEqual(manager.wait(running,10)['status'],'completed')
                self.assertEqual(first.requests,[])
                self.assertEqual(manager.tasks[held]['status'],'queued')
                manager.close()
                manager=Manager(root/'data',root/'files')
                time.sleep(.35)
                self.assertEqual(first.requests,[])
                manager.set_queue_enabled(queue,True)
                result=manager.wait(held,10)
                self.assertEqual(result['status'],'completed',result['error'])
                self.assertEqual(result['sha256'],hashlib.sha256(first.data).hexdigest())
                self.assertFalse(list((root/'files').glob('.lume-*')))
            finally:
                manager.close()
                first.close()
                second.close()

    def test_browser_confirmation_uses_selected_suspended_queue(self):
        from lume.browser import enqueue_link
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            transport=NoNetworkTransport()
            manager=Manager(root/'data',root/'files',transport=transport)
            try:
                queue=manager.create_queue('Browser')
                manager.set_queue_enabled(queue,False)
                enqueue_link(manager.data_dir,'https://example.test/video.mp4')
                deadline=time.monotonic()+3
                while not manager.list_requests() and time.monotonic()<deadline:time.sleep(.02)
                request=manager.list_requests()[0]
                identity=manager.confirm_request(request['id'],root/'files',queue_id=queue)
                time.sleep(.35)
                self.assertEqual(manager.tasks[identity]['queue_id'],queue)
                self.assertEqual(manager.tasks[identity]['status'],'queued')
                self.assertEqual(transport.calls,0)
            finally:manager.close()
