"""Bounded WebVTT timeline normalization for explicitly selected local subtitles."""
from __future__ import annotations

import math
from pathlib import Path
import re

from .network import DownloadError

MAX_TEXT_BYTES=16*1024*1024
MAX_CUES=100000
MAX_SECONDS=7*24*3600


def _timestamp(value):
    match=re.fullmatch(r'(?:(\d{2,3}):)?([0-5]\d):([0-5]\d)\.(\d{3})',value)
    if not match:raise DownloadError('stream_subtitles','Timestamp WebVTT inválido.')
    return ((int(match[1] or 0)*60+int(match[2]))*60+int(match[3]))*1000+int(match[4])


def _stamp(value):
    seconds,ms=divmod(value,1000);minutes,seconds=divmod(seconds,60);hours,minutes=divmod(minutes,60)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{ms:03}'


def merge_webvtt(paths, destination, control, *, origin=0.0):
    """Map HLS X-TIMESTAMP-MAP against the selected video's first timestamp.

    Reject ambiguous/out-of-range maps instead of silently shifting subtitles.
    Identical cues repeated at HLS segment boundaries are emitted once.
    """
    if not math.isfinite(origin) or abs(origin)>MAX_SECONDS:
        raise DownloadError('stream_subtitles','Origem temporal das legendas não confirmada.')
    cues=[];seen=set();total=0
    for path in paths:
        control.check();path=Path(path);size=path.stat().st_size
        total+=size
        if total>MAX_TEXT_BYTES:raise DownloadError('stream_capacity','Legendas excedem 16 MiB de texto.')
        with path.open('rb') as file:body=file.read(size+1)
        if len(body)!=size:raise DownloadError('stream_subtitles','Legenda mudou durante a leitura.')
        try:text=body.decode('utf-8-sig',errors='strict').replace('\r\n','\n').replace('\r','\n')
        except UnicodeError:raise DownloadError('stream_subtitles','WebVTT deve ser UTF-8 válido.') from None
        blocks=re.split(r'\n[ \t]*\n',text.strip())
        header=blocks[0].splitlines() if blocks else []
        if not header or not re.fullmatch(r'WEBVTT(?:[ \t].*)?',header[0]):
            raise DownloadError('stream_subtitles','A faixa de legendas não retornou WebVTT.')
        offset=0;maps=0
        for line in header[1:]:
            if line.startswith('X-TIMESTAMP-MAP='):
                maps+=1
                attrs=line.split('=',1)[1].split(',');mapping={}
                for item in attrs:
                    name,separator,value=item.partition(':')
                    if not separator or name in mapping:raise DownloadError('stream_subtitles','Mapa temporal WebVTT inválido.')
                    mapping[name]=value
                if set(mapping)!={'LOCAL','MPEGTS'} or not re.fullmatch(r'\d{1,10}',mapping['MPEGTS']) or int(mapping['MPEGTS'])>=2**33 or maps>1:
                    raise DownloadError('stream_subtitles','Mapa temporal WebVTT inválido.')
                offset=round(int(mapping['MPEGTS'])/90-origin*1000)-_timestamp(mapping['LOCAL'])
        for block in blocks[1:]:
            control.check();lines=block.splitlines()
            if not lines or lines[0]=='NOTE' or lines[0].startswith(('NOTE ','NOTE\t')):continue
            if lines[0] in ('STYLE','REGION'):
                raise DownloadError('stream_subtitles','Estilos/regiões WebVTT exigem tratamento adicional; nenhuma legenda foi omitida silenciosamente.')
            index=0 if '-->' in lines[0] else 1
            if len(lines)<=index:raise DownloadError('stream_subtitles','Cue WebVTT sem intervalo.')
            match=re.fullmatch(r'(\S+)[ \t]+-->[ \t]+(\S+)(?:[ \t]+.*)?',lines[index])
            if not match:raise DownloadError('stream_subtitles','Intervalo de legenda inválido.')
            start,end=_timestamp(match[1])+offset,_timestamp(match[2])+offset
            if end<=start or start<0 or end>MAX_SECONDS*1000:
                raise DownloadError('stream_subtitles','Legenda fora da timeline suportada; ajuste temporal não foi presumido.')
            payload='\n'.join(lines[index+1:])
            if not payload:continue
            cue=(start,end,payload)
            if cue not in seen:
                if len(cues)>=MAX_CUES:raise DownloadError('stream_capacity','Legendas excedem 100.000 cues.')
                seen.add(cue);cues.append(cue)
    control.check()
    # Empty WebVTT remains an explicit unsupported result, not a missing track.
    if not cues:raise DownloadError('stream_subtitles','A faixa de legendas selecionada não contém cues utilizáveis.')
    destination=Path(destination)
    with destination.open('x',encoding='utf-8',newline='\n') as output:
        output.write('WEBVTT\n\n')
        for start,end,payload in sorted(cues,key=lambda cue:(cue[0],cue[1])):
            control.check();output.write(f'{_stamp(start)} --> {_stamp(end)}\n{payload}\n\n')
