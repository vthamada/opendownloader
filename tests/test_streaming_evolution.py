"""Real-media regressions for cache durability and local segment assembly."""
from pathlib import Path
import math
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_streaming import Fixture, generate_media
from lume.network import Control, Interrupted, DownloadError
from lume.streaming import download_stream, resolve_stream, owned_stream_files


class StreamingEvolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=tempfile.TemporaryDirectory(prefix='lume-evolution-assets-')
        cls.resources=generate_media(Path(cls.assets.name))

    @classmethod
    def tearDownClass(cls):
        cls.assets.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lume-evolution-')
        self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name)/"São João's media";self.work.mkdir()
        self.server=Fixture(self.resources);self.addCleanup(self.server.close)

    def test_complete_media_does_not_copy_all_segments_to_intermediate_tracks(self):
        for protocol,path in [('hls','/fmp4/index.m3u8'),('dash','/dash/index.mpd')]:
            with self.subTest(protocol=protocol):
                work=self.work/protocol;work.mkdir()
                plan=resolve_stream(self.server.url+path,Control())
                result=download_stream(plan,work,Control(),concurrency=4)
                self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
                self.assertFalse(list(work.glob('streaming-track-*.bin')))
                self.assertIn(result['path'],owned_stream_files(work))
                self.assertGreaterEqual(result['timings']['transfer_s'],0)
                self.assertGreaterEqual(result['timings']['remux_s'],0)
                phases=('remux_prepare_s','remux_child_s','remux_fsync_s','remux_probe_s')
                for phase in phases:
                    self.assertTrue(math.isfinite(result['timings'][phase]))
                    self.assertGreaterEqual(result['timings'][phase],0)
                self.assertLessEqual(sum(result['timings'][phase] for phase in phases),
                                     result['timings']['remux_s']+1e-9)
                self.assertEqual(set(result['probe']),{'video','audio','streams','duration','bytes','format'})
                boxes=[]
                with result['path'].open('rb') as file:
                    end=result['path'].stat().st_size
                    while file.tell()<end:
                        size,kind=struct.unpack('>I4s',file.read(8))
                        header=8
                        if size==1:size=struct.unpack('>Q',file.read(8))[0];header=16
                        if size==0:size=end-file.tell()+header
                        self.assertGreaterEqual(size,header)
                        self.assertLessEqual(file.tell()+size-header,end)
                        boxes.append(kind);file.seek(size-header,1)
                self.assertLess(boxes.index(b'mdat'),boxes.index(b'moov'))

    def test_success_durability_is_at_owner_and_final_output_not_every_cache_file(self):
        plan=resolve_stream(self.server.url+'/hls/index.m3u8',Control())
        real_sync=os.fsync
        with patch('lume.streaming.os.fsync',wraps=real_sync) as sync:
            result=download_stream(plan,self.work,Control(),concurrency=3)
        self.assertTrue(result['path'].is_file())
        self.assertLess(sync.call_count,2*len(plan.tracks[0].segments))

    def test_remux_subphases_reach_persistent_queue_without_changing_media_facts(self):
        from lume.core import Manager
        from lume.storage import Store
        files=self.work/'files';profile=self.work/'data'
        manager=Manager(profile,files)
        try:
            identity=manager.add(self.server.url+'/fmp4/index.m3u8',files)
            task=manager.wait(identity,25)
            self.assertEqual(task['status'],'completed',task['error'])
        finally:
            manager.close()
        store=Store(profile/'queue.sqlite3')
        try:
            restored=next(task for task in store.load() if task['id']==identity)
        finally:
            store.close()
        phases=('remux_prepare_s','remux_child_s','remux_fsync_s','remux_probe_s')
        for phase in phases:
            self.assertTrue(math.isfinite(restored['timings'][phase]))
            self.assertGreaterEqual(restored['timings'][phase],0)
        self.assertLessEqual(sum(restored['timings'][phase] for phase in phases),
                             restored['timings']['remux_s']+1e-9)
        self.assertEqual(set(restored['media_probe']),{'video','audio','streams','duration','bytes','format'})
        self.assertTrue(restored['media_probe']['video'])
        self.assertTrue(restored['media_probe']['audio'])

    def test_bounded_handle_fallback_preserves_real_media(self):
        plan=resolve_stream(self.server.url+'/fmp4/index.m3u8',Control())
        with patch('lume.streaming.MAX_CONCAT_FILES',0):
            result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        self.assertTrue(list(self.work.glob('streaming-track-*.bin')))

    def test_graceful_pause_flushes_cache_and_resume_checks_remote_identity(self):
        plan=resolve_stream(self.server.url+'/hls/index.m3u8',Control())
        first=len(self.resources['/hls/seg-00.ts']);control=Control()
        with self.assertRaises(Interrupted):
            download_stream(plan,self.work,control,concurrency=1,
                            on_progress=lambda count:control.interrupt() if count>first else None)
        self.assertTrue((self.work/'streaming-seg-0-0.json').is_file())
        before=len(self.server.requests)
        result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video'])
        self.assertIn(('/hls/seg-00.ts','bytes=0-0'),self.server.requests[before:])

    def test_checkpoint_failure_reports_original_interruption_and_keeps_owner(self):
        plan=resolve_stream(self.server.url+'/hls/index.m3u8',Control())
        with patch('lume.streaming._transfer_segments',side_effect=Interrupted()), \
             patch('lume.streaming._checkpoint_cache',side_effect=OSError('disk sync failed')):
            with self.assertRaises(DownloadError) as caught:
                download_stream(plan,self.work,Control())
        self.assertEqual(caught.exception.code,'stream_checkpoint')
        self.assertIsInstance(caught.exception.__cause__,Interrupted)
        self.assertIn('disk sync failed',str(caught.exception))
        self.assertTrue((self.work/'streaming-owner.json').is_file())


if __name__=='__main__':unittest.main()
