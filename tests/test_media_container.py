"""Direct player downloads must not publish a protected MP4 as playable media."""
import struct
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from lume.browser import enqueue_request
from lume.core import Manager
from lume.network import Control, DownloadError, Interrupted
from tests.test_downloads import FixtureServer


def box(kind, body=b''):
    return struct.pack('>I4s',len(body)+8,kind)+body


def movie(protected=True, *, audio=False, tail=False):
    # Synthetic container metadata, no copyrighted samples or real keys.
    protection=box(b'sinf',box(b'frma',b'mp4a' if audio else b'avc1')+
        box(b'schm',bytes(4)+b'cenc'+bytes(4))+box(b'schi',box(b'tenc',bytes(24))))
    entry=box((b'enca' if audio else b'encv') if protected else (b'mp4a' if audio else b'avc1'),
        bytes(28 if audio else 78)+(protection if protected else b''))
    moov=box(b'moov',box(b'trak',box(b'mdia',box(b'minf',box(b'stbl',box(b'stsd',bytes(4)+struct.pack('>I',1)+entry))))))
    head=box(b'ftyp',b'isom'+bytes(4)+b'isom')
    return head+(box(b'mdat',bytes(32))+moov if tail else moov+box(b'mdat',bytes(32)))


class DirectProtectedMediaTests(unittest.TestCase):
    def test_player_download_reports_protection_and_preserves_owned_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);server=FixtureServer(root)
            server.resources['/v2.mp4']=('video/mp4',movie())
            manager=Manager(root/'data',root/'files')
            try:
                identity=enqueue_request(root/'data',{'version':2,'url':server.url+'/v2.mp4',
                    'media':True,'title':'Synthetic protected player'}).replace('-','')
                deadline=time.monotonic()+5
                while not manager.list_requests() and time.monotonic()<deadline:time.sleep(.02)
                manager.confirm_request(identity,root/'files',filename='protected.mp4')
                task=manager.wait(identity,10)
                self.assertEqual(task['status'],'failed',task)
                self.assertIn('proteção',task['error'])
                self.assertFalse((root/'files/protected.mp4').exists())
                self.assertTrue((root/'files'/('.lume-'+identity)).is_dir())
                self.assertIsNone(task.get('publication'))
                raw=manager.add(server.url+'/v2.mp4',root/'files',filename='explicit-bytes.mp4')
                self.assertEqual(manager.wait(raw,10)['status'],'completed')
                self.assertEqual((root/'files'/'explicit-bytes.mp4').read_bytes(),movie())
                # Model an older release's committed publication without a
                # successful completion receipt. Recovery must also guard media
                # and preserve that already published file without another GET.
                save=manager.store.save
                def fail_completion(row):
                    if row['filename']=='pending-bytes.mp4' and row['status']=='completed':
                        raise OSError('Synthetic final receipt commit failure')
                    return save(row)
                manager.store.save=fail_completion
                pending=manager.add(server.url+'/v2.mp4',root/'files',filename='pending-bytes.mp4')
                manager.wait(pending,10)
                incomplete=manager.store.task(pending)
                self.assertNotEqual(incomplete['status'],'completed')
                self.assertIsNotNone(incomplete.get('publication'))
                manager.store.save=save
                older={**incomplete,'status':'paused','expected_media':True}
                manager.store.save(older);manager.close()
                manager=Manager(root/'data',root/'files')
                requests=len(server.requests)
                manager.resume(pending);recovered=manager.wait(pending,10)
                self.assertEqual(recovered['status'],'failed')
                self.assertIn('proteção',recovered['error'])
                self.assertEqual(len(server.requests),requests)
                self.assertEqual((root/'files'/'explicit-bytes.mp4').read_bytes(),movie())
                self.assertEqual((root/'files'/'pending-bytes.mp4').read_bytes(),movie())
            finally:manager.close();server.close()


class ContainerGuardTests(unittest.TestCase):
    def check(self,data,control=None):
        from lume.media_container import reject_protected_mp4
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'media.tmp';path.write_bytes(data)
            return reject_protected_mp4(path,control or Control())

    def test_clear_and_other_formats_remain_unclassified(self):
        for data in (movie(False),b'not an MP4',b'\x1aE\xdf\xa3'+bytes(100),b''):
            self.check(data)

    def test_protected_video_audio_and_tail_metadata_are_rejected(self):
        for data in (movie(),movie(audio=True),movie(tail=True)):
            with self.subTest(data_size=len(data)),self.assertRaises(DownloadError) as caught:self.check(data)
            self.assertEqual(caught.exception.code,'media_protected')

    def test_incidental_markers_in_payload_are_not_protection_evidence(self):
        self.check(box(b'ftyp',b'isom'+bytes(4))+box(b'mdat',b'encv sinf schm cenc tenc'))

    def test_malformed_known_container_is_not_treated_as_clear(self):
        for data in (box(b'ftyp',b'isom')+struct.pack('>I4s',100,b'moov'),
                     box(b'ftyp',b'isom')+box(b'moov',b'bad')):
            with self.subTest(data=data),self.assertRaises(DownloadError) as caught:self.check(data)
            self.assertEqual(caught.exception.code,'media_container')

    def test_extended_and_to_eof_boxes_are_supported(self):
        self.check(box(b'ftyp',b'isom')+struct.pack('>I4sQ',1,b'mdat',16+8)+bytes(8))
        self.check(box(b'ftyp',b'isom')+struct.pack('>I4s',0,b'mdat')+bytes(8))

    def test_cancelled_inspection_preserves_cancellation(self):
        control=Control();control.interrupt()
        with self.assertRaises(Interrupted):self.check(movie(False),control)

    def test_capacity_and_cancellation_during_metadata_do_not_publish_clear_claims(self):
        with patch('lume.media_container.MAX_BOXES',3),self.assertRaises(DownloadError) as caught:
            self.check(box(b'ftyp',b'isom')+box(b'free')*4)
        self.assertEqual(caught.exception.code,'media_container')
        class CancelDuringScan(Control):
            calls=0
            def check(self):
                self.calls+=1
                if self.calls==3:self.interrupt()
                super().check()
        with self.assertRaises(Interrupted):self.check(movie(False),CancelDuringScan())

    def test_changed_unknown_file_is_still_an_ownership_failure(self):
        import os
        from lume import media_container
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'unknown.tmp';path.write_bytes(b'unknown other format')
            inspect=media_container._inspect
            def modify_timestamp(file,total,control):
                result=inspect(file,total,control)
                value=path.stat();os.utime(path,ns=(value.st_atime_ns,value.st_mtime_ns+100000000))
                return result
            with patch('lume.media_container._inspect',side_effect=modify_timestamp),self.assertRaises(DownloadError) as caught:
                media_container.reject_protected_mp4(path,Control())
            self.assertEqual(caught.exception.code,'ownership')
