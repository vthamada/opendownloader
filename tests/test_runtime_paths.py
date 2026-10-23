"""Portable runtime discovery and real local mux without modifying PATH."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from lume import runtime_paths
from lume.media import MediaError, locate_node_runtime
from lume.mux import MuxError, find_media_tools, mux_media


class RuntimePathsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='lume-runtime-paths-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.application=self.root/'application'
        package=self.application/'lume';package.mkdir(parents=True)
        self.anchor=package/'runtime_paths.py';self.anchor.touch()
        self.node_name='node.exe' if os.name=='nt' else 'node'
        self.ffmpeg_name='ffmpeg.exe' if os.name=='nt' else 'ffmpeg'
        self.ffprobe_name='ffprobe.exe' if os.name=='nt' else 'ffprobe'

    @contextmanager
    def source(self):
        with patch.object(runtime_paths,'__file__',str(self.anchor)), \
             patch.object(sys,'frozen',False,create=True):
            yield

    def file(self,path):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture only; never executed')
        return path.resolve()

    def test_source_roots_ignore_interpreter_and_unrelated_extraction(self):
        with self.source(),patch.object(sys,'executable',str(self.root/'python/python.exe')), \
             patch.object(sys,'_MEIPASS',str(self.root/'unrelated'),create=True):
            self.assertEqual(runtime_paths.application_directory(),self.application)
            self.assertEqual(runtime_paths.private_runtime_roots(),
                (self.application/'runtimes',self.application/'_internal/runtimes'))

    def test_frozen_roots_prefer_executable_sidecar_then_actual_internal_directory(self):
        internal=self.root/'unpacked'
        with patch.object(sys,'frozen',True,create=True), \
             patch.object(sys,'executable',str(self.application/'LumeDownloads.exe')), \
             patch.object(sys,'_MEIPASS',str(internal),create=True):
            self.assertEqual(runtime_paths.application_directory(),self.application)
            self.assertEqual(runtime_paths.private_runtime_roots(),
                (self.application/'runtimes',internal/'runtimes'))

    def test_frozen_without_meipass_uses_internal_next_to_executable(self):
        with patch.object(sys,'frozen',True,create=True), \
             patch.object(sys,'executable',str(self.application/'LumeDownloads.exe')):
            absent=object();previous=getattr(sys,'_MEIPASS',absent)
            sys.__dict__.pop('_MEIPASS',None)
            try:
                self.assertEqual(runtime_paths.private_runtime_roots(),
                    (self.application/'runtimes',self.application/'_internal/runtimes'))
            finally:
                if previous is not absent:sys._MEIPASS=previous

    def test_explicit_node_remains_first_and_missing_explicit_does_not_fall_back(self):
        private=self.file(self.application/'runtimes/node'/self.node_name)
        explicit=self.file(self.root/'selected'/self.node_name)
        with self.source(),patch('lume.media.shutil.which',side_effect=AssertionError('Unexpected PATH lookup')):
            self.assertEqual(locate_node_runtime(explicit),explicit)
            self.assertEqual(locate_node_runtime(),private)
            with self.assertRaises(MediaError):locate_node_runtime(self.root/'missing')

    def test_private_node_sidecar_precedes_internal_and_ignores_empty_path(self):
        sidecar=self.file(self.application/'runtimes/node'/self.node_name)
        self.file(self.application/'_internal/runtimes/node'/self.node_name)
        with self.source(),patch.dict(os.environ,{'PATH':''}):
            self.assertEqual(locate_node_runtime(),sidecar)

    def test_node_internal_and_existing_codex_and_path_fallbacks(self):
        internal=self.file(self.application/'_internal/runtimes/node'/self.node_name)
        with self.source():self.assertEqual(locate_node_runtime(),internal)
        internal.unlink()
        home=self.root/'home'
        codex=self.file(home/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
        with self.source(),patch.object(Path,'home',return_value=home):
            self.assertEqual(locate_node_runtime(),codex)
            codex.unlink()
            path_node=self.file(self.root/'from-path'/self.node_name)
            with patch('lume.media.shutil.which',return_value=str(path_node)):
                self.assertEqual(locate_node_runtime(),path_node)

    def test_private_ffmpeg_requires_its_matching_probe_instead_of_mixing_tools(self):
        tool=self.file(self.application/'runtimes/ffmpeg/bin'/self.ffmpeg_name)
        with self.source():
            with self.assertRaisesRegex(MuxError,'privado'):find_media_tools()
        probe=self.file(tool.with_name(self.ffprobe_name))
        with self.source(),patch('lume.mux.shutil.which',side_effect=AssertionError('Unexpected PATH lookup')):
            self.assertEqual(find_media_tools(),(tool,probe))

    def test_existing_ffmpeg_fallback_remains_available_without_private_payload(self):
        expected=find_media_tools()
        with self.source():self.assertEqual(find_media_tools(),expected)

    def test_real_private_internal_ffmpeg_muxes_and_probes_with_empty_child_path(self):
        existing=find_media_tools()
        private=self.application/'_internal/runtimes/ffmpeg/bin';private.mkdir(parents=True)
        for source in [*existing,*existing[0].parent.glob('*.dll')]:
            shutil.copy2(source,private/source.name)
        work=self.root/'work';work.mkdir()
        video=work/'video.mp4';audio=work/'audio.m4a'
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with patch.object(sys,'frozen',True,create=True), \
             patch.object(sys,'executable',str(self.application/'LumeDownloads.exe')), \
             patch.object(sys,'_MEIPASS',str(self.application/'_internal'),create=True), \
             patch.dict(os.environ,{'PATH':''}):
            ffmpeg,ffprobe=find_media_tools()
            self.assertEqual(ffmpeg,private/existing[0].name)
            self.assertEqual(ffprobe,private/existing[1].name)
            for command in (
                [str(ffmpeg),'-nostdin','-v','error','-n','-f','lavfi','-i','color=c=blue:s=32x32:r=5',
                 '-t','0.4','-an','-c:v','mpeg4',str(video)],
                [str(ffmpeg),'-nostdin','-v','error','-n','-f','lavfi','-i','anullsrc=r=22050:cl=mono',
                 '-t','0.4','-vn','-c:a','aac',str(audio)],
            ):
                created=subprocess.run(command,capture_output=True,timeout=30,creationflags=flags)
                self.assertEqual(created.returncode,0,created.stderr.decode(errors='replace'))
            original=(video.read_bytes(),audio.read_bytes())
            result=mux_media(work,[video,audio],work/'combined.mp4',threading.Event())
            self.assertTrue(result['video']);self.assertTrue(result['audio'])
            self.assertEqual((video.read_bytes(),audio.read_bytes()),original)


if __name__=='__main__':
    unittest.main()
