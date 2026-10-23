"""Local recorded-container scans, plus explicit malformed/process boundaries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from lume.media import MediaCancelled
from lume.mux import MuxCleanupPending, MuxError, find_media_tools
from lume.recording_media import validate_recording


class RecordingMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.path = self.work / "part-0.bin"
        self.path.write_bytes(b"fixture")
        self.stop = threading.Event()

    def probe(self, *, audio=True, duration="N/A"):
        streams = [{"codec_type": "video", "codec_name": "vp9", "width": 64,
                    "height": 32, "nb_read_packets": "3"}]
        if audio:
            streams.append({"codec_type": "audio", "codec_name": "opus", "nb_read_packets": "8"})
        return {"streams": streams, "format": {"format_name": "matroska,webm",
                 "size": str(self.path.stat().st_size), "duration": duration}}

    def run_probe(self, data, *, ext="webm", audio=True):
        with patch("lume.recording_media.find_media_tools", return_value=(Path("ffmpeg"), Path("ffprobe"))), patch(
            "lume.recording_media._run", return_value=json.dumps(data).encode()
        ) as runner:
            facts = validate_recording(self.work, self.path, ext, audio, self.stop)
        return facts, runner.call_args

    def test_packet_scan_is_local_bounded_and_preserves_unknown_duration(self):
        before = self.path.read_bytes()
        facts, called = self.run_probe(self.probe())
        self.assertEqual(facts["duration"], None)
        self.assertTrue(facts["video"] and facts["audio"])
        self.assertEqual(facts["streams"][0]["nb_read_packets"], 3)
        command, stop, timeout = called.args
        self.assertIs(stop, self.stop)
        self.assertEqual(timeout, 600)
        self.assertEqual(called.kwargs, {"work": self.work.resolve()})
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(command[command.index("-f") + 1], "matroska")
        self.assertEqual(command[command.index("-max_streams") + 1], "2")
        self.assertEqual(command[command.index("-max_alloc") + 1], str(64 * 1024 ** 2))
        self.assertIn("-count_packets", command)
        self.assertNotIn("-show_packets", command)
        self.assertEqual(self.path.read_bytes(), before)

    def test_mp4_disables_external_references_and_preserves_duration(self):
        data = self.probe()
        data["streams"][0]["codec_name"] = "h264"
        data["streams"][1]["codec_name"] = "aac"
        data["format"].update(format_name="mov,mp4,m4a,3gp,3g2,mj2", duration="1.25")
        facts, called = self.run_probe(data, ext="mp4")
        self.assertEqual(facts["duration"], 1.25)
        self.assertEqual(called.args[0][called.args[0].index("-enable_drefs") + 1], "0")

    def test_silent_capture_is_valid_only_when_not_promising_audio(self):
        self.assertFalse(self.run_probe(self.probe(audio=False), audio=False)[0]["audio"])
        with self.assertRaisesRegex(MuxError, "faixas prometidas"):
            self.run_probe(self.probe(audio=False))

    def test_malformed_media_facts_fail_closed(self):
        variants = []
        for field, value in (("codec_name", "unknown"), ("codec_name", ["vp9"]),
                             ("width", 999999), ("height", True),
                             ("nb_read_packets", "0"), ("nb_read_packets", "N/A")):
            data = self.probe()
            data["streams"][0][field] = value
            variants.append(data)
        for duration in ("nan", "inf", -1, True, "9999999"):
            data = self.probe(duration=duration)
            variants.append(data)
        for field, value in (("size", "1"), ("format_name", "hls")):
            data = self.probe()
            data["format"][field] = value
            variants.append(data)
        data = self.probe()
        data["streams"].append(dict(data["streams"][0]))
        variants.extend([data, {"streams": []}, {"error": {"code": 1}}, []])
        for value in variants:
            with self.subTest(value=value), self.assertRaises(MuxError):
                self.run_probe(value)

    def test_duplicate_json_nonfinite_and_truncation_fail_closed(self):
        for raw in (b'{"streams":[],"streams":[]}', b'{"streams":NaN}', b'{"streams":'):
            with self.subTest(raw=raw), patch("lume.recording_media.find_media_tools", return_value=(Path("f"), Path("p"))), patch(
                "lume.recording_media._run", return_value=raw
            ), self.assertRaises(MuxError):
                validate_recording(self.work, self.path, "webm", True, self.stop)

    def test_unadvertised_container_and_invalid_declaration_do_not_spawn(self):
        for ext, audio in (("hls", True), ("mp4 -i", True), ([], True), ("webm", 1)):
            with self.subTest(ext=ext), patch("lume.recording_media.find_media_tools", side_effect=AssertionError("No spawn")), self.assertRaises(MuxError):
                validate_recording(self.work, self.path, ext, audio, self.stop)

    def test_unowned_empty_and_link_sources_are_rejected(self):
        outside = self.work.parent / "outside.mp4"
        outside.write_bytes(b"unowned")
        with self.assertRaisesRegex(MuxError, "pasta de trabalho"):
            validate_recording(self.work, outside, "mp4", True, self.stop)
        self.path.write_bytes(b"")
        with self.assertRaisesRegex(MuxError, "vazio"):
            validate_recording(self.work, self.path, "mp4", True, self.stop)
        self.path.write_bytes(b"owned")
        link = self.work / "link.bin"
        try:
            os.link(self.path, link)
        except OSError:
            return  # Hardlink availability is not required for the other boundaries.
        with self.assertRaisesRegex(MuxError, "links adicionais"):
            validate_recording(self.work, link, "mp4", True, self.stop)

    def test_symlink_source_is_rejected_before_resolving(self):
        link = self.work / "link.bin"
        try:
            link.symlink_to(self.path)
        except OSError as error:
            self.skipTest(f"Symlink privilege unavailable: {error}")
        with self.assertRaisesRegex(MuxError, "links"):
            validate_recording(self.work, link, "mp4", True, self.stop)

    def test_source_mutation_during_probe_is_not_accepted(self):
        data = self.probe()
        def mutate(*args, **kwargs):
            self.path.write_bytes(b"changed source bytes")
            return json.dumps(data).encode()
        with patch("lume.recording_media.find_media_tools", return_value=(Path("f"), Path("p"))), patch(
            "lume.recording_media._run", side_effect=mutate
        ), self.assertRaisesRegex(MuxError, "mudou durante"):
            validate_recording(self.work, self.path, "webm", True, self.stop)

    def test_cancellation_and_cleanup_pending_preserve_process_semantics(self):
        self.stop.set()
        with patch("lume.recording_media.find_media_tools", side_effect=AssertionError("No spawn")), self.assertRaises(MediaCancelled):
            validate_recording(self.work, self.path, "webm", True, self.stop)
        self.stop.clear()
        pending = MuxCleanupPending(self.work, 123, "cleanup pending")
        with patch("lume.recording_media.retry_pending_cleanup", side_effect=pending), self.assertRaises(MuxCleanupPending) as raised:
            validate_recording(self.work, self.path, "webm", True, self.stop)
        self.assertIs(raised.exception, pending)

    def real_fixture(self, ext, audio):
        try:
            ffmpeg, _ = find_media_tools()
        except MuxError as error:
            self.skipTest(str(error))
        self.path.unlink()
        command = [str(ffmpeg), "-hide_banner", "-v", "error", "-nostdin", "-n", "-f", "lavfi",
                   "-i", "testsrc2=size=64x48:rate=5"]
        if audio:
            command.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"])
        command.extend(["-t", "0.6", "-c:v", "libvpx-vp9" if ext == "webm" else "libx264"])
        if audio:
            command.extend(["-c:a", "libopus" if ext == "webm" else "aac"])
        else:
            command.append("-an")
        command.extend(["-f", ext, str(self.path)])
        result = subprocess.run(command, capture_output=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode and b"Unknown encoder" in result.stderr:
            self.skipTest("Configured FFmpeg cannot encode the local test fixture.")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_real_mp4_full_packet_scan_preserves_input_bytes(self):
        self.real_fixture("mp4", True)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        facts = validate_recording(self.work, self.path, "mp4", True, self.stop)
        self.assertEqual({row["codec_name"] for row in facts["streams"]}, {"h264", "aac"})
        self.assertTrue(all(row["nb_read_packets"] > 0 for row in facts["streams"]))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_real_webm_video_only_is_valid(self):
        self.real_fixture("webm", False)
        facts = validate_recording(self.work, self.path, "webm", False, self.stop)
        self.assertTrue(facts["video"])
        self.assertFalse(facts["audio"])
        self.assertEqual(facts["streams"][0]["codec_name"], "vp9")

    def test_real_invalid_mp4_is_rejected_and_retained(self):
        self.path.write_bytes(b"<!doctype html><html>not a capture</html>")
        before = self.path.read_bytes()
        with self.assertRaises(MuxError):
            validate_recording(self.work, self.path, "mp4", False, self.stop)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
