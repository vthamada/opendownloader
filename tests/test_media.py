"""Media boundary tests use a fake extractor; live checks are separate."""
from __future__ import annotations

import contextlib
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

from lume.media import MediaCancelled, MediaError, is_media_url, resolve_media

VIDEO = "aGCdLKXNF3w"
URL = f"https://www.youtube.com/watch?v={VIDEO}"


class FakeYoutubeDL:
    result = None
    error = None
    captured = {}

    def __init__(self, options):
        FakeYoutubeDL.captured = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, url, *, download, ie_key=None):
        type(self).captured["called_url"] = url
        type(self).captured["ie_key"] = ie_key
        type(self).captured["download"] = download
        if type(self).error:
            raise type(self).error
        return type(self).result

    def urlopen(self, request):
        raise AssertionError("The fake does not access the network")


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.node = Path(__file__).resolve()
        FakeYoutubeDL.result = {
            "id": VIDEO, "title": "Vídeo de teste", "_type": "video",
            "url": "https://rr1.example.googlevideo.com/videoplayback?expire=123",
            "ext": "mp4", "format_id": "18", "vcodec": "avc1", "acodec": "mp4a",
            "protocol": "https", "filesize": 123456, "has_drm": False,
            "http_headers": {
                "User-Agent": "TestUA/1.0", "Accept": "*/*",
                "Cookie": "not forwarded", "Authorization": "not forwarded",
                "X-Unknown": "not forwarded", "Referer": "https://evil.example/",
            },
        }
        FakeYoutubeDL.error = None

    @contextlib.contextmanager
    def extractor(self):
        module = types.ModuleType("yt_dlp")
        module.YoutubeDL = FakeYoutubeDL
        module.globals = types.SimpleNamespace(plugin_dirs=types.SimpleNamespace(value=["default"]))
        with patch.dict("sys.modules", {"yt_dlp": module}):
            yield

    def resolve(self, url=URL):
        with self.extractor():
            return resolve_media(url, self.stop, node_runtime=self.node)

    def test_youtube_pages_are_routed_to_media_not_direct_html(self):
        for url in (URL, f"https://youtu.be/{VIDEO}?t=3",
                    f"https://m.youtube.com/shorts/{VIDEO}",
                    "https://www.youtube.com/playlist?list=abc"):
            self.assertTrue(is_media_url(url), url)
        for url in ("https://example.com/file.mp4", "https://youtube.com.evil.example/watch?v=x",
                    f"https://evil.example/?next={URL}", "file:///video.mp4", "not a URL"):
            self.assertFalse(is_media_url(url), url)

    def test_resolves_only_combined_https_without_download(self):
        result = self.resolve(f"https://youtu.be/{VIDEO}?list=not-followed")
        self.assertEqual(result["video_id"], VIDEO)
        self.assertEqual(result["filename"], f"YouTube-{VIDEO}.mp4")
        self.assertEqual(result["mimetype"], "video/mp4")
        self.assertEqual(result["expected_size"], 123456)
        self.assertNotEqual(result["stream_url"], URL)
        self.assertEqual(set(result["headers"]), {"User-Agent", "Accept", "Referer"})
        self.assertEqual(result["headers"]["Referer"], URL)
        captured = FakeYoutubeDL.captured
        self.assertEqual(captured["called_url"], URL)
        self.assertFalse(captured["download"])
        self.assertIs(captured["check_formats"], False)
        self.assertTrue(captured["noplaylist"])
        self.assertFalse(captured["nocheckcertificate"])
        self.assertFalse(captured["remote_components"])
        self.assertIsNone(captured["cookiefile"])
        self.assertIsNone(captured["cookiesfrombrowser"])
        self.assertFalse(captured["usenetrc"])
        self.assertFalse(captured["cachedir"])
        self.assertIn("[protocol=https]", captured["format"])

    def test_invalid_youtube_video_page_does_not_return_html(self):
        for url in ("https://www.youtube.com/playlist?list=abc",
                    "https://www.youtube.com/watch?v=short",
                    f"https://www.youtube.com/watch?v={VIDEO}&v=BaW_jenozKc"):
            with self.subTest(url=url), self.assertRaises(MediaError):
                self.resolve(url)

    def test_html_manifest_audio_only_split_and_drm_are_rejected(self):
        for changes in (
            {"url": URL},
            {"url": "http://rr1.googlevideo.com/videoplayback"},
            {"url": "https://rr1.googlevideo.com/video.m3u8", "protocol": "m3u8_native"},
            {"ext": "html"},
            {"acodec": "none"},
            {"vcodec": "none"},
            {"requested_formats": [{"format_id": "video"}, {"format_id": "audio"}]},
            {"has_drm": True},
            {"is_live": True},
            {"_type": "playlist", "entries": []},
            {"id": "BaW_jenozKc"},
        ):
            with self.subTest(changes=changes):
                original = dict(FakeYoutubeDL.result)
                FakeYoutubeDL.result.update(changes)
                with self.assertRaises(MediaError):
                    self.resolve()
                FakeYoutubeDL.result = original

    def test_extraction_and_authentication_errors_are_readable(self):
        for text in (
            "Sign in to confirm you're not a bot",
            "This video requires authentication",
            "Requested format is not available",
            "This video is unavailable",
        ):
            with self.subTest(message=text):
                FakeYoutubeDL.error = RuntimeError(text)
                with self.assertRaises(MediaError) as caught:
                    self.resolve()
                self.assertNotIn("Traceback", str(caught.exception))
                self.assertTrue(str(caught.exception))

    def test_cancel_before_and_after_extraction(self):
        self.stop.set()
        with self.assertRaises(MediaCancelled):
            self.resolve()
        self.stop.clear()
        original = FakeYoutubeDL.extract_info

        def cancel(instance, *args, **kwargs):
            self.stop.set()
            return original(instance, *args, **kwargs)

        with patch.object(FakeYoutubeDL, "extract_info", cancel):
            with self.assertRaises(MediaCancelled):
                self.resolve()

    def test_invalid_headers_do_not_cross_to_http_engine(self):
        FakeYoutubeDL.result["http_headers"]["User-Agent"] = "safe\r\nCookie: injected"
        with self.assertRaises(MediaError):
            self.resolve()

    def test_separate_https_video_and_audio_have_distinct_roles(self):
        video = {**FakeYoutubeDL.result, "acodec": "none", "format_id": "137"}
        audio = {**FakeYoutubeDL.result, "vcodec": "none", "ext": "m4a", "format_id": "140"}
        FakeYoutubeDL.result["requested_formats"] = [video, audio]
        result = self.resolve()
        self.assertEqual([stream["role"] for stream in result["streams"]], ["video", "audio"])
        self.assertEqual(result["streams"][1]["mimetype"], "audio/mp4")
        self.assertEqual(result["format_id"], "137+140")
        self.assertIsNone(result["stream_url"])
        self.assertIsNone(result["expected_size"])

    def test_approximate_size_is_not_reported_as_exact(self):
        FakeYoutubeDL.result.pop("filesize")
        FakeYoutubeDL.result["filesize_approx"] = 1234
        self.assertIsNone(self.resolve()["expected_size"])


if __name__ == "__main__":
    unittest.main()

