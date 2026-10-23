"""Bounded, metadata-first HLS/DASH VOD with local-only media processing.

Only this module interprets manifests and owns its ``streaming-*`` work files.
The Manager owns confirmation, task persistence and final publication.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .network import Control, DownloadError, Interrupted, Transport, content_length, content_range, validate_url, validator, transient_body_error
from .mux import remux_stream, decrypt_track, pending_work, retry_pending_cleanup, MAX_CONCAT_FILES
from .crypto_keys import AuthorizedKeys
from .media_container import inspect_remote_mp4

MAX_MANIFEST = 2 * 1024 * 1024
MAX_SEGMENTS = 10000
MAX_CHOICES = 128
MAX_SEGMENT_BYTES = 512 * 1024 * 1024
MAX_TRANSFER_BYTES = 64 * 1024 * 1024 * 1024
MAX_DURATION = 7 * 24 * 3600
# A bounded block reduces receive/hash/write overhead without the slow-body
# cancellation latency observed with 1 MiB reads. The shared space reservation
# below accounts for one block per in-flight writer.
CHUNK = 256 * 1024
SPACE_CHECK_BYTES = 8 * 1024 * 1024


def fail(message, code="stream_manifest"):
    raise DownloadError(code, message)


@dataclass(frozen=True)
class Encryption:
    key_url: str
    iv: str
    key_epoch: str
    method: str = "AES-128"


@dataclass(frozen=True)
class Segment:
    url: str
    start: int | None = None
    end: int | None = None
    encryption: Encryption | None = None


@dataclass(frozen=True)
class Track:
    role: str
    container: str
    segments: tuple[Segment, ...]
    duration: float | None = None
    headers: tuple[tuple[str, str], ...] | None = None
    protection: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class StreamPlan:
    original_url: str
    protocol: str
    selection: str
    filename: str
    ext: str
    audio_only: bool
    title: str
    fingerprint: str
    tracks: tuple[Track, ...]
    headers: tuple[tuple[str, str], ...] = ()


def is_stream_url(url: str) -> bool:
    try:
        return urlsplit(validate_url(url)).path.lower().endswith((".m3u8", ".mpd"))
    except DownloadError:
        return False


def _headers(headers):
    if headers is not None and not isinstance(headers, dict):
        fail("Cabeçalhos de streaming inválidos.", "stream_headers")
    result = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or name.lower() not in ("user-agent", "referer", "accept"):
            fail("O streaming aceita somente User-Agent, Referer e Accept explícitos, sem cookies ou credenciais.", "stream_headers")
        if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            fail("Cabeçalho de streaming inválido.", "stream_headers")
        if name.lower() == "referer":
            value = validate_url(value)
        result[{"user-agent":"User-Agent","referer":"Referer","accept":"Accept"}[name.lower()]] = value
    return result


def _url(base, value):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        fail("Manifest contém um endereço vazio ou longo demais.")
    result = validate_url(urljoin(base, value.strip()))
    if urlsplit(base).scheme == "https" and urlsplit(result).scheme != "https":
        fail("Manifest HTTPS não pode apontar para um recurso HTTP.", "tls_downgrade")
    return result


def _fetch(url, transport, control, headers):
    with transport.open(url, headers, control) as owned:
        response = owned.response
        if response.status != 200:
            fail(f"Manifest respondeu HTTP {response.status}.", "http")
        size = content_length(response)
        if size is not None and size > MAX_MANIFEST:
            fail("Manifest excede o limite de 2 MiB.", "stream_capacity")
        body = bytearray()
        while True:
            control.check()
            chunk = response.read(min(CHUNK, MAX_MANIFEST + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_MANIFEST:
                fail("Manifest excede o limite de 2 MiB.", "stream_capacity")
        if size is not None and size != len(body):
            fail("Manifest recebido incompleto.", "length")
        try:
            text = bytes(body).decode("utf-8-sig", errors="strict")
        except UnicodeError:
            fail("Manifest deve ser UTF-8 válido.")
        return owned.url, text


def _attrs(value):
    attributes = {}
    pattern = re.compile(r'([A-Z0-9-]+)=("[^"\r\n]*"|[^,\r\n]+)(?:,|$)')
    position = 0
    while position < len(value):
        match = pattern.match(value, position)
        if not match or match[1] in attributes:
            fail("Lista de atributos HLS inválida ou duplicada.")
        attributes[match[1]] = match[2].strip('"')
        position = match.end()
    return attributes


def _integer(value, *, minimum=0, maximum=2**53 - 1):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,16}", value):
        fail("Inteiro de manifest inválido.")
    result = int(value)
    if not minimum <= result <= maximum:
        fail("Inteiro de manifest fora da capacidade suportada.", "stream_capacity")
    return result


def _range(value, previous=None):
    match = re.fullmatch(r"([0-9]{1,16})(?:@([0-9]{1,16}))?", value)
    if not match:
        fail("Intervalo HLS inválido.")
    size = _integer(match[1], minimum=1, maximum=MAX_SEGMENT_BYTES)
    start = _integer(match[2]) if match[2] is not None else previous
    if start is None:
        fail("Intervalo HLS implícito sem segmento anterior do mesmo recurso.")
    if start + size > 2**53 - 1:
        fail("Intervalo HLS excede a capacidade.", "stream_capacity")
    return start, start + size - 1


@dataclass(frozen=True)
class _HLSKey:
    url: str
    iv: str | None
    epoch: str
    method: str


def _hls_key(url, line, index):
    attributes = _attrs(line.split(":", 1)[1])
    method = attributes.get("METHOD")
    if method == "NONE":
        if set(attributes) != {"METHOD"} or line.startswith("#EXT-X-SESSION-KEY:"):
            fail("HLS METHOD=NONE não admite outros atributos nem SESSION-KEY.")
        return None
    if method not in ("AES-128", "SAMPLE-AES"):
        fail("Método HLS não suportado. Use AES-128 ou SAMPLE-AES com chave identity autorizada.", "stream_protected")
    if attributes.get("KEYFORMAT", "identity") != "identity":
        fail("Este formato de chave/DRM não é suportado. HLS exige chave identity autorizada.", "stream_protected")
    versions = attributes.get("KEYFORMATVERSIONS", "1")
    if not re.fullmatch(r"[1-9][0-9]*(?:/[1-9][0-9]*)*", versions):
        fail("KEYFORMATVERSIONS HLS inválido.")
    if "1" not in versions.split("/"):
        fail("Versão do formato de chave HLS não suportada.", "stream_protected")
    iv = attributes.get("IV")
    if iv is not None:
        if not re.fullmatch(r"0[xX][0-9a-fA-F]{1,32}", iv):
            fail("IV HLS deve ser um inteiro hexadecimal de até 128 bits.")
        iv = f"{int(iv,16):032x}"
    return _HLSKey(_url(url, attributes.get("URI", "")), iv, hashlib.sha256(f"{url}\n{index}".encode()).hexdigest(), method)


def _hls_lines(url, text):
    if text.count("\n") > 3 * MAX_SEGMENTS + 1024:
        fail("Manifest HLS excede o limite de linhas.", "stream_capacity")
    # One canonical representation for both protection guards and consumers.
    # Otherwise an indented KEY could bypass the guard and later be stripped.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        fail("A resposta não é um manifest HLS válido.")
    if len(lines) > 3 * MAX_SEGMENTS + 1024 or any(len(line) > 16384 for line in lines):
        fail("Manifest HLS excede os limites de linhas.", "stream_capacity")
    keys = {}
    for index, line in enumerate(lines):
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            keys[index] = _hls_key(url, line, index)
        if line.startswith(("#EXT-X-DEFINE:", "#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:")):
            fail("Este recurso avançado/live de HLS ainda não é suportado.", "stream_unsupported")
        if line == "#EXT-X-I-FRAMES-ONLY":
            fail("Playlists de I-frames exigem tratamento próprio de intervalos e não são suportadas.", "stream_unsupported")
    return lines, keys


def _hls_rendition_groups(url, lines, kind):
    """Own rendition identity once; consumers select parsed tracks, not raw tags."""
    groups = {}
    count = 0
    for line in lines:
        if not line.startswith("#EXT-X-MEDIA:"):
            continue
        value = _attrs(line.split(":", 1)[1])
        if value.get("TYPE") != kind:
            continue
        group, name = value.get("GROUP-ID"), value.get("NAME")
        language = value.get("LANGUAGE", "")
        for item, maximum in ((group, 128), (name, 128), (language, 64)):
            if not isinstance(item, str) or len(item) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in item):
                fail("Identidade de faixa de áudio HLS ausente ou inválida.")
        if not group or not name:
            fail("Faixa de áudio HLS exige GROUP-ID e NAME.")
        if value.get("DEFAULT", "NO") not in ("YES", "NO") or value.get("AUTOSELECT", "NO") not in ("YES", "NO"):
            fail("DEFAULT/AUTOSELECT de áudio HLS inválido.")
        renditions = groups.setdefault(group, [])
        if any(track["name"] == name for track in renditions):
            fail("Faixas de áudio HLS têm nomes duplicados no mesmo grupo.")
        default = value.get("DEFAULT") == "YES"
        if default and any(track["default"] for track in renditions):
            fail("Grupo de áudio HLS possui mais de uma faixa padrão.")
        count += 1
        if count > MAX_CHOICES:
            fail("Manifest possui mais de 128 faixas de áudio.", "stream_capacity")
        uri = _url(url, value["URI"]) if "URI" in value else None
        if kind == "SUBTITLES" and uri is None:
            fail("Faixa de legenda HLS exige URI.")
        # Bind a named choice to its semantic identity even if a refreshed
        # manifest reuses its URI for another language or commentary track.
        identity = json.dumps([group, name, language, value.get("ASSOC-LANGUAGE", ""),
                               value.get("CHARACTERISTICS", ""), value.get("CHANNELS", "")],
                              ensure_ascii=False, separators=(",", ":"))
        renditions.append(dict(uri=uri, name=name, language=language, default=default, identity=identity))
    return groups


def _hls_catalog(url, text):
    lines, _ = _hls_lines(url, text)
    audio = _hls_rendition_groups(url, lines, "AUDIO")
    subtitles = _hls_rendition_groups(url, lines, "SUBTITLES")
    choices = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        value = _attrs(line.split(":", 1)[1])
        if index + 1 == len(lines) or lines[index + 1].startswith("#"):
            fail("Variante HLS não possui endereço.")
        uri = _url(url, lines[index + 1])
        resolution = value.get("RESOLUTION", "")
        match = re.fullmatch(r"([0-9]+)x([0-9]+)", resolution)
        width, height = (_integer(match[1], maximum=65536), _integer(match[2], maximum=65536)) if match else (None, None)
        bandwidth = _integer(value.get("BANDWIDTH", "0"))
        group = value.get("AUDIO")
        renditions = audio.get(group) if group is not None else None
        if group is not None and not renditions:
            fail("Grupo de áudio HLS ausente.")
        if renditions and len(renditions) > 1 and any(track["uri"] is None for track in renditions):
            fail("Selecionar várias faixas de áudio internas ao mesmo vídeo HLS ainda não é suportado.", "stream_unsupported")
        if renditions and len({track["uri"] for track in renditions}) != len(renditions):
            fail("Faixas de áudio HLS que compartilham a mesma playlist exigem seleção interna ainda não suportada.", "stream_unsupported")
        if renditions:
            primary = next((track for track in renditions if track["default"]), renditions[0])
            ordered = [primary, *(track for track in renditions if track is not primary)]
        else:
            ordered = [None]
        if len(ordered) > MAX_CHOICES-len(choices):
            fail("Manifest possui mais de 128 qualidades.", "stream_capacity")
        subtitle_group = value.get("SUBTITLES")
        subtitle_tracks = subtitles.get(subtitle_group, []) if subtitle_group not in (None,"NONE") else []
        for index, track in enumerate(ordered):
            audio_uri = track["uri"] if track else None
            identity = uri + "\n" + (audio_uri or "")
            # The existing default selection remains resumable byte-for-byte.
            # Alternate choices bind both URI and rendition identity, so a
            # disappeared named track requires a fresh explicit selection.
            if index:
                identity += "\n" + track["identity"]
            label = " · ".join(part for part in (track["name"], track["language"]) if part) if track else ""
            for subtitle in [None,*subtitle_tracks]:
                if len(choices)>=MAX_CHOICES:
                    fail("Combinações de qualidade, áudio e legendas excedem 128 opções.","stream_capacity")
                selected_identity=identity if subtitle is None else identity+"\nsubtitle:"+subtitle["uri"]+"\n"+subtitle["identity"]
                identifier = "hls:" + hashlib.sha256(selected_identity.encode()).hexdigest()[:24]
                choices.append(dict(id=identifier, uri=uri, audio_uri=audio_uri, audio_label=label,
                                    audio_language=track["language"] if track else None,
                                    subtitle_uri=subtitle["uri"] if subtitle else None,
                                    subtitle_label=" · ".join(part for part in (subtitle["name"],subtitle["language"]) if part) if subtitle else None,
                                    subtitle_language=subtitle["language"] if subtitle else None,
                                    subtitles_omitted=subtitle_group not in (None,"NONE") and subtitle is None,
                                    width=width, height=height, bandwidth=bandwidth))
    if not choices:
        choices = [dict(id="hls:original", uri=url, audio_uri=None, width=None, height=None, bandwidth=0)]
    if len({choice["id"] for choice in choices}) != len(choices):
        fail("Manifest contém variantes HLS duplicadas.")
    return sorted(choices, key=lambda c: (c["height"] or 0, c["bandwidth"]), reverse=True)


def _hls_track(url, text, role):
    lines, keys = _hls_lines(url, text)
    if "#EXT-X-ENDLIST" not in lines:
        fail("Este HLS é ao vivo ou ainda está aberto. Somente VOD completo é suportado.", "stream_live")
    if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
        fail("Variantes HLS aninhadas ainda não são suportadas.", "stream_unsupported")
    segments, initialization, byte_range = [], None, None
    previous_url, previous_end, duration, pending_duration = None, None, 0.0, None
    key, sequence, saw_sequence = None, 0, False
    for index, line in enumerate(lines):
        if line == "#EXT-X-DISCONTINUITY" or line.startswith("#EXT-X-GAP"):
            fail("HLS com descontinuidades/lacunas exige tratamento adicional.", "stream_unsupported")
        if line.startswith("#EXT-X-KEY:"):
            key = keys[index]
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            value = line.split(":", 1)[1]
            if saw_sequence or segments or not re.fullmatch(r"[0-9]{1,20}", value) or int(value) > 2**64-1:
                fail("Número de sequência HLS inválido, duplicado ou tardio.")
            sequence, saw_sequence = int(value), True
        elif line.startswith("#EXT-X-MAP:"):
            value = _attrs(line.split(":", 1)[1])
            uri = _url(url, value.get("URI", ""))
            start, end = _range(value["BYTERANGE"], 0) if "BYTERANGE" in value else (None, None)
            if key is not None and key.method == "AES-128" and key.iv is None:
                fail("Inicialização HLS AES-128 exige IV explícito.")
            encryption = Encryption(key.url, key.iv or "0"*32, key.epoch, key.method) if key is not None else None
            current = Segment(uri, start, end, encryption)
            if initialization is not None and current != initialization:
                fail("Mudança de inicialização HLS não suportada.", "stream_unsupported")
            if segments and initialization is None:
                fail("Inicialização HLS tardia não suportada.", "stream_unsupported")
            initialization = current
        elif line.startswith("#EXT-X-BYTERANGE:"):
            if byte_range is not None:
                fail("Intervalos HLS consecutivos sem segmento.")
            byte_range = line.split(":", 1)[1]
        elif line.startswith("#EXTINF:"):
            if pending_duration is not None:
                fail("Durações HLS consecutivas sem segmento.")
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                fail("Duração HLS inválida.")
            if not math.isfinite(pending_duration) or pending_duration <= 0:
                fail("Duração HLS inválida.")
        elif not line.startswith("#"):
            if pending_duration is None:
                fail("Segmento HLS sem duração.")
            duration += pending_duration
            if duration > MAX_DURATION:
                fail("Duração HLS excede sete dias.", "stream_capacity")
            uri = _url(url, line)
            if byte_range is not None:
                start, end = _range(byte_range, previous_end + 1 if previous_url == uri and previous_end is not None else None)
            else:
                start, end = None, None
            if sequence + len(segments) > 2**64-1:
                fail("Número de sequência HLS excede 64 bits.", "stream_capacity")
            encryption = Encryption(key.url, key.iv or f"{sequence+len(segments):032x}", key.epoch, key.method) if key else None
            segments.append(Segment(uri, start, end, encryption))
            if len(segments) > MAX_SEGMENTS:
                fail("Streaming excede 10.000 segmentos.", "stream_capacity")
            previous_url, previous_end, byte_range, pending_duration = uri, end, None, None
    if not segments or byte_range is not None or pending_duration is not None:
        fail("Playlist HLS incompleta ou sem segmentos.")
    container = "mov" if initialization else "mpegts"
    if role=="subtitle":
        if initialization or any(s.encryption and s.encryption.method!="AES-128" for s in segments):
            fail("Legendas HLS exigem WebVTT, sem SAMPLE-AES ou inicialização fMP4.","stream_unsupported")
        container="webvtt"
    all_segments = [*segments, *([initialization] if initialization else [])]
    methods = {segment.encryption.method for segment in all_segments if segment.encryption}
    if len(methods) > 1:
        fail("Alternância entre criptografia de segmento e amostra exige playlists separadas.", "stream_unsupported")
    if initialization and "SAMPLE-AES" in methods:
        fail("SAMPLE-AES em fMP4 ainda não está validado. Esta versão aceita SAMPLE-AES MPEG-TS H.264/AAC.", "stream_unsupported")
    if initialization:
        segments.insert(0, initialization)
    return Track(role, container, tuple(segments), duration)


def _name(element):
    return element.tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [child for child in element if _name(child) == name]


def _first(element, name):
    values = _children(element, name)
    if len(values) > 1:
        fail(f"Elementos DASH {name} ambíguos.", "stream_unsupported")
    return values[0] if values else None


def _duration(value):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value or "")
    if not match or not any(match.groups()):
        fail("Duração DASH ausente ou inválida.")
    seconds = sum(float(v or 0) * scale for v, scale in zip(match.groups(), (86400, 3600, 60, 1)))
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_DURATION:
        fail("Duração DASH excede a capacidade de sete dias.", "stream_capacity")
    return seconds


def _base(parent_url, element):
    base = _first(element, "BaseURL")
    return _url(parent_url, base.text or "") if base is not None else parent_url


def _mpd(url, text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        fail("Declarações de entidade/DOCTYPE não são aceitas em manifests DASH.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        fail("Manifest DASH XML inválido.")
    if _name(root) != "MPD":
        fail("A resposta não é um manifest DASH.")
    if root.get("type", "static") != "static":
        fail("Somente DASH estático/VOD é suportado.", "stream_live")
    nodes = 0
    for node in root.iter():
        nodes += 1
        if nodes > 20000:
            fail("Manifest DASH contém elementos demais.", "stream_capacity")
        if _name(node) == "ContentProtection":
            scheme = node.get("schemeIdUri", "")
            # UUID declarations identify license systems; they never authorize
            # a license request or key extraction. A common MP4 declaration and
            # explicitly supplied KID keys are required at the download boundary.
            if scheme != "urn:mpeg:dash:mp4protection:2011" and not re.fullmatch(r"urn:uuid:[0-9a-fA-F-]{36}", scheme):
                fail("Declaração de proteção DASH não suportada.", "stream_protected")
        if any("xlink" in name for name in node.attrib):
            fail("DASH com referências XML externas não é suportado.", "stream_unsupported")
    periods = _children(root, "Period")
    if len(periods) != 1:
        fail("DASH com múltiplos períodos exige tratamento adicional.", "stream_unsupported")
    period = periods[0]
    if period.get("start") not in (None, "PT0S", "PT0.0S"):
        fail("DASH com início de período deslocado não é suportado.", "stream_unsupported")
    duration = _duration(period.get("duration") or root.get("mediaPresentationDuration"))
    base = _base(_base(url, root), period)
    representations = []
    for adaptation in _children(period, "AdaptationSet"):
        adaptation_base = _base(base, adaptation)
        for rep in _children(adaptation, "Representation"):
            mime = rep.get("mimeType", adaptation.get("mimeType", ""))
            kind = rep.get("contentType", adaptation.get("contentType", mime.split("/")[0]))
            if mime=="text/vtt":kind="subtitle"
            if kind not in ("video", "audio", "subtitle"):
                continue
            if mime not in ("video/mp4", "audio/mp4", "text/vtt", ""):
                continue
            rep_id = rep.get("id")
            if not rep_id or len(rep_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_.-]+", rep_id):
                fail("Identificador de representação DASH inválido.")
            record = dict(id=rep_id, role=kind, base=_base(adaptation_base, rep), adaptation=adaptation,
                          element=rep, period=period, duration=duration,
                          bandwidth=_integer(rep.get("bandwidth", "0")),
                          width=_integer(rep.get("width", "0"), maximum=65536) or None,
                          height=_integer(rep.get("height", "0"), maximum=65536) or None)
            protection = [p for owner in (root,period,adaptation,rep) for p in _children(owner,"ContentProtection")]
            modes = {p.get("value") for p in protection if p.get("schemeIdUri") == "urn:mpeg:dash:mp4protection:2011"}
            if protection and (len(modes) != 1 or not modes <= {"cenc", "cens", "cbc1", "cbcs"}):
                fail("DASH protegido precisa declarar um único modo cenc, cens, cbc1 ou cbcs e chaves KID autorizadas.", "stream_protected")
            language=rep.get("lang",adaptation.get("lang",""))
            if len(language)>64 or any(ord(c)<32 or ord(c)==127 for c in language):
                fail("Idioma DASH inválido.")
            record["language"]=language
            record["protection"] = next(iter(modes)) if modes else None
            if kind=="subtitle" and protection:
                fail("Legendas WebVTT protegidas não são suportadas.","stream_unsupported")
            representations.append(record)
            if len(representations) > MAX_CHOICES:
                fail("DASH possui mais de 128 representações.", "stream_capacity")
    if not representations:
        fail("DASH não possui faixas MP4 de áudio/vídeo suportadas.", "stream_unsupported")
    if len({(r["role"], r["id"]) for r in representations}) != len(representations):
        fail("Representações DASH têm identidades duplicadas.")
    videos = sorted((r for r in representations if r["role"] == "video"), key=lambda r:(r["height"] or 0,r["bandwidth"]), reverse=True)
    audios = sorted((r for r in representations if r["role"] == "audio"), key=lambda r:r["bandwidth"], reverse=True)
    subtitles=[r for r in representations if r["role"]=="subtitle"]
    choices = []
    for video in videos:
        for audio_index,audio in enumerate(audios or [None]):
            basic=[video]+([audio] if audio else [])
            base_id="dash:"+"+".join(r["id"] for r in basic)
            if len(base_id)>200:base_id='dash:choice:'+hashlib.sha256(base_id.encode()).hexdigest()[:48]
            # Old default IDs remain valid; alternatives bind language as well.
            if audio_index:
                base_id+="~"+hashlib.sha256(audio["language"].encode()).hexdigest()[:12]
            for subtitle in [None,*subtitles]:
                tracks=basic+([subtitle] if subtitle else [])
                identity=base_id if subtitle is None else base_id+"~sub:"+hashlib.sha256((subtitle["id"]+"\n"+subtitle["language"]).encode()).hexdigest()[:16]
                choices.append(dict(id=identity,tracks=tracks,height=video["height"],width=video["width"],
                    bandwidth=sum(r["bandwidth"] for r in tracks),audio_only=False,
                    audio_label=audio["language"] if audio else None,
                    subtitle_label=subtitle["language"] or subtitle["id"] if subtitle else None))
    for audio in audios:
        choices.append(dict(id="dash:audio:" + audio["id"], tracks=[audio], height=None, width=None,
                            bandwidth=audio["bandwidth"], audio_only=True,audio_label=audio["language"]))
    if len(choices)>MAX_CHOICES:
        fail("Combinações de qualidade, áudio e legendas excedem 128 opções.","stream_capacity")
    return choices


def _template(template, rep, number=None, stamp=None):
    if not isinstance(template, str) or len(template) > 8192:
        fail("Template DASH ausente ou longo demais.")
    token = re.compile(r"\$\$|\$(RepresentationID|Bandwidth|Number|Time)(?:%0([1-9])d)?\$")
    def replace(match):
        if match[0] == "$$":
            return "$"
        value = {"RepresentationID": rep["id"], "Bandwidth": rep["bandwidth"], "Number": number, "Time": stamp}[match[1]]
        if value is None or match[2] and not isinstance(value, int):
            fail("Variável DASH indisponível no template.")
        return str(value).zfill(int(match[2])) if match[2] else str(value)
    result = token.sub(replace, template)
    # Escaped literal dollars are allowed, unknown substitutions are not.
    if re.search(r"\$[A-Za-z][^/]*\$", result):
        fail("Variável DASH não suportada.", "stream_unsupported")
    return _url(rep["base"], result)


def _dash_track(rep):
    if rep["role"]=="subtitle":
        if not any(_first(owner,"BaseURL") is not None for owner in (rep["adaptation"],rep["element"])) or any(_first(owner,name) is not None for owner in (rep["period"],rep["adaptation"],rep["element"]) for name in ("SegmentList","SegmentTemplate","SegmentBase")):
            fail("Legendas DASH aceitam WebVTT em um arquivo BaseURL; legendas segmentadas/fMP4 ainda não são suportadas.","stream_unsupported")
        return Track("subtitle","webvtt",(Segment(rep["base"]),),language=rep["language"] or None)
    chain = (rep["period"], rep["adaptation"], rep["element"])
    lists, templates = [], []
    for owner in chain:
        value = _first(owner, "SegmentList")
        if value is not None:
            lists.append(value)
        value = _first(owner, "SegmentTemplate")
        if value is not None:
            templates.append(value)
        if _first(owner, "SegmentBase") is not None:
            fail("DASH SegmentBase/SIDX ainda não é suportado.", "stream_unsupported")
    if lists and templates or not lists and not templates:
        fail("Modo de segmentos DASH ausente ou ambíguo.", "stream_unsupported")
    segments = []
    if lists:
        listing = lists[-1]
        initialization = _first(listing, "Initialization")
        if initialization is not None:
            start, end = _dash_range(initialization.get("range"))
            segments.append(Segment(_url(rep["base"], initialization.get("sourceURL", "")), start, end))
        for node in _children(listing, "SegmentURL"):
            start, end = _dash_range(node.get("mediaRange"))
            segments.append(Segment(_url(rep["base"], node.get("media", "")), start, end))
            if len(segments) > MAX_SEGMENTS:
                fail("DASH excede 10.000 segmentos.", "stream_capacity")
    else:
        attrs, timeline = {}, None
        for node in templates:
            attrs.update(node.attrib)
            value = _first(node, "SegmentTimeline")
            if value is not None:
                timeline = value
        timescale = _integer(attrs.get("timescale", "1"), minimum=1, maximum=10**9)
        start_number = _integer(attrs.get("startNumber", "1"))
        offset = _integer(attrs.get("presentationTimeOffset", "0"))
        initialization = attrs.get("initialization")
        if not initialization:
            fail("DASH fMP4 precisa de um segmento de inicialização.")
        segments.append(Segment(_template(initialization, rep)))
        stamps = []
        if timeline is not None:
            nodes = _children(timeline, "S")
            current = 0
            for index, node in enumerate(nodes):
                start = _integer(node.get("t")) if node.get("t") is not None else current
                step = _integer(node.get("d"), minimum=1)
                repeat_value = node.get("r", "0")
                if repeat_value == "-1":
                    until = _integer(nodes[index+1].get("t")) if index+1 < len(nodes) and nodes[index+1].get("t") is not None else math.ceil(rep["duration"]*timescale)+offset
                    count = max(0, math.ceil((until-start)/step))
                else:
                    count = _integer(repeat_value, maximum=MAX_SEGMENTS) + 1
                if start < current or not count or count > MAX_SEGMENTS-len(stamps):
                    fail("Expansão de timeline DASH inválida ou longa demais.", "stream_capacity")
                stamps.extend(start + i*step for i in range(count))
                current = start + count*step
        else:
            step = _integer(attrs.get("duration"), minimum=1)
            count = math.ceil(rep["duration"]*timescale/step)
            if not 0 < count < MAX_SEGMENTS:
                fail("DASH excede o limite de segmentos.", "stream_capacity")
            stamps = [offset + i*step for i in range(count)]
        for index, stamp in enumerate(stamps):
            if stamp > 2**53-1 or start_number+index > 2**53-1:
                fail("Timeline DASH excede a capacidade numérica.", "stream_capacity")
            segments.append(Segment(_template(attrs.get("media"), rep, start_number+index, stamp)))
    if len(segments) < 2:
        fail("DASH não possui segmentos completos de mídia.")
    return Track(rep["role"], "mov", tuple(segments), rep["duration"], protection=rep.get("protection"),language=rep.get("language") or None)


def _dash_range(value):
    if value is None:
        return None, None
    match = re.fullmatch(r"([0-9]{1,16})-([0-9]{1,16})", value)
    if not match:
        fail("Intervalo DASH inválido.")
    start, end = _integer(match[1]), _integer(match[2])
    if end < start or end-start+1 > MAX_SEGMENT_BYTES:
        fail("Intervalo DASH inconsistente.")
    return start, end


def _catalog(url, control, transport, headers):
    final, text = _fetch(url, transport, control, headers)
    if text.lstrip().startswith("#EXTM3U"):
        return "hls", final, text, _hls_catalog(final,text)
    if text.lstrip().startswith("<"):
        return "dash", final, text, _mpd(final,text)
    fail("O endereço não retornou um manifest HLS/DASH.")


@contextmanager
def _stream_transport(transport):
    """Only the creator may close a transport, including its idle sockets."""
    owned=transport is None
    transport=Transport() if owned else transport
    try:yield transport
    finally:
        if owned:transport.close()


def _option(choice, protocol, identity):
    audio = choice.get("audio_only", False)
    ext = "m4a" if audio else "mp4"
    quality = f'{choice["height"]}p' if choice.get("height") else ("Somente áudio" if audio else "Original")
    bandwidth = f' · {choice["bandwidth"]//1000} kb/s' if choice["bandwidth"] else ""
    audio_label = f' · {choice["audio_label"]}' if choice.get("audio_label") else ""
    subtitles = (" · Legenda: "+choice["subtitle_label"]) if choice.get("subtitle_label") else (" · sem legendas" if choice.get("subtitles_omitted") else "")
    protected=any(t.get("protection") for t in choice.get("tracks",[]))
    protection=" · exige chaves autorizadas" if protected else ""
    return dict(id=choice["id"], label=f"{quality} · {protocol.upper()} → {ext.upper()}{bandwidth}{audio_label}{subtitles}{protection}",
                filename=f"Streaming-{identity}.{ext}",ext=ext,mimetype="audio/mp4" if audio else "video/mp4",
                width=choice.get("width"),height=choice.get("height"),expected_size=None,audio_only=audio,audio_label=choice.get("audio_label"),subtitle_label=choice.get("subtitle_label"),protected=protected)


def discover_stream(url, stop_event, *, transport=None, headers=None):
    control = Control()
    control.stop = stop_event
    url = validate_url(url)
    with _stream_transport(transport) as active:
        protocol, _, _, choices = _catalog(url,control,active,_headers(headers))
    identity = hashlib.sha256(url.encode()).hexdigest()[:12]
    return dict(original_url=url,video_id=identity,title=f"Streaming {protocol.upper()}",
                filename=f"Streaming-{identity}.mp4",formats=[_option(c,protocol,identity) for c in choices])


def resolve_stream(url, control, *, selection=None, transport=None, headers=None, protocol=None, audio_only=None):
    if audio_only is not None and type(audio_only) is not bool:
        fail("Intenção de áudio do streaming inválida.","stream_selection")
    url = validate_url(url)
    headers = _headers(headers)
    with _stream_transport(transport) as transport:
        return _resolve_stream(url,control,selection,transport,headers,protocol,audio_only)


def _resolve_stream(url,control,selection,transport,headers,protocol,audio_only):
    hint = protocol
    protocol, final, text, choices = _catalog(url,control,transport,headers)
    if hint is not None and hint != protocol:
        fail("O manifest recebido não corresponde ao protocolo identificado.","stream_manifest")
    choice = choices[0] if selection is None else next((c for c in choices if c["id"] == selection), None)
    if choice is None:
        fail("A qualidade escolhida não está mais disponível. Consulte os formatos novamente.", "stream_selection")
    documents = [(final,text)]
    if protocol == "hls":
        if choice["uri"] == final:
            video_url, video_text = final, text
        else:
            video_url,video_text = _fetch(choice["uri"],transport,control,headers)
            documents.append((video_url,video_text))
        tracks = [_hls_track(video_url,video_text,"video" if choice["audio_uri"] else "muxed")]
        if choice["audio_uri"]:
            audio_url,audio_text = _fetch(choice["audio_uri"],transport,control,headers)
            documents.append((audio_url,audio_text))
            tracks.append(replace(_hls_track(audio_url,audio_text,"audio"),language=choice.get("audio_language") or None))
        if choice.get("subtitle_uri"):
            subtitle_url,subtitle_text=_fetch(choice["subtitle_uri"],transport,control,headers)
            documents.append((subtitle_url,subtitle_text))
            tracks.append(replace(_hls_track(subtitle_url,subtitle_text,"subtitle"),duration=None,language=choice.get("subtitle_language") or None))
    else:
        tracks = [_dash_track(record) for record in choice["tracks"]]
    if sum(len(track.segments) for track in tracks) > MAX_SEGMENTS:
        fail("Streaming excede 10.000 segmentos no total.", "stream_capacity")
    if audio_only:
        if len(tracks)!=1:
            fail("A seleção de áudio exige uma única faixa de origem.","stream_selection")
        tracks=[replace(tracks[0],role="audio")]
    tracks=[replace(track,headers=tuple(sorted(headers.items()))) for track in tracks]
    identity = hashlib.sha256(url.encode()).hexdigest()[:12]
    option = _option(choice,protocol,identity)
    if audio_only:
        option.update(filename=f"Streaming-{identity}.m4a",ext="m4a",audio_only=True)
    facts = [asdict(track) for track in tracks]
    # Preserve fingerprints of existing unencrypted paused downloads.
    for track in facts:
        track.pop("language",None)  # Source document and selected ID already bind rendition identity.
        if track["protection"] is None:
            del track["protection"]
        for segment in track["segments"]:
            if segment["encryption"] is None:
                del segment["encryption"]
            elif segment["encryption"]["method"] == "AES-128":
                del segment["encryption"]["method"]
    proof = json.dumps(dict(documents=documents,selection=choice["id"],tracks=facts,headers=headers),sort_keys=True,separators=(",",":"))
    return StreamPlan(url,protocol,choice["id"],option["filename"],option["ext"],option["audio_only"],
                      f"Streaming {protocol.upper()}",hashlib.sha256(proof.encode()).hexdigest(),tuple(tracks),tuple(sorted(headers.items())))


def combine_stream_plans(video_plan, audio_plan):
    """Pair two already-resolved independent manifests without fetching media."""
    if (not isinstance(video_plan,StreamPlan) or not isinstance(audio_plan,StreamPlan)
            or len(video_plan.tracks)!=1 or len(audio_plan.tracks)!=1
            or sum(len(t.segments) for t in (*video_plan.tracks,*audio_plan.tracks))>MAX_SEGMENTS):
        fail("A combinação exige uma faixa de vídeo e uma faixa de áudio separadas e limitadas.","stream_selection")
    video,audio=video_plan.tracks[0],audio_plan.tracks[0]
    tracks=(replace(video,role="video"),replace(audio,role="audio"))
    fingerprint=hashlib.sha256((video_plan.fingerprint+"\n"+audio_plan.fingerprint).encode()).hexdigest()
    return StreamPlan(video_plan.original_url,video_plan.protocol,video_plan.selection+"+"+audio_plan.selection,
                      video_plan.filename.rsplit(".",1)[0]+".mp4","mp4",False,video_plan.title,fingerprint,tracks,video_plan.headers)


def _regular(path):
    if path.is_symlink() or getattr(path,"is_junction",lambda:False)() or path.exists() and not path.is_file():
        fail("Um temporário de streaming foi substituído. Preservado.", "ownership")


def _json(path, limit=16384):
    _regular(path)
    if path.stat().st_size > limit:
        fail("Metadados de streaming excedem o limite.", "ownership")
    try:
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError("Chave JSON duplicada.")
                result[key]=value
            return result
        return json.loads(path.read_text(encoding="utf-8"),object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Constante JSON inválida.")))
    except (UnicodeError,ValueError):
        fail("Metadados de streaming inválidos; preservados.", "ownership")


def _save(path, value):
    temporary = path.with_name(path.name+".tmp")
    _regular(path);_regular(temporary)
    # The namespace marker owns this fixed temporary name before allocation.
    with temporary.open("wb") as output:
        output.write(json.dumps(value,separators=(",",":"),sort_keys=True).encode("utf-8"))
        # Receipts describe a verified cache, not a publication. A resumed cache
        # must still pass its full local digest and remote freshness check.
        output.flush()
    os.replace(temporary,path)


def _names(counts):
    result = {"streaming-owner.json","streaming-owner.json.tmp","streaming-output.mp4"}
    for track,count in enumerate(counts):
        result.update({f"streaming-track-{track}.bin",f"streaming-track-{track}.bin.part"})
        result.add(f"streaming-concat-{track}.txt")
        result.add(f"streaming-subtitle-{track}.vtt")
        result.update(f"streaming-protected-{track}{suffix}" for suffix in (".bin", ".bin.part", ".sample", ".clear"))
        for index in range(count):
            base=f"streaming-seg-{track}-{index}"
            result.update({base+suffix for suffix in (".bin",".part",".json",".json.tmp",".clear",".clear.part")})
    return result


def _owner(work):
    value = _json(work/"streaming-owner.json")
    if not isinstance(value,dict) or set(value)!={"app","version","fingerprint","counts"} or value.get("app")!="lume-streaming" or value.get("version")!=1:
        fail("Posse dos temporários de streaming não confirmada.", "ownership")
    counts=value["counts"]
    if (not isinstance(counts,list) or not 1<=len(counts)<=3 or any(type(v)is not int or not 1<=v<=MAX_SEGMENTS for v in counts)
        or sum(counts)>MAX_SEGMENTS or not re.fullmatch(r"[a-f0-9]{64}",value.get("fingerprint","") if isinstance(value.get("fingerprint"),str) else "")):
        fail("Identidade dos temporários de streaming inválida; preservados.", "ownership")
    return value


def owned_stream_files(work):
    """Return existing module-owned files for the Manager's exact cleanup list."""
    work=Path(work)
    marker=work/"streaming-owner.json"
    if not marker.exists() and not marker.is_symlink():
        if any(p.name.startswith("streaming-") for p in work.iterdir()):
            fail("Temporários de streaming sem prova de posse; preservados.","ownership")
        return ()
    allowed=_names(_owner(work)["counts"]);result=[]
    for path in work.iterdir():
        if not path.name.startswith("streaming-"):
            continue
        if path.name not in allowed:
            fail("Arquivo estranho na área de streaming; preservado.","ownership")
        _regular(path);result.append(path)
    # Namespace proof is removed last, so a failed cleanup remains retryable.
    return tuple(sorted(result, key=lambda path: (path.name == "streaming-owner.json", path.name)))


