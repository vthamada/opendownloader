"""Bounded local MP4 protection detection; never keys, payloads or decoding.

Recognizing no protected sample entry does not prove a playable/clear file.
Other formats remain unclassified. HTTP byte downloads keep their own contract.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import struct

from .network import Control, DownloadError, content_length, content_range, validator

MAX_BOXES = 100000
MAX_DESCRIPTIONS = 4096
REMOTE_BLOCK = 64 * 1024
MAX_REMOTE_BYTES = 2 * 1024 * 1024
MAX_REMOTE_REQUESTS = 32
_CHILD = {b'root':b'moov', b'moov':b'trak', b'trak':b'mdia', b'mdia':b'minf', b'minf':b'stbl'}


class _InspectionUnavailable(Exception):
    pass


class _RemoteMetadata:
    """Seekable metadata view, pinned to the probe's resource identity."""
    def __init__(self, identity, transport, control, headers):
        self.identity=identity;self.transport=transport;self.control=control
        self.headers=headers or {};self.position=0;self.cache_start=0;self.cache=b''
        self.requests=0;self.received=0

    def seek(self, position):
        self.position=position

    def read(self, count):
        self.control.check()
        if count<0 or count>REMOTE_BLOCK:
            raise _InspectionUnavailable('metadata_budget')
        start=self.position;total=self.identity['total']
        if start>=total:return b''
        count=min(count,total-start)
        if not self.cache_start<=start or start+count>self.cache_start+len(self.cache):
            end=min(total-1,start+REMOTE_BLOCK-1)
            if self.requests>=MAX_REMOTE_REQUESTS or self.received+end-start+1>MAX_REMOTE_BYTES:
                raise _InspectionUnavailable('metadata_budget')
            headers={**self.headers,'Range':f'bytes={start}-{end}','If-Range':self.identity['validator']}
            self.requests+=1
            with self.transport.open(self.identity['url'],headers,self.control) as owned:
                response=owned.response
                if response.status==200:
                    raise _InspectionUnavailable('range_ignored_or_changed')
                if (response.status!=206 or owned.url!=self.identity['url']
                        or validator(response)!=self.identity['validator']
                        or content_range(response.getheader('Content-Range'))!=(start,end,total)
                        or content_length(response) not in (None,end-start+1)):
                    raise DownloadError('changed','A identidade da mídia mudou durante a análise antecipada. Nenhuma mídia foi transferida.')
                body=response.read(end-start+2)
                self.received+=len(body)
                if len(body)!=end-start+1:
                    raise DownloadError('length','Metadados da mídia recebidos incompletos.')
            self.cache_start=start;self.cache=body
        offset=start-self.cache_start;self.position+=count
        return self.cache[offset:offset+count]


def inspect_remote_mp4(identity, transport, control, headers=None):
    """Best-effort early inspection; unknown must never mean clear or playable.

    Only bounded GET ranges with a stable validator are read. The existing local
    publication guard remains authoritative for unsupported servers/formats.
    """
    control.check()
    if not identity.get('ranges') or not identity.get('validator') or not identity.get('total'):
        return {'status':'unknown','reason':'no_stable_ranges','requests':0,'bytes':0}
    reader=_RemoteMetadata(identity,transport,control,headers)
    try:
        status=_inspect(reader,identity['total'],control)
        reason='no_protected_sample_entry' if status=='inspected' else 'unclassified_format'
    except _InspectionUnavailable as unavailable:
        status='unknown';reason=str(unavailable)
    return {'status':status or 'unknown','reason':reason,'requests':reader.requests,'bytes':reader.received}


def _identity(value):
    return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns


def _plain(value):
    return (stat.S_ISREG(value.st_mode) and value.st_nlink == 1
            and not getattr(value,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400))


def reject_protected_mp4(path: Path, control: Control) -> None:
    """Reject known protected sample entries before publishing a media result.

    Seek over mdat and unknown boxes; inspect only the movie/sample-description
    hierarchy. Allocation is constant and work is bounded by box count, not the
    size of the downloaded video. No format conversion or network is performed.
    """
    control.check()
    path=Path(path);before=path.lstat()
    if not _plain(before):
        raise DownloadError('ownership','A mídia foi substituída ou não é um arquivo regular; preservada.')
    with path.open('rb', buffering=0) as file:
        opened=os.fstat(file.fileno())
        if not _plain(opened) or _identity(before)!=_identity(opened):
            raise DownloadError('ownership','A identidade da mídia mudou durante a inspeção; preservada.')
        try:
            _inspect(file,opened.st_size,control)
        finally:
            after=os.fstat(file.fileno());named=path.lstat()
            if (not _plain(after) or not _plain(named)
                    or _identity(opened)!=_identity(after) or _identity(after)!=_identity(named)):
                raise DownloadError('ownership','A mídia mudou durante a inspeção; partes preservadas.')


def _inspect(file, total, control):
    head=file.read(8)
    if len(head)<8 or head[4:8] not in (b'ftyp',b'moov',b'free',b'skip',b'wide',b'mdat'):
        return  # Unknown, not an assertion of absence of protection.
    examined=0

    def malformed(message='Estrutura MP4 incompleta ou inconsistente. A mídia não foi publicada; partes preservadas.'):
        raise DownloadError('media_container',message)

    def walk(start,end,parent):
        nonlocal examined
        position=start;count=0
        while position<end:
            control.check();examined+=1;count+=1
            if examined>MAX_BOXES:
                malformed('MP4 excede a capacidade de inspeção de metadados; partes preservadas.')
            if end-position<8:malformed()
            file.seek(position);header=file.read(8)
            if len(header)!=8:malformed()
            size,kind=struct.unpack('>I4s',header);width=8
            if size==1:
                extended=file.read(8)
                if len(extended)!=8:malformed()
                size=struct.unpack('>Q',extended)[0];width=16
            elif size==0:size=end-position
            if size<width or size>end-position:malformed()
            body=position+width
            if parent==b'stsd' and kind in (b'encv',b'enca'):
                raise DownloadError('media_protected',
                    'O MP4 anuncia proteção/criptografia. O download direto não remove essa proteção '
                    'e não entrega um vídeo reproduzível. Use uma fonte autorizada ou o modo offline '
                    'oficial da plataforma. Partes preservadas.')
            if kind==_CHILD.get(parent):
                walk(body,position+size,kind)
            elif parent==b'stbl' and kind==b'stsd':
                if size-width<8:malformed()
                file.seek(body);description=file.read(8)
                if len(description)!=8:malformed()
                entries=int.from_bytes(description[4:8],'big')
                if entries>MAX_DESCRIPTIONS or entries>(size-width-8)//8:malformed()
                if walk(body+8,position+size,b'stsd')!=entries:malformed()
            position+=size
        return count

    walk(0,total,b'root')
    return 'inspected'
