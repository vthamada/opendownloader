"""Public catalogue/selection contracts; metadata fixtures never download bytes."""
import contextlib
import copy
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

from lume.media import MediaCancelled, MediaError, discover_media, resolve_media
from tests.test_media import FakeYoutubeDL, URL, VIDEO


class PlatformTests(unittest.TestCase):
    @contextlib.contextmanager
    def extractor(self, result):
        FakeYoutubeDL.result, FakeYoutubeDL.error = result, None
        module = types.ModuleType('yt_dlp')
        module.YoutubeDL = FakeYoutubeDL
        module.globals = types.SimpleNamespace(plugin_dirs=types.SimpleNamespace(value=[]))
        with patch.dict('sys.modules', {'yt_dlp': module}):
            yield

    def test_routes_explicit_pages_and_manifests_without_network_or_generic_html(self):
        from lume.media import media_source_kind, is_media_url
        cases = {
            'https://vimeo.com/76979871':'platform',
            'https://player.vimeo.com/video/76979871':'platform',
            'https://www.dailymotion.com/video/x84sh87':'platform',
            'https://dai.ly/x84sh87':'platform',
            'https://archive.org/details/example':'platform',
            'https://example.org/master.M3U8?auth=caller':'hls',
            'https://example.org/static.mpd':'dash',
            URL:'youtube',
        }
        for url, kind in cases.items():
            self.assertEqual(media_source_kind(url),kind,url)
            self.assertEqual(is_media_url(url),kind in ('youtube','platform'),url)
        for url in ('https://vimeo.com.evil.org/123','https://example.org/page','https://archive.org/download/example/a.mp4'):
            self.assertIsNone(media_source_kind(url),url)

    def test_vimeo_progressive_catalogue_uses_own_provider_and_metadata_only(self):
        page='https://vimeo.com/76979871'
        row=source('http-720p','mp4','avc1','mp4a',url='https://vod.example.net/clip.mp4',height=720)
        info={'id':'76979871','title':'Public Vimeo fixture','formats':[row]}
        with self.extractor(info), patch('lume.media.locate_node_runtime',side_effect=AssertionError('Vimeo must not require Node')):
            catalogue=discover_media(page,threading.Event())
            result=resolve_media(page,threading.Event(),selection='http-720p')
        self.assertEqual(FakeYoutubeDL.captured['ie_key'],'Vimeo')
        self.assertFalse(FakeYoutubeDL.captured['download'])
        self.assertIs(FakeYoutubeDL.captured['check_formats'],False)
        self.assertIsNone(FakeYoutubeDL.captured['cookiefile'])
        self.assertEqual(catalogue['filename'],'Vimeo-76979871.mp4')
        self.assertNotIn('stream_url',catalogue['formats'][0])
        self.assertEqual(result['stream_url'],row['url'])
        self.assertEqual(result['headers']['Referer'],'https://player.vimeo.com/video/76979871')
        self.assertEqual(FakeYoutubeDL.captured['called_url'],'https://player.vimeo.com/video/76979871')
        self.assertEqual(result['original_url'],page)
        self.assertEqual(result['filename'],'Vimeo-76979871.mp4')

    def test_hls_provider_variant_delegates_manifest_without_media_transfer(self):
        page='https://www.dailymotion.com/video/x84sh87'
        variant=source('hls-720','mp4','avc1','mp4a',protocol='m3u8_native',url='https://cdn.example.net/720.m3u8?expiry=1')
        with self.extractor({'id':'x84sh87','title':'Clip','formats':[variant]}):
            catalogue=discover_media(page,threading.Event())
            result=resolve_media(page,threading.Event(),selection='hls-720')
        self.assertEqual(result['manifest_protocol'],'hls')
        self.assertEqual(result['manifest_url'],variant['url'])
        self.assertEqual(result['streams'],[])
        self.assertIsNone(result['stream_url'])
        self.assertEqual(catalogue['formats'][0]['id'],'hls-720')
        self.assertNotIn('manifest_url',catalogue['formats'][0])

    def test_archive_filename_identity_is_preserved_separate_from_safe_output_name(self):
        page='https://archive.org/details/example/video.mp4'
        row=source('0','mp4',None,None,url='https://archive.org/download/example/video.mp4')
        with self.extractor({'id':'example/video.mp4','title':'Archive video','formats':[row]}):
            catalogue=discover_media(page,threading.Event())
            result=resolve_media(page,threading.Event(),selection='0')
        self.assertEqual(result['video_id'],'example/video.mp4')
        self.assertEqual(FakeYoutubeDL.captured['ie_key'],'ArchiveOrg')
        self.assertTrue(result['filename'].startswith('ArchiveOrg-'))
        self.assertNotIn('/',result['filename'])
        self.assertEqual(catalogue['formats'][0]['vcodec'],'desconhecido')

    def test_hls_separate_audio_is_paired_only_with_its_exact_master_group(self):
        page='https://vimeo.com/76979871'
        video=source('hls-720','mp4','avc1','none',protocol='m3u8_native',url='https://cdn.example.net/720.m3u8',manifest_url='https://cdn.example.net/master.m3u8')
        audio=source('hls-audio','mp4','none',None,protocol='m3u8_native',url='https://cdn.example.net/audio.m3u8',manifest_url=video['manifest_url'],abr=128)
        other=source('unrelated-audio','mp4','none','mp4a',protocol='m3u8_native',url='https://other.example.net/audio.m3u8',manifest_url='https://other.example.net/master.m3u8',abr=256)
        info={'id':'76979871','formats':[video,audio,other]}
        with self.extractor(info):
            catalogue=discover_media(page,threading.Event())
            result=resolve_media(page,threading.Event(),selection='hls-720+hls-audio')
            automatic=resolve_media(page,threading.Event())
            with self.assertRaises(MediaError):
                resolve_media(page,threading.Event(),selection='hls-720+unrelated-audio')
        self.assertIn('hls-720+hls-audio',{choice['id'] for choice in catalogue['formats']})
        self.assertNotIn('hls-720',{choice['id'] for choice in catalogue['formats']})
        self.assertEqual(result['manifest_audio_url'],audio['url'])
        self.assertEqual(result['manifest_url'],video['url'])
        self.assertEqual(automatic['manifest_audio_url'],audio['url'])
        self.assertEqual(result['streams'],[])
        self.assertTrue(any(choice['id']=='audio:hls-audio' and choice['audio_only'] for choice in catalogue['formats']))

    def test_video_only_manifest_without_audio_identity_is_not_advertised(self):
        row=source('hls-720','mp4','avc1','none',protocol='m3u8_native',url='https://cdn.example.net/720.m3u8')
        with self.extractor({'id':'76979871','formats':[row]}), self.assertRaises(MediaError):
            discover_media('https://vimeo.com/76979871',threading.Event())

    def test_platform_rejects_identity_drm_live_and_insecure_results(self):
        page='https://vimeo.com/76979871'
        row=source('http-720p','mp4','avc1','mp4a',url='https://vod.example.net/clip.mp4')
        for changes in ({'id':'999'}, {'has_drm':True}, {'is_live':True}, {'formats':[{**row,'url':'http://vod.example.net/clip.mp4'}]}):
            with self.subTest(changes=changes), self.extractor({'id':'76979871','formats':[row],**changes}):
                with self.assertRaises(MediaError):
                    discover_media(page,threading.Event())

    def test_playlist_is_refused_before_extractor_can_expand_its_entries(self):
        def process(instance, *args, **kwargs):
            return instance.process_ie_result({'_type':'playlist','entries':iter(range(1_000_000))},download=False)
        with self.extractor({'id':'example'}), patch.object(FakeYoutubeDL,'extract_info',process), \
                patch.object(FakeYoutubeDL,'process_ie_result',side_effect=AssertionError('playlist expansion must not run'),create=True):
            with self.assertRaises(MediaError) as caught:
                discover_media('https://archive.org/details/example',threading.Event())
        self.assertEqual(caught.exception.code,'media_metadata')