def _claim(work, plan):
    owner=dict(app="lume-streaming",version=1,fingerprint=plan.fingerprint,counts=[len(t.segments) for t in plan.tracks])
    marker=work/"streaming-owner.json"
    if marker.exists() or marker.is_symlink():
        existing=_owner(work)
        if existing != owner:
            fail("Manifest, qualidade ou identidade mudou. Use Reiniciar para não misturar segmentos.","stream_changed")
        owned_stream_files(work)
    else:
        owned_stream_files(work)
        with marker.open("xb") as output:
            output.write(json.dumps(owner,separators=(",",":"),sort_keys=True).encode())
            output.flush();os.fsync(output.fileno())


def _hash(path, control):
    digest=hashlib.sha256()
    with path.open("rb") as source:
        while chunk:=source.read(CHUNK):
            control.check();digest.update(chunk)
    return digest.hexdigest()


def _segment_identity(proof, expected, required):
    identity=(proof["url"],proof["validator"],proof["resource_total"])
    if required and not proof["validator"]:
        fail("O servidor não fornece identidade estável para reunir intervalos do mesmo recurso.","stream_identity")
    if expected is not None and identity!=expected:
        fail("Um recurso mudou entre seus segmentos/intervalos. Nenhuma mistura foi publicada.","stream_changed")
    return identity


