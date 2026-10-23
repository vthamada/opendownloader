"""Exercise the real Windows binaries with isolated profiles and loopback fixtures."""
from __future__ import annotations

import hashlib
import base64
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_downloads import DATA, DIGEST, FixtureServer
from tests.test_streaming import Fixture as StreamingFixture, generate_media
from tests.test_hls_crypto import encrypt, KEY, IV
from tests.test_acceleration import BalancedServer
from tests.sample_crypto_fixture import sample_aes_resources
from tests.test_cenc_streaming import cenc_resources, KID as CENC_KID, KEY as CENC_KEY
from tests.test_hls_crypto import decoded_hashes
from lume.core import Manager
from lume import VERSION, APP_NAME
from lume.mux import find_media_tools
from lume.deployment import bundle_inventory
from tools.package_extension import collect as collect_extension


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=root / "dist" / "LumeDownloads")
    parser.add_argument('--require-private-runtimes', action='store_true')
    args = parser.parse_args()
    app = args.bundle.resolve() / "LumeDownloads.exe"
    host = app.with_name("LumeNativeHost.exe")
    receipt = {"bundle":str(args.bundle.resolve()),
               "files": bundle_inventory(args.bundle.resolve()),
               "binaries": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (app, host)}, "checks": []}
    manifest, extension_files = collect_extension(args.bundle.resolve()/'browser-extension')
    receipt['extension']={'name':manifest['name'],'version':manifest['version'],'assets':len(extension_files)}
    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="lume-package-") as temp:
        profile = Path(temp)
        env = dict(os.environ, LOCALAPPDATA=str(profile / "local"))
        if args.require_private_runtimes:
            env['PATH'] = ''
        def run(arguments, expected=0, *, private_input=None):
            result = subprocess.run([str(app), *map(str, arguments)], env=env,
                                    creationflags=creation, capture_output=True, timeout=45, input=private_input)
            if result.returncode != expected:
                raise AssertionError(f"Exit code {result.returncode}; expected {expected}.")
            return result
        data_dir = profile / "self-test"
        run(["--self-test", "--data-dir", data_dir])
        receipt["desktop_self_test"] = json.loads((data_dir / "self-test.json").read_text())
        assert receipt["desktop_self_test"]["tk"] == "ok"
        assert receipt["desktop_self_test"]["app_name"] == APP_NAME
        receipt["checks"].append("packaged Tk/SQLite startup")
        assert len(receipt["desktop_self_test"]["button_layout"]) == 6
        assert {"Cancelar", "Salvar agora", "Só adicionar à fila"} <= set(receipt["desktop_self_test"]["recording_buttons"])
        assert receipt["desktop_self_test"]["media_scripts"] == "ok"
        receipt["checks"].append("packaged main/dialog button bounds + bundled EJS resources")
        if args.require_private_runtimes:
            private = args.bundle.resolve() / 'runtimes'
            assert receipt['desktop_self_test']['private_runtime_paths'] == {
                'node': str(private / 'node/node.exe'), 'ffmpeg': str(private / 'ffmpeg/bin/ffmpeg.exe'),
                'ffprobe': str(private / 'ffmpeg/bin/ffprobe.exe')}
            receipt['checks'].append('actual frozen runtime discovery selects all private components with empty PATH')
        server = FixtureServer(profile)
        try:
            files = profile / "files"
            run(["--download", server.url + "/file", "--data-dir", profile / "cli", "--download-dir", files,
                 "--name", "verified.bin", "--sha256", DIGEST])
            assert (files / "verified.bin").read_bytes() == DATA
            receipt["checks"].append("packaged parallel HTTP + byte comparison + SHA256")
            def native_request(payload, *, browser_mode=False):
                body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                arguments = ['chrome-extension://meicfjedeegbopmeifconaiekbmppbai/'] if browser_mode else ['--native-host']
                native = subprocess.run([str(host), *arguments],
                                        input=struct.pack("<I", len(body)) + body, env=env,
                                        creationflags=creation, capture_output=True, timeout=15)
                assert native.returncode == 0, native.stderr
                size = struct.unpack("<I", native.stdout[:4])[0]
                reply = json.loads(native.stdout[4:4 + size])
                assert reply["ok"] is True
                return reply
            diagnosis = native_request({'version': 1, 'command': 'status'}, browser_mode=True)
            assert diagnosis == {'ok': True, 'version': 1, 'app_version': VERSION, 'status': 'ready'}
            assert not (profile / 'local' / 'LumeDownloads').exists()
            receipt['checks'].append('packaged native browser-origin invocation: read-only status creates no profile or desktop')
            native_request({"url": server.url + "/file"})
            inbox = profile / "local" / "LumeDownloads" / "inbox"
            assert len(list(inbox.glob("*.json"))) == 1
            receipt["checks"].append("packaged native host real stdin/stdout framing + durable inbox")
            # This also tests the exact UUID emitted by the real native executable.
            manager = Manager(inbox.parent, files)
            try:
                import time
                until = time.monotonic() + 10
                while not manager.list_requests() and time.monotonic() < until:
                    time.sleep(.02)
                assert manager.list_tasks() == []
                assert not (files / 'file').exists()
                request = manager.list_requests()[0]
                manager.confirm_request(request['id'], files)
                task = manager.list_tasks()[0]
                assert manager.wait(task["id"], 30)["status"] == "completed"
                assert hashlib.sha256((files / "file").read_bytes()).hexdigest() == DIGEST
                run(["--from-browser", "--data-dir", inbox.parent, "--download-dir", files])
                assert len(manager.list_tasks()) == 1
                receipt["checks"].append("packaged browser startup reuses a locked profile without another GUI")
                # Exercise the new player protocol with a title that occupies
                # 256 Unicode code points but 257 UTF-16 code units in JS.
                title = 'a' * 255 + '\U0001f600'
                reply = native_request({'version': 2, 'url': server.url + '/file', 'title': title, 'media': True})
                until = time.monotonic() + 10
                while not manager.list_requests() and time.monotonic() < until:
                    time.sleep(.02)
                pending = manager.list_requests()[0]
                assert pending['title'] == title and pending['media'] is True
                assert pending['id'] == reply['id'].replace('-', '')
                count = len(server.requests)
                held_id = manager.confirm_request(pending['id'], files, filename='media-held.bin', start=False)
                assert manager.wait(held_id)['status'] == 'held'
                time.sleep(.2)
                assert len(server.requests) == count and not (files / 'media-held.bin').exists()
                manager.cancel(held_id)
                receipt['checks'].append('packaged native v2 Unicode/media framing + held item makes zero HTTP requests')
            finally:
                manager.close()
            # Actual frozen native host -> actual frozen local-import CLI.
            # Synthetic generation and independent decode use the existing
            # development FFmpeg; capture selection is a separate browser test.
            oracle, _ = find_media_tools()
            for extension, codec in (("mp4", "libx264"), ("webm", "libvpx-vp9")):
                original = profile / ("record-source." + extension)
                subprocess.run([str(oracle), "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                                "testsrc2=size=320x180:rate=15", "-f", "lavfi", "-i",
                                "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v", codec,
                                "-c:a", "aac" if extension == "mp4" else "libopus", str(original)],
                               capture_output=True, check=True, timeout=30, creationflags=creation)
                capture = original.read_bytes()
                native = subprocess.Popen([str(host), "--native-host"], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          env=env, creationflags=creation)
                def exchange(value):
                    raw = json.dumps(value).encode()
                    native.stdin.write(struct.pack("<I", len(raw)) + raw)
                    native.stdin.flush()
                    head = native.stdout.read(4)
                    assert len(head) == 4, "Frozen native host closed the recording port"
                    reply = json.loads(native.stdout.read(struct.unpack("<I", head)[0]))
                    assert reply.get('ok') is True, reply
                    return reply
                try:
                    started = exchange({'version': 1, 'command': 'recording.start', 'mime': 'video/' + extension,
                                        'title': 'Synthetic package recording', 'audio': True})
                    identity, sequence = started['recording_id'], 0
                    for offset in range(0, len(capture), started['max_chunk_bytes']):
                        chunk = capture[offset:offset+started['max_chunk_bytes']]
                        ack = exchange({'version': 1, 'command': 'recording.chunk', 'recording_id': identity,
                                        'sequence': sequence, 'data': base64.b64encode(chunk).decode()})
                        sequence += 1
                        assert ack['bytes'] == offset + len(chunk)
                    done = exchange({'version': 1, 'command': 'recording.finish', 'recording_id': identity,
                                     'sequence': sequence, 'bytes': len(capture)})
                    assert done['sha256'] == hashlib.sha256(capture).hexdigest()
                    native.stdin.close()
                    assert native.wait(timeout=10) == 0
                finally:
                    if native.poll() is None:
                        native.terminate(); native.wait(timeout=10)
                    for pipe in (native.stdin, native.stdout, native.stderr):
                        if pipe: pipe.close()
                target = files / ('recording.' + extension)
                run(['--save-recording', identity, '--data-dir', profile/'local/LumeDownloads',
                     '--download-dir', files, '--name', target.name])
                assert target.read_bytes() == capture
                assert decoded_hashes(target) == decoded_hashes(original)
                assert not (files / ('.lume-' + identity)).exists()
                receipt['checks'].append('frozen recording ' + extension + ': native streaming + explicit CLI approval + packet validation + exact bytes/full independent video audio decode')
            receipt["checks"].append("packaged-host -> desktop-engine handoff completed once")
            from tests.test_media_container import movie,box
            protected=movie(tail=True);moov=protected[protected.index(b'moov')-4:]
            server.resources['/protected.mp4']=('video/mp4',box(b'ftyp',b'isom'+bytes(4))+box(b'mdat',bytes(4*1024*1024))+moov)
            before=len(server.requests)
            result=run(['--download',server.url+'/protected.mp4','--media','--data-dir',profile/'protected',
                        '--download-dir',files,'--name','protected.mp4'],expected=1)
            task=json.loads(result.stdout)
            assert task['status']=='failed' and 'proteção' in task['error']
            requests=server.requests[before:]
            assert requests and all(headers.get('Range') for _,headers in requests)
            requested=sum(int(h['Range'].split('-')[1])-int(h['Range'].split('=')[1].split('-')[0])+1 for _,h in requests)
            assert requested<192*1024 and not (files/'protected.mp4').exists()
            receipt['early_protection']={'requested_bytes':requested,'source_bytes':len(server.resources['/protected.mp4'][1])}
            receipt['checks'].append('frozen media workflow rejects protected tail-moov MP4 before bulk transfer; bounded ranges and no final file')
        finally:
            server.close()
        tls = FixtureServer(profile, tls=True)
        try:
            run(["--download", tls.url + "/file", "--data-dir", profile / "bad-tls",
                 "--download-dir", profile / "tls-files", "--name", "invalid.bin"], expected=1)
            assert not (profile / "tls-files" / "invalid.bin").exists()
            receipt["checks"].append("packaged TLS rejects untrusted certificate")
        finally:
            tls.close()
        media_root = profile / 'streaming-source'
        media_root.mkdir()
        streaming = StreamingFixture(generate_media(media_root))
        try:
            for protocol,path in (('hls','/hls/index.m3u8'),('dash','/dash/index.mpd')):
                result=run(['--download',streaming.url+path,'--data-dir',profile/('packaged-'+protocol),
                            '--download-dir',files,'--name',protocol+'.mp4'])
                task=json.loads(result.stdout)
                file=files/(protocol+'.mp4')
                assert hashlib.sha256(file.read_bytes()).hexdigest()==task['sha256']
                probe=subprocess.run([str(find_media_tools()[1]),'-v','error','-show_entries','stream=codec_type',
                                      '-of','json',str(file)],capture_output=True,check=True,timeout=15,creationflags=creation)
                kinds={row['codec_type'] for row in json.loads(probe.stdout)['streams']}
                assert {'video','audio'}<=kinds
                receipt['checks'].append('packaged '+protocol.upper()+' complete segments + FFmpeg + independent SHA256/ffprobe')
            from lume.streaming import discover_stream
            import threading
            streaming.resources['/sub-master.m3u8']='''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Português",LANGUAGE="pt-BR",URI="hls/index.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English",LANGUAGE="en",URI="sub.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=96x64,AUDIO="a",SUBTITLES="s"
hls/index.m3u8
'''
            streaming.resources['/sub.m3u8']='#EXTM3U\n#EXTINF:3,\nsub.vtt\n#EXT-X-ENDLIST\n'
            streaming.resources['/sub.vtt']='WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\n00:00:00.100 --> 00:00:02.000\nSelected package subtitle\n'
            choices=discover_stream(streaming.url+'/sub-master.m3u8',threading.Event())
            chosen=next(c for c in choices['formats'] if c.get('subtitle_label'))
            result=run(['--download',streaming.url+'/sub-master.m3u8','--format-id',chosen['id'],
                        '--data-dir',profile/'packaged-subtitles','--download-dir',files,'--name','subtitles.mp4'])
            task=json.loads(result.stdout);assert task['status']=='completed'
            decoder=find_media_tools()[0]
            subtitle=subprocess.run([str(decoder),'-v','error','-i',str(files/'subtitles.mp4'),'-map','0:s:0','-f','webvtt','-'],capture_output=True,check=True,timeout=15,creationflags=creation)
            assert b'Selected package subtitle' in subtitle.stdout
            assert not (files/('.lume-'+task['id'])).exists()
            receipt['checks'].append('frozen chosen HLS audio+WebVTT -> MP4 subtitle encoding, independent extracted text and cleanup')
            # Independent fixture producer uses Cryptodome; frozen runtime uses
            # cryptography. Compare decoded video AND audio against clear HLS.
            plain = streaming.resources['/hls/index.m3u8'].decode('utf-8')
            streaming.resources['/aes/index.m3u8'] = plain.replace('#EXTM3U',
                '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key",IV=0x'+IV.hex(), 1)
            streaming.resources['/aes/key'] = KEY
            for name,data in list(streaming.resources.items()):
                if name.startswith('/hls/') and name.endswith('.ts'):
                    streaming.resources[name.replace('/hls/','/aes/',1)] = encrypt(data)
            result = run(['--download',streaming.url+'/aes/index.m3u8','--data-dir',profile/'packaged-aes',
                          '--download-dir',files,'--name','aes.mp4'])
            task=json.loads(result.stdout)
            assert task['status']=='completed'
            assert hashlib.sha256((files/'aes.mp4').read_bytes()).hexdigest()==task['sha256']
            # The distributed helper intentionally disables encoders. FFmpeg's
            # decoded hash muxer still requires rawvideo/PCM encoders, so use
            # the existing independent build-machine oracle, not an app fallback.
            # The real frozen download above retains empty PATH/private tools.
            decoder = find_media_tools()[0]
            version=subprocess.run([str(decoder),'-version'],capture_output=True,check=True,timeout=10,creationflags=creation)
            receipt['decoded_media_oracle']={'path':str(decoder),'sha256':hashlib.sha256(Path(decoder).read_bytes()).hexdigest(),
                'version':version.stdout.decode('utf-8',errors='replace').splitlines()[0]}
            receipt['aes_decoded_hashes']={}
            for stream in ('v:0','a:0'):
                hashes=[]
                for name in ('hls.mp4','aes.mp4'):
                    decoded=subprocess.run([str(decoder),'-nostdin','-v','error','-i',str(files/name),
                        '-map','0:'+stream,'-f','hash','-hash','sha256','-'],env=env,
                        capture_output=True,check=True,timeout=20,creationflags=creation)
                    assert not decoded.stderr
                    hashes.append(decoded.stdout)
                assert hashes[0]==hashes[1]
                receipt['aes_decoded_hashes'][stream]=hashes[1].decode().strip()
            assert len([name for name,_ in streaming.requests if name=='/aes/key'])==1
            assert not (files/('.lume-'+task['id'])).exists()
            receipt['checks'].append('packaged authorized HLS AES-128 + one ephemeral key read + full decoded video/audio SHA256 equality + cleanup')
            if args.require_private_runtimes:
                streaming.resources.update(sample_aes_resources(streaming.resources))
                result=run(['--download',streaming.url+'/sample/index.m3u8','--data-dir',profile/'packaged-sample',
                            '--download-dir',files,'--name','sample.mp4'])
                sample=json.loads(result.stdout)
                assert sample['status']=='completed'
                assert decoded_hashes(files/'sample.mp4')==decoded_hashes(files/'hls.mp4')
                assert not (files/('.lume-'+sample['id'])).exists()
                receipt['checks'].append('packaged SAMPLE-AES MPEG-TS H264/AAC + private helper + full decoded hashes + cleanup')
                crypto_source=profile/'crypto-fixtures';crypto_source.mkdir()
                encrypted,expected=cenc_resources(crypto_source)
                streaming.resources.update(encrypted)
                private_input=(CENC_KID+':'+CENC_KEY+'\n').encode('ascii')
                receipt['cenc_modes']={}
                for mode in ('cenc','cens','cbc1','cbcs'):
                    data=profile/('packaged-'+mode)
                    result=run(['--download',streaming.url+'/independent-'+mode+'/index.mpd',
                                '--data-dir',data,'--download-dir',files,'--name',mode+'.mp4','--keys-stdin'],
                                private_input=private_input)
                    task=json.loads(result.stdout)
                    assert task['status']=='completed'
                    assert decoded_hashes(files/(mode+'.mp4'))==expected
                    assert hashlib.sha256((files/(mode+'.mp4')).read_bytes()).hexdigest()==task['sha256']
                    assert bytes.fromhex(CENC_KEY) not in (data/'queue.sqlite3').read_bytes()
                    assert CENC_KEY.encode() not in (data/'queue.sqlite3').read_bytes()
                    assert not (files/('.lume-'+task['id'])).exists()
                    receipt['cenc_modes'][mode]={'decoded_video_audio_match':True,'sha256':task['sha256']}
                fragment_reference=profile/'fragment-reference.mp4'
                subprocess.run([str(decoder),'-nostdin','-v','error','-n','-i',str(crypto_source/'fmp4/index.m3u8'),
                                '-c','copy',str(fragment_reference)],capture_output=True,check=True,timeout=20,creationflags=creation)
                expected_fragmented=decoded_hashes(fragment_reference)
                for mode in ('cenc','cens','cbc1','cbcs'):
                    result=run(['--download',streaming.url+'/fragmented-'+mode+'/index.mpd',
                                '--data-dir',profile/('packaged-fragmented-'+mode),'--download-dir',files,
                                '--name','fragmented-'+mode+'.mp4','--keys-stdin'],private_input=private_input)
                    task=json.loads(result.stdout)
                    assert task['status']=='completed'
                    assert decoded_hashes(files/('fragmented-'+mode+'.mp4'))==expected_fragmented
                    assert not (files/('.lume-'+task['id'])).exists()
                    receipt['cenc_modes'][mode]['real_fragmented_video_audio_match']=True
                receipt['checks'].append('packaged CENC cenc/cens/cbc1/cbcs flat+fragmented MP4 + stdin-only keys + full decoded hashes + no persisted keys')
            # An extensionless manifest must retain the browser's parsed hint.
            streaming.resources['/manifest']=streaming.resources['/hls/index.m3u8'].replace(b'seg-',b'hls/seg-')
            before=len(streaming.requests)
            native_request({'version':3,'url':streaming.url+'/manifest','title':'Streaming','media':True,'source':'hls'})
            manager=Manager(inbox.parent,files)
            try:
                until=time.monotonic()+10
                while not manager.list_requests() and time.monotonic()<until:time.sleep(.02)
                pending=manager.list_requests()[0]
                assert pending['source']=='hls' and pending['filename'].endswith('.mp4')
                task_id=manager.confirm_request(pending['id'],files,filename='native-stream.mp4',start=False)
                assert manager.wait(task_id)['status']=='held' and len(streaming.requests)==before
                manager.resume(task_id)
                assert manager.wait(task_id,20)['status']=='completed'
                receipt['checks'].append('packaged native v3 extensionless hint + held/no HTTP -> source Manager complete transfer')
            finally:manager.close()
        finally:streaming.close()
        large = bytes(range(256)) * (28 * 1024 * 1024 // 256)
        balanced = BalancedServer(large, delay=.001)
        try:
            result = run(['--download',balanced.url,'--data-dir',profile/'packaged-balanced',
                          '--download-dir',files,'--name','balanced.bin','--parts','8'])
            task = json.loads(result.stdout)
            assert task['status']=='completed'
            assert task['error']==''
            assert task['sha256']==hashlib.sha256(large).hexdigest()
            assert hashlib.sha256((files/'balanced.bin').read_bytes()).hexdigest()==task['sha256']
            assert len(balanced.requests)>8 and len(balanced.clients)<=8
            assert not (files / ('.lume-' + task['id'])).exists()
            receipt['acceleration']={'bytes':len(large),'ranges':len(balanced.requests),
                                     'tcp_connections':len(balanced.clients),'sha256':task['sha256']}
            receipt['checks'].append('packaged 28 MiB bounded block scheduling + 8-worker setting + connection reuse + SHA256')
        finally:balanced.close()
    assert bundle_inventory(args.bundle.resolve()) == receipt['files'], 'Bundle changed during verification.'
    output = root / ".cache" / "package-verification.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
