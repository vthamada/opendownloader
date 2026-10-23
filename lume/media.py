"""Resolve explicitly supported public pages to direct media or HLS manifests.

yt-dlp extracts metadata only. The ordinary HTTP engine owns all output bytes,
hashing, publication, retries, and partial-file lifecycle.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import math
import os
import re
import shutil
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from .network import DownloadError, validate_url
from .runtime_paths import private_runtime_roots

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}
_PLATFORM_HOSTS = {
    "vimeo.com": "Vimeo", "www.vimeo.com": "Vimeo", "player.vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion", "www.dailymotion.com": "Dailymotion", "dai.ly": "Dailymotion",
    "archive.org": "ArchiveOrg", "www.archive.org": "ArchiveOrg",
}
_PLATFORM_DISCOVERY_FORMAT = "best/bestvideo/bestaudio"
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_FORMAT = (
    "bestvideo[ext=mp4][protocol=https]+bestaudio[ext=m4a][protocol=https]/"
    "best[ext=mp4][vcodec!=none][acodec!=none][protocol=https]"
)
_MIMETYPES = {"mp4": "video/mp4", "webm": "video/webm"}
_AUDIO_MIMETYPES = {"m4a": "audio/mp4", "webm": "audio/webm"}
_FORMAT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SELECTION = re.compile(r"(?:audio:[A-Za-z0-9_-]{1,128}|[A-Za-z0-9_-]{1,128}(?:\+[A-Za-z0-9_-]{1,128})?)\Z")
_DISCOVERY_FORMAT = "best[protocol=https]/bestvideo[protocol=https]/bestaudio[protocol=https]"
# One ordinary YouTube video normally exposes tens of variants. Reject a
# pathological provider response before constructing/sorting a growing catalogue.
_MAX_SOURCE_FORMATS = 1024
_EXTRACTOR_LOCK = threading.Lock()


class MediaError(ValueError):
    def __init__(self, message: str, code: str = "media"):
        self.code = code
        super().__init__(message)


class MediaCancelled(MediaError):
    def __init__(self):
        super().__init__("Resolução do vídeo interrompida pelo usuário.", "interrupted")


def _check(stop_event) -> None:
    if stop_event.is_set():
        raise MediaCancelled()


def _parsed_url(url):
    try:
        return urlsplit(validate_url(url))
    except DownloadError:
        return None


def media_source_kind(url: str) -> str | None:
    """Classify without network access; ordinary HTML never enters a generic extractor."""
    parsed = _parsed_url(url)
    if parsed is None:
        return None
    path = parsed.path.lower()
    if path.endswith('.m3u8'):
        return 'hls'
    if path.endswith('.mpd'):
        return 'dash'
    host = parsed.hostname.lower()
    if host in _YOUTUBE_HOSTS:
        return 'youtube'
    if host in _PLATFORM_HOSTS and (host not in {'archive.org', 'www.archive.org'} or path.startswith('/details/')):
        return 'platform'
    return None


def is_media_url(url: str) -> bool:
    """Known page providers only; the streaming owner handles direct manifests."""
    return media_source_kind(url) in {'youtube', 'platform'}


def _page_source(url: str) -> tuple[str, str, str]:
    """One routing owner for canonical URL, expected identity and named extractor."""
    parsed = _parsed_url(url)
    if parsed is None or parsed.port not in (None, 80 if parsed.scheme == 'http' else 443):
        raise MediaError('Informe uma página pública de mídia compatível.', 'media_url')
    host = parsed.hostname.lower()
    if host in _YOUTUBE_HOSTS:
        identity = _video_id(url)
        return f'https://www.youtube.com/watch?v={identity}', identity, 'Youtube'
    provider = _PLATFORM_HOSTS.get(host)
    if provider == 'Vimeo':
        match = re.fullmatch(r'/(?:video/)?([0-9]{1,20})(?:/([A-Za-z0-9]+))?/?', parsed.path)
        if match:
            try:
                hashes = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=128).get('h', [])
            except ValueError as error:
                raise MediaError('Parâmetros da página Vimeo inválidos.', 'media_url') from error
            if len(hashes) > 1 or (hashes and not re.fullmatch(r'[A-Za-z0-9]{1,128}', hashes[0])):
                raise MediaError('Identidade do link Vimeo ambígua.', 'media_url')
            token = match[2] or (hashes[0] if hashes else None)
            if match[2] and hashes and match[2] != hashes[0]:
                raise MediaError('Identidade do link Vimeo ambígua.', 'media_url')
            # Public embedding is Vimeo's unauthenticated player route. The web
            # page API may require a login even for an embeddable public video.
            canonical = f'https://player.vimeo.com/video/{match[1]}' + (f'?h={token}' if token else '')
            return canonical, match[1], provider
    elif provider == 'Dailymotion':
        match = re.fullmatch(r'/([A-Za-z0-9]+)(?:_[^/]*)?/?' if host == 'dai.ly' else r'/video/([A-Za-z0-9]+)(?:_[^/]*)?/?', parsed.path)
        if match:
            return f'https://www.dailymotion.com/video/{match[1]}', match[1], provider
    elif provider == 'ArchiveOrg':
        match = re.fullmatch(r'/details/([A-Za-z0-9_.-]{1,128})(?:/([^/]+))?/?', parsed.path)
        if match:
            identity = match[1] + ('/' + unquote(match[2]) if match[2] else '')
            return validate_url(url), identity, provider
    raise MediaError('Use o endereço de um vídeo individual público do YouTube, Vimeo, Dailymotion ou Internet Archive; coleções e páginas genéricas não são vídeos.', 'media_url')


def _filename(video_id: str, canonical: str, extension: str) -> str:
    parsed = _parsed_url(canonical)
    provider = 'YouTube' if parsed and parsed.hostname.lower() in _YOUTUBE_HOSTS else _PLATFORM_HOSTS.get(parsed.hostname.lower() if parsed else '', 'Media')
    identity = video_id if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', video_id) else hashlib.sha256(video_id.encode('utf-8')).hexdigest()[:20]
    return f'{provider}-{identity}.{extension}'


def _video_id(url: str) -> str:
    parsed = _parsed_url(url)
    if parsed is None or parsed.hostname.lower() not in _YOUTUBE_HOSTS:
        raise MediaError("Informe uma URL de vídeo do YouTube.", "media_url")
    if parsed.port not in (None, 80 if parsed.scheme == "http" else 443):
        raise MediaError("Porta não suportada na URL do YouTube.", "media_url")
    try:
        if parsed.hostname.lower() in ("youtu.be", "www.youtu.be"):
            video = parsed.path.removeprefix("/")
        elif parsed.path.rstrip("/") == "/watch":
            values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=128).get("v", [])
            video = values[0] if len(values) == 1 else ""
        elif parsed.path.startswith("/shorts/"):
            video = parsed.path[len("/shorts/"):].removesuffix("/")
        else:
            video = ""
    except ValueError:
        video = ""
    if not _VIDEO_ID.fullmatch(video):
        raise MediaError(
            "Use o link de um vídeo: youtube.com/watch?v=…, youtu.be/… ou youtube.com/shorts/…. "
            "Playlists e páginas do canal não são arquivos de vídeo.",
            "media_url",
        )
    return video


def locate_node_runtime(explicit: Path | None = None) -> Path:
    """Locate an existing runtime; never download or install one."""
    if explicit is not None:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate.resolve()
        raise MediaError("O runtime JavaScript configurado não foi encontrado.", "media_runtime")
    executable = 'node.exe' if os.name == 'nt' else 'node'
    for root in private_runtime_roots():
        candidate = root / 'node' / executable
        if candidate.is_file():
            return candidate.resolve()
    bundled = (
        Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
        / "dependencies" / "node" / "bin" / "node.exe"
    )
    if bundled.is_file():
        return bundled
    found = shutil.which("node")
    if found:
        return Path(found)
    raise MediaError(
        "O suporte ao YouTube precisa de um Node.js já instalado (versão 22 ou superior).",
        "media_runtime",
    )


class _QuietLogger:
    def __init__(self, stop_event):
        self.stop = stop_event

    def debug(self, _message):
        _check(self.stop)

    def warning(self, _message):
        _check(self.stop)

    def error(self, _message):
        _check(self.stop)


def _extraction_error(error: Exception, provider: str = 'YouTube') -> MediaError:
    text = str(error).lower()
    if any(word in text for word in ("sign in", "login", "logged-in", "logged in", "authentication", "private video",
                                     "members-only", "confirm your age", "not a bot")):
        return MediaError(
            f"{provider} exige login ou restringe este vídeo. Escolha um vídeo público disponível sem autenticação.",
            "media_access",
        )
    if 'impersonat' in text and ('not available' in text or 'none of' in text or 'dependency' in text):
        return MediaError(f'{provider} exige um componente de compatibilidade de rede que não está incluído nesta instalação do OpenDownloader.', 'media_dependency')
    if any(word in text for word in ("requested format", "no video formats", "no formats")):
        return MediaError(
            "Este vídeo não disponibiliza arquivos diretos MP4 e áudio compatível. "
            "Nenhuma página HTML foi baixada.",
            "media_format",
        )
    if any(word in text for word in ("certificate verify", "certificate_verify", "ssl:")):
        return MediaError(f"A conexão com {provider} não passou na verificação TLS.", "media_tls")
    if "unavailable" in text or "not available" in text or "removed" in text:
        return MediaError("Este vídeo não está disponível para acesso público neste momento.", "media_access")
    if "timed out" in text or "timeout" in text:
        return MediaError(f"{provider} demorou para responder. Tente novamente.", "media_network")
    return MediaError(
        f"Não foi possível resolver este vídeo público. {provider} pode ter alterado o acesso; "
        "verifique a URL ou atualize o suporte de mídia do OpenDownloader.",
        "media_extract",
    )


def _safe_headers(info: dict, canonical: str) -> dict[str, str]:
    raw = info.get("http_headers") or {}
    if not isinstance(raw, dict):
        raise MediaError("Cabeçalhos de mídia inválidos.", "media_metadata")
    headers = {"Referer": canonical}
    allowed = {"user-agent": "User-Agent", "accept": "Accept"}
    for key, value in raw.items():
        if not isinstance(key, str) or key.lower() not in allowed:
            continue
        if (
            not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) >= 127 for char in value)
        ):
            raise MediaError("Cabeçalho de mídia contém caracteres inválidos.", "media_metadata")
        normalized = allowed[key.lower()]
        if normalized in headers:
            raise MediaError("Cabeçalho de mídia duplicado.", "media_metadata")
        headers[normalized] = value
    return headers


def _stream(info: dict, role: str, canonical: str) -> dict:
    if not isinstance(info, dict) or info.get("has_drm"):
        raise MediaError("O vídeo exige proteção não suportada.", "media_format")
    youtube = media_source_kind(canonical) == 'youtube'
    manifest = not youtube and info.get('protocol') in {'m3u8', 'm3u8_native'}
    if manifest:
        try:
            url = validate_url(info.get('url'))
        except DownloadError as error:
            raise MediaError('O endereço do manifest de mídia é inválido.', 'media_metadata') from error
        if urlsplit(url).scheme != 'https':
            raise MediaError('O manifest retornado pela plataforma precisa usar HTTPS.', 'media_format')
        identity = info.get('format_id')
        if not isinstance(identity, str) or not _FORMAT_ID.fullmatch(identity):
            raise MediaError('Identificador de formato inválido.', 'media_metadata')
        audio_only = info.get('vcodec') == 'none' and info.get('acodec') != 'none'
        manifest_role = 'audio' if audio_only else 'video' if info.get('acodec') == 'none' else 'combined'
        group = info.get('manifest_url')
        if group is not None:
            try:
                group = validate_url(group)
            except DownloadError as error:
                raise MediaError('A identidade do manifest de origem é inválida.', 'media_metadata') from error
            if urlsplit(group).scheme != 'https':
                raise MediaError('O manifest de origem precisa usar HTTPS.', 'media_format')
        return {'role':'manifest', 'manifest_url':url, 'manifest_protocol':'hls', 'stream_url':None,
                'headers':_safe_headers(info, canonical), 'format_id':identity, 'expected_size':None,
                'ext':'m4a' if audio_only else 'mp4', 'mimetype':'audio/mp4' if audio_only else 'video/mp4',
                'audio_only':audio_only, 'manifest_role':manifest_role, 'manifest_group':group}
    video_codec = info.get('vcodec') or ('unknown' if not youtube else None)
    audio_codec = info.get('acodec') or ('unknown' if not youtube else None)
    has_video = bool(video_codec) and video_codec != "none"
    has_audio = bool(audio_codec) and audio_codec != "none"
    if (role == "combined" and not (has_video and has_audio)
            or role == "video" and (not has_video or has_audio)
            or role == "audio" and (not has_audio or has_video)):
        raise MediaError("Os codecs não correspondem ao papel do arquivo de mídia.", "media_format")
    mimes = _AUDIO_MIMETYPES if role == "audio" else _MIMETYPES
    if info.get("protocol") != "https" or info.get("ext") not in mimes:
        raise MediaError("O formato exige uma playlist ou processamento não suportado.", "media_format")
    try:
        stream = validate_url(info.get("url"))
    except DownloadError as error:
        raise MediaError("O endereço direto da mídia é inválido.", "media_metadata") from error
    parsed = urlsplit(stream)
    host = parsed.hostname.lower()
    if parsed.scheme != 'https':
        raise MediaError('O resultado da plataforma precisa usar HTTPS.', 'media_format')
    if youtube and (parsed.port not in (None, 443)
                    or not (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
                    or parsed.path != "/videoplayback"):
        raise MediaError("O resultado não é um arquivo direto de mídia do YouTube.", "media_format")
    format_id = info.get("format_id")
    if not isinstance(format_id, str) or not _FORMAT_ID.fullmatch(format_id):
        raise MediaError("Identificador de formato inválido.", "media_metadata")
    size = info.get("filesize")
    if type(size) is not int or not 0 < size <= 2**63 - 1:
        size = None
    return {"role": role, "stream_url": stream, "headers": _safe_headers(info, canonical),
            "mimetype": mimes[info["ext"]], "ext": info["ext"],
            "format_id": format_id, "expected_size": size}


def _validate_info(info: object, video_id: str) -> None:
    if not isinstance(info, dict) or info.get("_type", "video") != "video" or "entries" in info:
        raise MediaError("A URL não retornou um vídeo individual.", "media_metadata")
    if info.get("id") != video_id:
        raise MediaError("A identidade do vídeo retornado não coincide com a URL.", "media_metadata")
    if info.get("has_drm"):
        raise MediaError("O vídeo exige proteção não suportada.", "media_format")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise MediaError("Transmissões ao vivo não são suportadas neste modo.", "media_format")


def _title(info: dict, video_id: str) -> str:
    title = info.get("title")
    if not isinstance(title, str) or not title:
        title = f"Vídeo {video_id}"
    return "".join(char for char in title if ord(char) >= 32 and ord(char) != 127)[:512]


def _resolved(info: dict, original_url: str, video_id: str, streams: list[dict], canonical: str) -> dict:
    audio_only = len(streams) == 1 and (streams[0]["role"] == "audio" or streams[0].get('audio_only', False))
    extension = streams[0]["ext"]
    mimetype = streams[0]["mimetype"]
    result = {
        "original_url": original_url, "video_id": video_id, "title": _title(info, video_id),
        "filename": _filename(video_id, canonical, extension), "mimetype": mimetype,
        "ext": extension, "audio_only": audio_only,
        "streams": streams, "format_id": "+".join(stream["format_id"] for stream in streams),
        "stream_url": streams[0]["stream_url"] if len(streams) == 1 else None,
        "headers": streams[0]["headers"] if len(streams) == 1 else {},
        "expected_size": streams[0]["expected_size"] if len(streams) == 1 else None,
    }
    if len(streams) == 1 and streams[0]['role'] == 'manifest':
        result.update(manifest_url=streams[0]['manifest_url'], manifest_protocol=streams[0]['manifest_protocol'], streams=[])
    elif len(streams) == 2 and all(stream['role'] == 'manifest' for stream in streams):
        result.update(manifest_url=streams[0]['manifest_url'], manifest_protocol=streams[0]['manifest_protocol'],
                      manifest_audio_url=streams[1]['manifest_url'], manifest_audio_headers=streams[1]['headers'],
                      headers=streams[0]['headers'], streams=[])
    return result


def _result(info: object, original_url: str, video_id: str, canonical: str) -> dict:
    _validate_info(info, video_id)
    requested = info.get("requested_formats")
    if requested:
        if not isinstance(requested, list) or len(requested) != 2:
            raise MediaError("A seleção de formatos retornou uma combinação inválida.", "media_format")
        streams = [_stream(requested[0], "video", canonical), _stream(requested[1], "audio", canonical)]
    else:
        streams = [_stream(info, "combined", canonical)]
    return _resolved(info, original_url, video_id, streams, canonical)


def _number(value, maximum: float):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum else None


def _validated_formats(info: dict, video_id: str, canonical: str, stop_event) -> list[tuple]:
    _validate_info(info, video_id)
    raw = info.get("formats")
    if raw is None:
        raw = [info]
    if not isinstance(raw, list) or len(raw) > _MAX_SOURCE_FORMATS:
        raise MediaError("O catálogo de formatos é inválido ou excede o limite de recursos.", "media_metadata")
    validated = []
    identities = set()
    for candidate in raw:
        _check(stop_event)
        if not isinstance(candidate, dict):
            continue
        manifest = media_source_kind(canonical) != 'youtube' and candidate.get('protocol') in {'m3u8', 'm3u8_native'}
        video, audio = candidate.get("vcodec"), candidate.get("acodec")
        if media_source_kind(canonical) != 'youtube':
            video, audio = video or 'unknown', audio or 'unknown'
        if not all(isinstance(codec, str) and 0 < len(codec) <= 128 for codec in (video, audio)):
            continue
        if video == audio == "none":
            continue
        role = 'manifest' if manifest else "combined" if video != "none" and audio != "none" else "audio" if video == "none" else "video"
        try:
            stream = _stream(candidate, role, canonical)
        except MediaError:
            continue
        if stream["format_id"] in identities:
            raise MediaError("O provedor retornou identificadores de formato duplicados.", "media_metadata")
        identities.add(stream["format_id"])
        validated.append((candidate, stream))
    return validated


def _catalogue(info: dict, video_id: str, canonical: str, stop_event) -> list[dict]:
    validated = _validated_formats(info, video_id, canonical, stop_event)
    audio_by_container = {}
    manifest_audio = {}
    for candidate, stream in validated:
        if stream['role'] == 'manifest' and stream.get('manifest_role') == 'audio':
            group = stream.get('manifest_group')
            if group:
                score = (_number(candidate.get('abr'), 1_000_000) or _number(candidate.get('tbr'), 1_000_000) or 0,
                         _number(candidate.get('quality'), 1000) or 0, stream['format_id'])
                if group not in manifest_audio or score > manifest_audio[group][0]:
                    manifest_audio[group] = (score, stream, candidate.get('acodec') or 'desconhecido')
            continue
        if stream["role"] != "audio":
            continue
        group = stream["ext"]
        score = (_number(candidate.get("abr"), 1_000_000) or 0,
                 _number(candidate.get("asr"), 1_000_000) or 0, stream["format_id"])
        if group not in audio_by_container or score > audio_by_container[group][0]:
            audio_by_container[group] = (score, stream, candidate["acodec"])
    choices = []
    for candidate, stream in validated:
        _check(stop_event)
        streams = [stream]
        audio_only = stream["role"] == "audio" or stream.get('audio_only', False)
        if stream['role'] == 'manifest' and stream.get('manifest_role') == 'video':
            companion = manifest_audio.get(stream.get('manifest_group'))
            if companion is None:
                # A video-only variant cannot silently lose its external audio.
                continue
            streams.append(companion[1])
        elif stream["role"] == "video":
            companion = audio_by_container.get("m4a" if stream["ext"] == "mp4" else "webm")
            if companion is None:
                continue
            streams.append(companion[1])
        height = _number(candidate.get("height"), 65535)
        width = _number(candidate.get("width"), 65535)
        fps = _number(candidate.get("fps"), 1000)
        vcodec = candidate.get("vcodec") or 'desconhecido'
        acodec = (candidate.get("acodec") or 'desconhecido') if len(streams) == 1 else companion[2]
        sizes = [row["expected_size"] for row in streams]
        identity = "+".join(row["format_id"] for row in streams)
        if audio_only:
            identity = "audio:" + identity
            label = f"Somente áudio · {stream['ext'].upper()} · {acodec}"
        else:
            resolution = f"{height:g}p" if height else "Resolução desconhecida"
            label = f"{resolution}" + (f" · {fps:g} fps" if fps else "") + f" · {stream['ext'].upper()} · {vcodec}"
            if len(streams) == 2:
                label += " · vídeo + áudio"
        if stream['role'] == 'manifest':
            label += ' · HLS'
        if len(identity) > 256:
            continue
        choices.append({"id": identity, "label": label, "ext": stream["ext"], "mimetype": stream["mimetype"],
                        "filename": _filename(video_id, canonical, stream['ext']),
                        "width": width, "height": height, "fps": fps, "vcodec": vcodec, "acodec": acodec,
                        "expected_size": sum(sizes) if all(size is not None for size in sizes) else None,
                        "audio_only": audio_only, "streams": streams})
    if not choices:
        raise MediaError("Nenhum formato direto de vídeo/áudio compatível está disponível.", "media_format")
    choices.sort(key=lambda row: (row["audio_only"], -(row["height"] or 0), -(row["fps"] or 0), row["ext"], row["id"]))
    return choices


def _extract_media(url: str, stop_event, *, node_runtime: Path | None, format_selector: str) -> tuple:
    """Fresh metadata resolution; signed stream URLs must not be cached for resume.

    Cancellation is checked at request and extractor-log boundaries. An in-flight
    request can take up to its socket timeout; no media output is created here.
    """
    _check(stop_event)
    canonical, video_id, extractor_key = _page_source(url)
    youtube = extractor_key == 'Youtube'
    try:
        import yt_dlp
    except ImportError as error:
        raise MediaError(
            "O suporte a plataformas de vídeo não está incluído nesta instalação do OpenDownloader.", "media_dependency"
        ) from error
    node = locate_node_runtime(node_runtime) if youtube else None
    options = {
        "format": format_selector if youtube else _PLATFORM_DISCOVERY_FORMAT,
        "skip_download": True,
        "check_formats": False,
        "noplaylist": True,
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(stop_event),
        "cachedir": False,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "username": None,
        "password": None,
        "proxy": "",
        "nocheckcertificate": False,
        "allow_unplayable_formats": False,
        "remote_components": [],
        "js_runtimes": {"node": {"path": str(node)}} if node else {},
        "socket_timeout": 12,
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "compat_opts": {"no-certifi"},
        "writeinfojson": False,
        "writethumbnail": False,
        "writesubtitles": False,
    }

    class ControlledYoutubeDL(yt_dlp.YoutubeDL):
        def process_ie_result(self, ie_result, download=True, extra_info=None):
            _check(stop_event)
            if (not isinstance(ie_result, dict) or ie_result.get('_type', 'video') != 'video'
                    or 'entries' in ie_result):
                raise MediaError('A URL não retornou um vídeo individual; listas não são expandidas neste modo.', 'media_metadata')
            formats = ie_result.get('formats')
            if formats is not None and (not isinstance(formats, list) or len(formats) > _MAX_SOURCE_FORMATS):
                raise MediaError('O catálogo de formatos é inválido ou excede o limite de recursos.', 'media_metadata')
            return super().process_ie_result(ie_result, download=download, extra_info=extra_info)

        def urlopen(self, request):
            _check(stop_event)
            response = super().urlopen(request)
            if stop_event.is_set():
                response.close()
                raise MediaCancelled()
            return response

    # yt-dlp has process-wide plugin discovery state. OpenDownloader is its only owner:
    # reject ambient plugins and serialize initialisation/extraction.
    while not _EXTRACTOR_LOCK.acquire(timeout=.1):
        _check(stop_event)
    try:
        _check(stop_event)
        yt_dlp.globals.plugin_dirs.value = []
        with ControlledYoutubeDL(options) as extractor:
            info = extractor.extract_info(canonical, download=False, ie_key=extractor_key)
        _check(stop_event)
        _validate_info(info, video_id)
        return info, video_id, canonical
    except MediaError:
        raise
    except Exception as error:
        _check(stop_event)
        raise _extraction_error(error, 'YouTube' if youtube else extractor_key) from error
    finally:
        _EXTRACTOR_LOCK.release()


def discover_media(url: str, stop_event, *, node_runtime: Path | None = None) -> dict:
    """Return bounded, validated choices without saving signed URLs or media bytes."""
    info, video_id, canonical = _extract_media(url, stop_event, node_runtime=node_runtime,
                                              format_selector=_DISCOVERY_FORMAT)
    choices = _catalogue(info, video_id, canonical, stop_event)
    default_name = _filename(video_id, canonical, 'mp4') if media_source_kind(canonical) == 'youtube' else choices[0]['filename']
    return {"original_url": url, "video_id": video_id, "title": _title(info, video_id), "filename": default_name,
            "formats": [{key: value for key, value in choice.items() if key != "streams"} for choice in choices]}


def resolve_media(url: str, stop_event, *, node_runtime: Path | None = None, selection: str | None = None) -> dict:
    """Resolve a fresh version of the exact selected format, or the legacy default."""
    if selection is not None and (not isinstance(selection, str) or not _SELECTION.fullmatch(selection)):
        raise MediaError("A seleção de formato é inválida. Escolha uma opção do catálogo.", "media_selection")
    info, video_id, canonical = _extract_media(url, stop_event, node_runtime=node_runtime,
                                              format_selector=_FORMAT if selection is None else _DISCOVERY_FORMAT)
    if selection is None:
        if media_source_kind(canonical) != 'youtube':
            choice = _catalogue(info, video_id, canonical, stop_event)[0]
            return _resolved(info, url, video_id, choice['streams'], canonical)
        return _result(info, url, video_id, canonical)
    # The catalogue suggests one high-bitrate companion per video. A previously
    # chosen pair must still resolve exactly if a new/better companion appears.
    sources = {stream["format_id"]: stream for _, stream in _validated_formats(info, video_id, canonical, stop_event)}
    if selection.startswith("audio:"):
        stream = sources.get(selection[len("audio:"):])
        if stream and (stream["role"] == "audio" or stream.get('audio_only', False)):
            return _resolved(info, url, video_id, [stream], canonical)
    elif "+" in selection:
        video_id_selected, audio_id_selected = selection.split("+")
        video, audio = sources.get(video_id_selected), sources.get(audio_id_selected)
        if (video and audio and video["role"] == "video" and audio["role"] == "audio"
                and (video["ext"], audio["ext"]) in {("mp4", "m4a"), ("webm", "webm")}):
            return _resolved(info, url, video_id, [video, audio], canonical)
        if (video and audio and video['role'] == audio['role'] == 'manifest'
                and video.get('manifest_role') == 'video' and audio.get('manifest_role') == 'audio'
                and video.get('manifest_group') and video['manifest_group'] == audio.get('manifest_group')):
            return _resolved(info, url, video_id, [video, audio], canonical)
    else:
        stream = sources.get(selection)
        if stream and (stream['role'] == 'combined' or stream['role'] == 'manifest' and stream.get('manifest_role') == 'combined'):
            return _resolved(info, url, video_id, [stream], canonical)
    raise MediaError("O formato escolhido não está mais disponível. Escolha outro formato antes de reiniciar.", "media_selection")