class _KeyCache:
    """Download-scoped, coalesced key reads; raw keys never enter disk or argv."""
    def __init__(self, transport, control):
        self.transport, self.control = transport, control
        self.lock, self.locks, self.values, self.identities = threading.Lock(), {}, {}, {}

    def get(self, encryption, headers):
        resource = (encryption.key_url, tuple(sorted(headers.items())))
        identity = (*resource, encryption.key_epoch)
        with self.lock:
            if identity not in self.locks:
                if len(self.locks) >= MAX_SEGMENTS:
                    fail("Streaming excede o limite de chaves por download.", "stream_capacity")
                self.locks[identity] = threading.Lock()
            slot = self.locks[identity]
        with slot:
            self.control.check()
            if identity not in self.values:
                with self.transport.open(encryption.key_url, headers, self.control) as owned:
                    response = owned.response
                    if response.status != 200:
                        fail(f"A chave HLS respondeu HTTP {response.status}. É necessário acesso autorizado ao recurso.", "stream_key_access")
                    if content_length(response) not in (None, 16):
                        fail("A chave HLS AES-128 deve conter exatamente 16 bytes.", "stream_crypto")
                    value = response.read(17)
                    self.control.check()
                    if len(value) != 16:
                        fail("A chave HLS AES-128 deve conter exatamente 16 bytes.", "stream_crypto")
                    digest = hashlib.sha256(value).digest()
                    with self.lock:
                        if resource in self.identities and self.identities[resource] != digest:
                            fail("O mesmo endereço HLS retornou chaves diferentes. A rotação exige endereços de chave inequívocos.", "stream_changed")
                        self.identities[resource] = digest
                    self.values[identity] = value
            return self.values[identity]

    def clear(self):
        # Python/crypto backends can retain internal copies; no claim of secure
        # memory erasure. This releases our references after all workers settle.
        self.values.clear()
        self.locks.clear()
        self.identities.clear()


