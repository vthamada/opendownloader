"""Resolve at the public queue boundary; transfer/publication still use the HTTP engine."""
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
import shutil
import subprocess
import threading
from unittest.mock import patch

from lume.browser import enqueue_link
from lume.core import Manager
from lume.network import DownloadError
from lume.media import MediaError
from test_downloads import FixtureServer, DATA, DIGEST


class MediaQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lume-media-queue-")
        self.root = Path(self.temporary.name)
        (self.root / "files").mkdir()
        self.server = FixtureServer(self.root)
        self.server.content_type = "video/mp4"
        self.manager = Manager(self.root / "data", self.root / "files")
        self.url = "https://www.youtube.com/watch?v=BaW_jenozKc"
        self.resolved = dict(original_url=self.url, video_id="BaW_jenozKc", title="Fixture video",
                             filename="YouTube-BaW_jenozKc.mp4", mimetype="video/mp4",
                             stream_url=self.server.url + "/file", headers={"User-Agent": "LumeMediaFixture"},
                             format_id="18", expected_size=len(DATA))

    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.temporary.cleanup()

    def test_browser_youtube_link_downloads_resolved_video_and_preserves_input(self):
        with patch("lume.core.resolve_media", return_value=self.resolved) as resolver:
            request = enqueue_link(self.root / "data", self.url)
            until = time.monotonic() + 5
            while not self.manager.list_requests() and time.monotonic() < until:
                time.sleep(.02)
            self.assertEqual(self.server.requests, [])
            self.manager.confirm_request(request.replace('-', ''), self.root / 'files')
            task = self.manager.wait(request.replace("-", ""), 10)
        self.assertEqual(task["status"], "completed", task["error"])
        self.assertEqual(task["source_url"], self.url)
        self.assertEqual(task["url"], self.url)
        self.assertEqual(task["media"]["video_id"], "BaW_jenozKc")
        self.assertEqual(task["sha256"], DIGEST)
        self.assertEqual(hashlib.sha256((self.root / "files" / task["filename"]).read_bytes()).hexdigest(), DIGEST)
        resolver.assert_called_once()
        self.assertTrue(all(headers["User-Agent"] == "LumeMediaFixture" for _, headers in self.server.requests))

    def test_html_from_resolved_endpoint_is_failed_and_never_published(self):
        self.server.content_type = "text/html; charset=utf-8"
        with patch("lume.core.resolve_media", return_value=self.resolved):
            identity = self.manager.add(self.url, self.root / "files")
            task = self.manager.wait(identity)
        self.assertEqual(task["status"], "failed")
        self.assertIn("vídeo", task["error"])
        self.assertFalse((self.root / "files" / task["filename"]).exists())
        self.assertEqual(task["downloaded"], 0)

    def test_provider_unavailable_reports_reason_without_fetching_webpage(self):
        with patch("lume.core.resolve_media", side_effect=MediaError("Vídeo indisponível neste momento.")):
            identity = self.manager.add(self.url, self.root / "files")
            task = self.manager.wait(identity)
        self.assertEqual(task["status"], "failed")
        self.assertIn("indisponível", task["error"])
        self.assertEqual(self.server.requests, [])

    def test_selected_name_is_preserved_and_resolved_collision_is_not_overwritten(self):
        destination = self.root / "files" / self.resolved["filename"]
        destination.write_bytes(b"earlier file")
        with patch("lume.core.resolve_media", return_value=self.resolved):
            identity = self.manager.add(self.url, self.root / "files")
            self.assertEqual(self.manager.wait(identity)["status"], "failed")
            self.assertEqual(destination.read_bytes(), b"earlier file")
            custom = self.manager.add(self.url, self.root / "files", filename="my-video.mp4")
            task = self.manager.wait(custom)
        self.assertEqual(task["status"], "completed", task["error"])
        self.assertEqual(task["filename"], "my-video.mp4")

    def test_size_divergence_fails_before_video_is_published(self):
        self.resolved["expected_size"] += 1
        with patch("lume.core.resolve_media", return_value=self.resolved):
            identity = self.manager.add(self.url, self.root / "files")
            task = self.manager.wait(identity)
        self.assertEqual(task["status"], "failed")
        self.assertIn("tamanho", task["error"])
        self.assertFalse((self.root / "files" / task["filename"]).exists())

    def test_selected_audio_uses_audio_mime_and_keeps_original_url(self):
        self.server.resources['/audio'] = ('audio/mp4', DATA)
        resolved = dict(self.resolved, filename='YouTube-BaW_jenozKc.m4a', mimetype='audio/mp4',
                        ext='m4a', audio_only=True, format_id='140', stream_url=self.server.url+'/audio',
                        streams=[dict(role='audio', ext='m4a', mimetype='audio/mp4', format_id='140',
                                      expected_size=len(DATA), headers={}, stream_url=self.server.url+'/audio')])
        with patch('lume.core.resolve_media', return_value=resolved) as resolver:
            identity = self.manager.add(self.url, self.root/'files', media_selection='audio:140')
            task = self.manager.wait(identity, 10)
        resolver.assert_called_once_with(self.url, unittest.mock.ANY, selection='audio:140')
        self.assertEqual(task['status'], 'completed', task['error'])
        self.assertEqual(task['source_url'], self.url)
        self.assertTrue(task['media']['audio_only'])
        self.assertEqual((self.root/'files'/task['filename']).read_bytes(), DATA)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Existing FFmpeg/ffprobe required')
    def test_selected_webm_tracks_publish_playable_video_and_audio(self):
        video, audio = self.root/'video.webm', self.root/'audio.webm'
        creation = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        for args in (["-f","lavfi","-i","color=c=blue:s=64x64:r=10","-t","1","-an","-c:v","libvpx",str(video)],
                     ["-f","lavfi","-i","sine=frequency=440:sample_rate=48000","-t","1","-vn","-c:a","libopus",str(audio)]):
            subprocess.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-n',*args], creationflags=creation,
                           capture_output=True,check=True,timeout=15)
        self.server.resources.update({'/video':('video/webm',video.read_bytes()),'/audio':('audio/webm',audio.read_bytes())})
        resolved = dict(self.resolved, filename='YouTube-BaW_jenozKc.webm', mimetype='video/webm',
                        ext='webm', stream_url=None, format_id='248+251', expected_size=None,
                        streams=[dict(role=role, ext='webm', mimetype=mime, format_id=fmt,
                                      expected_size=file.stat().st_size, headers={}, stream_url=self.server.url+'/'+role)
                                 for role,mime,fmt,file in [('video','video/webm','248',video),('audio','audio/webm','251',audio)]])
        with patch('lume.core.resolve_media',return_value=resolved) as resolver:
            identity=self.manager.add(self.url,self.root/'files',media_selection='248+251')
            task=self.manager.wait(identity,15)
        self.assertEqual(task['status'],'completed',task['error'])
        self.assertEqual(task['media']['ext'],'webm')
        final=self.root/'files'/task['filename']
        result=subprocess.run([shutil.which('ffprobe'),'-v','error','-show_entries','stream=codec_name','-of','csv=p=0',str(final)],
                              creationflags=creation,capture_output=True,check=True,timeout=15)
        self.assertEqual(set(result.stdout.decode().split()),{'vp8','opus'})
        self.assertEqual(hashlib.sha256(final.read_bytes()).hexdigest(),task['sha256'])
        self.assertFalse(self.manager._work(task).exists())

    def test_pending_process_cleanup_blocks_file_reuse_and_queue_release_until_retry_succeeds(self):
        identity = self.manager.add(self.url, self.root / "files", scheduled_at=time.time()+3600)
        self.manager.pause(identity)
        work = self.manager._work(self.manager.tasks[identity])
        with patch("lume.core.pending_work", return_value=True), patch(
            "lume.core.retry_pending_cleanup", side_effect=MediaError("Processo ainda ocupado.")
        ):
            for action in (self.manager.resume, self.manager.restart, self.manager.remove):
                with self.subTest(action=action.__name__), self.assertRaises(DownloadError):
                    action(identity)
                self.assertTrue((work / "owner.json").is_file())
            with self.assertRaises(DownloadError):
                self.manager.wait(identity, .1)
            with self.assertRaises(DownloadError):
                self.manager.close()
            self.assertFalse(self.manager.closed)
        self.manager.close()
        self.assertTrue(self.manager.closed)
        self.assertTrue((work / "owner.json").is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Existing FFmpeg/ffprobe required")
    def test_separate_http_tracks_produce_playable_mp4_with_audio_and_video(self):
        video, audio = self.root / "video.mp4", self.root / "audio.m4a"
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for args in (
            ["-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10", "-t", "1", "-an", "-c:v", "libx264", str(video)],
            ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1", "-vn", "-c:a", "aac", str(audio)],
        ):
            subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-n", *args],
                           creationflags=creation, capture_output=True, check=True, timeout=15)
        self.server.resources.update({"/video": ("video/mp4", video.read_bytes()),
                                      "/audio": ("audio/mp4", audio.read_bytes())})
        self.resolved.update(stream_url=None, headers={}, format_id="137+140", expected_size=None,
                             streams=[dict(role=role, stream_url=self.server.url + "/" + role,
                                           mimetype=mime, headers={}, expected_size=file.stat().st_size, format_id=fmt)
                                      for role, mime, file, fmt in (("video", "video/mp4", video, "137"),
                                                                    ("audio", "audio/mp4", audio, "140"))])
        with patch("lume.core.resolve_media", return_value=self.resolved):
            identity = self.manager.add(self.url, self.root / "files")
            task = self.manager.wait(identity, 30)
        self.assertEqual(task["status"], "completed", task["error"])
        final = self.root / "files" / task["filename"]
        # Independent probe of the published file, not the resolver's declared codecs.
        result = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
                                 "-of", "csv=p=0", str(final)], creationflags=creation,
                                capture_output=True, check=True, timeout=15)
        self.assertEqual(set(result.stdout.decode().split()), {"video", "audio"})
        self.assertEqual(hashlib.sha256(final.read_bytes()).hexdigest(), task["sha256"])
        self.assertFalse(self.manager._work(task).exists())


if __name__ == "__main__":
    unittest.main()
