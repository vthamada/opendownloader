"""Public network contracts for early protection and bounded segment recovery."""
import hashlib
import http.client
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from lume.core import Manager
from lume.browser import enqueue_request
from lume.network import Control, DownloadError, Transport
from lume.streaming import Segment, _segment
from test_downloads import FixtureServer
from test_media_container import movie, box


class EarlyProtectionTests(unittest.TestCase):
    def test_unknown_is_explicit_and_does_not_mean_unprotected(self):
        from lume.media_container import inspect_remote_mp4
        from lume.network import Interrupted
        control=Control()
        self.assertEqual(inspect_remote_mp4({},None,control)['status'],'unknown')
        control.interrupt()
        with self.assertRaises(Interrupted):inspect_remote_mp4({},None,control)

    def test_preflight_capacity_and_changed_resource(self):
        from lume.media_container import inspect_remote_mp4
        with tempfile.TemporaryDirectory() as directory:
            server=FixtureServer(Path(directory));transport=Transport()
            server.resources['/clear.mp4']=('video/mp4',movie(False))
            identity={'url':server.url+'/clear.mp4','ranges':True,'validator':server.etag,'total':len(movie(False))}
            try:
                with patch('lume.media_container.MAX_REMOTE_REQUESTS',0):
                    result=inspect_remote_mp4(identity,transport,Control())
                self.assertEqual(result['reason'],'metadata_budget');self.assertEqual(server.requests,[])
                server.etag='"changed"'
                with self.assertRaises(DownloadError) as caught:inspect_remote_mp4(identity,transport,Control())
                self.assertEqual(caught.exception.code,'changed')
            finally:transport.close();server.close()

    def test_player_rejects_tail_protection_without_media_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=FixtureServer(root)
            small=movie(tail=True)
            moov=small[small.index(b'moov')-4:]
            content=box(b'ftyp',b'isom'+bytes(4))+box(b'mdat',bytes(4*1024*1024))+moov
            server.resources['/protected.mp4']=('video/mp4',content)
            manager=Manager(root/'data',root/'files')
            try:
                task_id=enqueue_request(root/'data',{'version':2,'url':server.url+'/protected.mp4','media':True}).replace('-','')
                until=time.monotonic()+5
                while not manager.list_requests() and time.monotonic()<until:time.sleep(.02)
                manager.confirm_request(task_id,root/'files',filename='protected.mp4')
                task=manager.wait(task_id,10)
                self.assertEqual(task['status'],'failed')
                self.assertIn('proteção',task['error'])
                self.assertEqual(task['downloaded'],0)
                self.assertEqual(task['parts_state'],[])
                self.assertTrue(all(headers.get('Range') for _,headers in server.requests))
                requested=sum(int(h['Range'].split('-')[1])-int(h['Range'].split('=')[1].split('-')[0])+1 for _,h in server.requests)
                self.assertLess(requested,192*1024)
                self.assertFalse((root/'files/protected.mp4').exists())
            finally:manager.close();server.close()


class BodyRecoveryTests(unittest.TestCase):
    def test_truncated_body_is_retried_and_committed_once(self):
        # A real HTTP response stream with an independently framed short body.
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        content=b'complete segment'*100;attempts=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                attempts.append(dict(self.headers));self.send_response(200)
                self.send_header('Content-Length',str(len(content)));self.send_header('ETag','"same"');self.end_headers()
                self.wfile.write(content[:100] if len(attempts)==1 else content)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        transport=Transport()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'segment.bin'
                result=_segment(Segment(f'http://127.0.0.1:{server.server_port}/seg'),path,path.with_suffix('.json'),transport,Control(),{},lambda _:None)
                self.assertEqual(path.read_bytes(),content)
                self.assertEqual(result['sha256'],hashlib.sha256(content).hexdigest())
                self.assertEqual(len(attempts),2)
                self.assertFalse(path.with_suffix('.part').exists())
        finally:transport.close();server.shutdown();server.server_close();thread.join(3)

    def test_exhaustion_cancellation_and_unsafe_failures_do_not_retry_forever(self):
        from lume.streaming import _ShortSegment
        from lume.network import Interrupted
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'segment.bin';control=Control();calls=[]
            def incomplete(*args,**options):
                calls.append(options['expected_identity'])
                options['on_identity']({'url':'https://example.org/a','validator':'"stable"','resource_total':100})
                raise _ShortSegment()
            with patch('lume.streaming._segment_once',side_effect=incomplete),self.assertRaises(DownloadError):
                _segment(Segment('https://example.org/a'),path,path.with_suffix('.json'),None,control,{},lambda _:None)
            self.assertEqual(len(calls),3);self.assertEqual(calls[1],('https://example.org/a','"stable"',100))
            def cancelled(*args,**options):control.interrupt();raise _ShortSegment()
            with patch('lume.streaming._segment_once',side_effect=cancelled) as mocked,self.assertRaises(Interrupted):
                _segment(Segment('https://example.org/a'),path,path.with_suffix('.json'),None,control,{},lambda _:None)
            self.assertEqual(mocked.call_count,1)
            for error in (DownloadError('stream_changed','changed'),PermissionError('permission')):
                with patch('lume.streaming._segment_once',side_effect=error) as mocked,self.assertRaises(type(error)):
                    _segment(Segment('https://example.org/a'),path,path.with_suffix('.json'),None,Control(),{},lambda _:None)
                self.assertEqual(mocked.call_count,1)