def _encryption_proof(encryption, key):
    return {"method":encryption.method, "iv":encryption.iv, "key_sha256":hashlib.sha256(key).hexdigest()}


def _decrypt_segment(segment, path, key, control):
    """Stream a verified ciphertext cache into a separately owned local input."""
    target, partial = path.with_suffix(".clear"), path.with_suffix(".clear.part")
    _regular(path);_regular(target);_regular(partial)
    target.unlink(missing_ok=True);partial.unlink(missing_ok=True)
    size = path.stat().st_size
    if not size or size % 16:
        fail("Segmento AES-128 não contém blocos CBC completos.", "stream_crypto")
    proof = _json(path.with_suffix(".json"))
    if proof.get("encryption") != _encryption_proof(segment.encryption, key):
        fail("A chave HLS mudou desde o segmento salvo. Use Reiniciar.", "stream_changed")
    digest = hashlib.sha256()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes.fromhex(segment.encryption.iv))).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        with path.open("rb") as source, partial.open("xb") as output:
            while chunk := source.read(CHUNK):
                control.check();digest.update(chunk)
                output.write(unpadder.update(decryptor.update(chunk)))
            output.write(unpadder.update(decryptor.finalize()) + unpadder.finalize())
            control.check()
            if digest.hexdigest() != proof["sha256"]:
                fail("O segmento criptografado mudou durante o processamento.", "stream_changed")
            if not output.tell():
                fail("Segmento AES-128 vazio após descriptografia.", "stream_crypto")
            output.flush()
    except ValueError:
        fail("Chave, IV ou preenchimento AES-128 inválido. A saída não foi publicada.", "stream_crypto")
    control.check()
    os.rename(partial, target)
    return target


