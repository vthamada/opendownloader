"""Native Messaging ingress with an optional composition-owned desktop wakeup.

The desktop manager owns consuming ``data_dir/inbox/<uuid>.json``. Each file
contains a validated legacy or versioned request in UTF-8. A producer publishes by
atomic rename only after closing/fsyncing a private dot-prefixed temporary file.
Consumers must claim a ready ``*.json`` by atomic rename to their private work
area before parsing it. Ignore dot files; preserve/recover a claim on failure.
An acknowledgement means *persisted in the inbox*, not downloaded or imported
into the desktop database. The executable may request desktop startup only
after the request has been durably accepted; URLs never select an executable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
from typing import BinaryIO, Callable
from urllib.parse import urlsplit
import uuid

from . import VERSION
from .recording import MAX_CHUNK_BYTES, RecordingError, RecordingSession

HOST_NAME = "app.lume.downloads"
MAX_MESSAGE_BYTES = 64 * 1024
MAX_URL_LENGTH = 8192


def validate_url(value: object) -> str:
    """Validate the untrusted browser boundary, retaining the exact source URL."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("Informe um link HTTP ou HTTPS de até 8192 caracteres.")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 or char.isspace() for char in value) or "\\" in value:
        raise ValueError("O link contém espaços, controles ou barras inválidas.")
    try:
        value.encode("utf-8", errors="strict")
        parsed = urlsplit(value)
        port = parsed.port
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise ValueError
        if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Use um link HTTP ou HTTPS válido, sem usuário e senha na URL.") from exc
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON duplicada.")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Constante JSON inválida.")


def parse_request(value: object) -> dict:
    """One owner for both native frames and revalidated durable inbox data."""
    if not isinstance(value, dict):
        raise ValueError("O pedido deve ser um objeto JSON.")
    if set(value) == {"url"}:
        return {"url": validate_url(value["url"])}
    version = value.get("version")
    allowed = {"version", "url", "title", "media"} | ({"source"} if version == 3 else set())
    if (type(version) is not int or version not in (2, 3)
            or not {"version", "url"}.issubset(value)
            or set(value) - allowed):
        raise ValueError("Pedido de navegador inválido ou versão não suportada.")
    result = {"version": version, "url": validate_url(value["url"])}
    if version == 3:
        if value.get("media") is not True or value.get("source") not in ("hls", "dash"):
            raise ValueError("O pedido de streaming deve indicar HLS ou DASH e expectativa de mídia.")
        result["source"] = value["source"]
    if "title" in value:
        title = value["title"]
        if (not isinstance(title, str) or len(title) > 256
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in title)):
            raise ValueError("Título de mídia inválido.")
        title.encode("utf-8", errors="strict")
        result["title"] = title
    if "media" in value:
        if type(value["media"]) is not bool:
            raise ValueError("Expectativa de mídia inválida.")
        result["media"] = value["media"]
    return result


def _decode_json(body: bytes) -> object:
    return json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def decode_request(body: bytes) -> dict:
    return parse_request(_decode_json(body))


def enqueue_link(data_dir: Path, url: object) -> str:
    """Persist one URL request; no request field can select a local file path."""
    return enqueue_request(data_dir, {"url": url})


def enqueue_request(data_dir: Path, value: object) -> str:
    request = parse_request(value)
    inbox = Path(data_dir) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if inbox.is_symlink() or (hasattr(inbox, "is_junction") and inbox.is_junction()):
        raise OSError("A caixa de entrada não pode ser um link de diretório.")
    identity = str(uuid.uuid4())
    temporary = inbox / f".{identity}.tmp"
    ready = inbox / f"{identity}.json"
    owns_temporary = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owns_temporary = True
        with os.fdopen(descriptor, "wb") as output:
            output.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, ready)
    finally:
        if owns_temporary:
            temporary.unlink(missing_ok=True)
    return identity


def _read_exact(stream: BinaryIO, count: int) -> bytes:
    pieces = bytearray()
    while len(pieces) < count:
        piece = stream.read(count - len(pieces))
        if not piece:
            break
        pieces.extend(piece)
    return bytes(pieces)


def _reply(stream: BinaryIO, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("<I", len(body)))
    stream.write(body)
    stream.flush()