class TrackSelectionTests(unittest.TestCase):
    def test_hls_exposes_audio_and_subtitle_combinations(self):
        from lume.streaming import _hls_catalog
        text='''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Português",LANGUAGE="pt-BR",DEFAULT=YES,URI="pt.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",LANGUAGE="en",URI="en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="Português",LANGUAGE="pt-BR",URI="sub.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=96x64,AUDIO="a",SUBTITLES="s"
video.m3u8
'''
        choices=_hls_catalog('https://example.org/index.m3u8',text)
        self.assertEqual(len(choices),4)
        self.assertEqual(sum(bool(c.get('subtitle_uri')) for c in choices),2)
        self.assertEqual(len({c['id'] for c in choices}),4)

    def test_dash_exposes_every_audio_language_and_webvtt(self):
        from lume.streaming import _mpd
        text='''<MPD mediaPresentationDuration="PT3S"><Period>
<AdaptationSet mimeType="video/mp4"><Representation id="v" height="64"/></AdaptationSet>
<AdaptationSet mimeType="audio/mp4" lang="pt-BR"><Representation id="pt" bandwidth="1000"/></AdaptationSet>
<AdaptationSet mimeType="audio/mp4" lang="en"><Representation id="en" bandwidth="500"/></AdaptationSet>
<AdaptationSet mimeType="text/vtt" lang="pt-BR"><Representation id="s"><BaseURL>sub.vtt</BaseURL></Representation></AdaptationSet>
</Period></MPD>'''
        choices=_mpd('https://example.org/index.mpd',text)
        video=[c for c in choices if not c.get('audio_only')]
        self.assertEqual(len(video),4)
        self.assertTrue(any(c.get('audio_label')=='en' for c in video))
        self.assertEqual(sum(any(t['role']=='subtitle' for t in c['tracks']) for c in video),2)


class RealSubtitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_streaming import generate_media
        cls.assets=tempfile.TemporaryDirectory();cls.resources=generate_media(Path(cls.assets.name))
    @classmethod
    def tearDownClass(cls):cls.assets.cleanup()

    def test_selected_hls_audio_and_segmented_vtt_are_in_final_mp4(self):
        from test_streaming import Fixture
        from lume.streaming import discover_stream,resolve_stream,download_stream,owned_stream_files
        from lume.mux import find_media_tools
        import subprocess,threading
        resources=dict(self.resources)
        resources['/master.m3u8']='''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Português",LANGUAGE="pt-BR",URI="hls/index.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English",LANGUAGE="en",URI="sub.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=96x64,AUDIO="a",SUBTITLES="s"
hls/index.m3u8
'''
        resources['/sub.m3u8']='#EXTM3U\n#EXTINF:1.5,\nsub.vtt\n#EXTINF:1.5,\nsub-2.vtt\n#EXT-X-ENDLIST\n'
        resources['/sub.vtt']='WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\n00:00:00.100 --> 00:00:02.000\nAuthorized fixture subtitle\n'
        resources['/sub-2.vtt']='WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\n00:00:02.100 --> 00:00:02.900\nSecond segment subtitle\n'
        server=Fixture(resources);transport=Transport()
        try:
            catalog=discover_stream(server.url+'/master.m3u8',threading.Event(),transport=transport)
            selected=next(item for item in catalog['formats'] if item.get('subtitle_label'))
            plan=resolve_stream(server.url+'/master.m3u8',Control(),selection=selected['id'],transport=transport)
            self.assertEqual([t.role for t in plan.tracks],['video','audio','subtitle'])
            with tempfile.TemporaryDirectory() as directory:
                result=download_stream(plan,Path(directory),Control(),transport=transport)
                self.assertEqual({s['codec_type'] for s in result['probe']['streams']},{'video','audio','subtitle'})
                ffmpeg,_=find_media_tools()
                extracted=subprocess.run([str(ffmpeg),'-v','error','-i',str(result['path']),'-map','0:s:0','-f','webvtt','-'],capture_output=True,check=True,timeout=15)
                self.assertIn(b'Authorized fixture subtitle',extracted.stdout)
                self.assertIn(Path(directory)/'streaming-subtitle-2.vtt',owned_stream_files(Path(directory)))
                other=Path(directory)/'fallback';other.mkdir()
                with patch('lume.streaming.MAX_CONCAT_FILES',0):
                    fallback=download_stream(plan,other,Control(),transport=transport)
                self.assertTrue(any(s['codec_type']=='subtitle' for s in fallback['probe']['streams']))
                self.assertIn('Second segment subtitle',(other/'streaming-subtitle-2.vtt').read_text())
        finally:transport.close();server.close()

    def test_dash_selected_language_and_webvtt_download(self):
        import copy,threading,xml.etree.ElementTree as ET
        from test_streaming import Fixture
        from lume.streaming import discover_stream,resolve_stream,download_stream
        resources=dict(self.resources);root=ET.fromstring(resources['/dash/index.mpd'])
        ns={'d':'urn:mpeg:dash:schema:mpd:2011'};period=root.find('d:Period',ns)
        audio=next(a for a in period if a.get('contentType')=='audio');audio.set('lang','pt-BR')
        alternate=copy.deepcopy(audio);alternate.set('id','english');alternate.set('lang','en')
        rep=alternate.find('d:Representation',ns);old=rep.get('id');rep.set('id','english')
        for template in alternate.iter('{'+ns['d']+'}SegmentTemplate'):
            for key in ('initialization','media'):
                if template.get(key):template.set(key,template.get(key).replace('$RepresentationID$',old))
        period.append(alternate)
        subtitle=ET.SubElement(period,'{'+ns['d']+'}AdaptationSet',{'mimeType':'text/vtt','lang':'en'})
        subrep=ET.SubElement(subtitle,'{'+ns['d']+'}Representation',{'id':'subtitle'})
        ET.SubElement(subrep,'{'+ns['d']+'}BaseURL').text='sub.vtt'
        resources['/dash/index.mpd']=ET.tostring(root,encoding='unicode')
        resources['/dash/sub.vtt']='WEBVTT\n\n00:00:00.100 --> 00:00:02.000\nDASH selected subtitle\n'
        server=Fixture(resources);transport=Transport()
        try:
            catalog=discover_stream(server.url+'/dash/index.mpd',threading.Event(),transport=transport)
            selected=next(c for c in catalog['formats'] if c.get('audio_label')=='en' and c.get('subtitle_label')=='en')
            plan=resolve_stream(server.url+'/dash/index.mpd',Control(),selection=selected['id'],transport=transport)
            self.assertEqual(plan.tracks[1].language,'en')
            with tempfile.TemporaryDirectory() as directory:
                result=download_stream(plan,Path(directory),Control(),transport=transport)
                self.assertEqual({s['codec_type'] for s in result['probe']['streams']},{'video','audio','subtitle'})
        finally:transport.close();server.close()


class SubtitleBoundaryTests(unittest.TestCase):
    def test_timestamp_mapping_deduplication_and_explicit_unsupported_styles(self):
        from lume.subtitles import merge_webvtt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'s.vtt';output=root/'out.vtt'
            source.write_text('WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\n00:00:00.500 --> 00:00:01.000\nTest\n',encoding='utf-8')
            merge_webvtt((source,source),output,Control(),origin=1.4)
            self.assertEqual(output.read_text().count('Test'),1);self.assertIn('00:00:00.500',output.read_text())
            source.write_text('WEBVTT\n\nSTYLE\n::cue { color:red; }\n',encoding='utf-8')
            with self.assertRaises(DownloadError):merge_webvtt((source,),root/'bad.vtt',Control())
            self.assertFalse((root/'bad.vtt').exists())
