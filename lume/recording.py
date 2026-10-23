"""Bounded local vault for recordings explicitly shared through the browser.

The browser supplies bytes, never filesystem authority. Ready receipts are the
handoff boundary; interrupted captures remain owned and are never auto-deleted.
This module does not capture, decode, decrypt or execute media.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

MAX_CHUNK_BYTES = 36864
MAX_RECORDING_BYTES = 64 * 1024 ** 3
MAX_RECORDING_SECONDS = 24 * 60 * 60
MAX_RECORDINGS = 1000
DISK_CHECK_BYTES = 4 * 1024 ** 2
DISK_RESERVE_BYTES = 8 * 1024 ** 2
MAX_METADATA_BYTES = 8192
OWNER_ID = "opendownloader-recording-v1"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CODEC = re.compile(r"(?:vp8|vp9|vp09(?:\.[0-9A-Za-z]+)*|av1|av01(?:\.[0-9A-Za-z]+)*|avc1(?:\.[0-9A-Fa-f]+)?|h264|opus|vorbis|mp4a(?:\.[0-9A-Fa-f]+)*|aac)\Z", re.I)


class RecordingError(ValueError):
    """A capture boundary failed; callers may report the safe, bounded message."""


def _plain(path: Path, *, directory: bool | None = None, absent=False) -> Path:
    path = Path(os.path.abspath(path))
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if current == path and absent:
                return path
            if current != path:
                continue
            raise RecordingError("Arquivo de gravação ausente.") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RecordingError("A gravação não aceita links ou pontos de reanálise.")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise RecordingError("Ancestral da gravação não é uma pasta.")
    if directory is True and not path.is_dir() or directory is False and not path.is_file():
        raise RecordingError("Tipo de arquivo da gravação inválido.")
    if directory is False and path.stat().st_nlink != 1:
        raise RecordingError("A gravação não aceita arquivos com links adicionais.")
    return path


def _identity(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RecordingError("Identificador da gravação inválido.")
    return value


def _text(value: object) -> str:
    if (not isinstance(value, str) or len(value) > 256
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
        raise RecordingError("Título da gravação inválido.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise RecordingError("Título da gravação inválido.") from None
    return value


def _mime(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 160:
        raise RecordingError("Formato de gravação inválido.")
    parts = value.split(";")
    if parts[0] not in ("video/webm", "video/mp4") or len(parts) > 2:
        raise RecordingError("Use uma gravação WebM ou MP4.")
    if len(parts) == 2:
        parameter = parts[1].strip()
        if not parameter.startswith("codecs="):
            raise RecordingError("Parâmetro do formato de gravação inválido.")
        codecs = parameter[7:]
        if codecs.startswith('"') and codecs.endswith('"'):
            codecs = codecs[1:-1]
        values = codecs.split(",")
        if not 1 <= len(values) <= 3 or any(not _CODEC.fullmatch(codec.strip()) for codec in values):
            raise RecordingError("Codec de gravação inválido.")
    return value, "webm" if parts[0] == "video/webm" else "mp4"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecordingError("Metadados duplicados na gravação.")
        result[key] = value
    return result


def _read_json(path: Path) -> dict:
    _plain(path, directory=False)
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise RecordingError("Metadados da gravação excedem o limite.")
    with path.open("rb") as source:
        raw = source.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise RecordingError("Metadados da gravação excedem o limite.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(RecordingError("Metadados inválidos.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise RecordingError("Metadados da gravação inválidos.") from None
    if not isinstance(value, dict):
        raise RecordingError("Metadados da gravação inválidos.")
    return value


def _write_new(path: Path, value: dict):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_METADATA_BYTES:
        raise RecordingError("Metadados da gravação excedem o limite.")
    _plain(path, absent=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def _atomic_receipt(folder: Path, value: dict):
    _plain(folder, directory=True)
    destination = folder / "receipt.json"
    if destination.exists():
        previous = _receipt(folder)
        if previous["id"] != value["id"]:
            raise RecordingError("A identidade da gravação foi alterada.")
    temporary = folder / (".receipt-" + uuid.uuid4().hex + ".tmp")
    _write_new(temporary, value)
    # Leave a failed publication's owned temporary receipt for recovery.
    _plain(destination, absent=True)
    os.replace(temporary, destination)


def _owner(folder: Path) -> dict:
    _plain(folder, directory=True)
    owner = _read_json(folder / "owner.json")
    if (set(owner) != {"owner", "id", "created_at"} or owner.get("owner") != OWNER_ID
            or _identity(owner.get("id")) != folder.name):
        raise RecordingError("Propriedade da gravação não comprovada.")
    created = owner.get("created_at")
    if not isinstance(created, str) or len(created) > 40:
        raise RecordingError("Data da gravação inválida.")
    try:
        parsed = datetime.fromisoformat(created)
    except ValueError:
        raise RecordingError("Data da gravação inválida.") from None
    if parsed.tzinfo is None:
        raise RecordingError("Data da gravação inválida.")
    return owner


def _receipt(folder: Path) -> dict:
    owner = _owner(folder)
    value = _read_json(folder / "receipt.json")
    if set(value) != {"version", "id", "status", "mime", "ext", "title", "audio", "bytes", "sha256", "created_at", "sequence"}:
        raise RecordingError("Contrato dos metadados da gravação inválido.")
    mime, ext = _mime(value["mime"])
    if (type(value["version"]) is not int or value["version"] != 1
            or value["id"] != owner["id"] or value["created_at"] != owner["created_at"]
            or not isinstance(value["status"], str) or value["status"] not in {"recording", "interrupted", "ready"}
            or value["ext"] != ext or type(value["audio"]) is not bool
            or type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_RECORDING_BYTES
            or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= MAX_RECORDING_BYTES
            or not isinstance(value["sha256"], str) or not _DIGEST.fullmatch(value["sha256"])):
        raise RecordingError("Metadados da gravação inválidos.")
    _text(value["title"])
    if value["status"] == "ready" and value["bytes"] == 0:
        raise RecordingError("A gravação está vazia.")
    return value


@dataclass(frozen=True)
class RecordingFacts:
    id: str
    path: Path
    mime: str
    ext: str
    title: str
    audio: bool
    bytes: int
    sha256: str
    created_at: str

    def to_dict(self):
        return {"id": self.id, "path": str(self.path), "mime": self.mime, "ext": self.ext,
                "title": self.title, "audio": self.audio, "bytes": self.bytes,
                "sha256": self.sha256, "created_at": self.created_at}


def get_recording(data_dir: Path, identity: str) -> RecordingFacts:
    folder = Path(data_dir) / "recordings" / _identity(identity)
    value = _receipt(folder)
    if value["status"] != "ready":
        raise RecordingError("A gravação ainda não foi concluída.")
    capture = _plain(folder / ("capture." + value["ext"]), directory=False)
    if capture.stat().st_size != value["bytes"]:
        raise RecordingError("O tamanho da gravação foi alterado.")
    return RecordingFacts(value["id"], capture, value["mime"], value["ext"], value["title"],
                          value["audio"], value["bytes"], value["sha256"], value["created_at"])


def ready_recordings(data_dir: Path) -> tuple[RecordingFacts, ...]:
    root = _plain(Path(data_dir) / "recordings", absent=True)
    if not root.exists():
        return ()
    _plain(root, directory=True)
    result = []
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries, 1):
            if count > MAX_RECORDINGS:
                raise RecordingError("Há mais de 1000 entradas na pasta de gravações; a verificação está incompleta.")
            if not _ID.fullmatch(entry.name):
                continue
            folder = Path(entry.path)
            # Unknown directories are not owned and are never modified.
            _plain(folder, directory=True)
            if not (folder / "owner.json").exists():
                continue
            value = _receipt(folder)
            if value["status"] == "ready":
                result.append(get_recording(data_dir, entry.name))
    return tuple(sorted(result, key=lambda facts: (facts.created_at, facts.id)))


class RecordingSession:
    def __init__(self, data_dir: Path, *, mime: str, title: str, audio: bool):
        self.mime, self.ext = _mime(mime)
        self.title = _text(title)
        if type(audio) is not bool:
            raise RecordingError("Expectativa de áudio da gravação inválida.")
        self.audio = audio
        self.root = _plain(Path(data_dir) / "recordings", absent=True)
        self.root.mkdir(parents=True, exist_ok=True)
        _plain(self.root, directory=True)
        # Bound the retained vault before allocating another capture.
        with os.scandir(self.root) as entries:
            if sum(1 for _, _entry in zip(range(MAX_RECORDINGS), entries)) >= MAX_RECORDINGS:
                raise RecordingError("A pasta de gravações atingiu o limite de 1000 entradas.")
        self.id = uuid.uuid4().hex
        self.folder = self.root / self.id
        self.folder.mkdir(mode=0o700)
        self.created_at = datetime.now(timezone.utc).isoformat()
        _write_new(self.folder / "owner.json", {"owner": OWNER_ID, "id": self.id, "created_at": self.created_at})
        self.sequence = 0
        self.bytes = 0
        self._hash = hashlib.sha256()
        self._started = time.monotonic()
        self._next_disk_check = 0
        self._closed = False
        self._file = None
        self._status = "recording"
        _atomic_receipt(self.folder, self._metadata())
        try:
            self._disk(0)
            path = self.folder / ("capture." + self.ext + ".part")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self._file = os.fdopen(descriptor, "wb")
            self._file_identity = os.fstat(self._file.fileno())
        except BaseException:
            self.interrupt()
            raise

    def _metadata(self):
        return {"version": 1, "id": self.id, "status": self._status, "mime": self.mime,
                "ext": self.ext, "title": self.title, "audio": self.audio, "bytes": self.bytes,
                "sha256": self._hash.hexdigest(), "created_at": self.created_at, "sequence": self.sequence}

    def _disk(self, incoming: int):
        if self.bytes >= self._next_disk_check:
            _plain(self.folder, directory=True)
            if shutil.disk_usage(self.folder).free < DISK_RESERVE_BYTES + incoming:
                raise RecordingError("Espaço livre insuficiente para continuar a gravação.")
            self._next_disk_check = self.bytes + DISK_CHECK_BYTES

    def _active(self, identity):
        if _identity(identity) != self.id or self._closed:
            raise RecordingError("A gravação não está ativa nesta conexão.")
        if time.monotonic() - self._started > MAX_RECORDING_SECONDS:
            raise RecordingError("A gravação excedeu o limite de 24 horas.")
        owner = _owner(self.folder)
        if owner["created_at"] != self.created_at:
            raise RecordingError("A propriedade da gravação foi alterada.")
        path = _plain(self.folder / ("capture." + self.ext + ".part"), directory=False)
        current = path.stat()
        if not os.path.samestat(self._file_identity, current):
            raise RecordingError("O arquivo ativo da gravação foi substituído.")

    def chunk(self, identity: str, sequence: int, data: str) -> dict:
        self._active(identity)
        if type(sequence) is not int or sequence != self.sequence:
            raise RecordingError("Sequência da gravação inválida; envie cada bloco uma única vez.")
        if not isinstance(data, str) or not data or len(data) > ((MAX_CHUNK_BYTES + 2) // 3) * 4:
            raise RecordingError("Bloco da gravação excede o limite de 36 KiB.")
        try:
            block = base64.b64decode(data.encode("ascii"), validate=True)
        except (ValueError, UnicodeError, binascii.Error):
            raise RecordingError("Bloco da gravação não é Base64 válido.") from None
        if not block or len(block) > MAX_CHUNK_BYTES or base64.b64encode(block).decode("ascii") != data:
            raise RecordingError("Bloco da gravação inválido.")
        if self.bytes + len(block) > MAX_RECORDING_BYTES:
            raise RecordingError("A gravação excedeu o limite de 64 GiB.")
        self._disk(len(block))
        self._file.write(block)
        self._hash.update(block)
        self.bytes += len(block)
        self.sequence += 1
        return {"ok": True, "version": 1, "recording_id": self.id, "sequence": sequence, "bytes": self.bytes}

    def finish(self, identity: str, sequence: int, total: int) -> RecordingFacts:
        self._active(identity)
        if type(sequence) is not int or sequence != self.sequence or type(total) is not int or total != self.bytes:
            raise RecordingError("O total ou a sequência final da gravação não confere.")
        if not self.bytes:
            raise RecordingError("A gravação está vazia.")
        self._file.flush()
        os.fsync(self._file.fileno())
        if os.fstat(self._file.fileno()).st_size != self.bytes:
            raise RecordingError("O tamanho final da gravação não confere.")
        self._file.close()
        self._file = None
        part = self.folder / ("capture." + self.ext + ".part")
        target = self.folder / ("capture." + self.ext)
        _plain(part, directory=False)
        if target.exists():
            raise RecordingError("O destino da gravação já existe.")
        os.rename(part, target)
        self._status = "ready"
        try:
            _atomic_receipt(self.folder, self._metadata())
        except BaseException:
            # A closed file with no ready receipt is deliberately not importable.
            self._status = "interrupted"
            raise
        self._closed = True
        return get_recording(self.root.parent, self.id)

    def abort(self, identity: str) -> dict:
        if _identity(identity) != self.id or self._closed:
            raise RecordingError("A gravação não está ativa nesta conexão.")
        self.interrupt()
        return {"ok": True, "version": 1, "recording_id": self.id, "status": "interrupted", "bytes": self.bytes}

    def interrupt(self):
        if self._closed:
            return
        try:
            if self._file is not None:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                finally:
                    self._file.close()
                    self._file = None
        finally:
            self._closed = True
            self._status = "interrupted"
            # On failed cleanup retain ownership. The original recording receipt
            # still means unfinished, never ready, if this publication fails.
            _atomic_receipt(self.folder, self._metadata())
