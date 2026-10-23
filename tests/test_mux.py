"""Real local-media tests plus owned-process interruption/resource checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lume.media import MediaCancelled
from lume.mux import (MuxCleanupPending, MuxError, _run, find_media_tools, mux_media,
                      pending_work, retry_pending_cleanup)


class MuxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.stop = threading.Event()

    def fixtures(self):
        try:
            ffmpeg, _ = find_media_tools()
        except MuxError as error:
            self.skipTest(str(error))
        video, audio = self.work / "video.mp4", self.work / "audio.m4a"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for command in (
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
             "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=5", "-t", "0.4",
             "-an", "-c:v", "mpeg4", str(video)],
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
             "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.4",
             "-vn", "-c:a", "aac", str(audio)],
        ):
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30, creationflags=flags)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return video, audio

    def test_mux_and_probe_confirm_audio_and_video_without_changing_inputs(self):
        video, audio = self.fixtures()
        original = (video.read_bytes(), audio.read_bytes())
        output = self.work / "combined.mp4"
        result = mux_media(self.work, [video, audio], output, self.stop)
        self.assertTrue(result["video"])
        self.assertTrue(result["audio"])
        self.assertGreater(result["bytes"], 0)
        self.assertEqual({row["codec_type"] for row in result["streams"]}, {"video", "audio"})
        self.assertEqual((video.read_bytes(), audio.read_bytes()), original)

    def test_webm_video_and_opus_audio_are_muxed_without_conversion(self):
        try:
            ffmpeg, _ = find_media_tools()
        except MuxError as error:
            self.skipTest(str(error))
        video, audio = self.work / "video.webm", self.work / "audio.webm"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for command in (
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "lavfi",
             "-i", "color=c=blue:s=32x32:r=5", "-t", "0.4", "-an", "-c:v", "libvpx",
             "-deadline", "realtime", str(video)],
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "lavfi",
             "-i", "anullsrc=r=48000:cl=mono", "-t", "0.4", "-vn", "-c:a", "libopus", str(audio)],
        ):
            completed = subprocess.run(command, capture_output=True, timeout=30, creationflags=flags)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        original = (video.read_bytes(), audio.read_bytes())
        result = mux_media(self.work, [video, audio], self.work / "combined.webm", self.stop,
                           output_ext="webm", video_ext="webm", audio_ext="webm")
        self.assertEqual({stream["codec_name"] for stream in result["streams"]}, {"vp8", "opus"})
        self.assertEqual((video.read_bytes(), audio.read_bytes()), original)

    def test_unadvertised_container_cannot_select_an_arbitrary_demuxer(self):
        source = self.work / "source.tmp"
        source.write_bytes(b"not opened")
        with patch("lume.mux.find_media_tools", side_effect=AssertionError("No child for invalid container")):
            for extension in ["concat", "hls", "m3u8", "mp4 -i file"]:
                with self.subTest(extension=extension), self.assertRaises(MuxError):
                    mux_media(self.work, [source], self.work / "output.tmp", self.stop,
                              output_ext=extension, video_ext=extension)

    def test_existing_output_and_unowned_paths_are_preserved(self):
        video, audio = self.fixtures()
        existing = self.work / "existing.mp4"
        existing.write_bytes(b"preserve")
        with self.assertRaises(MuxError):
            mux_media(self.work, [video, audio], existing, self.stop)
        self.assertEqual(existing.read_bytes(), b"preserve")
        with self.assertRaises(MuxError):
            mux_media(self.work, [video, audio], self.root / "outside.mp4", self.stop)
        self.assertFalse((self.root / "outside.mp4").exists())

    def test_invalid_container_is_not_published_as_video(self):
        self.fixtures()
        html = self.work / "fake.mp4"
        html.write_bytes(b"<!doctype html><html>not video</html>")
        with self.assertRaises(MuxError):
            mux_media(self.work, [html], self.work / "bad-output.mp4", self.stop)

    def test_stopped_before_mux_creates_no_output(self):
        self.stop.set()
        output = self.work / "canceled.mp4"
        with self.assertRaises(MediaCancelled):
            mux_media(self.work, [], output, self.stop)
        self.assertFalse(output.exists())

    def test_owned_process_cancel_returns_promptly(self):
        timer = threading.Timer(.2, self.stop.set)
        timer.start()
        start = time.monotonic()
        try:
            with self.assertRaises(MediaCancelled):
                _run([sys.executable, "-c", "import time; time.sleep(30)"], self.stop, 10)
        finally:
            timer.cancel()
            timer.join()
        self.assertLess(time.monotonic() - start, 5)

    def test_owned_process_timeout_and_bounded_output(self):
        start = time.monotonic()
        with self.assertRaises(MuxError):
            _run([sys.executable, "-c", "import time; time.sleep(30)"], self.stop, .15)
        self.assertLess(time.monotonic() - start, 5)
        with self.assertRaises(MuxError):
            _run([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000)"], self.stop, 10)

    def test_reader_start_failure_still_settles_the_real_child(self):
        original = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        with patch("lume.mux.subprocess.Popen", side_effect=spawn), patch(
            "lume.mux.threading.Thread.start", side_effect=RuntimeError("no reader thread")
        ):
            with self.assertRaises(MuxError):
                _run([sys.executable, "-c", "import time; time.sleep(30)"], self.stop, 10)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def make_pending(self, work, cleanup_error=None):
        original = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            terminate = process.terminate

            def blocked():
                if cleanup_error is not None:
                    raise cleanup_error
                raise OSError("temporary cleanup denial")

            process.terminate = blocked
            children.append((process, terminate))
            return process

        with patch("lume.mux.subprocess.Popen", side_effect=spawn):
            with self.assertRaises(MuxCleanupPending) as caught:
                _run([sys.executable, "-c", "import time; time.sleep(30)"], self.stop, .05, work=work)
        process, terminate = children[0]
        self.assertEqual(caught.exception.work, work.resolve())
        self.assertEqual(caught.exception.process_pid, process.pid)

        def cleanup():
            process.terminate = terminate
            retry_pending_cleanup(work)

        self.addCleanup(cleanup)
        return process, terminate

    def test_cleanup_failure_retains_work_and_retry_releases_only_selected_owner(self):
        other = self.root / "other-work"
        other.mkdir()
        first, first_terminate = self.make_pending(self.work)
        second, _ = self.make_pending(other)
        self.assertTrue(pending_work(self.work))
        self.assertTrue(pending_work(other))
        first.terminate = first_terminate
        retry_pending_cleanup(self.work)
        self.assertFalse(pending_work(self.work))
        self.assertTrue(pending_work(other))
        self.assertIsNotNone(first.poll())
        self.assertIsNone(second.poll())

    def test_global_retry_settles_successes_even_when_another_owner_still_fails(self):
        other = self.root / "other-work"
        other.mkdir()
        first, _ = self.make_pending(self.work)
        second, second_terminate = self.make_pending(other)
        second.terminate = second_terminate
        with self.assertRaises(MuxCleanupPending):
            retry_pending_cleanup()
        self.assertTrue(pending_work(self.work))
        self.assertFalse(pending_work(other))
        self.assertIsNone(first.poll())
        self.assertIsNotNone(second.poll())

    def test_interruption_during_final_cleanup_retains_owner_for_retry(self):
        process, terminate = self.make_pending(self.work, KeyboardInterrupt("cleanup interrupted"))
        self.assertTrue(pending_work(self.work))
        self.assertIsNone(process.poll())
        process.terminate = terminate
        retry_pending_cleanup(self.work)
        self.assertFalse(pending_work(self.work))
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()

