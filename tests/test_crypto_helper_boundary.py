"""Real private helper boundary contracts not covered by HTTP workflows."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lume.mux import find_media_tools,find_crypto_helper,MuxError
from tests.cenc_crypto_fixture import encrypt_mp4


TOOLS=Path(__file__).resolve().parents[1]/'.cache/release-1.10.0/crypto-tools'
KEY=b'authorized-key01'
KID=bytes.fromhex('fedcba98765432100123456789abcdef')


class CryptoHelperBoundaryTests(unittest.TestCase):
    def test_private_key_recipient_never_falls_back_to_external_ffmpeg(self):
        with patch('lume.mux.private_runtime_roots',return_value=()),patch('lume.mux.find_media_tools') as external:
            with self.assertRaises(MuxError):find_crypto_helper()
            external.assert_not_called()

    @classmethod
    def setUpClass(cls):
        cls.helper=TOOLS/'OpenDownloaderCrypto.exe'
        if not cls.helper.is_file():
            raise unittest.SkipTest('Build the pinned private crypto helper/test tools first.')
        cls.generated=tempfile.TemporaryDirectory(prefix='opendownloader-crypto-boundary-')
        cls.root=Path(cls.generated.name)
        ffmpeg,_=find_media_tools()
        cls.clear=cls.root/'clear.mp4'
        subprocess.run([str(ffmpeg),'-v','error','-nostdin','-n','-f','lavfi','-i',
                        'testsrc2=s=96x64:r=10','-t','1','-c:v','libx264','-preset',
                        'ultrafast',str(cls.clear)],check=True,capture_output=True,timeout=20,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        cls.encrypted=cls.root/'encrypted.mp4'
        encrypt_mp4(cls.clear,cls.encrypted,key=KEY,kid=KID)

    @classmethod
    def tearDownClass(cls):
        cls.generated.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='opendownloader-helper-work-')
        self.work=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self,mode,source,output,*,records=None):
        env={key:os.environ[key] for key in ('SystemRoot','WINDIR','TEMP','TMP') if key in os.environ}
        env['PATH']=''
        records=records if records is not None else (KID.hex()+':'+KEY.hex()+'\n').encode()
        result=subprocess.run([str(self.helper),mode,str(source),str(output)],input=records,
                              capture_output=True,timeout=15,env=env,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertNotIn(KEY.hex().encode(),result.stdout+result.stderr)
        return result

    def test_secret_input_has_strict_bounded_unambiguous_records(self):
        record=(KID.hex()+':'+KEY.hex()+'\n').encode()
        examples=(b'',record[:-1],record*2,b'x'*5000+b'\n',b'\0'+record,
                  record.replace(b'a',b'g'),
                  b''.join((f'{n:032x}:'+KEY.hex()+'\n').encode() for n in range(17)))
        for content in examples:
            with self.subTest(length=len(content)):
                output=self.work/'never-created.mp4'
                result=self.invoke('mov',self.encrypted,output,records=content)
                self.assertEqual(result.returncode,2)
                self.assertEqual(result.stderr.strip(),b'crypto_keys')
                self.assertFalse(output.exists())

    def test_existing_output_survives_no_overwrite_failure(self):
        output=self.work/'existing.mp4'
        output.write_bytes(b'existing caller file')
        result=self.invoke('mov',self.encrypted,output)
        self.assertEqual(result.returncode,1)
        self.assertEqual(output.read_bytes(),b'existing caller file')

    def test_unicode_windows_paths_with_private_dlls_and_empty_path(self):
        folder=self.work/'vídeos autorização 日本語'
        folder.mkdir()
        source=folder/'episódio.mp4'
        source.write_bytes(self.encrypted.read_bytes())
        output=folder/'vídeo completo.mp4'
        result=self.invoke('mov',source,output)
        self.assertEqual(result.returncode,0,result.stderr)
        ffmpeg,_=find_media_tools()
        def video_hash(path):
            return subprocess.run([str(ffmpeg),'-v','error','-nostdin','-i',str(path),
                                   '-map','0:v:0','-f','hash','-hash','sha256','-'],
                                  capture_output=True,check=True,timeout=20,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
        self.assertEqual(video_hash(output),video_hash(self.clear))

    def test_sample_descriptor_refuses_network_foreign_files_and_unknown_key(self):
        rows=(
            'http://127.0.0.1:9/never\t'+KID.hex()+'\t'+'0'*32+'\n',
            '../outside.bin\t'+KID.hex()+'\t'+'0'*32+'\n',
            'streaming-unexpected.bin\t'+KID.hex()+'\t'+'0'*32+'\n',
            'streaming-seg-0-0.bin\t'+'0'*32+'\t'+'0'*32+'\n',
        )
        for row in rows:
            with self.subTest(row=row.split('\t')[0]):
                descriptor=self.work/'streaming-protected-0.sample'
                descriptor.write_text(row,encoding='ascii')
                output=self.work/'never.mp4'
                result=self.invoke('sample',descriptor,output)
                self.assertEqual(result.returncode,1)
                self.assertFalse(output.exists())

    def test_sample_descriptor_requires_every_segment_and_bounded_rows(self):
        descriptor=self.work/'streaming-protected-0.sample'
        for content in ('streaming-seg-0-0.bin\t-\t-\n',
                        'streaming-seg-0-0.bin\t-\t-\n'*10001,
                        'x'*(1024*1024+1)):
            descriptor.write_text(content,encoding='ascii')
            output=self.work/'never.mp4'
            result=self.invoke('sample',descriptor,output)
            self.assertEqual(result.returncode,1)
            self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
