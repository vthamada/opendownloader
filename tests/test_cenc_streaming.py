"""Explicit caller keys through real DASH/HTTP and the private native helper."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from lume.core import Manager
from lume.crypto_keys import parse_authorized_keys
from lume.network import Control, DownloadError
from lume.streaming import discover_stream, resolve_stream, download_stream, owned_stream_files
from tests.test_streaming import Fixture, generate_media
from tests.test_hls_crypto import decoded_hashes
from tests.cenc_crypto_fixture import encrypt_mp4
from tests.fragmented_crypto_fixture import encrypt_fragmented_resources

KID = "fedcba98765432100123456789abcdef"
KEY = "00112233445566778899aabbccddeeff"
PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / ".cache/release-1.10.0/crypto-tools"


def cenc_resources(folder):
    """FFmpeg's independent encryption muxer produces a real two-track MP4."""
    resources = generate_media(folder)
    oracle = Path("C:/ffmpeg/bin/ffmpeg.exe")
    common = [str(oracle),"-nostdin","-v","error","-n","-i",str(folder/"hls/index.m3u8"),"-c","copy","-movflags","+faststart"]
    plain, cipher = folder/"clear.mp4",folder/"encrypted.mp4"
    for target, options in ((plain,[]),(cipher,["-encryption_scheme","cenc-aes-ctr","-encryption_kid",KID,"-encryption_key",KEY])):
        subprocess.run([*common,*options,str(target)],capture_output=True,check=True,timeout=25,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    data=cipher.read_bytes()
    offset=0
    while data[offset+4:offset+8] != b"mdat":
        offset+=int.from_bytes(data[offset:offset+4],"big")
    resources["/cenc/init.mp4"] = data[:offset]
    resources["/cenc/movie.mp4"] = data[offset:]
    resources["/cenc/index.mpd"] = ('<MPD type="static" mediaPresentationDuration="PT3S" xmlns="urn:mpeg:dash:schema:mpd:2011">'
        '<Period><AdaptationSet mimeType="video/mp4" contentType="video">'
        '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/>'
        '<Representation id="muxed" bandwidth="100000"><SegmentList><Initialization sourceURL="init.mp4"/><SegmentURL media="movie.mp4"/>'
        '</SegmentList></Representation></AdaptationSet></Period></MPD>')
    end=folder/"clear-end.mp4"
    subprocess.run([str(oracle),"-nostdin","-v","error","-n","-i",str(plain),"-c","copy",str(end)],
                   capture_output=True,check=True,timeout=25,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    for scheme in ("cenc","cens","cbc1","cbcs"):
        target=folder/(scheme+"-independent.mp4")
        encrypt_mp4(end,target,key=bytes.fromhex(KEY),kid=bytes.fromhex(KID),scheme=scheme)
        data=target.read_bytes();split=int.from_bytes(data[:4],"big")
        prefix="/independent-"+scheme
        resources[prefix+"/init.mp4"]=data[:split]
        resources[prefix+"/movie.mp4"]=data[split:]
        resources[prefix+"/index.mpd"]=resources["/cenc/index.mpd"].replace('value="cenc"','value="'+scheme+'"')
        resources.update(encrypt_fragmented_resources(resources,scheme=scheme,key=bytes.fromhex(KEY),
                         kid=bytes.fromhex(KID),prefix="/fragmented-"+scheme))
    return resources,decoded_hashes(plain)


class CencStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (TOOLS/"OpenDownloaderCrypto.exe").is_file():
            raise unittest.SkipTest("Build the pinned private crypto helper/test tools first.")
        cls.generated=tempfile.TemporaryDirectory(prefix="od-cenc-fixture-")
        cls.resources,cls.expected=cenc_resources(Path(cls.generated.name))
        cls.fragment_baseline=Path(cls.generated.name)/"fragmented-clear.mp4"
        subprocess.run(["C:/ffmpeg/bin/ffmpeg.exe","-nostdin","-v","error","-n","-i",
                        str(Path(cls.generated.name)/"fmp4/index.m3u8"),"-c","copy",str(cls.fragment_baseline)],
                       capture_output=True,check=True,timeout=25,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        cls.expected_fragmented=decoded_hashes(cls.fragment_baseline)

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="od-cenc-work-")
        self.work=Path(self.temp.name)
        self.server=Fixture(self.resources)
        self.private=patch("lume.mux.find_media_tools",return_value=(TOOLS/"ffmpeg.exe",TOOLS/"ffprobe.exe"))
        self.private.start()
        self.helper=patch("lume.mux.find_crypto_helper",return_value=TOOLS/"OpenDownloaderCrypto.exe")
        self.helper.start()
        self.keys=parse_authorized_keys(KID+":"+KEY)

    def tearDown(self):
        self.helper.stop();self.private.stop();self.server.close();self.temp.cleanup()

    def plan(self):
        return resolve_stream(self.server.url+"/cenc/index.mpd",Control())

    def test_metadata_is_available_without_keys_or_media_requests(self):
        catalogue=discover_stream(self.server.url+"/cenc/index.mpd",threading.Event())
        self.assertEqual(len(catalogue["formats"]),1)
        self.assertEqual(self.plan().tracks[0].protection,"cenc")
        self.assertTrue(all(path.endswith(".mpd") for path,_ in self.server.requests))

    def test_missing_keys_fail_before_media_transfer(self):
        with self.assertRaises(DownloadError) as caught:
            download_stream(self.plan(),self.work,Control())
        self.assertEqual(caught.exception.code,"stream_keys")
        self.assertFalse(any(path.endswith(".mp4") for path,_ in self.server.requests))

    def test_explicit_keys_on_clear_hls_are_rejected_before_media_requests(self):
        plan=resolve_stream(self.server.url+"/hls/index.m3u8",Control())
        self.server.requests.clear()
        with self.assertRaises(DownloadError) as caught:
            download_stream(plan,self.work,Control(),authorized_keys=self.keys)
        self.assertEqual(caught.exception.code,"authorized_keys_unused")
        self.assertEqual(self.server.requests,[])

    def test_download_matches_independent_full_decoded_video_and_audio(self):
        result=download_stream(self.plan(),self.work,Control(),authorized_keys=self.keys)
        self.assertEqual(decoded_hashes(result["path"]),self.expected)
        for path in owned_stream_files(self.work):
            self.assertFalse(path.name.endswith(".clear"))
            self.assertNotIn(bytes.fromhex(KEY),path.read_bytes())
            self.assertNotIn(KEY.encode(),path.read_bytes())

    def test_four_independently_encrypted_cenc_modes_and_wrong_keys(self):
        for scheme in ("cenc","cens","cbc1","cbcs"):
            with self.subTest(scheme=scheme):
                area=self.work/scheme;area.mkdir()
                plan=resolve_stream(self.server.url+"/independent-"+scheme+"/index.mpd",Control())
                result=download_stream(plan,area,Control(),authorized_keys=self.keys)
                self.assertEqual(decoded_hashes(result["path"]),self.expected)
                bad=self.work/(scheme+"-bad");bad.mkdir()
                with self.assertRaises(Exception):
                    download_stream(plan,bad,Control(),authorized_keys=parse_authorized_keys(KID+":"+"00"*16))
                self.assertFalse((bad/"streaming-output.mp4").exists())

    def test_wrong_key_and_missing_kid_never_produce_final_output(self):
        for text in (KID+":"+"00"*16,"00"*16+":"+KEY):
            with self.subTest(text=text[:32]):
                area=self.work/text[:8];area.mkdir(exist_ok=True)
                # Different keys bind different resumable identities; isolate attempts.
                child=area/("wrong" if text.startswith(KID) else "missing");child.mkdir()
                with self.assertRaises(Exception):
                    download_stream(self.plan(),child,Control(),authorized_keys=parse_authorized_keys(text))
                self.assertFalse((child/"streaming-output.mp4").exists())
                self.assertFalse(any(path.name.endswith(".clear") for path in owned_stream_files(child)))

    def test_four_modes_with_real_fragmented_initialization_and_moof_mdat(self):
        for scheme in ("cenc","cens","cbc1","cbcs"):
            with self.subTest(scheme=scheme):
                area=self.work/scheme;area.mkdir()
                plan=resolve_stream(self.server.url+"/fragmented-"+scheme+"/index.mpd",Control())
                self.assertEqual(len(plan.tracks[0].segments),4)
                result=download_stream(plan,area,Control(),authorized_keys=self.keys,concurrency=3)
                self.assertEqual(decoded_hashes(result["path"]),self.expected_fragmented)

    def test_key_change_rejects_cached_ciphertext_until_explicit_restart(self):
        plan=self.plan()
        download_stream(plan,self.work,Control(),authorized_keys=self.keys)
        self.server.requests.clear()
        with self.assertRaises(DownloadError) as caught:
            download_stream(plan,self.work,Control(),authorized_keys=parse_authorized_keys(KID+":"+"00"*16))
        self.assertEqual(caught.exception.code,"stream_changed")
        self.assertEqual(self.server.requests,[])

    def test_real_manager_completes_and_persists_no_key_material(self):
        manager=Manager(self.work/"profile",self.work/"files")
        try:
            task=manager.add(self.server.url+"/cenc/index.mpd",self.work/"files",authorized_keys=self.keys)
            result=manager.wait(task,30)
            self.assertEqual(result["status"],"completed",result["error"])
            self.assertEqual(decoded_hashes(self.work/"files"/result["filename"]),self.expected)
            self.assertNotIn(KEY,json.dumps(result))
        finally:
            manager.close()
        self.assertNotIn(bytes.fromhex(KEY),(self.work/"profile/queue.sqlite3").read_bytes())


if __name__ == "__main__":
    unittest.main()
