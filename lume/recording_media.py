"""Read-only packet validation for a recording copied into an owned task.

Capture and vault ownership belong to their producers. This boundary accepts
only a regular local copy and reports parsed media facts; it never remuxes,
decodes, obtains keys, or follows a remote playlist.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat

from .media import MediaCancelled
from .mux import MuxError, _run, find_media_tools, retry_pending_cleanup
from .recording import MAX_RECORDING_BYTES


MAX_DIMENSION = 16384
MAX_PIXELS = 8192 * 8192
_CODECS = {
    "mp4": {"video": {"h264", "hevc", "av1", "vp9"}, "audio": {"aac", "opus"}},
    "webm": {"video": {"vp8", "vp9", "av1"}, "audio": {"opus", "vorbis"}},
}


def _source(work: Path, path: Path) -> tuple[Path, Path, os.stat_result]:
    """Check lexical ancestors before resolution can hide a link/reparse point."""
    owned = Path(os.path.abspath(work))
    source = Path(os.path.abspath(path))
    if source == owned or not source.is_relative_to(owned):
        raise MuxError("A gravação deve permanecer na pasta de trabalho desta tarefa.")
    try:
        for current in (*reversed(source.parents), source):
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MuxError("A validação da gravação não aceita links ou pontos de reanálise.")
            if current != source and not stat.S_ISDIR(info.st_mode):
                raise MuxError("Uma pasta da gravação não é um diretório regular.")
        info = source.lstat()
    except OSError as error:
        raise MuxError(f"Não foi possível abrir a gravação local: {error}") from error
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MuxError("A gravação precisa ser um arquivo regular sem links adicionais.")
    if not 0 < info.st_size <= MAX_RECORDING_BYTES:
        raise MuxError("O tamanho da gravação está vazio ou excede o limite de 64 GiB.")
    return owned.resolve(strict=True), source.resolve(strict=True), info


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("campo JSON repetido")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("valor JSON não finito")


def _positive_int(value, label: str, maximum: int) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(label)
    return value


def _facts(raw: bytes, ext: str, audio: bool, size: int) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(value, dict) or "error" in value:
            raise ValueError("resposta inválida do ffprobe")
        streams = value.get("streams")
        if not isinstance(streams, list) or not 1 <= len(streams) <= 2:
            raise ValueError("quantidade de faixas incompatível com uma gravação")
        tracks = []
        roles = []
        for stream in streams:
            if not isinstance(stream, dict):
                raise ValueError("faixa de mídia inválida")
            role = stream.get("codec_type")
            codec = stream.get("codec_name")
            if role not in {"video", "audio"} or codec not in _CODECS[ext][role]:
                raise ValueError("codec ou tipo de faixa não suportado")
            if role in roles:
                raise ValueError("faixa de gravação repetida")
            roles.append(role)
            track = {"codec_type": role, "codec_name": codec,
                     "nb_read_packets": _positive_int(stream.get("nb_read_packets"),
                                                      "faixa sem pacotes legíveis", 2 ** 53 - 1)}
            if role == "video":
                width = _positive_int(stream.get("width"), "largura inválida", MAX_DIMENSION)
                height = _positive_int(stream.get("height"), "altura inválida", MAX_DIMENSION)
                if width * height > MAX_PIXELS:
                    raise ValueError("resolução da gravação excede o limite")
                track.update(width=width, height=height)
            tracks.append(track)
        if "video" not in roles or audio and "audio" not in roles:
            raise ValueError("a gravação não contém as faixas prometidas")
        info = value.get("format")
        if not isinstance(info, dict):
            raise ValueError("contêiner da gravação ausente")
        names = info.get("format_name")
        allowed_names = {"matroska", "webm"} if ext == "webm" else {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
        if not isinstance(names, str) or not names or not set(names.split(",")).issubset(allowed_names):
            raise ValueError("contêiner da gravação incompatível")
        if _positive_int(info.get("size"), "tamanho de mídia inválido", MAX_RECORDING_BYTES) != size:
            raise ValueError("tamanho da gravação mudou durante a leitura")
        duration = info.get("duration")
        if duration not in (None, "N/A"):
            if isinstance(duration, bool):
                raise ValueError("duração inválida")
            duration = float(duration)
            if not math.isfinite(duration) or duration <= 0 or duration > 25 * 60 * 60:
                raise ValueError("duração da gravação inválida")
        else:
            duration = None
    except (UnicodeError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise MuxError(f"A gravação não foi confirmada como mídia válida: {error}.") from error
    return {"video": True, "audio": "audio" in roles, "streams": tracks,
            "duration": duration, "bytes": size, "format": names}


def validate_recording(work: Path, path: Path, ext: str, audio: bool, stop_event) -> dict:
    """Scan all packets without changing bytes, preserving unknown WebM duration.

    The caller proves task ownership and verifies the copied capture digest.
    Shared process supervision retains cleanup failures against that same work
    folder, so publication/cleanup cannot race an unsettled child.
    """
    if stop_event.is_set():
        raise MediaCancelled()
    if not isinstance(ext, str) or ext not in _CODECS or type(audio) is not bool:
        raise MuxError("Contêiner ou declaração de áudio da gravação inválidos.")
    owned, source, before = _source(work, path)
    retry_pending_cleanup(owned)
    _, ffprobe = find_media_tools()
    command = [str(ffprobe), "-v", "error", "-max_alloc", str(64 * 1024 ** 2),
               "-max_streams", "2", "-protocol_whitelist", "file", "-f",
               "matroska" if ext == "webm" else "mov"]
    if ext == "mp4":
        command.extend(["-enable_drefs", "0"])
    command.extend(["-count_packets", "-show_error", "-show_entries",
                    "stream=codec_type,codec_name,width,height,nb_read_packets:format=format_name,duration,size",
                    "-of", "json", str(source)])
    raw = _run(command, stop_event, 600, work=owned)
    _, after_path, after = _source(work, path)
    if after_path != source or _identity(before) != _identity(after):
        raise MuxError("A gravação mudou durante a validação; o arquivo não foi publicado.")
    return _facts(raw, ext, audio, before.st_size)
