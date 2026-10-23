"""Authorized SAMPLE-AES through real HTTP, private processing, decoded hashes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from lume.mux import MuxCleanupPending, MuxError, pending_work, retry_pending_cleanup
from lume.network import Control, DownloadError, Interrupted
from lume.streaming import discover_stream, download_stream, owned_stream_files, resolve_stream
from tests.sample_crypto_fixture import sample_aes_resources, sample_aes_ts
from tests.test_hls_crypto import decoded_hashes
from tests.test_streaming import Fixture


KEY=b'authorized-key01'
OTHER_KEY=b'authorized-key02'
IV=bytes.fromhex('00'*15+'a5')
TOOLS=Path(__file__).resolve().parents[1]/'.cache/release-1.10.0/crypto-tools'


class SampleCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (TOOLS/'OpenDownloaderCrypto.exe').is_file():
            raise unittest.SkipTest('Build the pinned private crypto helper/test tools first.')
        cls.generated=tempfile.TemporaryDirectory(prefix='od-sample-source-')
        source=Path(cls.generated.name)
        folder=source/'hls';folder.mkdir()
        # Moving detail exercises several protected blocks per NAL. A flat
        # color fixture can miss encrypted buffered final video PES packets.
        oracle='C:/ffmpeg/bin/ffmpeg.exe'
        subprocess.run([oracle,'-nostdin','-v','error','-n','-f','lavfi','-i','testsrc2=s=96x64:r=10',
                        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264',
                        '-preset','ultrafast','-g','10','-sc_threshold','0','-c:a','aac','-f','hls',
                        '-hls_time','1','-hls_list_size','0','-hls_segment_filename',str(folder/'seg-%02d.ts'),
                        str(folder/'index.m3u8')],capture_output=True,check=True,timeout=25,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        cls.resources={'/hls/'+file.name:file.read_bytes() for file in folder.iterdir()}
        plain=source/'baseline.mp4'
        subprocess.run([oracle,'-nostdin','-v','error','-n','-i',str(folder/'index.m3u8'),'-c','copy',str(plain)],
                       capture_output=True,check=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        cls.expected=decoded_hashes(plain)

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='od-sample-work-')
        self.work=Path(self.temp.name)
        self.server=Fixture(sample_aes_resources(self.resources))
        self.private=patch('lume.mux.find_media_tools',return_value=(TOOLS/'ffmpeg.exe',TOOLS/'ffprobe.exe'))
        self.private.start()
        self.helper=patch('lume.mux.find_crypto_helper',return_value=TOOLS/'OpenDownloaderCrypto.exe')
        self.helper.start()

    def tearDown(self):
        self.helper.stop();self.private.stop();self.server.close();self.temp.cleanup()

    def plan(self):
        return resolve_stream(self.server.url+'/sample/index.m3u8',Control())

    def assert_media(self, result):
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        self.assertEqual(decoded_hashes(result['path']),self.expected)

    def assert_no_clear_or_keys(self):
        for path in owned_stream_files(self.work):
            self.assertFalse(path.name.endswith(('.clear','.clear.part')))
            value=path.read_bytes()
            for key in (KEY,OTHER_KEY):
                self.assertNotIn(key,value)
                self.assertNotIn(key.hex().encode(),value)
        self.assertFalse(any(path.name.startswith('streaming-key-') for path in self.work.iterdir()))

    def test_metadata_does_not_fetch_key_or_media(self):
        catalogue=discover_stream(self.server.url+'/sample/index.m3u8',threading.Event())
        self.assertEqual(len(catalogue['formats']),1)
        plan=self.plan()
        self.assertTrue(all(segment.encryption.method=='SAMPLE-AES' for segment in plan.tracks[0].segments))
        self.assertEqual(self.server.requests,[('/sample/index.m3u8',None)]*2)

    def test_explicit_iv_dense_video_and_final_buffered_pes_match_clear(self):
        command_lines=[]
        original=subprocess.Popen
        def launch(command,**kwargs):
            command_lines.append(tuple(command))
            return original(command,**kwargs)
        with patch('lume.mux.subprocess.Popen',side_effect=launch):
            result=download_stream(self.plan(),self.work,Control())
        self.assert_media(result)
        self.assert_no_clear_or_keys()
        for command in command_lines:
            self.assertNotIn(KEY.hex(),' '.join(command))
            self.assertNotIn(KEY.decode(),' '.join(command))

    def test_implicit_sequence_iv_is_owned_by_each_segment(self):
        lines=['#EXTM3U','#EXT-X-VERSION:5','#EXT-X-TARGETDURATION:1',
               '#EXT-X-MEDIA-SEQUENCE:71','#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"']
        for index in range(3):
            self.server.resources[f'/sample/seg-{index:02d}.ts']=sample_aes_ts(
                self.resources[f'/hls/seg-{index:02d}.ts'],KEY,(71+index).to_bytes(16,'big'))
            lines+=['#EXTINF:1,',f'seg-{index:02d}.ts']
        self.server.resources['/sample/index.m3u8']='\n'.join(lines+['#EXT-X-ENDLIST',''])
        self.assert_media(download_stream(self.plan(),self.work,Control()))
        self.assert_no_clear_or_keys()

    def test_method_none_keeps_clear_segments_in_the_same_track(self):
        body=self.server.resources['/sample/index.m3u8']
        self.server.resources['/sample/index.m3u8']=body.replace('#EXTINF:1,\nseg-02.ts',
            '#EXT-X-KEY:METHOD=NONE\n#EXTINF:1,\nseg-02.ts')
        self.server.resources['/sample/seg-02.ts']=self.resources['/hls/seg-02.ts']
        self.assert_media(download_stream(self.plan(),self.work,Control()))
        self.assert_no_clear_or_keys()

    def test_unvalidated_sample_aes_fmp4_is_refused_before_key_or_media_reads(self):
        self.server.resources['/sample/index.m3u8']='#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\nseg.m4s\n#EXT-X-ENDLIST\n'
        with self.assertRaises(DownloadError) as caught:self.plan()
        self.assertEqual(caught.exception.code,'stream_unsupported')
        self.assertEqual(self.server.requests,[('/sample/index.m3u8',None)])

    def test_key_rotation_never_uses_read_ahead_key_for_previous_packets(self):
        lines=['#EXTM3U','#EXT-X-VERSION:5','#EXT-X-TARGETDURATION:1','#EXT-X-MEDIA-SEQUENCE:0']
        self.server.resources['/sample/key2']=OTHER_KEY
        for index in range(3):
            key=KEY if index!=1 else OTHER_KEY
            name='key' if index!=1 else 'key2'
            lines += [f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="{name}",IV=0x{IV.hex()}',
                      '#EXTINF:1,',f'seg-{index:02d}.ts']
            self.server.resources[f'/sample/seg-{index:02d}.ts']=sample_aes_ts(
                self.resources[f'/hls/seg-{index:02d}.ts'],key,IV)
        self.server.resources['/sample/index.m3u8']='\n'.join(lines+['#EXT-X-ENDLIST',''])
        self.assert_media(download_stream(self.plan(),self.work,Control()))
        self.assert_no_clear_or_keys()

    def test_missing_key_fails_without_output(self):
        plan=self.plan();del self.server.resources['/sample/key']
        with self.assertRaises(DownloadError) as error:
            download_stream(plan,self.work,Control())
        self.assertEqual(error.exception.code,'stream_key_access')
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assert_no_clear_or_keys()

    def test_wrong_key_fails_decode_without_output(self):
        self.server.resources['/sample/key']=b'\x00'*16
        with self.assertRaises(MuxError):
            download_stream(self.plan(),self.work,Control())
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assert_no_clear_or_keys()

    def test_cancelled_transfer_retains_ciphertext_and_resumes_with_fresh_key(self):
        plan=self.plan();control=Control()
        first=len(self.server.resources['/sample/seg-00.ts'])
        def progress(value):
            if value>first:control.interrupt()
        with self.assertRaises(Interrupted):
            download_stream(plan,self.work,control,on_progress=progress,concurrency=1)
        self.assertTrue((self.work/'streaming-seg-0-0.json').exists())
        self.assert_no_clear_or_keys()
        self.server.requests.clear()
        self.assert_media(download_stream(plan,self.work,Control()))
        self.assertIn(('/sample/key',None),self.server.requests)
        self.assertIn(('/sample/seg-00.ts','bytes=0-0'),self.server.requests)
        self.assertNotIn(('/sample/seg-00.ts',None),self.server.requests)
        self.assert_no_clear_or_keys()

    def test_cancelled_before_start_does_not_fetch_keys(self):
        plan=self.plan();before=list(self.server.requests);control=Control();control.interrupt()
        with self.assertRaises(Interrupted):download_stream(plan,self.work,control)
        self.assertEqual(self.server.requests,before)
        self.assertEqual(list(self.work.iterdir()),[])

    def test_pending_private_child_keeps_ownership_until_cleanup_then_resumes(self):
        plan=self.plan();control=Control();original=subprocess.Popen;children=[]
        def spawn(command,**kwargs):
            child=original([sys.executable,'-c','import sys,time;sys.stdin.buffer.read();time.sleep(30)'],**kwargs)
            terminate=child.terminate
            def blocked():raise OSError('fixture cleanup blocked')
            child.terminate=blocked;children.append((child,terminate));control.stop.set()
            return child
        def cleanup():
            for child,terminate in children:child.terminate=terminate
            retry_pending_cleanup(self.work)
        try:
            with patch('lume.mux.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(MuxCleanupPending):download_stream(plan,self.work,control)
            self.assertTrue(pending_work(self.work))
            self.assertIsNone(children[0][0].poll())
            self.assertTrue((self.work/'streaming-owner.json').exists())
            self.assertFalse((self.work/'streaming-output.mp4').exists())
            cleanup()
            self.assertFalse(pending_work(self.work))
            self.assertIsNotNone(children[0][0].poll())
            self.assert_media(download_stream(plan,self.work,Control()))
            self.assert_no_clear_or_keys()
        finally:cleanup()


if __name__=='__main__':unittest.main()