class _ShortSegment(DownloadError):
    def __init__(self):
        super().__init__('length','Segmento recebido incompleto após as tentativas limitadas; partes válidas preservadas.')


def _segment(segment, path, receipt, transport, control, headers, on_chunk, *, expected_identity=None, require_identity=False, encryption_proof=None):
    # A published segment/receipt is never overwritten or silently repaired.
    # A retry discards only this owned, uncommitted .part and starts the segment
    # again. Pin identity before reading, so bytes from changed resources cannot
    # be mixed. Header/status retries remain owned by Transport.
    pinned=expected_identity
    def remember(identity):
        nonlocal pinned
        if pinned is None and identity['validator'] and identity['resource_total'] is not None:
            pinned=(identity['url'],identity['validator'],identity['resource_total'])
    for attempt in range(3):
        control.check()
        try:
            return _segment_once(segment,path,receipt,transport,control,headers,on_chunk,
                expected_identity=pinned,require_identity=require_identity,encryption_proof=encryption_proof,
                on_identity=remember)
        except BaseException as error:
            control.check()
            if (attempt==2 or receipt.exists() or path.exists()
                    or not (isinstance(error,_ShortSegment) or transient_body_error(error))):
                raise
            if pinned is None:
                # No stable identity: expose failure rather than guessing that
                # the resource is still the same version on the next attempt.
                raise
            if control.stop.wait(.25*2**attempt):control.check()