class _RecordingConnection:
    """One ordered recording per native port; EOF always settles ownership."""
    def __init__(self, data_dir: Path, on_queued):
        self.data_dir = data_dir
        self.on_queued = on_queued
        self.session = None

    def interrupt(self):
        current, self.session = self.session, None
        if current is not None:
            current.interrupt()

    def handle(self, value: dict) -> dict:
        command = value.get("command")
        schemas = {
            "recording.start": {"version", "command", "mime", "title", "audio"},
            "recording.chunk": {"version", "command", "recording_id", "sequence", "data"},
            "recording.finish": {"version", "command", "recording_id", "sequence", "bytes"},
            "recording.abort": {"version", "command", "recording_id"},
        }
        try:
            if (not isinstance(command, str) or command not in schemas
                    or set(value) != schemas[command] or type(value.get("version")) is not int
                    or value["version"] != 1):
                raise RecordingError("Contrato da gravação inválido ou versão não suportada.")
            if command == "recording.start":
                if self.session is not None:
                    raise RecordingError("Já existe uma gravação ativa nesta conexão.")
                self.session = RecordingSession(self.data_dir, mime=value["mime"],
                                                title=value["title"], audio=value["audio"])
                return {"ok": True, "version": 1, "recording_id": self.session.id,
                        "max_chunk_bytes": MAX_CHUNK_BYTES, "sequence": 0}
            if self.session is None:
                raise RecordingError("A gravação não está ativa nesta conexão.")
            if command == "recording.chunk":
                return self.session.chunk(value["recording_id"], value["sequence"], value["data"])
            if command == "recording.abort":
                reply = self.session.abort(value["recording_id"])
                self.session = None
                return reply
            facts = self.session.finish(value["recording_id"], value["sequence"], value["bytes"])
            self.session = None
            reply = {"ok": True, "version": 1, "recording_id": facts.id, "status": "ready",
                     "bytes": facts.bytes, "sha256": facts.sha256}
            if self.on_queued is not None:
                try:
                    self.on_queued(self.data_dir)
                    reply["desktop"] = "requested"
                except OSError:
                    reply["desktop"] = "unavailable"
                    reply["warning"] = "Gravação salva. Abra o OpenDownloader para importar o vídeo."
            return reply
        except (ValueError, OSError):
            if command != "recording.start":
                self.interrupt()
            raise


def run_native_host(data_dir: Path, *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
                    on_queued: Callable[[Path], None] | None = None) -> int:
    """Process bounded LE32/UTF-8 frames until EOF; return 1 for broken framing.

    Supplying streams provides a test seam. The executable should call this
    before importing any GUI and must keep all logging away from stdout.
    """
    if stdin is None or stdout is None:
        if os.name == "nt":
            import msvcrt
            for stream in (sys.stdin, sys.stdout):
                if stream is not None:
                    msvcrt.setmode(stream.fileno(), os.O_BINARY)
        stdin = stdin if stdin is not None else sys.stdin.buffer
        stdout = stdout if stdout is not None else sys.stdout.buffer
    connection = _RecordingConnection(Path(data_dir), on_queued)
    try:
        return _run_native_frames(Path(data_dir), stdin, stdout, on_queued, connection)
    finally:
        try:
            connection.interrupt()
        except (ValueError, OSError):
            # Preserve the owned partial and its last unfinished receipt when
            # even final receipt publication fails. Never claim readiness.
            pass


def _run_native_frames(data_dir, stdin, stdout, on_queued, connection):
    while True:
        header = _read_exact(stdin, 4)
        if not header:
            return 0
        if len(header) != 4:
            _reply(stdout, {"ok": False, "error": "Cabeçalho Native Messaging incompleto."})
            return 1
        length = struct.unpack("<I", header)[0]
        if length > MAX_MESSAGE_BYTES:
            _reply(stdout, {"ok": False, "error": "A mensagem excede o limite de 64 KiB."})
            return 1
        body = _read_exact(stdin, length)
        if len(body) != length:
            _reply(stdout, {"ok": False, "error": "Mensagem Native Messaging incompleta."})
            return 1
        recording_frame = False
        try:
            value = _decode_json(body)
            if (isinstance(value, dict) and isinstance(value.get("command"), str)
                    and value["command"].startswith("recording.")):
                recording_frame = True
                _reply(stdout, connection.handle(value))
                continue
            # This exact, read-only frame is transport-only. Durable inbox
            # records still accept download requests exclusively.
            if (isinstance(value, dict) and set(value) == {"version", "command"}
                    and type(value["version"]) is int and value["version"] == 1
                    and value["command"] == "status"):
                _reply(stdout, {"ok": True, "version": 1, "app_version": VERSION, "status": "ready"})
                continue
            request = parse_request(value)
            identity = enqueue_request(Path(data_dir), request)
        except (ValueError, UnicodeError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "A mensagem deve ser um objeto JSON UTF-8 válido."
            _reply(stdout, {"ok": False, "error": message})
        except OSError:
            message = ("Não foi possível salvar a gravação. A captura parcial foi preservada quando possível."
                       if recording_frame else "Não foi possível salvar o link na caixa de entrada do OpenDownloader.")
            _reply(stdout, {"ok": False, "error": message})
        else:
            reply = {"ok": True, "id": identity}
            if on_queued is not None:
                try:
                    on_queued(Path(data_dir))
                    reply["desktop"] = "requested"
                except OSError:
                    reply["desktop"] = "unavailable"
                    reply["warning"] = "Link salvo. Não foi possível abrir o OpenDownloader automaticamente; abra-o para processar a fila."
            _reply(stdout, reply)
