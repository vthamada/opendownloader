"""Real HTTP and local FFmpeg contracts for non-DRM streaming."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from lume.network import Control, DownloadError, Interrupted, Transport
import lume.mux as mux_module
from lume.media import MediaCancelled
from lume.mux import find_media_tools, MuxCleanupPending, pending_work, retry_pending_cleanup
from lume.streaming import (combine_stream_plans, discover_stream, download_stream, is_stream_url,
                            owned_stream_files, resolve_stream, MAX_SEGMENTS)


class Fixture:
    def __init__(self, resources):
        self.resources = dict(resources)
        self.requests = []
        self.ignore_range = False
        self.etag = True
        self.bad_range = False
        self.on_get = None
        self.request_headers=[]
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                name = urlsplit(self.path).path
                fixture.requests.append((name, self.headers.get('Range')))
                fixture.request_headers.append(dict(self.headers))
                if fixture.on_get:fixture.on_get(name,self.headers.get('Range'))
                item = fixture.resources.get(name)
                if item is None:
                    self.send_error(404); return
                data = item.encode() if isinstance(item, str) else item
                start, end = 0, len(data)-1
                value = self.headers.get('Range')
                if value and not fixture.ignore_range:
                    match = re.fullmatch(r'bytes=(\d+)-(\d+)', value)
                    if not match:
                        self.send_error(416); return
                    start, end = int(match[1]), int(match[2])
                    if not 0 <= start <= end < len(data):
                        self.send_error(416); return
                response = data[start:end+1]
                self.send_response(206 if value and not fixture.ignore_range else 200)
                if value and not fixture.ignore_range:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)+(1 if fixture.bad_range else 0)}')
                self.send_header('Content-Length', str(len(response)))
                self.send_header('Content-Type', 'application/octet-stream')
                if fixture.etag:
                    self.send_header('ETag', '"'+hashlib.sha256(data).hexdigest()+'"')
                self.end_headers()
                try:
                    self.wfile.write(response)
                except OSError:
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)


def generate_media(directory):
    """One owned fixture directory, reusable at the public Manager boundary."""
    directory=Path(directory)
    ffmpeg,_=find_media_tools()
    resources={}
    base=['-f','lavfi','-i','color=c=blue:s=96x64:r=10','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
          '-t','3','-c:v','libx264','-preset','ultrafast','-g','10','-sc_threshold','0','-c:a','aac']
    for kind in ('hls','fmp4','dash'):
        folder=directory/kind;folder.mkdir()
        if kind=='dash':
            args=['-f','dash','-seg_duration','1','-use_template','1','-use_timeline','1',str(folder/'index.mpd')]
        else:
            args=['-f','hls','-hls_time','1','-hls_list_size','0','-hls_segment_filename',str(folder/('seg-%02d.'+('ts' if kind=='hls' else 'm4s')))]
            if kind=='fmp4':args+=['-hls_segment_type','fmp4','-hls_fmp4_init_filename','init.mp4']
            args+=[str(folder/'index.m3u8')]
        subprocess.run([str(ffmpeg),'-nostdin','-v','error','-n',*base,*args],capture_output=True,check=True,timeout=20,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=folder)
        for file in folder.iterdir():resources['/'+kind+'/'+file.name]=file.read_bytes()
    return resources


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated=tempfile.TemporaryDirectory(prefix='lume-stream-generated-')
        cls.media=Path(cls.generated.name)
        cls.resources=generate_media(cls.media)
    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lume-stream-work-')
        self.work = Path(self.temp.name)
        self.server = Fixture(self.resources)
    def tearDown(self):
        self.server.close(); self.temp.cleanup()
    def resolve(self, path='/hls/index.m3u8', **kwargs):
        return resolve_stream(self.server.url+path, Control(), **kwargs)
    def test_direct_manifest_detection_and_metadata_do_not_fetch_segments(self):
        self.assertTrue(is_stream_url('https://example.org/test.M3U8?token=a'))
        self.assertTrue(is_stream_url('https://example.org/test.mpd'))
        self.assertFalse(is_stream_url('file:///C:/test.mpd'))
        catalogue = discover_stream(self.server.url+'/hls/index.m3u8', threading.Event())
        self.assertEqual(len(catalogue['formats']),1)
        self.assertIsNone(catalogue['formats'][0]['expected_size'])
        self.assertEqual(self.server.requests,[('/hls/index.m3u8',None)])
    def test_hls_ts_transfers_and_remuxes_real_video_audio(self):
        plan = self.resolve()
        progress=[]
        result=download_stream(plan,self.work,Control(),on_progress=progress.append)
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        self.assertGreater(float(result['probe']['duration']),2.9)
        self.assertEqual(result['bytes'],result['path'].stat().st_size)
        self.assertEqual(progress,sorted(progress))
        self.assertGreater(progress[-1],0)
        owned=owned_stream_files(self.work)
        self.assertEqual(owned[-1].name,'streaming-owner.json')
        self.assertIn(result['path'],owned)
    def test_parallel_segments_overlap_and_keep_exact_media_order(self):
        plan=self.resolve()
        together=threading.Barrier(3)
        def overlap(name,interval):
            if name.startswith('/hls/seg-'):together.wait(3)
        self.server.on_get=overlap
        progress=[]
        result=download_stream(plan,self.work,Control(),concurrency=3,on_progress=progress.append)
        self.assertEqual(progress,sorted(progress))
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        self.server.on_get=None
        sequential=self.work/'sequential';sequential.mkdir()
        baseline=download_stream(plan,sequential,Control(),concurrency=1)
        self.assertEqual(result['path'].read_bytes(),baseline['path'].read_bytes())
    def test_parallel_cancel_settles_writers_before_return_and_can_resume(self):
        plan=self.resolve();together=threading.Barrier(3);control=Control()
        def overlap(name,interval):
            if name.startswith('/hls/seg-'):together.wait(3)
        self.server.on_get=overlap
        with self.assertRaises(Interrupted):
            download_stream(plan,self.work,control,concurrency=3,on_progress=lambda value:control.interrupt())
        self.assertFalse(any(t.name.startswith('lume-stream-segment') for t in threading.enumerate()))
        self.assertFalse((self.work/'streaming-output.mp4').exists())
        self.assertEqual(owned_stream_files(self.work)[-1].name,'streaming-owner.json')
        self.server.on_get=None
        result=download_stream(plan,self.work,Control(),concurrency=3)
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
    def test_parallel_failure_settles_other_writers_and_keeps_original_error(self):
        plan=self.resolve();del self.server.resources['/hls/seg-00.ts']
        with self.assertRaises(DownloadError) as error:
            download_stream(plan,self.work,Control(),concurrency=3)
        self.assertEqual(error.exception.code,'http')
        self.assertFalse(any(t.name.startswith('lume-stream-segment') for t in threading.enumerate()))
        self.assertFalse((self.work/'streaming-track-0.bin').exists())
        self.assertTrue((self.work/'streaming-owner.json').exists())
    def test_parallelism_bounds_are_checked_before_namespace_claim(self):
        plan=self.resolve();before=list(self.server.requests)
        for value in (0,9,True,1.5,None):
            with self.assertRaises(DownloadError) as error:
                download_stream(plan,self.work,Control(),concurrency=value)
            self.assertEqual(error.exception.code,'stream_capacity')
        self.assertEqual(self.server.requests,before)
        self.assertFalse((self.work/'streaming-owner.json').exists())
    def test_only_internal_transports_are_closed_including_metadata_failure(self):
        created=[]
        class ObservedTransport(Transport):
            def __init__(self):
                super().__init__();self.closed_by_owner=False;created.append(self)
            def close(self):
                self.closed_by_owner=True;super().close()
        with patch('lume.streaming.Transport',ObservedTransport):
            discover_stream(self.server.url+'/hls/index.m3u8',threading.Event())
            plan=self.resolve()
            download_stream(plan,self.work,Control())
            with self.assertRaises(DownloadError):self.resolve('/missing.m3u8')
        self.assertEqual(len(created),4)
        self.assertTrue(all(item.closed_by_owner for item in created))
        supplied=ObservedTransport();self.addCleanup(supplied.close)
        self.resolve(transport=supplied)
        self.assertFalse(supplied.closed_by_owner)
    def test_hls_fragmented_mp4_map_is_local_only(self):
        plan=self.resolve('/fmp4/index.m3u8')
        self.assertEqual(plan.tracks[0].container,'mov')
        self.assertTrue(plan.tracks[0].segments[0].url.endswith('/init.mp4'))
        result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
    def test_dash_timeline_two_tracks_and_selected_audio(self):
        url=self.server.url+'/dash/index.mpd'
        catalogue=discover_stream(url,threading.Event())
        self.assertGreaterEqual(len(catalogue['formats']),2)
        plan=self.resolve('/dash/index.mpd')
        self.assertEqual([t.role for t in plan.tracks],['video','audio'])
        result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        audio=next(f for f in catalogue['formats'] if f['audio_only'])
        other=self.work/'audio';other.mkdir()
        audio_plan=self.resolve('/dash/index.mpd',selection=audio['id'])
        audio_result=download_stream(audio_plan,other,Control())
        self.assertFalse(audio_result['probe']['video']);self.assertTrue(audio_result['probe']['audio'])
        self.assertEqual(audio_plan.ext,'m4a')
    def test_hls_master_quality_choice_and_separate_audio(self):
        self.server.resources['/master.m3u8'] = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Main",DEFAULT=YES,URI="hls/index.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=96x64,AUDIO="a"\nhls/low.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=192x128,AUDIO="a"\nhls/high.m3u8\n'
        for name in ('low','high'):
            self.server.resources[f'/hls/{name}.m3u8']=self.server.resources['/hls/index.m3u8']
        catalogue=discover_stream(self.server.url+'/master.m3u8',threading.Event())
        self.assertEqual([f['height'] for f in catalogue['formats']],[128,64])
        self.assertEqual(self.server.requests,[('/master.m3u8',None)])
        plan=self.resolve('/master.m3u8',selection=catalogue['formats'][1]['id'])
        self.assertIn('/low.m3u8',plan.tracks[0].segments[0].url.replace('/seg-00.ts','/low.m3u8'))
        self.assertEqual([t.role for t in plan.tracks],['video','audio'])
        result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
    def test_independent_hls_video_audio_plans_are_combined_without_network(self):
        video=self.resolve();audio=self.resolve('/fmp4/index.m3u8')
        before=len(self.server.requests)
        combined=combine_stream_plans(video,audio)
        self.assertEqual(len(self.server.requests),before)
        self.assertEqual([t.role for t in combined.tracks],['video','audio'])
        self.assertNotEqual(combined.fingerprint,video.fingerprint)
        result=download_stream(combined,self.work,Control())
        self.assertTrue(result['probe']['video']);self.assertTrue(result['probe']['audio'])
    def test_hls_byte_ranges_with_implicit_offsets(self):
        segments=[self.resources[f'/hls/seg-{i:02d}.ts'] for i in range(3)]
        self.server.resources['/all.ts']=b''.join(segments)
        lines=['#EXTM3U','#EXT-X-TARGETDURATION:1']
        for index,segment in enumerate(segments):
            lines += ['#EXTINF:1,',f'#EXT-X-BYTERANGE:{len(segment)}'+('@0' if index==0 else ''),'all.ts']
        lines+=['#EXT-X-ENDLIST']
        self.server.resources['/range.m3u8']='\n'.join(lines)+'\n'
        plan=self.resolve('/range.m3u8')
        self.assertEqual(plan.tracks[0].segments[1].start,len(segments[0]))
        result=download_stream(plan,self.work,Control())
        self.assertTrue(result['probe']['video'])
        self.assertTrue(all(value is not None for name,value in self.server.requests if name=='/all.ts'))
        ranges=[h for (name,_),h in zip(self.server.requests,self.server.request_headers) if name=='/all.ts']
        self.assertNotIn('If-Range',ranges[0]);self.assertIn('If-Range',ranges[1])
    def test_changed_resource_between_fresh_byte_ranges_is_rejected(self):
        self.server.resources['/all.ts']=b'A'*20
        self.server.resources['/range.m3u8']='#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:10@0\nall.ts\n#EXTINF:1,\n#EXT-X-BYTERANGE:10\nall.ts\n#EXT-X-ENDLIST\n'
        def change(name,value):
            if name=='/all.ts' and sum(n=='/all.ts' for n,_ in self.server.requests)>1:
                self.server.resources['/all.ts']=b'B'*20
        self.server.on_get=change
        with self.assertRaises(DownloadError) as error:download_stream(self.resolve('/range.m3u8'),self.work,Control())
        self.assertEqual(error.exception.code,'stream_changed')
        self.assertEqual((self.work/'streaming-seg-0-0.bin').read_bytes(),b'A'*10)
        self.assertFalse((self.work/'streaming-seg-0-1.bin').exists())
        self.assertFalse((self.work/'streaming-track-0.bin').exists())
    def test_repeated_byte_ranges_without_stable_validator_are_rejected(self):
        self.server.etag=False
        self.server.resources['/all.ts']=b'A'*20
        self.server.resources['/range.m3u8']='#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:10@0\nall.ts\n#EXTINF:1,\n#EXT-X-BYTERANGE:10\nall.ts\n#EXT-X-ENDLIST\n'
        with self.assertRaises(DownloadError) as error:download_stream(self.resolve('/range.m3u8'),self.work,Control())
        self.assertEqual(error.exception.code,'stream_identity')
        self.assertFalse((self.work/'streaming-seg-0-0.bin').exists())
    def test_audio_only_hls_intent_is_bound_and_strips_video(self):
        plan=self.resolve(audio_only=True,headers={'Accept':'*/*'})
        self.assertTrue(plan.audio_only);self.assertEqual(plan.ext,'m4a');self.assertEqual(plan.tracks[0].role,'audio')
        self.assertNotEqual(plan.fingerprint,self.resolve(headers={'Accept':'*/*'}).fingerprint)
        result=download_stream(plan,self.work,Control(),headers={'Accept':'*/*'})
        self.assertFalse(result['probe']['video']);self.assertTrue(result['probe']['audio'])
        with self.assertRaises(DownloadError):download_stream(plan,self.work,Control(),headers={'Accept':'video/*'})
    def test_dash_segment_list_and_duration_template(self):
        initialization=self.resources['/dash/init-stream0.m4s']
        fragments=sorted(name for name in self.resources if name.startswith('/dash/chunk-stream0-'))
        listing='<Initialization sourceURL="dash/init-stream0.m4s"/>'+''.join(f'<SegmentURL media="{name.lstrip("/")}"/>' for name in fragments)
        self.server.resources['/list.mpd']=f'<MPD type="static" mediaPresentationDuration="PT3S"><Period><AdaptationSet mimeType="video/mp4"><Representation id="v"><SegmentList>{listing}</SegmentList></Representation></AdaptationSet></Period></MPD>'
        plan=self.resolve('/list.mpd')
        self.assertEqual(len(plan.tracks[0].segments),1+len(fragments))
        self.assertTrue(download_stream(plan,self.work,Control())['probe']['video'])
        other=self.work/'template';other.mkdir()
        self.server.resources['/template.mpd']='<MPD mediaPresentationDuration="PT3S"><Period><AdaptationSet mimeType="video/mp4"><SegmentTemplate timescale="1" duration="1" initialization="dash/init-stream0.m4s" media="dash/chunk-stream0-$Number%05d$.m4s"/><Representation id="v"/></AdaptationSet></Period></MPD>'
        self.assertEqual(len(self.resolve('/template.mpd').tracks[0].segments),4)
    def test_pause_and_resume_revalidates_complete_segments(self):
        plan=self.resolve()
        first_size=len(self.resources['/hls/seg-00.ts'])
        control=Control()
        def progress(value):
            if value>first_size:control.interrupt()
        with self.assertRaises(Interrupted):
            download_stream(plan,self.work,control,on_progress=progress,concurrency=1)
        self.assertTrue((self.work/'streaming-seg-0-0.json').exists())
        before=len(self.server.requests)
        result=download_stream(self.resolve(),self.work,Control())
        self.assertTrue(result['probe']['video'])
        resumed=self.server.requests[before:]
        self.assertIn(('/hls/seg-00.ts','bytes=0-0'),resumed)
        self.assertNotIn(('/hls/seg-00.ts',None),resumed)
    def test_changed_manifest_or_segment_does_not_mix_old_bytes(self):
        plan=self.resolve()
        download_stream(plan,self.work,Control())
        self.server.resources['/hls/seg-00.ts']=b'changed segment'
        with self.assertRaises(DownloadError) as error:
            download_stream(self.resolve(),self.work,Control())
        self.assertEqual(error.exception.code,'stream_changed')
        self.assertTrue((self.work/'streaming-seg-0-0.bin').exists())
        self.server.resources['/hls/index.m3u8']=self.server.resources['/hls/index.m3u8']+b'\n#changed\n'
        with self.assertRaises(DownloadError) as error:
            download_stream(self.resolve(),self.work,Control())
        self.assertEqual(error.exception.code,'stream_changed')
    def test_resume_without_validator_requires_restart(self):
        self.server.etag=False
        plan=self.resolve();download_stream(plan,self.work,Control())
        with self.assertRaises(DownloadError) as error:
            download_stream(plan,self.work,Control())
        self.assertEqual(error.exception.code,'stream_resume')
    def test_cleanup_marker_last_and_foreign_files_preserved(self):
        download_stream(self.resolve(),self.work,Control())
        files=owned_stream_files(self.work)
        files[0].unlink()
        self.assertEqual(owned_stream_files(self.work)[-1].name,'streaming-owner.json')
        foreign=self.work/'streaming-foreign.txt';foreign.write_text('preserve')
        with self.assertRaises(DownloadError):owned_stream_files(self.work)
        self.assertEqual(foreign.read_text(),'preserve')
    def test_cancellation_before_start_fetches_nothing(self):
        control=Control();control.interrupt()
        with self.assertRaises(Interrupted):resolve_stream(self.server.url+'/hls/index.m3u8',control)
        self.assertEqual(self.server.requests,[])
    def test_cancellation_during_final_child_cleanup_preserves_owned_output(self):
        control=Control();settle=mux_module._settle
        def final_cleanup(owner):
            settle(owner);control.stop.set()
        with patch('lume.mux._settle',side_effect=final_cleanup):
            with self.assertRaises(MediaCancelled):download_stream(self.resolve(),self.work,control)
        self.assertFalse(pending_work(self.work))
        self.assertIn(self.work/'streaming-output.mp4',owned_stream_files(self.work))
    def test_failed_final_child_cleanup_retains_owner_until_retry(self):
        control=Control();popen=subprocess.Popen;children=[]
        def spawn(command,**kwargs):
            process=popen([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
            terminate=process.terminate
            def blocked():raise OSError('fixture cleanup blocked')
            process.terminate=blocked;children.append((process,terminate));control.stop.set()
            return process
        def cleanup():
            for process,terminate in children:process.terminate=terminate
            retry_pending_cleanup(self.work)
        self.addCleanup(cleanup)
        with patch('lume.mux.subprocess.Popen',side_effect=spawn):
            with self.assertRaises(MuxCleanupPending):download_stream(self.resolve(),self.work,control)
        self.assertTrue(pending_work(self.work));self.assertTrue((self.work/'streaming-owner.json').exists())
        self.assertIsNone(children[0][0].poll())
        cleanup();self.assertFalse(pending_work(self.work));self.assertIsNotNone(children[0][0].poll())
    def test_live_encrypted_and_discontinuous_hls_are_explicit_errors(self):
        for body,code in [('#EXTM3U\n#EXTINF:1,\na.ts\n','stream_live'),
                          ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="key"\n#EXTINF:1,\na.ts\n#EXT-X-ENDLIST\n','stream_protected'),
                          ('#EXTM3U\n#EXT-X-DISCONTINUITY\n#EXTINF:1,\na.ts\n#EXT-X-ENDLIST\n','stream_unsupported')]:
            with self.subTest(code=code):
                self.server.resources['/bad.m3u8']=body
                with self.assertRaises(DownloadError) as error:self.resolve('/bad.m3u8')
                self.assertEqual(error.exception.code,code)
        self.assertFalse(any(name in ('/key','/a.ts') for name,_ in self.server.requests))
    def test_indented_protection_and_low_latency_tags_cannot_bypass_guards(self):
        for tag,code in [(' #EXT-X-KEY:METHOD=AES-128,URI="key",KEYFORMAT="com.apple.streamingkeydelivery"','stream_protected'),
                         ('\t#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES-CTR,URI="key"','stream_protected'),
                         (' #EXT-X-PART:DURATION=1,URI="a.ts"','stream_unsupported')]:
            self.server.resources['/bad.m3u8']=f'#EXTM3U\n{tag}\n#EXTINF:1,\na.ts\n#EXT-X-ENDLIST\n'
            with self.assertRaises(DownloadError) as error:self.resolve('/bad.m3u8')
            self.assertEqual(error.exception.code,code)
    def test_manifest_segment_protocols_and_bad_headers_are_rejected(self):
        for source in ('file:///C:/secret','data:text/plain,test','https://user:pass@example.org/v.ts'):
            self.server.resources['/bad.m3u8']=f'#EXTM3U\n#EXTINF:1,\n{source}\n#EXT-X-ENDLIST\n'
            with self.assertRaises(DownloadError):self.resolve('/bad.m3u8')
        with self.assertRaises(DownloadError) as error:self.resolve(headers={'Cookie':'private'})
        self.assertEqual(error.exception.code,'stream_headers')
    def test_drm_dynamic_xml_entities_and_expansion_are_rejected(self):
        cases=[('<MPD type="dynamic"/>','stream_live'),
               ('<!DOCTYPE MPD [<!ENTITY x "x">]><MPD/>','stream_manifest'),
               ('<MPD><ContentProtection/></MPD>','stream_protected'),
               ('<MPD mediaPresentationDuration="PT1S"><Period/><Period/></MPD>','stream_unsupported'),
               ('<html>error</html>','stream_manifest')]
        for text,code in cases:
            with self.subTest(code=code):
                self.server.resources['/bad.mpd']=text
                with self.assertRaises(DownloadError) as error:self.resolve('/bad.mpd')
                self.assertEqual(error.exception.code,code)
        text='<MPD mediaPresentationDuration="PT1S"><Period><AdaptationSet mimeType="video/mp4"><Representation id="v"><SegmentTemplate initialization="init.mp4" media="$Time$.m4s"><SegmentTimeline><S d="1" r="10000"/></SegmentTimeline></SegmentTemplate></Representation></AdaptationSet></Period></MPD>'
        self.server.resources['/bad.mpd']=text
        with self.assertRaises(DownloadError) as error:self.resolve('/bad.mpd')
        self.assertEqual(error.exception.code,'stream_capacity')
    def test_malformed_media_and_protocol_hint_fail_before_publish(self):
        self.server.resources['/extensionless']=self.resources['/hls/index.m3u8']
        plan=self.resolve('/extensionless',protocol='hls')
        self.assertEqual(plan.protocol,'hls')
        with self.assertRaises(DownloadError):self.resolve('/extensionless',protocol='dash')
        self.server.resources['/hls/seg-00.ts']=b'not a video'
        with self.assertRaises(Exception):download_stream(self.resolve(),self.work,Control())
        self.assertFalse((self.work/'completed.mp4').exists())


if __name__ == '__main__':
    unittest.main()
