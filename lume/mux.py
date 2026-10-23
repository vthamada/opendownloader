"""Mux locally owned MP4/M4A files without network access or shell execution."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from .media import MediaCancelled, MediaError
from .runtime_paths import private_runtime_roots
from .network import Control
from .subtitles import merge_webvtt


class MuxError(MediaError):
    def __init__(self, message):
        super().__init__(message, "media_mux")


class MuxCleanupPending(MuxError):
    def __init__(self, work: Path | None, process_pid: int, message: str):
        self.work = work
        self.process_pid = process_pid
        super().__init__(message)
        self.code = "media_cleanup_pending"


def find_media_tools() -> tuple[Path, Path]:
    executable = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    probe_name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    for root in private_runtime_roots():
        ffmpeg = root / 'ffmpeg' / 'bin' / executable
        if ffmpeg.is_file():
            ffprobe = ffmpeg.with_name(probe_name)
            if not ffprobe.is_file():
                raise MuxError("O ffprobe não foi encontrado junto ao FFmpeg privado do aplicativo.")
            return ffmpeg.resolve(), ffprobe.resolve()
    local = Path("C:/ffmpeg/bin/ffmpeg.exe")
    ffmpeg = local if local.is_file() else Path(shutil.which("ffmpeg") or "")
    if not ffmpeg.is_file():
        raise MuxError("O FFmpeg não foi encontrado. Configure uma instalação local existente.")
    sibling = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    ffprobe = sibling if sibling.is_file() else Path(shutil.which("ffprobe") or "")
    if not ffprobe.is_file():
        raise MuxError("O ffprobe não foi encontrado junto ao FFmpeg.")
    return ffmpeg.resolve(), ffprobe.resolve()


@dataclass(eq=False)
class _ProcessOwner:
    process: subprocess.Popen
    work: Path | None = None
    readers: list[threading.Thread] = field(default_factory=list)
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    overflow: bool = False
    read_error: bool = False
    settle_lock: threading.Lock = field(default_factory=threading.Lock)


_PENDING: set[_ProcessOwner] = set()
_PENDING_LOCK = threading.Lock()


def _read(owner: _ProcessOwner, pipe, target: bytearray, limit: int, tail: bool):
    try:
        while chunk := pipe.read(4096):
            if tail:
                target.extend(chunk)
                if len(target) > limit:
                    del target[:-limit]
            else:
                capacity = limit - len(target)
                target.extend(chunk[:capacity])
                if len(chunk) > capacity:
                    owner.overflow = True
    except (OSError, ValueError):
        owner.read_error = True


def _settle_locked(owner: _ProcessOwner) -> None:
    process = owner.process
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for reader in owner.readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in owner.readers):
            raise MuxError("A coleta da saída do FFmpeg ainda não terminou.")
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe:
                pipe.close()
    except BaseException as error:
        with _PENDING_LOCK:
            _PENDING.add(owner)
        raise MuxCleanupPending(owner.work, process.pid,
            f"A limpeza do processo de mídia ficou pendente (PID {process.pid}): {error}"
        ) from error
    with _PENDING_LOCK:
        _PENDING.discard(owner)


def _settle(owner: _ProcessOwner) -> None:
    with owner.settle_lock:
        _settle_locked(owner)


def pending_work(work: Path) -> bool:
    """A work folder remains busy while any failed owner is still retained."""
    selected = Path(work).resolve()
    with _PENDING_LOCK:
        return any(owner.work == selected for owner in _PENDING)


def retry_pending_cleanup(work: Path | None = None) -> None:
    """Retry retained failed owners for one work folder, or explicitly all."""
    selected = Path(work).resolve() if work is not None else None
    with _PENDING_LOCK:
        owners = tuple(owner for owner in _PENDING if selected is None or owner.work == selected)
    failures = []
    for owner in owners:
        try:
            _settle(owner)
        except MuxCleanupPending as error:
            failures.append(error)
    if failures:
        raise MuxCleanupPending(selected, failures[0].process_pid,
                                "\n".join(str(error) for error in failures))


def _write_input(owner: _ProcessOwner, value: bytes) -> None:
    try:
        owner.process.stdin.write(value)
        owner.process.stdin.close()
    except (OSError, ValueError):
        owner.read_error = True


def _run(command: list[str], stop_event, timeout: float, *, work: Path | None = None,
         private_input: bytes | None = None) -> bytes:
    if stop_event.is_set():
        raise MediaCancelled()
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE if private_input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as error:
        raise MuxError(f"Não foi possível iniciar a ferramenta de mídia: {error}") from error
    owner = _ProcessOwner(process, Path(work).resolve() if work is not None else None)
    failure = None
    try:
        if private_input is not None:
            writer = threading.Thread(target=_write_input, args=(owner,private_input), daemon=True)
            writer.start()
            owner.readers.append(writer)
        for pipe, target, limit, tail in (
            (process.stdout, owner.stdout, 65536, False),
            (process.stderr, owner.stderr, 16384, True),
        ):
            thread = threading.Thread(target=_read, args=(owner, pipe, target, limit, tail), daemon=True)
            thread.start()
            owner.readers.append(thread)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if stop_event.is_set():
                raise MediaCancelled()
            if time.monotonic() >= deadline:
                raise MuxError("A ferramenta de mídia excedeu o tempo permitido.")
            time.sleep(.05)
    except BaseException as error:
        failure = error
    finally:
        _settle(owner)
    if failure is not None:
        if isinstance(failure, MediaError) or not isinstance(failure, Exception):
            raise failure
        raise MuxError(f"Falha ao supervisionar a ferramenta de mídia: {failure}") from failure
    if stop_event.is_set():
        raise MediaCancelled()
    if owner.read_error or owner.overflow:
        raise MuxError("A saída da ferramenta de mídia excedeu os limites ou ficou incompleta.")
    if process.returncode:
        reason = owner.stderr.decode("utf-8", errors="replace").strip()[-2048:]
        raise MuxError("A ferramenta de mídia não conseguiu processar o arquivo." + (f"\n{reason}" if reason else ""))
    return bytes(owner.stdout)


def find_crypto_helper() -> Path:
    # Caller secrets must never be passed to a program found through PATH or
    # beside an unrelated external FFmpeg. Installation owns these roots.
    for root in private_runtime_roots():
        helper = root / "ffmpeg" / "bin" / "OpenDownloaderCrypto.exe"
        if helper.is_file() and not helper.is_symlink():
            return helper.resolve(strict=True)
    raise MuxError("O componente privado de criptografia não está disponível. Atualize a instalação do OpenDownloader.")


def decrypt_track(work: Path, source: Path, output: Path, stop_event, *, mode: str,
                  keys: dict[str, bytes]) -> Path:
    """Private libavformat helper: keys travel only through a bounded stdin pipe.

    The helper accepts generated local inputs, never a remote manifest. It
    demuxes/decrypts, checks decoding, and copies packets without transcoding.
    Existing process ownership supervises the pipe writer as well as readers.
    """
    import re
    if (mode not in {"mov","sample"} or not isinstance(keys,dict) or not 1<=len(keys)<=16
            or any(not isinstance(k,str) or not re.fullmatch(r"[0-9a-f]{32}",k)
                   or type(v) is not bytes or len(v)!=16 for k,v in keys.items())):
        raise MuxError("Chaves ou modo de criptografia local inválidos.")
    owned = Path(work).resolve(strict=True)
    source = _owned_path(owned,source,existing=True)
    destination = _owned_path(owned,output,existing=False)
    helper = find_crypto_helper()
    # This helper suppresses libav logs. Even malformed secrets are never used
    # in command arguments, error formatting, environment, or named files.
    value = "".join(k+":"+v.hex()+"\n" for k,v in sorted(keys.items())).encode("ascii")
    _run([str(helper),mode,str(source),str(destination)],stop_event,6*60*60,work=owned,private_input=value)
    if not destination.is_file() or not destination.stat().st_size:
        raise MuxError("O processamento criptográfico não produziu uma mídia válida.")
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())
    return destination


def _owned_path(work: Path, path: Path, *, existing: bool) -> Path:
    try:
        resolved = Path(path).resolve(strict=existing)
    except OSError as error:
        raise MuxError("Um arquivo de mídia não foi encontrado.") from error
    if not resolved.is_relative_to(work) or resolved == work:
        raise MuxError("Arquivos de mídia devem permanecer na pasta de trabalho desta tarefa.")
    if existing and not resolved.is_file():
        raise MuxError("Uma entrada de mídia não é um arquivo regular.")
    if not existing and (resolved.exists() or not resolved.parent.is_dir()):
        raise MuxError("O destino intermediário já existe ou sua pasta não está disponível.")
    return resolved


def mux_media(work: Path, inputs: list[Path], output: Path, stop_event, *,
              output_ext: str = "mp4", video_ext: str = "mp4", audio_ext: str = "m4a") -> dict:
    """Create unpublished MP4/WebM and require actual video+audio in ffprobe.

    Inputs must be one combined resource or the matching video/audio pair.
    The caller retains file ownership and cleans unpublished output on failure.
    """
    if stop_event.is_set():
        raise MediaCancelled()
    try:
        owned = Path(work).resolve(strict=True)
    except OSError as error:
        raise MuxError("A pasta de trabalho da mídia não existe.") from error
    if not owned.is_dir() or not isinstance(inputs, list) or len(inputs) not in (1, 2):
        raise MuxError("A combinação precisa de um MP4 completo ou das duas faixas MP4/M4A.")
    if (output_ext not in {"mp4", "webm"} or video_ext != output_ext
            or len(inputs) == 2 and (video_ext, audio_ext) not in {("mp4", "m4a"), ("webm", "webm")}):
        raise MuxError("A combinação de contêineres de vídeo e áudio não é suportada.")
    retry_pending_cleanup(owned)
    sources = [_owned_path(owned, path, existing=True) for path in inputs]
    destination = _owned_path(owned, output, existing=False)
    ffmpeg, ffprobe = find_media_tools()
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
    extensions = [video_ext, audio_ext] if len(sources) == 2 else [video_ext]
    for source, extension in zip(sources, extensions):
        # Forced demuxers cannot follow a disguised playlist. MOV external data
        # references are disabled; every child is limited to local file I/O.
        command.extend(["-protocol_whitelist", "file", "-f", "matroska" if extension == "webm" else "mov"])
        if extension != "webm":
            command.extend(["-enable_drefs", "0"])
        command.extend(["-i", str(source)])
    command.extend(["-map", "0:v:0", "-map", "1:a:0" if len(sources) == 2 else "0:a:0",
                    "-c", "copy"])
    if output_ext == "mp4":
        command.extend(["-movflags", "+faststart"])
    command.extend(["-f", output_ext, str(destination)])
    _run(command, stop_event, 300, work=owned)
    try:
        # Windows requires a writable handle for FlushFileBuffers/fsync.
        with destination.open("r+b") as target:
            target.flush()
            os.fsync(target.fileno())
            if os.fstat(target.fileno()).st_size <= 0:
                raise MuxError("O arquivo combinado ficou vazio.")
    except OSError as error:
        raise MuxError(f"Não foi possível sincronizar o arquivo combinado: {error}") from error
    probe_command = [str(ffprobe), "-v", "error", "-protocol_whitelist", "file", "-f",
                     "matroska" if output_ext == "webm" else "mov"]
    if output_ext == "mp4":
        probe_command.extend(["-enable_drefs", "0"])
    raw = _run([*probe_command,
        "-show_entries", "stream=codec_type,codec_name:format=format_name,duration,size",
        "-of", "json", str(destination),
    ], stop_event, 30, work=owned)
    try:
        probe = json.loads(raw)
        streams = probe["streams"]
        if not isinstance(streams, list) or not all(isinstance(stream, dict) for stream in streams):
            raise ValueError()
        video = any(stream.get("codec_type") == "video" for stream in streams)
        audio = any(stream.get("codec_type") == "audio" for stream in streams)
        if not video or not audio:
            raise ValueError()
        format_info = probe.get("format", {})
        if not isinstance(format_info, dict):
            raise ValueError()
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise MuxError("O arquivo combinado não foi confirmado como vídeo com áudio.") from error
    return {"video": video, "audio": audio, "streams": streams,
            "duration": format_info.get("duration"), "bytes": destination.stat().st_size,
            "format": format_info.get("format_name")}


MAX_CONCAT_FILES = 768  # Bound simultaneously open FFmpeg file handles/buffers.


@dataclass(frozen=True)
class StreamRemuxResult:
    """Media facts and disjoint operation timings, interpreted by their owner."""
    probe: dict
    timings: dict[str, float]


def remux_stream(work: Path, inputs: list[Path | tuple[Path, ...]], output: Path, stop_event, *,
                 containers: list[str], roles: list[str], audio_only: bool = False,
                 languages: list[str | None] | None = None) -> StreamRemuxResult:
    """Remux concatenated local TS/fMP4 tracks; never give FFmpeg a manifest.

    The streaming owner has already transferred each segment through Transport.
    Child ownership, cancellation and failed cleanup remain shared with mux_media.
    """
    started = time.monotonic()
    owned = Path(work).resolve(strict=True)
    media_roles=[role for role in roles if role!='subtitle']
    if (not owned.is_dir() or not 1 <= len(inputs) <= 3 or len(containers) != len(inputs)
            or len(roles) != len(inputs) or any(value not in {"mov", "mpegts", "webvtt"} for value in containers)
            or media_roles not in (["audio"],["video"],["muxed"],["video","audio"])
            or roles not in (media_roles,media_roles+["subtitle"])
            or any((role=='subtitle')!=(container=='webvtt') for role,container in zip(roles,containers))):
        raise MuxError("Faixas locais de streaming incompatíveis.")
    if languages is not None and (len(languages)!=len(inputs) or any(value is not None and (not isinstance(value,str) or len(value)>64 or any(ord(c)<32 or ord(c)==127 for c in value)) for value in languages)):
        raise MuxError("Idiomas das faixas inválidos.")
    retry_pending_cleanup(owned)
    if sum(len(item) if isinstance(item, tuple) and container!='webvtt' else 0 for item,container in zip(inputs,containers)) > MAX_CONCAT_FILES:
        raise MuxError("Muitos arquivos simultâneos para a montagem local.")
    sources = []
    for index, item in enumerate(inputs):
        if containers[index]=='webvtt':
            for path in item if isinstance(item,tuple) else (item,):
                _owned_path(owned,path,existing=True)
            sources.append(('', 'file'))  # Normalized below; no concat list or open-handle expansion.
            continue
        if not isinstance(item, tuple):
            sources.append((str(_owned_path(owned, item, existing=True)), "file"))
            continue
        if not item:
            raise MuxError("Faixa local sem segmentos.")
        paths = []
        for path in item:
            if Path(path).is_symlink():
                raise MuxError("Segmento local não pode ser um link simbólico.")
            source = _owned_path(owned, path, existing=True)
            token = "file:" + source.as_posix()
            if any(c in token for c in "\r\n\0"):
                raise MuxError("Caminho incompatível com a lista local de segmentos.")
            # concatf uses FFmpeg av_get_token, not shell escaping.
            paths.append("'" + token.replace("'", "'\\''") + "'\n")
        listing = owned / f"streaming-concat-{index}.txt"
        if listing.is_symlink():
            raise MuxError("Lista local não pode ser um link simbólico.")
        with listing.open("w", encoding="utf-8", newline="\n") as target:
            target.writelines(paths)
        sources.append(("concatf:" + listing.as_posix(), "file,concatf"))
    destination = _owned_path(owned, output, existing=False)
    ffmpeg, ffprobe = find_media_tools()
    if 'subtitle' in roles:
        first,protocols=sources[0]
        raw=_run([str(ffprobe),'-v','error','-protocol_whitelist',protocols,'-f',containers[0],
                  '-show_entries','format=start_time','-of','json',first],stop_event,30,work=owned)
        try:origin=float(json.loads(raw)['format']['start_time'])
        except (ValueError,TypeError,KeyError,UnicodeError):raise MuxError('Não foi possível confirmar a origem temporal do vídeo para as legendas.') from None
        index=roles.index('subtitle');item=inputs[index]
        paths=item if isinstance(item,tuple) else (item,)
        normalized=owned/f'streaming-subtitle-{index}.vtt'
        if normalized.is_symlink() or normalized.exists() and not normalized.is_file():raise MuxError('Legenda local substituída; preservada.')
        normalized.unlink(missing_ok=True)
        control=Control();control.stop=stop_event
        merge_webvtt(paths,normalized,control,origin=origin)
        sources[index]=(str(normalized),'file')
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
    for (source, protocols), container in zip(sources, containers):
        command.extend(["-protocol_whitelist", protocols, "-f", container])
        if container == "mov":
            command.extend(["-enable_drefs", "0"])
        command.extend(["-i", str(source)])
    if audio_only or roles == ["audio"]:
        command.extend(["-map", "0:a:0"])
    elif media_roles == ["video","audio"]:
        command.extend(["-map", "0:v:0", "-map", "1:a:0"])
    else:
        command.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
    if 'subtitle' in roles:
        command.extend(['-map',f'{roles.index("subtitle")}:s:0'])
    # Completed local downloads need no second full-file shift of the mdat.
    # moov at EOF is a normal seekable MP4, verified below before publication.
    command.extend(["-c", "copy"])
    if 'subtitle' in roles:command.extend(['-c:s','mov_text'])
    for index,role in enumerate(roles):
        language=(languages or [None]*len(roles))[index]
        if language:
            spec={'audio':'a:0','video':'v:0','subtitle':'s:0'}.get(role)
            if spec:
                # MP4's compact language field uses ISO 639-2; retain the exact
                # source label separately rather than truncating a BCP-47 tag.
                common={'pt':'por','en':'eng','es':'spa','fr':'fra','de':'deu','it':'ita','ja':'jpn','zh':'zho','ko':'kor','ar':'ara','ru':'rus'}
                primary=language.split('-',1)[0].lower()
                packed=primary if len(primary)==3 and primary.isascii() and primary.isalpha() else common.get(primary,'und')
                command.extend([f'-metadata:s:{spec}',f'language={packed}',f'-metadata:s:{spec}',f'handler_name={language}'])
    command.extend(["-f", "mp4", str(destination)])
    prepared = time.monotonic()
    _run(command, stop_event, 300, work=owned)
    child_done = time.monotonic()
    with destination.open("r+b") as target:
        target.flush()
        os.fsync(target.fileno())
        if os.fstat(target.fileno()).st_size <= 0:
            raise MuxError("O streaming combinado ficou vazio.")
    synced = time.monotonic()
    raw = _run([str(ffprobe), "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
                "-enable_drefs", "0", "-show_entries", "stream=codec_type,codec_name,duration,start_time:format=format_name,duration,size",
                "-of", "json", str(destination)], stop_event, 30, work=owned)
    try:
        value = json.loads(raw)
        streams = value["streams"]
        if not isinstance(streams, list) or not streams or not all(isinstance(s, dict) for s in streams):
            raise ValueError()
        video = any(s.get("codec_type") == "video" for s in streams)
        audio = any(s.get("codec_type") == "audio" for s in streams)
        if (not video and not audio or (audio_only or "audio" in roles) and not audio
                or "video" in roles and not video or audio_only and video):
            raise ValueError()
        if 'subtitle' in roles and not any(s.get('codec_type')=='subtitle' for s in streams):raise ValueError()
        info = value["format"]
        duration = float(info["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise MuxError("O streaming final não foi confirmado como mídia reproduzível com as faixas esperadas.") from error
    probe = dict(video=video, audio=audio, streams=streams, duration=info["duration"],
                 bytes=destination.stat().st_size, format=info.get("format_name"))
    verified = time.monotonic()
    return StreamRemuxResult(probe, {
        "remux_prepare_s": prepared - started,
        "remux_child_s": child_done - prepared,
        "remux_fsync_s": synced - child_done,
        "remux_probe_s": verified - synced,
    })

