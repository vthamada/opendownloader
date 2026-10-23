"""HLS audio alternatives through the public catalogue, resolver and remux."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from lume.mux import find_media_tools
from lume.network import Control, DownloadError
from lume.streaming import discover_stream, download_stream, resolve_stream
from test_streaming import Fixture, generate_media


PORTUGUESE = '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="sound",NAME="Português",LANGUAGE="pt-BR",DEFAULT=YES,URI="pt/index.m3u8"'
ENGLISH = '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="sound",NAME="English",LANGUAGE="en",DEFAULT=NO,URI="en/index.m3u8"'


def master(*audio, variant='hls/index.m3u8', extra=''):
    return '\n'.join(('#EXTM3U', *audio,
        '#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=96x64,AUDIO="sound"'+extra,
        variant, ''))


class HLSAudioSelectionTests(unittest.TestCase):
    def setUp(self):
        self.server = Fixture({'/master.m3u8': master(PORTUGUESE, ENGLISH),
            '/hls/index.m3u8': '#EXTM3U\n#EXTINF:1,\nvideo.ts\n#EXT-X-ENDLIST\n',
            '/pt/index.m3u8': '#EXTM3U\n#EXTINF:1,\nportuguese.ts\n#EXT-X-ENDLIST\n',
            '/en/index.m3u8': '#EXTM3U\n#EXTINF:1,\nenglish.ts\n#EXT-X-ENDLIST\n'})
        self.addCleanup(self.server.close)
        self.url = self.server.url+'/master.m3u8'

    def discover(self):
        return discover_stream(self.url, threading.Event())['formats']

    def test_catalogue_exposes_audio_language_and_preserves_default_legacy_identity(self):
        options = self.discover()
        self.assertEqual(len(options), 2)
        self.assertIn('Português', options[0]['label'])
        self.assertIn('pt-BR', options[0]['label'])
        self.assertIn('English', options[1]['label'])
        old = 'hls:'+hashlib.sha256((self.server.url+'/hls/index.m3u8\n'+self.server.url+'/pt/index.m3u8').encode()).hexdigest()[:24]
        self.assertEqual(options[0]['id'], old)
        self.assertEqual(self.server.requests, [('/master.m3u8', None)])

    def test_explicit_audio_choice_fetches_only_selected_playlists(self):
        options = self.discover()
        plan = resolve_stream(self.url, Control(), selection=options[1]['id'])
        self.assertEqual([track.role for track in plan.tracks], ['video', 'audio'])
        self.assertTrue(plan.tracks[1].segments[0].url.endswith('/en/english.ts'))
        self.assertNotIn(('/pt/index.m3u8', None), self.server.requests)
        self.assertFalse(any(name.endswith('.ts') for name, _ in self.server.requests))

    def test_alternate_identity_is_independent_of_declaration_order(self):
        options = self.discover()
        self.server.resources['/master.m3u8'] = master(ENGLISH, PORTUGUESE)
        reordered = self.discover()
        self.assertEqual([option['id'] for option in options], [option['id'] for option in reordered])
        plan = resolve_stream(self.url, Control(), selection=options[1]['id'])
        self.assertTrue(plan.tracks[1].segments[0].url.endswith('/en/english.ts'))

    def test_disappeared_track_does_not_silently_select_another_language(self):
        selected = self.discover()[1]['id']
        self.server.resources['/master.m3u8'] = master(PORTUGUESE)
        with self.assertRaises(DownloadError) as caught:
            resolve_stream(self.url, Control(), selection=selected)
        self.assertEqual(caught.exception.code, 'stream_selection')
        self.assertEqual(set(name for name, _ in self.server.requests), {'/master.m3u8'})

    def test_renamed_alternate_does_not_retarget_an_existing_selection(self):
        selected = self.discover()[1]['id']
        self.server.resources['/master.m3u8'] = master(PORTUGUESE, ENGLISH.replace('English', 'Commentary'))
        with self.assertRaises(DownloadError) as caught:
            resolve_stream(self.url, Control(), selection=selected)
        self.assertEqual(caught.exception.code, 'stream_selection')

    def test_same_uri_alternatives_need_explicit_internal_stream_mapping(self):
        self.server.resources['/master.m3u8'] = master(PORTUGUESE, ENGLISH.replace('en/index.m3u8', 'pt/index.m3u8'))
        with self.assertRaises(DownloadError) as caught:
            self.discover()
        self.assertEqual(caught.exception.code, 'stream_unsupported')
        self.assertEqual(self.server.requests, [('/master.m3u8', None)])

    def test_ambiguous_groups_fail_before_child_fetch(self):
        cases = [
            (master(PORTUGUESE, ENGLISH.replace('DEFAULT=NO', 'DEFAULT=YES')), 'stream_manifest'),
            (master(PORTUGUESE, ENGLISH.replace('NAME="English"', 'NAME="Português"')), 'stream_manifest'),
            (master(PORTUGUESE.replace(',URI="pt/index.m3u8"', ''), ENGLISH), 'stream_unsupported'),
            (master(PORTUGUESE.replace('GROUP-ID="sound",', '')), 'stream_manifest'),
        ]
        for body, code in cases:
            with self.subTest(code=code, body=body):
                self.server.resources['/master.m3u8'] = body
                with self.assertRaises(DownloadError) as caught:
                    self.discover()
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(set(name for name, _ in self.server.requests), {'/master.m3u8'})

    def test_subtitle_group_does_not_block_video_but_omission_is_explicit(self):
        self.server.resources['/master.m3u8'] = master(PORTUGUESE, extra=',SUBTITLES="subs"')
        options = self.discover()
        self.assertIn('sem legendas', options[0]['label'])
        plan = resolve_stream(self.url, Control(), selection=options[0]['id'])
        self.assertEqual([track.role for track in plan.tracks], ['video', 'audio'])
        self.assertFalse(any('subs' in name for name, _ in self.server.requests))

    def test_combined_quality_audio_choices_obey_catalogue_capacity(self):
        audio = [PORTUGUESE.replace('Português', f'Track-{index}').replace('pt/index.m3u8', f'track-{index}/index.m3u8').replace('DEFAULT=YES', 'DEFAULT=NO')
                 for index in range(128)]
        body = master(*audio)
        body += '#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=48x32,AUDIO="sound"\nhls/low.m3u8\n'
        self.server.resources['/master.m3u8'] = body
        with self.assertRaises(DownloadError) as caught:
            self.discover()
        self.assertEqual(caught.exception.code, 'stream_capacity')

    def test_selected_audio_live_playlist_remains_an_explicit_error(self):
        selected = self.discover()[1]['id']
        self.server.resources['/en/index.m3u8'] = '#EXTM3U\n#EXTINF:1,\nenglish.ts\n'
        with self.assertRaises(DownloadError) as caught:
            resolve_stream(self.url, Control(), selection=selected)
        self.assertEqual(caught.exception.code, 'stream_live')
        self.assertFalse(any(name.endswith('.ts') for name, _ in self.server.requests))

    def test_real_selected_audio_download_is_remuxed_and_decoded(self):
        with tempfile.TemporaryDirectory(prefix='lume-audio-language-') as temporary:
            directory = Path(temporary)
            resources = generate_media(directory)
            ffmpeg, _ = find_media_tools()
            audio_dir = directory/'english'; audio_dir.mkdir()
            subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                'sine=frequency=880:sample_rate=48000', '-t', '3', '-c:a', 'aac', '-f', 'hls',
                '-hls_time', '1', '-hls_list_size', '0', '-hls_segment_filename', str(audio_dir/'audio-%02d.ts'),
                str(audio_dir/'index.m3u8')], check=True, capture_output=True, timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            resources.update({'/en/'+file.name: file.read_bytes() for file in audio_dir.iterdir()})
            resources['/pt/index.m3u8'] = resources['/hls/index.m3u8']
            self.server.resources.update(resources)
            selected = self.discover()[1]['id']
            plan = resolve_stream(self.url, Control(), selection=selected)
            work = directory/'work'; work.mkdir()
            output = download_stream(plan, work, Control(), concurrency=3)
            self.assertTrue(output['probe']['video']); self.assertTrue(output['probe']['audio'])
            decoded = subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-i',
                str(output['path']), '-map', '0:a:0', '-t', '1', '-ar', '8000', '-ac', '1', '-f', 's16le', '-'],
                check=True, capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            import array
            samples = array.array('h', decoded.stdout)
            crossings = sum(first < 0 <= second for first, second in zip(samples, samples[1:]))
            frequency = crossings/(len(samples)/8000)
            self.assertAlmostEqual(frequency, 880, delta=12)
            self.assertFalse(any(name.startswith('/pt/') for name, _ in self.server.requests))


if __name__ == '__main__':
    unittest.main()
