"""Independent synthetic fragmented-MP4 Common Encryption test producer.

Uses clear fragments from generate_media and Cryptodome sample encryption;
the production MOV demuxer does not produce or interpret these test records.
The fixture deliberately supports one moof/mdat and one trun per track per
fragment, with explicit moof-relative offsets. It is not a general BMFF writer.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import tempfile

from tests.cenc_crypto_fixture import _box, _boxes, _encrypt


def _protected_init(data, scheme, kid):
    track_number=-1
    def rewrite(kind,content,video=None):
        nonlocal track_number
        if kind==b'trak':
            track_number+=1
            video=track_number==0
        if kind in {b'moov',b'trak',b'mdia',b'minf',b'stbl'}:
            return _box(kind,b''.join(rewrite(k,v,video) for k,v in _boxes(content)))
        if kind==b'stsd':
            assert int.from_bytes(content[4:8],'big')==1
            entries=list(_boxes(content[8:]));assert len(entries)==1
            original,sample_entry=entries[0]
            assert original==(b'avc1' if video else b'mp4a')
            pattern=(0x19 if video else 0x10) if scheme in {'cens','cbcs'} else 0
            tenc=bytes([1 if pattern else 0,0,0,0,0,pattern,1,16])+kid
            sinf=_box(b'sinf',_box(b'frma',original)+_box(b'schm',b'\0'*4+scheme.encode()+struct.pack('>I',0x10000))+
                      _box(b'schi',_box(b'tenc',tenc)))
            return _box(kind,content[:8]+_box(b'encv' if video else b'enca',sample_entry+sinf))
        return _box(kind,content)
    return b''.join(rewrite(k,v) for k,v in _boxes(data))


def _fragment(data, records):
    # senc grows moof; every moof-relative sample data offset must move by
    # exactly that growth, preserving tfdt timing and untouched mdat layout.
    addition=sum(16+sum(len(value) for value in samples) for samples in records.values())
    def rewrite(kind,content,track=None):
        if kind==b'moof':
            return _box(kind,b''.join(rewrite(k,v) for k,v in _boxes(content)))
        if kind==b'traf':
            children=list(_boxes(content))
            headers=[payload for child,payload in children if child==b'tfhd']
            assert len(headers)==1
            track=int.from_bytes(headers[0][4:8],'big')
            flags=int.from_bytes(headers[0][:4],'big')&0xffffff
            assert flags & 0x020000 and not flags & 1, 'fixture needs default-base-is-moof'
            samples=records[track]
            senc=_box(b'senc',b'\0\0\0\2'+struct.pack('>I',len(samples))+b''.join(samples))
            return _box(kind,b''.join(rewrite(k,v,track) for k,v in children)+senc)
        if kind==b'trun':
            content=bytearray(content)
            flags=int.from_bytes(content[:4],'big')&0xffffff
            assert flags & 1, 'fixture needs an explicit trun data offset'
            offset=int.from_bytes(content[8:12],'big',signed=True)
            content[8:12]=(offset+addition).to_bytes(4,'big',signed=True)
            return _box(kind,content)
        if kind==b'sidx':
            content=bytearray(content)
            version=content[0]
            start=32 if version else 24
            count=int.from_bytes(content[start-2:start],'big')
            assert count==1
            size=int.from_bytes(content[start:start+4],'big')
            assert size>>31==0
            content[start:start+4]=(size+addition).to_bytes(4,'big')
            return _box(kind,content)
        return _box(kind,content)
    result=b''.join(rewrite(k,v) for k,v in _boxes(data))
    assert len(result)==len(data)+addition
    return result


def encrypt_fragmented_resources(resources, *, scheme='cenc', key: bytes,
                                 kid: bytes, prefix='/fragmented',
                                 ffprobe=Path('C:/ffmpeg/bin/ffprobe.exe')):
    """Return real encrypted init + three moof/mdat fragments and a DASH MPD."""
    assert scheme in {'cenc','cens','cbc1','cbcs'} and len(key)==len(kid)==16
    init=resources['/fmp4/init.mp4']
    result={prefix+'/init.mp4':_protected_init(init,scheme,kid)}
    with tempfile.TemporaryDirectory(prefix='od-fragment-producer-') as temporary:
        clear=Path(temporary)/'sample.mp4'
        for number in range(3):
            fragment=bytearray(resources[f'/fmp4/seg-{number:02d}.m4s'])
            clear.write_bytes(init+fragment)
            probe=subprocess.run([str(ffprobe),'-v','error','-show_packets','-show_streams','-of','json',str(clear)],
                                 capture_output=True,check=True,timeout=20,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            facts=json.loads(probe.stdout)
            assert [s['codec_type'] for s in facts['streams']]==['video','audio']
            records={1:[],2:[]}
            for packet in facts['packets']:
                track=int(packet['stream_index'])+1
                offset=int(packet['pos'])-len(init);size=int(packet['size'])
                assert 0<=offset<offset+size<=len(fragment)
                iv=bytes([track,number])+len(records[track]).to_bytes(14,'big')
                encrypted,metadata=_encrypt(bytes(fragment[offset:offset+size]),key=key,iv=iv,scheme=scheme,video=track==1)
                fragment[offset:offset+size]=encrypted
                records[track].append(metadata)
            assert all(records.values())
            result[f'{prefix}/seg-{number:02d}.m4s']=_fragment(bytes(fragment),records)
    result[prefix+'/index.mpd']=(
        '<MPD type="static" mediaPresentationDuration="PT3S" xmlns="urn:mpeg:dash:schema:mpd:2011">'
        '<Period><AdaptationSet mimeType="video/mp4" contentType="video">'
        '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="'+scheme+'"/>'
        '<Representation id="muxed" bandwidth="100000"><SegmentList><Initialization sourceURL="init.mp4"/>'+
        ''.join(f'<SegmentURL media="seg-{n:02d}.m4s"/>' for n in range(3))+
        '</SegmentList></Representation></AdaptationSet></Period></MPD>')
    return result
