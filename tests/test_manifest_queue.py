"""Public native request/Manager/HTTP/FFmpeg/publication acceptance for streaming."""
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lume.browser import enqueue_request
from lume.core import Manager
from tests.test_streaming import Fixture, generate_media


class ManifestQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = tempfile.TemporaryDirectory(prefix='lume-manifest-source-')
        cls.resources = generate_media(Path(cls.generated.name))

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lume-manifest-queue-')
        self.root = Path(self.temp.name)
        self.files = self.root / 'files'
        self.server = Fixture(self.resources)
        self.manager = Manager(self.root/'data', self.files)

    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.temp.cleanup()

    def request(self, path, source):
        identity = enqueue_request(self.root/'data', {'version':3, 'url':self.server.url+path,
                                   'title':'Fixture streaming', 'media':True, 'source':source}).replace('-','')
        until = time.monotonic()+5
        while time.monotonic()<until:
            if self.manager.list_requests(): return identity
            time.sleep(.02)
        self.fail('Pending manifest was not imported')

    def assert_complete(self, identity):
        task = self.manager.wait(identity, 25)
        self.assertEqual(task['status'],'completed',task['error'])
        file = self.files / task['filename']
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),task['sha256'])
        self.assertTrue(task['media_probe']['video'])
        self.assertTrue(task['media_probe']['audio'])
        self.assertFalse(self.manager._work(task).exists(),task.get('error'))
        return task

    def test_extensionless_manifest_pending_restart_held_then_continue(self):
        self.server.resources['/manifest'] = self.resources['/hls/index.m3u8'].replace(b'seg-',b'hls/seg-')
        identity = self.request('/manifest','hls')
        self.assertEqual(self.server.requests,[])
        self.assertTrue(self.manager.list_requests()[0]['filename'].endswith('.mp4'))
        self.manager.close()
        self.manager = Manager(self.root/'data',self.files)
        self.manager.confirm_request(identity,self.files,start=False)
        self.assertEqual(self.manager.wait(identity)['status'],'held')
        self.assertEqual(self.server.requests,[])
        self.manager.resume(identity)
        task = self.assert_complete(identity)
        self.assertEqual(task['source_url'],self.server.url+'/manifest')
        self.assertEqual(task['streaming']['protocol'],'hls')

    def test_dash_and_duplicate_keep_original_and_selected_filename(self):
        url = self.server.url+'/dash/index.mpd'
        identity = self.manager.add(url,self.files,filename='selected.mp4')
        first = self.assert_complete(identity)
        identity = self.manager.add(url,self.files,filename='selected.mp4',duplicate_policy='copy')
        second = self.assert_complete(identity)
        self.assertEqual(second['filename'],'selected (1).mp4')
        self.assertEqual(hashlib.sha256((self.files/'selected.mp4').read_bytes()).hexdigest(),first['sha256'])

    def test_platform_selected_hls_pair_retains_format_and_both_tracks(self):
        original='https://vimeo.com/76979871'
        resolved=dict(original_url=original,video_id='76979871',title='Fixture',filename='Vimeo-fixture.mp4',
                      format_id='video+audio',expected_size=None,mimetype='video/mp4',ext='mp4',audio_only=False,
                      manifest_url=self.server.url+'/hls/index.m3u8',manifest_protocol='hls',
                      manifest_audio_url=self.server.url+'/fmp4/index.m3u8',
                      headers={'Referer':original},manifest_audio_headers={'Referer':original,'Accept':'*/*'},streams=[])
        with patch('lume.core.resolve_media',return_value=resolved) as resolver:
            identity=self.manager.add(original,self.files,filename='vimeo-fixture.mp4',media_selection='video+audio')
            task=self.assert_complete(identity)
        resolver.assert_called_once_with(original,unittest.mock.ANY,selection='video+audio')
        self.assertEqual(task['source_url'],original)
        self.assertEqual(task['media']['format_id'],'video+audio')
        self.assertEqual(task['filename'],'vimeo-fixture.mp4')

    def test_platform_audio_only_hls_produces_audio_without_video(self):
        original='https://vimeo.com/76979871'
        resolved=dict(original_url=original,video_id='76979871',title='Fixture audio',filename='Vimeo-audio.m4a',
                      format_id='audio',expected_size=None,mimetype='audio/mp4',ext='m4a',audio_only=True,
                      manifest_url=self.server.url+'/hls/index.m3u8',manifest_protocol='hls',headers={},streams=[])
        with patch('lume.core.resolve_media',return_value=resolved):
            identity=self.manager.add(original,self.files,filename='chosen.m4a',media_selection='audio:audio')
            task=self.manager.wait(identity,20)
        self.assertEqual(task['status'],'completed',task['error'])
        self.assertTrue(task['media']['audio_only'])
        self.assertTrue(task['media_probe']['audio'])
        self.assertFalse(task['media_probe']['video'])
        self.assertEqual(task['filename'],'chosen.m4a')

    def test_paused_manifest_is_restartable_with_owned_namespace(self):
        reached, release = threading.Event(), threading.Event()
        def limit(_size,control):
            reached.set()
            release.wait(5)
            control.check()
        with patch.object(self.manager,'_limit',side_effect=limit):
            identity = self.manager.add(self.server.url+'/fmp4/index.m3u8',self.files)
            self.assertTrue(reached.wait(5))
            self.manager.pause(identity)
            release.set()
            self.assertEqual(self.manager.wait(identity,10)['status'],'paused')
        self.manager.restart(identity)
        self.assert_complete(identity)

    def test_manifest_hint_mismatch_and_protection_never_publish(self):
        identity = self.request('/dash/index.mpd','hls')
        self.manager.confirm_request(identity,self.files)
        task = self.manager.wait(identity,10)
        self.assertEqual(task['status'],'failed')
        self.assertFalse((self.files/task['filename']).exists())
        self.assertTrue(all(name.endswith('.mpd') for name,_ in self.server.requests))
        self.server.resources['/protected.m3u8'] = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="key"\n#EXTINF:1,\nsecret.ts\n#EXT-X-ENDLIST\n'
        identity = self.manager.add(self.server.url+'/protected.m3u8',self.files)
        task = self.manager.wait(identity,10)
        self.assertEqual(task['status'],'failed')
        self.assertFalse(any(name.endswith('secret.ts') or name.endswith('/key') for name,_ in self.server.requests))

    def test_publication_ack_failure_recovers_without_new_http(self):
        import os
        rename = os.rename
        def uncertain(source,destination):
            rename(source,destination)
            if Path(destination) == self.files/'publication.mp4': raise OSError('Simulated lost acknowledgement')
        with patch('lume.core.os.rename',side_effect=uncertain):
            identity = self.manager.add(self.server.url+'/hls/index.m3u8',self.files,filename='publication.mp4')
            self.assertEqual(self.manager.wait(identity,20)['status'],'failed')
        self.assertTrue((self.files/'publication.mp4').is_file())
        count = len(self.server.requests)
        self.manager.close()
        self.manager = Manager(self.root/'data',self.files)
        self.manager.resume(identity)
        self.assert_complete(identity)
        self.assertEqual(len(self.server.requests),count)

    def test_cleanup_retry_retains_streaming_ownership_marker(self):
        unlink = Path.unlink
        refused = []
        def guarded(path,*args,**kwargs):
            if (not refused and path.name == 'streaming-seg-0-0.bin'
                    and (self.files/'cleanup.mp4').exists()):
                refused.append(path)
                raise PermissionError('Simulated retained file')
            return unlink(path,*args,**kwargs)
        with patch('pathlib.Path.unlink',new=guarded):
            identity = self.manager.add(self.server.url+'/hls/index.m3u8',self.files,filename='cleanup.mp4')
            task = self.manager.wait(identity,20)
        self.assertEqual(task['status'],'completed',task['error'])
        self.assertTrue(refused)
        self.assertTrue((self.manager._work(task)/'streaming-owner.json').exists())
        self.manager.remove(identity)
        self.assertFalse(self.manager._work(task).exists())
        self.assertTrue((self.files/'cleanup.mp4').exists())