def _segment_once(segment, path, receipt, transport, control, headers, on_chunk, *, expected_identity=None, require_identity=False, encryption_proof=None,on_identity=None):
    _regular(path);_regular(receipt)
    if receipt.exists():
        proof=_json(receipt)
        expected_fields = {"url","validator","bytes","sha256","resource_total"} | ({"encryption"} if encryption_proof else set())
        if (not isinstance(proof,dict) or set(proof)!=expected_fields
            or type(proof.get("bytes"))is not int or not 0<proof["bytes"]<=MAX_SEGMENT_BYTES
            or not isinstance(proof.get("url"),str) or len(proof["url"])>8192
            or type(proof.get("resource_total"))is not int or not proof["bytes"]<=proof["resource_total"]<=2**63-1
            or proof.get("validator") is not None and (not isinstance(proof["validator"],str) or not proof["validator"] or len(proof["validator"])>8192 or any(ord(c)<32 or ord(c)>255 for c in proof["validator"]))
            or not isinstance(proof.get("sha256"),str) or not re.fullmatch(r"[a-f0-9]{64}",proof["sha256"])
            or not path.exists() or path.stat().st_size!=proof["bytes"] or _hash(path,control)!=proof["sha256"]):
            fail("Um segmento salvo foi alterado. Use Reiniciar.","stream_changed")
        if encryption_proof is not None and proof["encryption"] != encryption_proof:
            fail("A chave HLS mudou desde o segmento salvo. Use Reiniciar.", "stream_changed")
        if validate_url(proof["url"])!=proof["url"]:
            fail("Identidade do segmento salvo foi alterada.","stream_changed")
        # A local digest alone cannot prove remote freshness after a pause.
        if not proof["validator"]:
            fail("O servidor não oferece identidade segura para retomar este streaming. Use Reiniciar.","stream_resume")
        _segment_identity(proof,expected_identity,require_identity)
        first=segment.start or 0
        with transport.open(segment.url,{**headers,"Range":f"bytes={first}-{first}","If-Range":proof["validator"]},control) as owned:
            response=owned.response
            if response.status!=206 or content_range(response.getheader("Content-Range"))!=(first,first,proof["resource_total"]) or owned.url!=proof["url"] or validator(response)!=proof["validator"] or content_length(response) not in (None,1) or len(response.read(2))!=1:
                fail("A identidade de um segmento mudou ou não permite retomada segura. Use Reiniciar.","stream_changed")
        return proof
    if path.exists():
        fail("Segmento sem recibo de publicação; preservado. Use Reiniciar.","stream_resume")
    partial=path.with_suffix(".part");_regular(partial)
    # No prefix is resumed without a segment receipt; namespace ownership permits
    # discarding only this incomplete file before requesting the segment again.
    partial.unlink(missing_ok=True)
    request_headers=dict(headers)
    if segment.start is not None:
        request_headers["Range"]=f"bytes={segment.start}-{segment.end}"
        if expected_identity is not None:
            request_headers["If-Range"]=expected_identity[1]
    digest=hashlib.sha256();size=0
    with transport.open(segment.url,request_headers,control) as owned:
        response=owned.response
        expected=content_length(response)
        resource_total=expected
        if segment.start is not None:
            if response.status!=206:
                if expected_identity is not None:
                    fail("Um recurso mudou ou não honrou If-Range entre seus intervalos.","stream_changed")
                fail("Servidor ignorou o intervalo de um segmento.","range")
            start,end,resource_total=content_range(response.getheader("Content-Range"))
            if (start,end)!=(segment.start,segment.end) or expected not in (None,end-start+1):
                fail("Intervalo de segmento inconsistente.","range")
            expected=end-start+1
        elif response.status!=200:
            fail(f"Segmento respondeu HTTP {response.status}.","http")
        current_identity=dict(url=owned.url,validator=validator(response),resource_total=resource_total)
        if resource_total is not None:
            _segment_identity(current_identity,expected_identity,require_identity)
        elif require_identity and not current_identity["validator"]:
            _segment_identity(current_identity,expected_identity,True)
        if expected is not None and (expected<=0 or expected>MAX_SEGMENT_BYTES):
            fail("Segmento vazio ou maior que 512 MiB.","stream_capacity")
        if (response.getheader("Content-Type") or "").split(";",1)[0].lower() in ("text/html","application/xhtml+xml"):
            fail("Servidor retornou HTML no lugar de um segmento.","media_type")
        if on_identity:on_identity(current_identity)
        with partial.open("xb") as target:
            while chunk:=response.read(CHUNK):
                control.check();size+=len(chunk)
                if size>MAX_SEGMENT_BYTES or expected is not None and size>expected:
                    fail("Segmento excedeu o tamanho permitido.","stream_capacity")
                on_chunk(len(chunk));control.check();target.write(chunk);digest.update(chunk)
            if not size or expected is not None and size!=expected:
                raise _ShortSegment()
            target.flush()
        proof=dict(url=owned.url,validator=validator(response),bytes=size,sha256=digest.hexdigest(),resource_total=resource_total if resource_total is not None else size)
        if encryption_proof is not None:
            proof["encryption"] = encryption_proof
        _segment_identity(proof,expected_identity,require_identity)
    control.check()
    # Windows rename is no-clobber, like the Manager's final publication.
    if os.name=="nt":os.rename(partial,path)
    else:os.link(partial,path);partial.unlink()
    _save(receipt,proof)
    return proof