def source(identity, ext, video, audio, **changes):
    return {"format_id": identity, "ext": ext, "vcodec": video, "acodec": audio,
            "url": "https://rr1.googlevideo.com/videoplayback?id=" + identity,
            "protocol": "https", "filesize": 1000, "width": 1920 if video != "none" else None,
            "height": 1080 if video != "none" else None, "fps": 30 if video != "none" else None,
            **changes}


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.node = Path(__file__).resolve()
        self.formats = [source("18", "mp4", "avc1", "mp4a", height=360),
                        source("137", "mp4", "avc1", "none"),
                        source("140", "m4a", "none", "mp4a", abr=128),
                        source("248", "webm", "vp9", "none"),
                        source("251", "webm", "none", "opus", abr=160)]
        FakeYoutubeDL.result = {"id": VIDEO, "title": "Catálogo", "_type": "video", "formats": self.formats}
        FakeYoutubeDL.error = None

    @contextlib.contextmanager
    def extractor(self):
        module = types.ModuleType("yt_dlp")
        module.YoutubeDL = FakeYoutubeDL
        module.globals = types.SimpleNamespace(plugin_dirs=types.SimpleNamespace(value=[]))
        with patch.dict("sys.modules", {"yt_dlp": module}):
            yield

    def discover(self):
        with self.extractor():
            return discover_media(URL, self.stop, node_runtime=self.node)

    def resolve(self, selection):
        with self.extractor():
            return resolve_media(URL, self.stop, node_runtime=self.node, selection=selection)

    def test_catalogue_lists_real_variants_and_audio_without_expiring_stream_urls(self):
        result = self.discover()
        choices = {item["id"]: item for item in result["formats"]}
        self.assertEqual(set(choices), {"18", "137+140", "audio:140", "248+251", "audio:251"})
        self.assertEqual(choices["18"]["height"], 360)
        self.assertEqual(choices["137+140"]["expected_size"], 2000)
        self.assertTrue(choices["audio:140"]["audio_only"])
        self.assertEqual(choices["audio:140"]["ext"], "m4a")
        self.assertTrue(all("streams" not in item and "stream_url" not in item for item in choices.values()))
        self.assertFalse(FakeYoutubeDL.captured["download"])
        self.assertTrue(FakeYoutubeDL.captured["skip_download"])
        self.assertIs(FakeYoutubeDL.captured["check_formats"], False)

    def test_explicit_pair_and_audio_selection_preserve_the_actual_source_formats(self):
        result = self.resolve("137+140")
        self.assertEqual([stream["format_id"] for stream in result["streams"]], ["137", "140"])
        self.assertEqual(result["filename"], f"YouTube-{VIDEO}.mp4")
        audio = self.resolve("audio:140")
        self.assertEqual(audio["mimetype"], "audio/mp4")
        self.assertEqual(audio["streams"][0]["role"], "audio")
        self.assertTrue(audio["audio_only"])
        self.assertEqual(audio["filename"], f"YouTube-{VIDEO}.m4a")

    def test_webm_pair_and_opus_audio_have_correct_output_container(self):
        result = self.resolve("248+251")
        self.assertEqual(result["ext"], "webm")
        self.assertEqual(result["filename"], f"YouTube-{VIDEO}.webm")
        self.assertEqual(result["streams"][1]["mimetype"], "audio/webm")
        self.assertEqual(self.resolve("audio:251")["mimetype"], "audio/webm")

    def test_automatic_filename_matches_mp4_default_even_if_higher_webm_is_first(self):
        self.formats[3]["height"] = 2160
        self.formats[3]["width"] = 3840
        catalogue = self.discover()
        self.assertEqual(catalogue["formats"][0]["ext"], "webm")
        self.assertEqual(catalogue["filename"], f"YouTube-{VIDEO}.mp4")
        self.assertEqual(catalogue["formats"][0]["filename"], f"YouTube-{VIDEO}.webm")

    def test_selected_format_disappearing_does_not_silently_choose_another(self):
        self.discover()
        FakeYoutubeDL.result["formats"] = [self.formats[0]]
        with self.assertRaises(MediaError) as caught:
            self.resolve("137+140")
        self.assertEqual(caught.exception.code, "media_selection")

    def test_explicit_audio_pair_survives_a_new_higher_bitrate_alternative(self):
        self.discover()
        self.formats.append(source("141", "m4a", "none", "mp4a", abr=256))
        self.assertEqual([item["format_id"] for item in self.resolve("137+140")["streams"]], ["137", "140"])

    def test_arbitrary_extractor_expressions_are_never_accepted_as_selection(self):
        for value in ["best", "137/18", "137+140/18", "audio:140;touch", "audio:140+141", "137[height>0]", 7, ""]:
            with self.subTest(value=value), self.assertRaises(MediaError):
                self.resolve(value)

    def test_invalid_protected_or_playlist_sources_are_not_advertised(self):
        FakeYoutubeDL.result["formats"].extend([
            source("bad1", "mp4", "avc1", "mp4a", url="https://example.org/file.mp4"),
            source("bad2", "mp4", "avc1", "mp4a", protocol="m3u8_native"),
            source("bad3", "mp4", "avc1", "mp4a", has_drm=True),
        ])
        self.assertFalse(any(item["id"].startswith("bad") for item in self.discover()["formats"]))

    def test_approximate_sizes_remain_unknown_in_catalogue(self):
        for item in self.formats:
            item.pop("filesize")
            item["filesize_approx"] = 1234
        self.assertTrue(all(item["expected_size"] is None for item in self.discover()["formats"]))

    def test_duplicate_format_identity_is_not_guessed(self):
        self.formats.append(copy.deepcopy(self.formats[0]))
        with self.assertRaises(MediaError):
            self.discover()

    def test_cancelled_catalogue_does_not_return_partial_choices(self):
        self.stop.set()
        with self.assertRaises(MediaCancelled):
            self.discover()

    def test_pathological_source_catalogue_is_rejected_before_expansion(self):
        FakeYoutubeDL.result["formats"] = [self.formats[0]] * 1025
        with self.assertRaises(MediaError) as caught:
            self.discover()
        self.assertEqual(caught.exception.code, "media_metadata")

    def test_expired_signed_url_is_replaced_when_resolving_the_selected_format(self):
        self.discover()
        self.formats[1]["url"] = "https://rr2.googlevideo.com/videoplayback?fresh=1"
        selected = self.resolve("137+140")
        self.assertEqual(selected["streams"][0]["stream_url"], self.formats[1]["url"])

    def test_audio_video_role_and_container_mismatch_cannot_be_selected(self):
        for value in ["audio:137", "137", "140", "137+251", "248+140", "18+140"]:
            with self.subTest(value=value), self.assertRaises(MediaError) as caught:
                self.resolve(value)
            self.assertEqual(caught.exception.code, "media_selection")


if __name__ == "__main__":
    unittest.main()