def _transfer_segments(plan,work,control,transport,concurrency,on_progress,limit,keys):
    """Bound in-flight groups; repeated-resource identity has one serial owner.

    Different resources may arrive out of order. Aggregation always follows the
    immutable plan, and no worker survives either successful or failed return.
    """
    groups=defaultdict(list)
    for track_index,track in enumerate(plan.tracks):
        headers=track.headers if track.headers is not None else plan.headers
        for index,segment in enumerate(track.segments):
            groups[(segment.url,headers)].append((track_index,index,segment))
    progress_lock=threading.Lock();error_lock=threading.Lock()
    downloaded=0;first_error=[];checked_bytes=0;last_space=0.;last_report=0.
    def progress(count,*,transferred):
        nonlocal downloaded,checked_bytes,last_space,last_report
        if transferred and limit:limit(count,control)
        with progress_lock:
            downloaded+=count
            if downloaded>MAX_TRANSFER_BYTES:
                fail("Streaming excede o limite de 64 GiB.","stream_capacity")
            control.check()
            now=time.monotonic()
            if transferred and (not last_space or downloaded-checked_bytes>=SPACE_CHECK_BYTES or now-last_space>=.5):
                # One shared budget covers the next check and every in-flight
                # writer. Do not serialize a filesystem query for every block.
                if shutil.disk_usage(work).free < SPACE_CHECK_BYTES+concurrency*CHUNK+1024*1024:
                    fail("Espaço insuficiente durante streaming.","space")
                checked_bytes=downloaded;last_space=now
            if on_progress and (not last_report or now-last_report>=.1):
                on_progress(downloaded);last_report=now
    def transfer_group(key,jobs):
        identity=None;total=0
        try:
            for track_index,index,segment in jobs:
                control.check();path=work/f"streaming-seg-{track_index}-{index}.bin"
                received=0
                def chunk(count):
                    nonlocal received
                    received+=count;progress(count,transferred=True)
                encryption_proof = _encryption_proof(segment.encryption, keys.get(segment.encryption, dict(key[1]))) if segment.encryption else None
                proof=_segment(segment,path,path.with_suffix(".json"),transport,control,dict(key[1]),chunk,
                               expected_identity=identity,require_identity=len(jobs)>1,encryption_proof=encryption_proof)
                identity=(proof["url"],proof["validator"],proof["resource_total"])
                if not received:progress(proof["bytes"],transferred=False)
                total+=proof["bytes"]
            return total
        except Exception as error:
            with error_lock:
                if not first_error:
                    # An external stop may surface as a socket error in another
                    # worker before the stopping worker reaches check(). Keep
                    # caller cancellation distinct from a real transfer fault.
                    first_error.append(Interrupted() if control.stop.is_set() else error)
            control.interrupt()
            raise
    pending={};source=iter(groups.items());total=0
    with ThreadPoolExecutor(max_workers=concurrency,thread_name_prefix="lume-stream-segment") as pool:
        def submit():
            control.check()
            item=next(source,None)
            if item is not None:
                key,jobs=item;pending[pool.submit(transfer_group,key,jobs)]=key
        try:
            for _ in range(min(concurrency,len(groups))):submit()
            while pending:
                completed,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in completed:
                    del pending[future];total+=future.result()
                for _ in completed:submit()
        except BaseException:
            control.interrupt()
            for future in pending:future.cancel()
            if first_error:raise first_error[0]
            raise
    control.check()
    if on_progress:on_progress(downloaded)
    return total


def _checkpoint_cache(work):
    """Graceful interruption settles writers before making the cache durable.

    A hard process/power loss can lose uncommitted cache files; checksums and
    freshness probes still reject an incomplete or changed cache on resume.
    Completed output has its own fsync before the Manager publishes it.
    """
    for path in owned_stream_files(work):
        if path.name.startswith('streaming-seg-') and path.suffix in ('.bin','.json'):
            with path.open('r+b') as cached:os.fsync(cached.fileno())


def download_stream(plan,work,control,*,transport=None,on_progress=None,limit=None,headers=None,concurrency=4,authorized_keys=None):
    """Transfer the chosen immutable plan, returning unpublished local MP4/M4A."""
    if not isinstance(plan,StreamPlan):
        fail("Plano de streaming inválido.")
    if type(concurrency)is not int or not 1<=concurrency<=8:
        fail("Escolha de 1 a 8 segmentos simultâneos.","stream_capacity")
    if authorized_keys is not None and not isinstance(authorized_keys, AuthorizedKeys):
        fail("Forneça chaves KID autorizadas válidas.", "stream_keys")
    if authorized_keys and not any(track.protection for track in plan.tracks):
        fail("Chaves KID explícitas exigem um manifesto DASH com proteção CENC. Nenhuma chave foi aplicada a esta origem.", "authorized_keys_unused")
    if any(track.protection for track in plan.tracks):
        if not authorized_keys:
            fail("Este DASH exige chaves KID autorizadas. Informe-as na confirmação; não são extraídas do navegador.", "stream_keys")
        plan = replace(plan, fingerprint=hashlib.sha256((plan.fingerprint+authorized_keys.fingerprint()).encode()).hexdigest())
    work=Path(work).resolve(strict=True)
    if not work.is_dir():
        fail("Área de trabalho de streaming indisponível.","ownership")
    control.check();retry_pending_cleanup(work)
    _claim(work,plan)
    if headers is not None and _headers(headers)!=dict(plan.headers):
        fail("Os cabeçalhos do plano de streaming mudaram. Resolva a origem novamente.","stream_changed")
    try:
        return _download_claimed(plan,work,control,transport,on_progress,limit,concurrency,authorized_keys)
    except BaseException as error:
        # _transfer_segments has joined its pool before crossing this boundary.
        try:
            _checkpoint_cache(work)
        except Exception as checkpoint:
            # Preserve child-cleanup ownership and its retry semantics. A failed
            # cache checkpoint must not masquerade as a durable paused cache.
            if getattr(error,'code',None)=='media_cleanup_pending':
                error.add_note(f"Falha adicional no checkpoint do cache: {checkpoint}")
                raise error from checkpoint
            raise DownloadError('stream_checkpoint',
                f"{error} Não foi possível sincronizar o cache interrompido: {checkpoint}. "
                "Temporários preservados; a retomada verificará sua integridade.") from error
        raise


def _download_claimed(plan,work,control,transport,on_progress,limit,concurrency,authorized_keys):
    with _stream_transport(transport) as active:
        keys = _KeyCache(active, control)
        try:
            return _download_active(plan,work,control,active,keys,on_progress,limit,concurrency,authorized_keys)
        finally:
            # All transfer workers have settled here. A still-owned mux child
            # may retain local input handles; its cleanup must complete first.
            keys.clear()
            if not pending_work(work):
                for path in owned_stream_files(work):
                    if path.name.endswith((".clear", ".clear.part")):
                        path.unlink()


def _protected_track(work,index,track,keys,authorized_keys,control,headers):
    """Turn verified local ciphertext into a checked, unpublished MP4 track.

    Only generated local descriptors reach the helper. The streaming parser
    owns each segment's key and IV; demux read-ahead cannot change that owner.
    """
    mapping = authorized_keys.by_kid() if track.protection else {}
    paths = []
    for number,segment in enumerate(track.segments):
        path = work/f"streaming-seg-{index}-{number}.bin"
        _regular(path)
        proof = _json(path.with_suffix(".json"))
        if _hash(path,control) != proof["sha256"]:
            fail("Um segmento mudou antes do processamento criptográfico.","stream_changed")
        if segment.encryption:
            value = keys.get(segment.encryption,headers)
            if proof.get("encryption") != _encryption_proof(segment.encryption,value):
                fail("A chave mudou antes do processamento criptográfico.","stream_changed")
            mapping[segment.encryption.key_epoch[:32]] = value
        paths.append(path)
    if not 1 <= len(mapping) <= 16:
        fail("A faixa exige entre 1 e 16 chaves autorizadas por download.","stream_capacity")
    output = work/f"streaming-protected-{index}.clear"
    _regular(output);output.unlink(missing_ok=True)
    if track.protection:
        source = work/f"streaming-protected-{index}.bin"
        partial = source.with_suffix(".bin.part")
        _regular(source);_regular(partial)
        source.unlink(missing_ok=True);partial.unlink(missing_ok=True)
        with partial.open("xb") as target:
            for path in paths:
                with path.open("rb") as data:
                    while chunk := data.read(CHUNK):
                        control.check();target.write(chunk)
            target.flush()
        control.check();os.rename(partial,source)
        mode = "mov"
    else:
        source = work/f"streaming-protected-{index}.sample"
        _regular(source)
        lines = []
        for segment,path in zip(track.segments,paths):
            if segment.encryption:
                enc = segment.encryption
                lines.append(f"{path.name}\t{enc.key_epoch[:32]}\t{enc.iv}")
            else:
                lines.append(f"{path.name}\t-\t-")
        source.write_text("\n".join(lines)+"\n",encoding="utf-8")
        mode = "sample"
    return decrypt_track(work,source,output,control.stop,mode=mode,keys=mapping)


def _download_active(plan,work,control,transport,keys,on_progress,limit,concurrency,authorized_keys):
    started=time.monotonic()
    preflight=[]
    for track in plan.tracks:
        # Declared CENC goes through the existing explicit-key contract. Inspect
        # unencrypted fMP4 initialization for undeclared protection before fanout.
        if track.container!='mov' or track.protection or not track.segments:continue
        first=track.segments[0]
        if first.start is not None or first.encryption:continue
        headers=dict(track.headers if track.headers is not None else plan.headers)
        with transport.open(first.url,{**headers,'Range':'bytes=0-0'},control) as owned:
            response=owned.response
            if response.status!=206:
                preflight.append({'status':'unknown','reason':'no_initialization_ranges'});continue
            start,end,total=content_range(response.getheader('Content-Range'))
            if (start,end)!=(0,0) or content_length(response) not in (None,1) or len(response.read(2))!=1:
                fail('Teste de inicialização MP4 inconsistente.','range')
            identity=dict(url=owned.url,total=total,validator=validator(response),ranges=True)
        preflight.append(inspect_remote_mp4(identity,transport,control,headers))
    aggregate_bytes=_transfer_segments(plan,work,control,transport,concurrency,on_progress,limit,keys)
    transferred=time.monotonic()
    inputs=[]
    direct_concat=sum(len(t.segments) for t in plan.tracks)<=MAX_CONCAT_FILES
    required=aggregate_bytes*(1 if direct_concat else 2)+1024*1024
    # Ciphertext remains a resumable cache. Allow additional space for all
    # plaintext inputs before allocating any of them; padding only shrinks it.
    required+=sum((work/f"streaming-seg-{t}-{s}.bin").stat().st_size
                  for t,track in enumerate(plan.tracks) for s,segment in enumerate(track.segments) if segment.encryption)
    required+=2*sum((work/f"streaming-seg-{t}-{s}.bin").stat().st_size
                  for t,track in enumerate(plan.tracks) for s,segment in enumerate(track.segments)
                  if track.protection or segment.encryption and segment.encryption.method=="SAMPLE-AES")
    if shutil.disk_usage(work).free < required:
        fail("Espaço insuficiente para reunir as faixas de streaming.","space")
    prepared=[]
    for track_index,track in enumerate(plan.tracks):
        control.check()
        headers=dict(track.headers if track.headers is not None else plan.headers)
        if track.protection or any(s.encryption and s.encryption.method == "SAMPLE-AES" for s in track.segments):
            prepared.append((_protected_track(work, track_index, track, keys, authorized_keys, control, headers),))
            continue
        paths=[]
        for index,segment in enumerate(track.segments):
            control.check()
            path=work/f"streaming-seg-{track_index}-{index}.bin"
            if segment.encryption:
                path=_decrypt_segment(segment,path,keys.get(segment.encryption,headers),control)
            paths.append(path)
        prepared.append(tuple(paths))
    decrypted=time.monotonic()
    for track_index,paths in enumerate(prepared):
        # WebVTT has one header/timestamp map per segment. It is parsed
        # sequentially by its owner, never concatenated as raw media bytes.
        if direct_concat or plan.tracks[track_index].role=='subtitle':
            inputs.append(paths)
            continue
        aggregate=work/f"streaming-track-{track_index}.bin"
        partial=aggregate.with_name(aggregate.name+".part")
        _regular(aggregate);_regular(partial)
        aggregate.unlink(missing_ok=True);partial.unlink(missing_ok=True)
        with partial.open("xb") as output:
            for path in paths:
                with path.open("rb") as source:
                    while chunk:=source.read(1024*1024):
                        control.check();output.write(chunk)
            output.flush();os.fsync(output.fileno())
        os.rename(partial,aggregate);inputs.append(aggregate)
    aggregated=time.monotonic()
    output=work/"streaming-output.mp4"
    _regular(output)
    if pending_work(work):
        fail("Processamento anterior de streaming ainda precisa concluir a limpeza.","media_cleanup_pending")
    output.unlink(missing_ok=True)
    remux=remux_stream(work,inputs,output,control.stop,containers=["mov" if t.protection or any(s.encryption and s.encryption.method=="SAMPLE-AES" for s in t.segments) else t.container for t in plan.tracks],
                       roles=[t.role for t in plan.tracks],audio_only=plan.audio_only,languages=[t.language for t in plan.tracks])
    probe=remux.probe
    for track in plan.tracks:
        if track.duration is None:
            continue
        expected_roles = {"audio","video"} if track.role=="muxed" else {track.role}
        measured=[s for s in probe["streams"] if s.get("codec_type") in expected_roles]
        for stream in measured:
            try:
                duration=float(stream["duration"])
            except (KeyError,TypeError,ValueError):
                fail("Não foi possível confirmar a duração da faixa final de streaming.","stream_duration")
            if not math.isfinite(duration) or duration < track.duration-max(.25,track.duration*.001):
                fail("A duração da mídia final diverge do manifest. Saída incompleta não foi publicada.","stream_duration")
    control.check()
    return dict(path=output,probe=probe,bytes=output.stat().st_size,preflight=preflight,
                timings=dict(transfer_s=transferred-started,aggregate_s=aggregated-decrypted,
                             decrypt_s=decrypted-transferred,remux_s=time.monotonic()-aggregated,**remux.timings))
