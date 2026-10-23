"""Persistent download coordination, with private temporary-file ownership."""
from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
import copy
from dataclasses import dataclass
import hashlib
import heapq
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import sqlite3
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from .network import (Control, DownloadError, Interrupted, Transport, content_length,
                      content_range, content_disposition_filename, display_url, validate_url, validator)
from .storage import InstanceLock, Store
from .organization import FILE_CATEGORIES, filename_category
from .browser import decode_request
from .media import is_media_url, media_source_kind, resolve_media, MediaError
from .streaming import is_stream_url, resolve_stream, download_stream, owned_stream_files, combine_stream_plans
from .mux import mux_media, pending_work, retry_pending_cleanup
from .crypto_keys import AuthorizedKeys, validate_authorized_keys
from .recording import get_recording, ready_recordings, RecordingError
from .recording_media import validate_recording
from .media_container import reject_protected_mp4, inspect_remote_mp4

ACTIVE = {"connecting", "downloading", "verifying"}
TERMINAL = {"completed", "cancelled", "failed"}
CHUNK = 1024 * 1024
CHECKPOINT_BYTES = 8 * 1024 * 1024
TRANSFER_BLOCK_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BLOCKS = 128


def _queue_name(name: str) -> str:
    if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in name)):
        raise DownloadError("queue", "Informe um nome de fila entre 1 e 80 caracteres, sem caracteres de controle.")
    if name.strip().casefold() == "todas as filas":
        raise DownloadError("queue", "Escolha outro nome; Todas as filas é o filtro geral.")
    return name.strip()


def _validate_queues(queues):
    """Persisted catalog is interpreted once before the scheduler can use it."""
    if not isinstance(queues, list) or not queues:
        raise DownloadError("queue", "Catálogo de filas inválido; os dados foram preservados.")
    identities, names = set(), set()
    for queue in queues:
        if (not isinstance(queue, dict) or not isinstance(queue.get("id"), str)
                or (queue["id"] != "default" and not re.fullmatch(r"[0-9a-f]{32}", queue["id"]))
                or type(queue.get("enabled")) is not bool):
            raise DownloadError("queue", "Catálogo de filas inválido; os dados foram preservados.")
        name = _queue_name(queue.get("name"))
        if name != queue["name"] or queue["id"] in identities or name.casefold() in names:
            raise DownloadError("queue", "Catálogo de filas contém nomes ou identidades inconsistentes; dados preservados.")
        identities.add(queue["id"])
        names.add(name.casefold())
    if "default" not in identities:
        raise DownloadError("queue", "A fila Principal não está identificada no catálogo; dados preservados.")


def _category_rules(value: dict) -> dict:
    if (not isinstance(value, dict)
            or any(key not in FILE_CATEGORIES and key != "Outros" for key in value)):
        raise DownloadError("category", "As regras devem usar categorias existentes.")
    if any(not isinstance(path, str) or not path.strip() for path in value.values()):
        raise DownloadError("directory", "As regras devem conter pastas válidas.")
    try:
        return {key: str(Path(path).expanduser().resolve()) for key, path in value.items()}
    except (OSError, ValueError) as error:
        raise DownloadError("directory", "Uma pasta da categoria é inválida; confira as regras.") from error


def _part_filename(index: int, stream: int | None = None) -> str:
    """Own the bounded block namespace for creation, resume and cleanup."""
    if type(index) is not int or not 0 <= index < MAX_TRANSFER_BLOCKS or (stream is not None and (type(stream) is not int or stream not in (0, 1))):
        raise DownloadError("ownership", "Identidade de parte temporária inválida.")
    return (f"stream-{stream}-" if stream is not None else "") + f"part-{index}.bin"


@dataclass(frozen=True)
class SourceFacts:
    url: str
    headers: dict
    media: bool | str
    streams: list
    manifest: bool = False
    manifest_protocol: str | None = None
    stream_selection: str | None = None
    manifest_audio_url: str | None = None
    manifest_audio_headers: dict | None = None
    audio_only: bool = False


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "LumeDownloads"


def default_download_dir() -> Path:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(os.path.expandvars(value)).resolve()
        except (OSError, ValueError, TypeError):
            pass
    return Path.home() / "Downloads"


def safe_filename(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 180:
        raise DownloadError("filename", "Nome de arquivo vazio ou longo demais (máximo 180 caracteres).")
    if name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name) or name[-1] in " .":
        raise DownloadError("filename", "O nome contém caracteres não permitidos no Windows.")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
                                       *[f"LPT{i}" for i in range(10)]}:
        raise DownloadError("filename", "Este nome é reservado pelo Windows.")
    return name


def suggested_filename(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "download.bin"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).rstrip(" .")[:160]
    try:
        return safe_filename(name)
    except DownloadError:
        return "download.bin"


def initial_filename(url: str, identity: str, source: str | None = None) -> str:
    kind = source or media_source_kind(url)
    if kind in {"hls", "dash"}:
        return "Streaming-" + identity[:12] + ".mp4"
    if kind in {"youtube", "platform"}:
        return ("YouTube-" if kind == "youtube" else "Midia-") + identity[:12] + ".mp4"
    return suggested_filename(url)


def recording_filename(title: str, identity: str, ext: str) -> str:
    """Derive a Windows filename from a retained capture title, not a URL."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title).strip().rstrip(' .')[:150]
    if name.casefold().endswith('.' + ext):
        name = name[:-len(ext)-1].rstrip(' .')
    try:
        return safe_filename(name + '.' + ext) if name else 'Gravacao-' + identity[:12] + '.' + ext
    except DownloadError:
        return 'Gravacao-' + identity[:12] + '.' + ext


class Manager:
    def __init__(self, data_dir: Path, download_dir: Path | None = None, *,
                 transport: Transport | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.instance = InstanceLock(self.data_dir / "desktop.lock")
        try:
            self.store = Store(self.data_dir / "queue.sqlite3")
            self.lock = threading.RLock()
            loaded = self.store.load()
            # Legacy rows retain insertion order. Explicit positions precede any
            # newly discovered legacy rows; normalize only during initialization.
            loaded.sort(key=lambda task: (0, task["queue_order"])
                        if type(task.get("queue_order")) is int and task["queue_order"] >= 0 else (1, 0))
            changed = []
            for position, task in enumerate(loaded):
                if type(task.get("queue_order")) is not int or task["queue_order"] != position:
                    task["queue_order"] = position
                    changed.append(task)
            if changed:
                self.store.save_many(changed)
            self.tasks = {task["id"]: task for task in loaded}
            self._authorized_keys: dict[str, AuthorizedKeys] = {}
            self.requests = {r["id"]: r for r in self.store.pending_requests() if r["id"] not in self.tasks}
            self.options = {"max_active": 2, "speed_limit_kib": 0,
                            "default_directory": str(download_dir or default_download_dir()), "browser_auto_start": False}
            self.options.update(self.store.options())
            self.options.setdefault("queues", [{"id": "default", "name": "Principal", "enabled": True}])
            self.options.setdefault("category_directories", {})
            _validate_queues(self.options["queues"])
            # Validate saved rules before opening worker resources, preserving
            # their exact saved representation until the caller edits them.
            _category_rules(self.options["category_directories"])
            self.controls: dict[str, Control] = {}
            self.workers: dict[str, threading.Thread] = {}
            self.rates: dict[str, list[tuple[float, int]]] = {}
            self.live_progress: dict[tuple[str, int], int] = {}
            self.closing = threading.Event()
            self.closed = False
            self.recording_error = ""
            self._next_recording_scan = 0.0
            self._owns_transport = transport is None
            self.transport = transport or Transport(max_connections_per_host=8)
            self.limit_lock = threading.Lock()
            self.next_byte_time = 0.0
            for task in self.tasks.values():
                if task.get("requires_keys") and task["status"] in ACTIVE | {"queued", "scheduled"}:
                    task["status"] = "paused"
                    task["error"] = "Informe novamente as chaves autorizadas para continuar; elas não são salvas."
                    self.store.save(task)
                elif task["status"] in ACTIVE:
                    task["status"] = "paused"
                    task["error"] = "Sessão anterior interrompida. Use Retomar para conferir e continuar."
                    self.store.save(task)
            (self.data_dir / "inbox").mkdir(exist_ok=True)
            self.scheduler = threading.Thread(target=self._schedule, name="lume-queue", daemon=True)
            self.scheduler.start()
        except Exception:
            try:
                if hasattr(self, "store"):
                    self.store.close()
            finally:
                self.instance.close()
            raise

    def get_options(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.options)

    def _save_options(self, changed: dict):
        # A failed transaction must not change scheduler policy in memory.
        self.store.save_options(changed)
        self.options = changed

    def list_queues(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.options["queues"])

    def _queue(self, queue_id: str) -> dict:
        if not isinstance(queue_id, str):
            raise DownloadError("queue", "Escolha uma fila existente.")
        queue = next((row for row in self.options["queues"] if row["id"] == queue_id), None)
        if queue is None:
            raise DownloadError("queue", "A fila escolhida não existe.")
        return queue

    def create_queue(self, name: str) -> str:
        name = _queue_name(name)
        with self.lock:
            if any(row["name"].casefold() == name.casefold() for row in self.options["queues"]):
                raise DownloadError("queue", "Já existe uma fila com esse nome.")
            identity = uuid.uuid4().hex
            changed = copy.deepcopy(self.options)
            changed["queues"].append({"id": identity, "name": name, "enabled": True})
            self._save_options(changed)
            return identity

    def set_queue_enabled(self, queue_id: str, enabled: bool):
        if type(enabled) is not bool:
            raise DownloadError("queue", "O estado da fila deve ser verdadeiro ou falso.")
        with self.lock:
            self._queue(queue_id)
            changed = copy.deepcopy(self.options)
            next(row for row in changed["queues"] if row["id"] == queue_id)["enabled"] = enabled
            self._save_options(changed)

    def assign_queue(self, task_id: str, queue_id: str):
        with self.lock:
            self._queue(queue_id)
            task = self.tasks[task_id]
            if task_id in self.workers or task["status"] in ACTIVE or task.get("publication"):
                raise DownloadError("busy", "Pause e aguarde a liberação antes de mudar a fila.")
            changed = {**task, "queue_id": queue_id}
            self.store.save(changed)
            task.update(changed)

    def set_category_directory(self, category: str, directory: str | Path | None):
        if not isinstance(category, str) or (category not in FILE_CATEGORIES and category != "Outros"):
            raise DownloadError("category", "Escolha uma categoria existente.")
        if directory is not None and (not isinstance(directory, (str, Path)) or not str(directory).strip()):
            raise DownloadError("directory", "Informe uma pasta válida.")
        resolved = str(Path(directory).expanduser().resolve()) if directory is not None else None
        with self.lock:
            changed = copy.deepcopy(self.options)
            if resolved is None:
                changed["category_directories"].pop(category, None)
            else:
                changed["category_directories"][category] = resolved
            self._save_options(changed)

    def suggest_directory(self, filename: str) -> str | None:
        if not isinstance(filename, str):
            raise DownloadError("filename", "Informe o nome do arquivo para consultar a pasta.")
        with self.lock:
            return self.options["category_directories"].get(filename_category(filename))

    def set_options(self, max_active: int = 2, speed_limit_kib: int = 0, **options):
        if type(max_active) is not int or not 1 <= max_active <= 3:
            raise DownloadError("options", "Escolha de 1 a 3 downloads simultâneos.")
        if type(speed_limit_kib) is not int or not 0 <= speed_limit_kib <= 1024 * 1024:
            raise DownloadError("options", "Informe um limite de velocidade válido; zero significa sem limite.")
        if "browser_auto_start" in options and type(options["browser_auto_start"]) is not bool:
            raise DownloadError("options", "A opção de início automático deve ser verdadeira ou falsa.")
        directories = options.get("category_directories")
        if directories is not None:
            directories = _category_rules(directories)
        with self.lock:
            changed = copy.deepcopy(self.options)
            changed.update(max_active=max_active, speed_limit_kib=speed_limit_kib)
            if options.get("default_directory"):
                changed["default_directory"] = str(Path(options["default_directory"]).resolve())
            if "browser_auto_start" in options:
                changed["browser_auto_start"] = options["browser_auto_start"]
            if directories is not None:
                changed["category_directories"] = directories
            self._save_options(changed)

    def list_requests(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(list(self.requests.values()))

    def save_recording(self, identity: str, directory: str | Path, filename: str | None = None) -> str:
        """Explicit local CLI approval for an existing ready capture identity."""
        try:
            get_recording(self.data_dir, identity)
        except (RecordingError, OSError) as error:
            raise DownloadError("recording", str(error)) from error
        self._consume_recordings()
        with self.lock:
            receipt = self.store.request(identity)
            if receipt and receipt.get("status") == "cancelled":
                raise DownloadError("recording", "O pedido desta gravação foi cancelado; original preservado.")
            if identity not in self.tasks and identity not in self.requests:
                raise DownloadError("recording", self.recording_error or "Gravação sem pedido disponível; original preservado.")
            return self.confirm_request(identity, directory, filename)

    def dismiss_request(self, request_id: str):
        with self.lock:
            request = self.requests[request_id]
            # Reconcile an uncertain prior task commit before calling this only
            # a pending request. A user's cancellation must survive restart.
            admitted = self.tasks.get(request_id) or self.store.task(request_id)
            if admitted is not None:
                self.tasks[request_id] = admitted
                self.cancel(request_id)
            self.store.save_request({**request, "status": "cancelled"})
            del self.requests[request_id]

    def confirm_request(self, request_id: str, directory: str | Path, filename: str | None = None,
                        parts: int = 4, *, start: bool = True, duplicate_policy: str = "copy",
                        media_selection: str | None = None, queue_id: str = "default",
                        authorized_keys: AuthorizedKeys | None = None) -> str:
        validate_authorized_keys(authorized_keys)
        with self.lock:
            if request_id in self.tasks:
                return request_id
            persisted = self.store.task(request_id)
            if persisted is not None:
                self.tasks[request_id] = persisted
                self.requests.pop(request_id, None)
                return request_id
            request = self.requests[request_id]
            try:
                if request.get("recording"):
                    if authorized_keys or media_selection:
                        raise DownloadError("recording", "A gravação mantém o formato capturado pelo navegador.")
                    task_id = self._add_recording(request, directory, filename, start, duplicate_policy, queue_id)
                else:
                    task_id = self.add(request["url"], directory, filename, parts,
                                   _request_id=request_id, start=start, duplicate_policy=duplicate_policy,
                                   media_selection=media_selection, _expected_media=request.get("media", False),
                                   _media_source=request.get("source"), queue_id=queue_id,
                                   authorized_keys=authorized_keys)
            except (DownloadError, OSError, ValueError) as error:
                changed = {**request, "error": str(error)}
                self.store.request_error(changed)
                self.requests[request_id] = changed
                raise
            # Admission commits the queue item and terminal receipt together.
            del self.requests[request_id]
            return task_id

    def _reserved_names(self, directory: Path, exclude: str | None = None) -> set[str]:
        return {task["filename"].casefold() for task in self.tasks.values()
                if task["id"] != exclude and task["directory"].casefold() == str(directory).casefold()
                and task["status"] not in {"cancelled", "failed"}}

    def preview_destination(self, directory: str | Path, filename: str, duplicate_policy: str = "copy") -> dict:
        """Advisory display facts; confirmation and publication recheck the path."""
        if duplicate_policy not in ("copy", "error"):
            raise DownloadError("options", "Política de cópia inválida.")
        name = safe_filename(filename)
        directory = Path(directory).expanduser().resolve()
        with self.lock:
            path = directory / name
            exists = path.exists() or path.is_symlink()
            reserved = name.casefold() in self._reserved_names(directory)
            proposed = self._destination_name(directory, name, "copy") if duplicate_policy == "copy" else name
            return {"filename": proposed, "conflict": exists or reserved,
                    "existing_path": str(path) if exists else None, "reserved": reserved}

    def _destination_name(self, directory: Path, name: str, policy: str, *, exclude: str | None = None) -> str:
        """Choose once at the destination owner, keeping publication no-clobber."""
        reserved = self._reserved_names(directory, exclude)
        def exists(candidate):
            path = directory / candidate
            return candidate.casefold() in reserved or path.exists() or path.is_symlink()
        if not exists(name):
            return name
        if policy == "error":
            raise DownloadError("exists", "Já existe um arquivo ou download com esse nome. Escolha outro nome ou salvar nova cópia.")
        original = Path(name)
        # Bound filesystem work on directories containing thousands of copies.
        deadline = time.monotonic() + .5
        index = 1
        while True:
            suffix = f" ({index}){original.suffix}"
            candidate = safe_filename(original.stem[:180-len(suffix)] + suffix)
            if not exists(candidate):
                return candidate
            if time.monotonic() > deadline:
                raise DownloadError("exists", "Há muitas cópias com esse nome. Escolha outro nome ou pasta.")
            index += 1

    def add(self, url: str, directory: str | Path, filename: str | None = None, parts: int = 4,
            sha256: str | None = None, scheduled_at: float | None = None, *, _request_id: str | None = None,
            start: bool = True, duplicate_policy: str = "error", media_selection: str | None = None,
            _expected_media: bool = False, _media_source: str | None = None, queue_id: str = "default",
            authorized_keys: AuthorizedKeys | None = None) -> str:
        validate_authorized_keys(authorized_keys)
        source_url = url
        if not isinstance(url, str):
            raise DownloadError("url", "Informe uma URL HTTP ou HTTPS válida.")
        url = validate_url(url.strip())
        media = is_media_url(url)
        if _media_source not in (None, "hls", "dash") or type(_expected_media) is not bool:
            raise DownloadError("media", "A indicação de origem da mídia é inválida.")
        stream = is_stream_url(url) or _media_source is not None
        if type(start) is not bool or not isinstance(duplicate_policy, str) or duplicate_policy not in {"error", "copy"}:
            raise DownloadError("options", "Escolha uma ação e uma política de cópia válidas.")
        if (media_selection is not None and (not (media or stream) or not isinstance(media_selection, str)
                or not media_selection or len(media_selection) > 256)):
            raise DownloadError("media", "A seleção de formato não é válida para este endereço.")
        filename_selected = bool(filename)
        filename = safe_filename(filename) if filename else initial_filename(url, _request_id or uuid.uuid4().hex, _media_source)
        if type(parts) is not int or not 1 <= parts <= 8:
            raise DownloadError("parts", "Escolha de 1 a 8 partes por arquivo.")
        if sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise DownloadError("sha256", "SHA-256 deve conter exatamente 64 caracteres hexadecimais.")
        if scheduled_at is not None and (not isinstance(scheduled_at, (int, float)) or
                                         not math.isfinite(scheduled_at)):
            raise DownloadError("schedule", "Data de agendamento inválida.")
        with self.lock:
            self._queue(queue_id)
            if self.closed or self.closing.is_set():
                raise DownloadError("closed", "O aplicativo está encerrando.")
            if _request_id is not None:
                if not re.fullmatch(r"[0-9a-f]{32}", _request_id):
                    raise DownloadError("request", "Identidade de solicitação inválida.")
                if _request_id in self.tasks:
                    return _request_id
            directory = Path(directory).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            filename = self._destination_name(directory, filename, duplicate_policy)
            task_id = _request_id or uuid.uuid4().hex
            task = {"id": task_id, "source_url": source_url, "url": url, "display_url": display_url(url), "directory": str(directory),
                    "filename": filename, "parts": parts, "sha256_expected": sha256.lower() if sha256 else None,
                    "sha256": None, "scheduled_at": scheduled_at, "created_at": time.time(),
                    "status": "scheduled" if scheduled_at and scheduled_at > time.time() else ("queued" if start else "held"),
                    "total": None, "downloaded": 0, "error": "", "parts_state": [], "identity": None,
                    "mode": None, "kind": ("stream" if stream else
                         ("youtube" if media_source_kind(url) == "youtube" else "media") if media else "file"),
                    "filename_selected": filename_selected,
                    "media_selection": media_selection, "duplicate_policy": duplicate_policy,
                    "expected_media": _expected_media, "media_source": _media_source,
                    "queue_id": queue_id,
                    "queue_order": max((row["queue_order"] for row in self.tasks.values()), default=-1) + 1}
            if authorized_keys:
                task["requires_keys"] = True
            return self._admit_task(task, authorized_keys)

    def _admit_task(self, task: dict, authorized_keys: AuthorizedKeys | None = None) -> str:
        """One owner for work-directory creation and uncertain admission commits.

        Both network downloads and completed local recordings enter here while
        holding the manager lock; the scheduler cannot start before admission.
        """
        task_id = task["id"]
        work = self._work(task)
        if work.exists() and task_id in self.requests:
            self._owned(task)
            if {p.name for p in work.iterdir()} != {"owner.json"}:
                raise DownloadError("ownership", "Pedido anterior possui temporários sem metadados; preservados.")
        else:
            work.mkdir(exist_ok=False)
        (work / "owner.json").write_text(json.dumps({"app": "lume-downloads", "id": task_id}), encoding="utf-8")
        try:
            self.store.admit(task, self.requests.get(task_id))
        except Exception:
            # A commit exception does not prove failure. Read back the named
            # identity; preserve the owned marker if read-back is unavailable.
            persisted = self.store.task(task_id)
            if persisted is None:
                raise
            task = persisted
        # The scheduler shares this lock. Install ephemeral keys before the
        # admitted row becomes eligible to start; SQLite never receives them.
        if authorized_keys:
            self._authorized_keys[task_id] = authorized_keys
        self.tasks[task_id] = task
        return task_id

    def _add_recording(self, request: dict, directory, filename, start, duplicate_policy, queue_id) -> str:
        facts = get_recording(self.data_dir, request["id"])
        proof = request["recording"]
        if proof != {"id": facts.id, "sha256": facts.sha256, "bytes": facts.bytes, "ext": facts.ext}:
            raise DownloadError("recording", "A identidade da gravação mudou; os arquivos foram preservados.")
        if type(start) is not bool or duplicate_policy not in {"copy", "error"}:
            raise DownloadError("options", "Escolha uma ação e uma política de cópia válidas.")
        if self.closed or self.closing.is_set():
            raise DownloadError("closed", "O aplicativo está encerrando.")
        self._queue(queue_id)
        name = safe_filename(filename or request["filename"])
        if Path(name).suffix.lower() != "." + facts.ext:
            raise DownloadError("filename", "Mantenha a extensão ." + facts.ext + " da gravação original.")
        directory = Path(directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        name = self._destination_name(directory, name, duplicate_policy)
        task = {"id": facts.id, "source_url": request["url"], "url": request["url"],
                "display_url": "Gravação da reprodução", "directory": str(directory),
                "filename": name, "parts": 1, "sha256_expected": facts.sha256, "sha256": None,
                "scheduled_at": None, "created_at": time.time(), "status": "queued" if start else "held",
                "total": facts.bytes, "downloaded": 0, "error": "", "parts_state": [], "identity": None,
                "mode": "local", "kind": "recording", "recording": proof,
                "filename_selected": True, "media_selection": None, "duplicate_policy": duplicate_policy,
                "expected_media": False, "media_source": None, "queue_id": queue_id,
                "queue_order": max((row["queue_order"] for row in self.tasks.values()), default=-1) + 1}
        return self._admit_task(task)

    def list_tasks(self) -> list[dict]:
        with self.lock:
            now = time.monotonic()
            output = []
            queues = {queue["id"]: queue for queue in self.options["queues"]}
            for task in self._ordered_tasks():
                row = copy.deepcopy(task)
                row["queue_id"] = task.get("queue_id", "default")
                queue = queues.get(row["queue_id"]) if isinstance(row["queue_id"], str) else None
                row["queue_enabled"] = queue["enabled"] if queue else False
                row["queue_known"] = queue is not None
                row["cleanup_pending"] = pending_work(self._work(task))
                row["downloaded"] += sum(value for (identity, _), value in self.live_progress.items() if identity == task["id"])
                history = self.rates.get(task["id"], [])
                history[:] = [x for x in history if now - x[0] < 3]
                row["speed"] = ((history[-1][1] - history[0][1]) / max(.1, history[-1][0] - history[0][0])
                                if len(history) > 1 and task["status"] == "downloading" else 0)
                row["connections"] = len(self.controls.get(task["id"], Control()).connections)
                output.append(row)
            return output

    def _ordered_tasks(self) -> list[dict]:
        return sorted(self.tasks.values(), key=lambda task: task["queue_order"])

    def move(self, task_id: str, offset: int):
        """Move one queue position atomically without changing execution state."""
        if type(offset) is not int or offset not in (-1, 1):
            raise DownloadError("options", "Escolha subir ou descer uma posição na fila.")
        with self.lock:
            self.tasks[task_id]  # Preserve the same missing-task contract as other actions.
            queue_id = self.tasks[task_id].get("queue_id", "default")
            rows = [row for row in self._ordered_tasks() if row.get("queue_id", "default") == queue_id]
            current = next(index for index, task in enumerate(rows) if task["id"] == task_id)
            target = current + offset
            if not 0 <= target < len(rows):
                return
            first, second = rows[current], rows[target]
            first_order, second_order = first["queue_order"], second["queue_order"]
            self.store.save_many([{**first, "queue_order": second_order}, {**second, "queue_order": first_order}])
            first["queue_order"], second["queue_order"] = second_order, first_order

    def replace_source(self, task_id: str, url: str):
        """Discard owned partials for a new source, requiring explicit resume."""
        if not isinstance(url, str):
            raise DownloadError("url", "Informe uma URL HTTP ou HTTPS válida.")
        source_url = url
        normalized = validate_url(url.strip())
        with self.lock:
            task = self.tasks[task_id]
            if task.get("kind") == "recording":
                raise DownloadError("recording", "Uma gravação não possui endereço de download substituível.")
            if (task_id in self.workers or task["status"] not in {"held", "paused", "failed"}
                    or task.get("publication") is not None):
                raise DownloadError("busy", "Pause e aguarde a transferência; resultados em publicação não podem trocar de endereço.")
            filename = task["filename"] if task.get("filename_selected") else initial_filename(normalized, task_id)
            filename = self._destination_name(Path(task["directory"]), filename,
                                               task.get("duplicate_policy", "error"), exclude=task_id)
            self._clean(task, keep_owner=True)
            media = is_media_url(normalized)
            changed = {**task, "source_url": source_url, "url": normalized, "display_url": display_url(normalized),
                       "filename": filename, "kind": "stream" if is_stream_url(normalized) else
                       ("youtube" if media_source_kind(normalized) == "youtube" else "media") if media else "file",
                       "parts_state": [], "streams_state": [], "streaming": None, "media": None,
                       "media_probe": None, "media_selection": None, "media_source": None, "expected_media": False,
                       "identity": None, "mode": None, "total": None, "downloaded": 0, "sha256": None,
                       "publication": None, "timings": {}, "status": "held", "error": "", "scheduled_at": None}
            self.store.save(changed)
            task.update(changed)
            self.rates.pop(task_id, None)

    def wait(self, task_id: str, timeout: float = 30) -> dict:
        """Wait for a settled state, including completion of worker cleanup."""
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            with self.lock:
                task = self.tasks[task_id]
                if task["status"] not in ACTIVE | {"queued", "scheduled"} and task_id not in self.workers:
                    self._settle_media(task)
                    return next(t for t in self.list_tasks() if t["id"] == task_id)
            time.sleep(.02)
        raise TimeoutError("A transferência ainda não terminou de liberar seus recursos.")

    def pause(self, task_id: str):
        self._interrupt(task_id, "paused")

    def cancel(self, task_id: str):
        # Parts remain owned until restart/remove, so a failed cleanup never loses their owner.
        self._interrupt(task_id, "cancelled")

    def _interrupt(self, task_id: str, state: str):
        with self.lock:
            task = self.tasks[task_id]
            if task["status"] == "completed":
                return
            self._change(task, status=state, error="")
            control = self.controls.get(task_id)
        if control:
            control.interrupt()

    def resume(self, task_id: str):
        with self.lock:
            if task_id in self.workers:
                raise DownloadError("busy", "Aguarde a transferência liberar seus recursos.")
            task = self.tasks[task_id]
            if task["status"] in {"completed", "queued", "scheduled"}:
                return
            self._require_authorized_keys(task)
            self._settle_media(task)
            self._change(task, status="queued", error="", scheduled_at=None)

    def restart(self, task_id: str):
        with self.lock:
            if task_id in self.workers:
                raise DownloadError("busy", "Pause e aguarde a liberação antes de reiniciar.")
            task = self.tasks[task_id]
            self._settle_media(task)
            if task["status"] == "completed":
                raise DownloadError("completed", "O arquivo já foi concluído; adicione outro destino.")
            self._require_authorized_keys(task)
            self._clean(task, keep_owner=True)
            self._change(task, parts_state=[], downloaded=0, total=None, identity=None, mode=None,
                         sha256=None, publication=None, streams_state=[], streaming=None,
                         status="queued", error="", scheduled_at=None)

    def remove(self, task_id: str):
        with self.lock:
            if task_id in self.workers:
                raise DownloadError("busy", "Pause e aguarde a liberação antes de remover.")
            task = self.tasks[task_id]
            self._settle_media(task)
            if self._work(task).exists():
                self._clean(task, keep_owner=False)
            self.store.remove(task_id)
            del self.tasks[task_id]
            self._authorized_keys.pop(task_id, None)
            self.rates.pop(task_id, None)

    def needs_authorized_keys(self, task_id: str) -> bool:
        with self.lock:
            task = self.tasks[task_id]
            return bool(task.get("requires_keys") and not self._authorized_keys.get(task_id))

    def _require_authorized_keys(self, task: dict) -> None:
        if task.get("requires_keys") and not self._authorized_keys.get(task["id"]):
            raise DownloadError("authorized_keys_required", "Informe novamente as chaves autorizadas para continuar; elas não são salvas.")

    def set_authorized_keys(self, task_id: str, keys: AuthorizedKeys) -> None:
        validate_authorized_keys(keys)
        if not keys:
            raise DownloadError("authorized_keys_required", "Informe ao menos uma chave autorizada.")
        with self.lock:
            task = self.tasks[task_id]
            if self.closed or self.closing.is_set():
                raise DownloadError("closed", "O aplicativo está encerrando.")
            if task_id in self.workers or task["status"] in ACTIVE | {"queued", "scheduled", "completed"}:
                raise DownloadError("busy", "Pause e aguarde a transferência antes de informar chaves.")
            self._change(task, requires_keys=True)
            self._authorized_keys[task_id] = keys

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closing.set()
            controls = list(self.controls.values())
            for task_id in self.workers:
                task = self.tasks[task_id]
                if task["status"] in ACTIVE:
                    self._change(task, status="paused", error="")
        for control in controls:
            control.interrupt()
        self.scheduler.join()
        while True:
            with self.lock:
                workers = list(self.workers.values())
            if not workers:
                break
            for worker in workers:
                worker.join()
        close_transport = getattr(self.transport, "close", None)
        if self._owns_transport and close_transport:
            close_transport()
        with self.lock:
            self._authorized_keys.clear()
            for task in self.tasks.values():
                self._settle_media(task)
            self.store.close()
            self.instance.close()
            self.closed = True

    def _change(self, task: dict, **fields):
        task.update(fields)
        self._save(task)

    def _save(self, task: dict):
        # Stream checkpoints belong to their queue item, never to separate SQLite rows.
        if "stream_index" in task:
            owner = self.tasks[task["id"]]
            streams = owner["streams_state"]
            owner["downloaded"] = sum(stream["downloaded"] for stream in streams)
            owner["total"] = (sum(stream["total"] for stream in streams)
                              if all(stream["total"] is not None for stream in streams) else None)
            self.store.save(owner)
        else:
            self.store.save(task)

    def _timing(self, task: dict, stage: str, seconds: float):
        """Persist stage totals only; never turn per-chunk telemetry into I/O."""
        with self.lock:
            owner = self.tasks[task["id"]] if "stream_index" in task else task
            timings = dict(owner.get("timings") or {})
            timings[stage] = timings.get(stage, 0) + seconds
            self._change(owner, timings=timings)

    def _part_path(self, task: dict, index: int) -> Path:
        return self._owned(task) / _part_filename(index, task.get("stream_index"))

    @contextmanager
    def _open_part(self, task: dict, index: int):
        """Prove a regular exclusive file descriptor before any write/truncate.

        Open without truncation, then bind path and descriptor identities. This
        is shared by network resume, append and recording-copy publication.
        """
        work = self._owned(task)
        work_identity = work.lstat()
        path = work / _part_filename(index, task.get("stream_index"))
        try:
            before = path.lstat()
        except FileNotFoundError:
            before = None
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if before is None:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            opened, current = os.fstat(descriptor), path.lstat()
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                    getattr(current, "st_file_attributes", 0) & 0x400 or
                    not os.path.samestat(opened, current) or
                    before is not None and not os.path.samestat(before, opened) or
                    not os.path.samestat(work_identity, self._owned(task).lstat())):
                raise DownloadError("ownership", "A identidade da parte temporária mudou; nenhuma escrita foi realizada.")
            file = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
        with file:
            yield file

    def _work(self, task: dict) -> Path:
        return Path(task["directory"]) / (".lume-" + task["id"])

    def _settle_media(self, task: dict):
        work = self._work(task)
        if pending_work(work):
            try:
                retry_pending_cleanup(work)
            except MediaError as error:
                self._change(task, error=str(error))
                raise DownloadError("busy", "A limpeza da ferramenta de mídia ainda está pendente. Tente novamente.") from error

    def _owned(self, task: dict) -> Path:
        work = self._work(task)
        if (work.is_symlink() or (hasattr(work, "is_junction") and work.is_junction()) or
                work.resolve().parent != Path(task["directory"]).resolve()):
            raise DownloadError("ownership", "A identidade da pasta temporária mudou; arquivos preservados.")
        try:
            marker = work / "owner.json"
            info = work.stat(follow_symlinks=False)
            identity = {"device": info.st_dev, "inode": info.st_ino,
                        "birth_ns": getattr(info, "st_birthtime_ns", None)}
            if not marker.exists() and not marker.is_symlink() and task.get("cleanup_owner") == identity:
                return work
            if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != {"app": "lume-downloads", "id": task["id"]}:
                raise ValueError()
        except (OSError, ValueError):
            raise DownloadError("ownership", "Não foi possível comprovar a posse dos temporários.") from None
        return work

    def _clean(self, task: dict, *, keep_owner: bool):
        self._settle_media(task)
        work = self._owned(task)
        allowed = {"owner.json", "assembled.tmp"}
        allowed.update(_part_filename(index, stream) for stream in (None, 0, 1) for index in range(MAX_TRANSFER_BLOCKS))
        allowed.update({"stream-0.tmp", "stream-1.tmp"})
        stream_files = owned_stream_files(work)
        allowed.update(path.name for path in stream_files)
        children = list(work.iterdir())
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in children):
            raise DownloadError("ownership", "Temporários contêm arquivos externos; limpeza recusada.")
        # Namespace proof is last in its owner's ordered list, so failed
        # deletion retains enough ownership to retry incomplete cleanup.
        for path in [p for p in children if p not in stream_files] + list(stream_files):
            if path.name != "owner.json":
                path.unlink()
        if not keep_owner:
            info = work.stat(follow_symlinks=False)
            with self.lock:
                self._change(task, cleanup_owner={"device": info.st_dev, "inode": info.st_ino,
                                                 "birth_ns": getattr(info, "st_birthtime_ns", None)})
            (work / "owner.json").unlink(missing_ok=True)
            work.rmdir()

    def _schedule(self):
        while not self.closing.wait(.15):
            self._consume_inbox()
            if time.monotonic() >= self._next_recording_scan:
                self._consume_recordings()
                self._next_recording_scan = time.monotonic() + 3
            with self.lock:
                enabled_queues = {queue["id"] for queue in self.options["queues"] if queue["enabled"]}
                for task in self._ordered_tasks():
                    if task["status"] == "scheduled" and (task["scheduled_at"] or 0) <= time.time():
                        self._change(task, status="queued")
                    queue_id = task.get("queue_id", "default")
                    enabled = isinstance(queue_id, str) and queue_id in enabled_queues
                    if enabled and task["status"] == "queued" and task["id"] not in self.workers and len(self.workers) < self.options["max_active"]:
                        if task.get("requires_keys") and not self._authorized_keys.get(task["id"]):
                            self._change(task, status="paused", error="Informe novamente as chaves autorizadas para continuar; elas não são salvas.")
                            continue
                        task_id = task["id"]
                        control = Control()
                        worker = threading.Thread(target=self._run, args=(task, control), name="lume-" + task_id[:8], daemon=True)
                        self.controls[task_id] = control
                        self.workers[task_id] = worker
                        self._change(task, status="connecting", error="")
                        self.rates[task_id] = [(time.monotonic(), task["downloaded"])]
                        worker.start()

    def _consume_inbox(self):
        inbox = self.data_dir / "inbox"
        # A durable request ID is also the task ID, so a crash after SQLite commit is reconciled.
        for path in [*inbox.glob("*.processing"), *inbox.glob("*.json")][:32]:
            if path.is_symlink():
                continue
            try:
                request_uuid = uuid.UUID(path.stem)
                if path.stem not in (str(request_uuid), request_uuid.hex):
                    continue
                request_id = request_uuid.hex
            except ValueError:
                continue
            claimed = path.with_suffix(".processing")
            try:
                if path != claimed:
                    path.rename(claimed)
                with self.lock:
                    known = self.store.request(request_id)
                    if request_id in self.tasks or known:
                        if request_id not in self.tasks and known and known.get("status") == "awaiting_confirmation":
                            self.requests[request_id] = known
                        # Receipt is already durable, even when a prior unlink failed.
                        claimed.unlink()
                        continue
                    message, error = {}, ""
                    try:
                        if claimed.stat().st_size > 65536:
                            raise ValueError("Mensagem de navegador muito grande.")
                        message = decode_request(claimed.read_bytes())
                    except (ValueError, UnicodeError, KeyError, TypeError, RecursionError) as failure:
                        error = str(failure)
                    url = message.get("url", "")
                    request = {"id": request_id, "url": url, "display_url": display_url(url) if url else "Pedido inválido",
                               "filename": initial_filename(url, request_id, message.get("source")) if url else "Pedido inválido",
                               "directory": self.options["default_directory"], "parts": 4,
                               "title": message.get("title", ""), "media": message.get("media", False),
                               "status": "awaiting_confirmation", "error": error, "created_at": time.time()}
                    if "source" in message:
                        request["source"] = message["source"]
                    self.store.save_request(request)
                    self.requests[request_id] = request
                    if error:
                        claimed.rename(claimed.with_suffix(".rejected-" + uuid.uuid4().hex))
                    else:
                        claimed.unlink()
                    if self.options["browser_auto_start"] and not error:
                        try:
                            self.confirm_request(request_id, request["directory"], duplicate_policy="copy")
                        except (OSError, ValueError, DownloadError):
                            pass  # The durable, visible request retains the failure reason.
            except (OSError, ValueError, KeyError, TypeError, RecursionError, DownloadError, sqlite3.Error):
                # Keep the owned claim for retry if durable persistence/cleanup failed.
                continue

    def _consume_recordings(self):
        try:
            facts_list = ready_recordings(self.data_dir)
            with self.lock:
                for facts in facts_list:
                    known = self.store.request(facts.id)
                    if facts.id in self.tasks or known:
                        if facts.id not in self.tasks and known and known.get("status") == "awaiting_confirmation":
                            self.requests[facts.id] = known
                        continue
                    request = {"id": facts.id, "url": "recording:" + facts.id,
                               "display_url": "Gravação da reprodução", "title": facts.title,
                               "filename": recording_filename(facts.title, facts.id, facts.ext),
                               "directory": self.options["default_directory"], "parts": 1,
                               "media": False, "created_at": facts.created_at,
                               "status": "awaiting_confirmation", "error": "",
                               "recording": {"id": facts.id, "sha256": facts.sha256,
                                             "bytes": facts.bytes, "ext": facts.ext}}
                    self.store.save_request(request)
                    self.requests[facts.id] = request
                self.recording_error = ""
        except (RecordingError, OSError, ValueError, sqlite3.Error) as error:
            # Preserve the vault and expose the reason; a scan failure must not
            # terminate network scheduling or imply that no recordings exist.
            self.recording_error = "Gravações: " + str(error)

    @staticmethod
    def _check_media_response(response, expected=True):
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        prefix = ("video/", "audio/") if expected == "media" else (
            "audio/" if isinstance(expected, str) and expected.startswith("audio/") else "video/")
        if not content_type.startswith(prefix):
            raise DownloadError("media_type", "O servidor não retornou a mídia de vídeo/áudio esperada. Nenhuma página HTML foi salva como conteúdo.")

    def _prepare_source(self, task: dict, control: Control) -> SourceFacts:
        # Every attempt resolves a fresh stream. The caller's original URL is never replaced.
        if is_stream_url(task["url"]) or task.get("media_source") in ("hls", "dash"):
            return SourceFacts(task["url"], {}, "media", [], manifest=True,
                               manifest_protocol=task.get("media_source"), stream_selection=task.get("media_selection"))
        media = task.get("kind") in {"youtube", "media"} or is_media_url(task["url"])
        if not media:
            return SourceFacts(task["url"], {}, "media" if task.get("expected_media") else False, [])
        try:
            selected = task.get("media_selection")
            resolved = resolve_media(task["url"], control.stop, **({"selection": selected} if selected else {}))
        except MediaError as error:
            control.check()
            raise DownloadError("media", str(error)) from None
        control.check()
        with self.lock:
            filename = task["filename"] if task.get("filename_selected") else safe_filename(resolved["filename"])
            filename = self._destination_name(Path(task["directory"]), filename,
                                              task.get("duplicate_policy", "error"), exclude=task["id"])
            self._change(task, kind="youtube" if media_source_kind(task["url"]) == "youtube" else "media", filename=filename,
                         media={key: resolved[key] for key in ("video_id", "title", "format_id", "expected_size", "mimetype")}
                         | {"ext": resolved.get("ext", "mp4"), "audio_only": resolved.get("audio_only", False)})
        streams = resolved.get("streams", [])
        return SourceFacts(resolved.get("manifest_url") or resolved.get("stream_url", ""),
                           resolved.get("headers", {}), resolved["mimetype"], streams,
                           manifest=bool(resolved.get("manifest_url")), manifest_protocol=resolved.get("manifest_protocol"),
                           manifest_audio_url=resolved.get("manifest_audio_url"),
                           manifest_audio_headers=resolved.get("manifest_audio_headers"), audio_only=resolved.get("audio_only", False))

    def _download_manifest(self, task: dict, control: Control, source: SourceFacts):
        if task.get("parts_state") or task.get("streams_state"):
            raise DownloadError("media_changed", "O endereço passou a usar streaming. Use Reiniciar para não misturar dados.")
        work = self._owned(task)
        resolved_at = time.monotonic()
        plan = resolve_stream(source.url, control, selection=source.stream_selection,
                              transport=self.transport, headers=source.headers, protocol=source.manifest_protocol,
                              audio_only=True if source.audio_only else None)
        if source.manifest_audio_url:
            audio = resolve_stream(source.manifest_audio_url, control, transport=self.transport,
                                   headers=source.manifest_audio_headers or source.headers,
                                   protocol=source.manifest_protocol, audio_only=True)
            plan = combine_stream_plans(plan, audio)
        self._timing(task, "manifest_resolve_s", time.monotonic() - resolved_at)
        with self.lock:
            control.check()
            filename = (safe_filename(plan.filename) if not task.get("filename_selected") and task.get("kind") == "stream"
                        else task["filename"])
            filename = self._destination_name(Path(task["directory"]), filename,
                                               task.get("duplicate_policy", "error"), exclude=task["id"])
            facts = dict(task.get("media") or {})
            facts.update(video_id=facts.get("video_id", plan.fingerprint[:16]), title=facts.get("title", plan.title),
                         format_id=facts.get("format_id", plan.selection), expected_size=None,
                         mimetype="audio/mp4" if plan.audio_only else "video/mp4", ext=plan.ext, audio_only=plan.audio_only)
            self._change(task, filename=filename, media=facts, status="downloading", total=None, downloaded=0,
                         streaming={"protocol":plan.protocol,"selection":plan.selection,"fingerprint":plan.fingerprint})

        def progress(current):
            # The segment owner persists durable receipts. Queue progress is a
            # display snapshot and must never become a second resume authority.
            with self.lock:
                task["downloaded"] = current
                history = self.rates.setdefault(task["id"], [])
                history.append((time.monotonic(), current))
                del history[:-128]

        with self.lock:
            self._require_authorized_keys(task)
            keys = self._authorized_keys.get(task["id"])
        key_options = {"authorized_keys": keys} if keys else {}
        result = download_stream(plan, work, control, transport=self.transport,
                                 on_progress=progress, limit=self._limit, headers=source.headers,
                                 concurrency=task["parts"], **key_options)
        control.check()
        output = Path(result["path"])
        if output.parent != work or output not in owned_stream_files(work):
            raise DownloadError("ownership", "O resultado de streaming não pertence à tarefa; preservado.")
        with self.lock:
            control.check()
            self._change(task, status="verifying", media_probe=result["probe"],
                         media_preflight=result.get('preflight',[]),
                         streaming={**task["streaming"], "output":output.name},
                         timings={**(task.get("timings") or {}), **result.get("timings", {})})
        self._publish_stream_output(task, control)

    def _publish_stream_output(self, task: dict, control: Control):
        work = self._owned(task)
        name = task.get("streaming", {}).get("output")
        output = next((path for path in owned_stream_files(work) if path.name == name), None)
        if output is None:
            raise DownloadError("publication", "A saída verificada de streaming não está disponível. Use Reiniciar; temporários preservados.")
        digest = hashlib.sha256()
        hashed_at = time.monotonic()
        with output.open("r+b") as file:
            before = os.fstat(file.fileno())
            while chunk := file.read(1024 * 1024):
                control.check()
                digest.update(chunk)
            after = os.fstat(file.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise DownloadError("changed", "A saída de streaming mudou durante a verificação; preservada.")
            os.fsync(file.fileno())
        if task["sha256_expected"] and digest.hexdigest() != task["sha256_expected"]:
            raise DownloadError("integrity", "SHA-256 divergente. Arquivo não publicado; temporários preservados.")
        control.check()
        self._timing(task, "hash_s", time.monotonic() - hashed_at)
        self._publish(task, control, output, before.st_size, digest.hexdigest())

    def _probe(self, task: dict, control: Control, *, url=None, headers=None, media=False) -> dict:
        with self.transport.open(url or task["url"], {**(headers or {}), "Range": "bytes=0-0"}, control) as owned:
            response = owned.response
            if media and response.status in (200, 206):
                self._check_media_response(response, media)
            if response.status == 206:
                first, last, total = content_range(response.getheader("Content-Range"))
                if (first, last) != (0, 0) or content_length(response) not in (None, 1):
                    raise DownloadError("range", "Resposta ao teste de intervalo inconsistente.")
                if len(response.read(2)) != 1:
                    raise DownloadError("range", "Corpo do teste de intervalo inconsistente.")
                identity = {"url": owned.url, "total": total, "validator": validator(response), "ranges": True}
            elif response.status == 200:
                identity = {"url": owned.url, "total": content_length(response),
                        "validator": validator(response), "ranges": False}
            elif response.status == 416 and response.getheader("Content-Range") == "bytes */0":
                identity = {"url": owned.url, "total": 0, "validator": validator(response), "ranges": False}
            else:
                raise DownloadError("http", f"Servidor respondeu HTTP {response.status}.")
            if (task.get("kind") == "file" and not task.get("filename_selected")
                    and not task["parts_state"] and "stream_index" not in task):
                suggested = content_disposition_filename(response.getheader("Content-Disposition"))
                if suggested:
                    # RFC 6266 names are advisory. Only their final component may
                    # become a destination, and the core owns all validation.
                    try:
                        name = safe_filename(re.split(r"[/\\]", suggested)[-1])
                    except DownloadError:
                        pass  # Invalid server suggestions retain the URL fallback.
                    else:
                        with self.lock:
                            control.check()
                            name = self._destination_name(Path(task["directory"]), name,
                                                          task.get("duplicate_policy", "error"), exclude=task["id"])
                            self._change(task, filename=name, http_filename=suggested)
            return identity

    def _plan(self, task: dict, identity: dict, control: Control):
        work = self._owned(task)
        if task["parts_state"]:
            old = task["identity"]
            if (not old or not old["validator"] or not identity["validator"] or
                any(old[key] != identity[key] for key in ("url", "total", "validator")) or not identity["ranges"]):
                raise DownloadError("changed", "O recurso mudou ou não permite retomada segura. Use Reiniciar.")
            for i, part in enumerate(task["parts_state"]):
                path = self._part_path(task, i)
                if path.is_symlink():
                    raise DownloadError("ownership", "Arquivo temporário substituído; preservado.")
                actual = path.stat().st_size if path.exists() else 0
                if actual < part["done"]:
                    raise DownloadError("partial", "Parte salva foi alterada ou removida. Use Reiniciar.")
                if actual > part["done"]:
                    with self._open_part(task, i) as file:
                        file.truncate(part["done"])
            return
        total = identity["total"]
        segmented = bool(identity["ranges"] and identity["validator"] and total and total >= 512 * 1024)
        count = min(task["parts"], max(1, (total or 0) // (256 * 1024))) if segmented else 1
        if segmented and task["parts"] > 1:
            # Keep an immutable, resumable byte map. Workers claim the largest
            # remaining block whenever free instead of owning one long quarter
            # for the entire transfer. Bound persisted metadata independently
            # of file size; existing checkpoints retain their original map.
            pending_budget = (min(task["parts"] * 2, max(1, total // (256 * 1024)))
                              if total >= TRANSFER_BLOCK_BYTES else count)
            count = min(MAX_TRANSFER_BLOCKS, max(count, pending_budget,
                                               (total + TRANSFER_BLOCK_BYTES - 1) // TRANSFER_BLOCK_BYTES))
        use_ranges = bool(identity["ranges"] and identity["validator"])
        parts = []
        for i in range(count):
            start = (total * i // count) if use_ranges and total else 0
            end = (total * (i + 1) // count - 1) if use_ranges and total else None
            parts.append({"start": start, "end": end, "done": 0})
        with self.lock:
            self._change(task, identity=identity, total=total, mode="range" if use_ranges else "single", parts_state=parts)

    def _run(self, task: dict, control: Control):
        try:
            control.check()
            work = self._owned(task)
            if task.get("publication"):
                destination = Path(task["directory"]) / task["filename"]
                if destination.exists():
                    if destination.is_symlink():
                        raise DownloadError("ownership", "O arquivo final foi substituído por um link; preservado.")
                    digest = hashlib.sha256()
                    with destination.open("rb") as published:
                        before = os.fstat(published.fileno())
                        while chunk := published.read(1024 * 1024):
                            control.check()
                            digest.update(chunk)
                        after = os.fstat(published.fileno())
                    proof = task["publication"]
                    if (before.st_size != proof["size"] or digest.hexdigest() != proof["sha256"] or
                        (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                        raise DownloadError("publication", "O arquivo final diverge do registro de publicação; preservado.")
                    self._check_media_publication(task, destination, control)
                    with self.lock:
                        control.check()
                        self._change(task, status="completed", sha256=digest.hexdigest(), total=before.st_size,
                                     downloaded=before.st_size, error="")
                    try:
                        self._clean(task, keep_owner=False)
                    except (OSError, DownloadError):
                        with self.lock:
                            self._change(task, error="Concluído; temporários preservados para nova tentativa de limpeza.")
                    return
                self._finalize(task, control)
                return
            if (Path(task["directory"]) / task["filename"]).exists():
                raise DownloadError("exists", "O destino já existe. Nenhum arquivo foi sobrescrito.")
            if task.get("kind") == "recording":
                self._save_recording(task, control)
                return
            resolved_at = time.monotonic()
            source = self._prepare_source(task, control)
            self._timing(task, "resolve_s", time.monotonic() - resolved_at)
            if (Path(task["directory"]) / task["filename"]).exists():
                raise DownloadError("exists", "O destino já existe. Nenhum arquivo foi sobrescrito.")
            if source.manifest:
                self._download_manifest(task, control, source)
                return
            with self.lock:
                if self._authorized_keys.get(task["id"]):
                    raise DownloadError("authorized_keys_unused", "Esta origem não usa o fluxo DASH com chaves KID autorizadas. Nenhum arquivo foi baixado; adicione o endereço sem chaves ou informe um manifest compatível.")
            url, headers, media, streams = source.url, source.headers, source.media, source.streams
            if len(streams) == 2:
                self._download_media_streams(task, control, streams)
                with self.lock:
                    control.check()
                    self._change(task, status="verifying")
                self._finish_media(task, control)
                return
            if task.get("streams_state"):
                raise DownloadError("media_changed", "O vídeo passou a usar um formato único. Use Reiniciar antes de continuar.")
            if streams:
                url, headers = streams[0]["stream_url"], streams[0]["headers"]
            identity = self._probe(task, control, url=url, headers=headers, media=media)
            if media:
                checked_at=time.monotonic()
                preflight=inspect_remote_mp4(identity,self.transport,control,headers)
                with self.lock:
                    control.check()
                    self._change(task,media_preflight=preflight)
                self._timing(task,'media_preflight_s',time.monotonic()-checked_at)
            if task.get("media") and task["media"]["expected_size"] is not None and identity["total"] != task["media"]["expected_size"]:
                raise DownloadError("media_size", "O tamanho do vídeo diverge do informado pelo provedor.")
            control.check()
            self._plan(task, identity, control)
            if task["total"] is not None:
                required = max(0, task["total"] - task["downloaded"]) + task["total"]
                if shutil.disk_usage(work).free < required + 1024 * 1024:
                    raise DownloadError("space", "Espaço insuficiente para partes e arquivo final.")
            with self.lock:
                control.check()
                self._change(task, status="downloading")
            try:
                transfer_at = time.monotonic()
                self._transfer(task, control, headers=headers, media=media)
                self._timing(task, "transfer_s", time.monotonic() - transfer_at)
            except DownloadError as error:
                if error.code != "range_ignored" or task["downloaded"]:
                    raise
                # A lying Range implementation is safe to retry only before any committed data.
                with self.lock:
                    if task["status"] in {"paused", "cancelled"} or self.closing.is_set():
                        raise Interrupted()
                    self._clean(task, keep_owner=True)
                    control.stop.clear()
                    self._change(task, mode="single", parts_state=[{"start": 0, "end": None, "done": 0}])
                self._transfer(task, control, headers=headers, media=media)
                self._timing(task, "transfer_s", time.monotonic() - transfer_at)
            with self.lock:
                control.check()
                self._change(task, status="verifying")
            self._finalize(task, control)
        except Exception as error:
            with self.lock:
                if task["status"] not in {"paused", "cancelled", "completed"}:
                    if isinstance(error, ssl.SSLCertVerificationError):
                        message = "Certificado TLS recusado: cadeia ou nome do servidor inválido."
                    elif isinstance(error, DownloadError):
                        message = str(error)
                    elif isinstance(error, OSError):
                        message = "Falha de rede ou disco (código " + str(error.errno or "indisponível") + "). As partes foram preservadas."
                    elif isinstance(error, (http.client.HTTPException, TimeoutError)):
                        message = "Resposta interrompida ou inválida. Use Retomar para tentar novamente."
                    else:
                        message = "Não foi possível concluir a transferência. As partes foram preservadas."
                    extra = {"requires_keys": True} if isinstance(error, DownloadError) and error.code == "stream_keys" else {}
                    self._change(task, status="failed", error=message, **extra)
        finally:
            control.interrupt()
            with self.lock:
                try:
                    self._settle_media(task)
                except DownloadError:
                    pass  # Retained process ownership blocks all reuse and cleanup operations.
                self.controls.pop(task["id"], None)
                self.workers.pop(task["id"], None)
                for key in [key for key in self.live_progress if key[0] == task["id"]]:
                    self.live_progress.pop(key, None)

    def _save_recording(self, task: dict, control: Control):
        facts = get_recording(self.data_dir, task["id"])
        if task["recording"] != {"id": facts.id, "sha256": facts.sha256, "bytes": facts.bytes, "ext": facts.ext}:
            raise DownloadError("recording", "A identidade da gravação mudou; os arquivos foram preservados.")
        work = self._owned(task)
        if shutil.disk_usage(work).free < facts.bytes * 2 + CHUNK:
            raise DownloadError("space", "Espaço insuficiente para conferir e salvar a gravação.")
        part = self._part_path(task, 0)
        # A fresh bounded copy on resume is deliberate: the retained source is
        # authoritative; no byte checkpoint is trusted without hashing it again.
        with self.lock:
            control.check()
            self._change(task, status="downloading", total=facts.bytes, downloaded=0,
                         parts_state=[{"start": 0, "end": facts.bytes - 1, "done": 0}])
        digest, count = hashlib.sha256(), 0
        expected_source = facts.path.lstat()
        with facts.path.open("rb") as source, self._open_part(task, 0) as target:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    getattr(expected_source, "st_file_attributes", 0) & 0x400 or
                    (expected_source.st_dev, expected_source.st_ino, expected_source.st_size, expected_source.st_mtime_ns) !=
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
                raise DownloadError("ownership", "A captura foi substituída durante a abertura; dados preservados.")
            target.truncate(0)
            while chunk := source.read(CHUNK):
                control.check()
                count += len(chunk)
                if count > facts.bytes:
                    raise DownloadError("integrity", "A gravação mudou durante a cópia; original preservado.")
                target.write(chunk)
                digest.update(chunk)
                with self.lock:
                    self.live_progress[(task["id"], 0)] = count
            target.flush()
            os.fsync(target.fileno())
            after = os.fstat(source.fileno())
        if (count != facts.bytes or digest.hexdigest() != facts.sha256 or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise DownloadError("integrity", "A gravação não confere com o recibo; nenhum arquivo foi publicado.")
        with self.lock:
            control.check()
            self.live_progress.pop((task["id"], 0), None)
            self._change(task, status="verifying", downloaded=count,
                         parts_state=[{"start": 0, "end": count - 1, "done": count}])
        try:
            media = validate_recording(work, part, facts.ext, facts.audio, control.stop)
        except MediaError as error:
            control.check()
            raise DownloadError("recording_media", str(error)) from error
        with self.lock:
            control.check()
            self._change(task, recorded_media=media)
        self._finalize(task, control)

    def _transfer(self, task: dict, control: Control, *, headers=None, media=False):
        error = None
        available = []
        for index, part in enumerate(task["parts_state"]):
            remaining = None if part["end"] is None else part["end"] - part["start"] + 1 - part["done"]
            if remaining is None or remaining > 0:
                available.append((-(remaining or 0), index))
        heapq.heapify(available)
        claim_lock = threading.Lock()
        def transfer_blocks():
            nonlocal error
            while True:
                control.check()
                with claim_lock:
                    if not available:
                        return
                    _, index = heapq.heappop(available)
                try:
                    self._part(task, index, control, headers=headers, media=media)
                except Exception as caught:
                    with claim_lock:
                        if error is None:
                            error = caught
                    control.interrupt()
                    raise
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(task["parts"], max(1, len(available))), thread_name_prefix="lume-part") as pool:
            futures = [pool.submit(transfer_blocks) for _ in range(min(task["parts"], len(available)))]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as caught:
                    if error is None:
                        error = caught
                    control.interrupt()
        if error:
            raise error

    def _limit(self, size: int, control: Control):
        with self.limit_lock:
            limit = self.options["speed_limit_kib"] * 1024
            if not limit:
                self.next_byte_time = time.monotonic()
                return
            until = max(time.monotonic(), self.next_byte_time) + size / limit
            self.next_byte_time = until
        while time.monotonic() < until:
            if control.stop.wait(min(.1, until - time.monotonic())):
                raise Interrupted()

    def _part(self, task: dict, index: int, control: Control, *, headers=None, media=False):
        control.check()
        part = task["parts_state"][index]
        start = part["start"] + part["done"]
        end = part["end"]
        if end is not None and start > end:
            return
        headers = dict(headers or {})
        if task["mode"] == "range":
            headers.update({"Range": f"bytes={start}-{end}", "If-Range": task["identity"]["validator"]})
        elif part["done"]:
            raise DownloadError("resume", "Servidor sem retomada segura. Use Reiniciar.")
        with self.transport.open(task["identity"]["url"], headers, control) as owned:
            response = owned.response
            if media and response.status in (200, 206):
                self._check_media_response(response, media)
            if owned.url != task["identity"]["url"]:
                raise DownloadError("changed", "Destino do recurso mudou. Use Reiniciar.")
            if task["mode"] == "range":
                if response.status == 200:
                    raise DownloadError("range_ignored", "Servidor ignorou o intervalo ou alterou o arquivo. Use Reiniciar.")
                if response.status != 206 or content_range(response.getheader("Content-Range")) != (start, end, task["total"]):
                    raise DownloadError("range", "Resposta parcial não corresponde aos bytes solicitados.")
                if validator(response) != task["identity"]["validator"]:
                    raise DownloadError("changed", "Identidade do arquivo mudou. Use Reiniciar.")
                expected = end - start + 1
            else:
                if response.status != 200:
                    raise DownloadError("http", f"Servidor respondeu HTTP {response.status}.")
                if task["identity"]["validator"] and validator(response) != task["identity"]["validator"]:
                    raise DownloadError("changed", "Identidade do arquivo mudou. Use Reiniciar.")
                expected = content_length(response)
                if task["total"] is not None and expected != task["total"]:
                    raise DownloadError("changed", "Tamanho do recurso mudou. Use Reiniciar.")
            length = content_length(response)
            if length is not None and expected is not None and length != expected:
                raise DownloadError("length", "Comprimento da resposta não corresponde ao intervalo.")
            received = 0
            with self._open_part(task, index) as file:
                file.seek(0, os.SEEK_END)
                if file.tell() != part["done"]:
                    raise DownloadError("partial", "A parte temporária mudou após o planejamento; dados preservados.")
                pending = 0
                def checkpoint():
                    nonlocal pending
                    if pending:
                        file.flush()
                        os.fsync(file.fileno())
                        with self.lock:
                            part["done"] += pending
                            pending = 0
                            self.live_progress[(task["id"], index)] = 0
                            task["downloaded"] = sum(p["done"] for p in task["parts_state"])
                            self._save(task)
                try:
                    while True:
                        control.check()
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        if expected is not None and received + len(chunk) > expected:
                            raise DownloadError("length", "Resposta excedeu o tamanho esperado.")
                        self._limit(len(chunk), control)
                        control.check()
                        file.write(chunk)
                        pending += len(chunk)
                        received += len(chunk)
                        with self.lock:
                            self.live_progress[(task["id"], index)] = pending
                            current = self.tasks[task["id"]]["downloaded"] + sum(value for (identity, _), value in self.live_progress.items() if identity == task["id"])
                            history = self.rates.setdefault(task["id"], [])
                            history.append((time.monotonic(), current))
                            del history[:-128]
                        if pending >= CHECKPOINT_BYTES:
                            checkpoint()
                finally:
                    # Pause/cancel acknowledges completion only after all received bytes
                    # have been flushed and their checkpoint committed. A crash before
                    # this point replays from the last durable offset, truncating the tail.
                    checkpoint()
            if expected is not None and received != expected:
                raise DownloadError("truncated", "Transferência incompleta. As partes válidas foram preservadas.")

    def _download_media_streams(self, task: dict, control: Control, streams: list):
        if task["parts_state"]:
            raise DownloadError("media_changed", "O formato de mídia mudou. Use Reiniciar para baixar as faixas separadas.")
        previous = task.get("streams_state", [])
        if previous and [stream["format_id"] for stream in previous] != [stream["format_id"] for stream in streams]:
            raise DownloadError("media_changed", "Os formatos do vídeo mudaram. Use Reiniciar.")
        if not previous:
            with self.lock:
                self._change(task, streams_state=[{
                    "id": task["id"], "directory": task["directory"], "stream_index": index,
                    "format_id": stream["format_id"], "parts": task["parts"], "parts_state": [],
                    "identity": None, "total": stream["expected_size"], "downloaded": 0,
                    "mode": None, "sha256_expected": None,
                    "ext": stream.get("ext", "mp4" if index == 0 else "m4a"),
                } for index, stream in enumerate(streams)])
        for index, stream in enumerate(streams):
            control.check()
            child = task["streams_state"][index]
            identity = self._probe(child, control, url=stream["stream_url"], headers=stream["headers"],
                                   media=stream["mimetype"])
            inspect_remote_mp4(identity,self.transport,control,stream["headers"])
            if stream["expected_size"] is not None and identity["total"] != stream["expected_size"]:
                raise DownloadError("media_size", "O tamanho da faixa de mídia diverge do informado pelo provedor.")
            self._plan(child, identity, control)
            with self.lock:
                control.check()
                self._change(task, status="downloading")
            required = (identity["total"] or 0) * 2
            if shutil.disk_usage(self._owned(task)).free < required + 1024 * 1024:
                raise DownloadError("space", "Espaço insuficiente para as faixas e o arquivo final.")
            self._transfer(child, control, headers=stream["headers"], media=stream["mimetype"])
            # Reuse the same bounded live-progress slots only after every part worker exited.
            with self.lock:
                for key in [key for key in self.live_progress if key[0] == task["id"]]:
                    self.live_progress.pop(key, None)

    def _assemble_parts(self, task: dict, control: Control, assembled: Path) -> tuple[int, str]:
        assembled_at = time.monotonic()
        if assembled.is_symlink():
            raise DownloadError("ownership", "Temporário substituído; finalização recusada.")
        digest = hashlib.sha256()
        size = 0
        with assembled.open("wb") as output:
            for index in range(len(task["parts_state"])):
                control.check()
                source = self._part_path(task, index)
                if source.is_symlink():
                    raise DownloadError("ownership", "Parte substituída; finalização recusada.")
                with source.open("rb") as file:
                    while chunk := file.read(1024 * 1024):
                        control.check()
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if task["total"] is not None and size != task["total"]:
            raise DownloadError("length", "Tamanho final divergente. Arquivo não publicado.")
        if task["sha256_expected"] and digest.hexdigest() != task["sha256_expected"]:
            raise DownloadError("integrity", "SHA-256 divergente. Arquivo não publicado; temporários preservados.")
        self._timing(task, "aggregate_hash_s", time.monotonic() - assembled_at)
        return size, digest.hexdigest()

    def _finish_media(self, task: dict, control: Control):
        work = self._owned(task)
        required = sum(stream["downloaded"] for stream in task["streams_state"]) * 2
        if shutil.disk_usage(work).free < required + 1024 * 1024:
            raise DownloadError("space", "Espaço insuficiente para unir as faixas e publicar o vídeo.")
        inputs = []
        for index, child in enumerate(task["streams_state"]):
            path = work / f"stream-{index}.tmp"
            self._assemble_parts(child, control, path)
            inputs.append(path)
        assembled = work / "assembled.tmp"
        if assembled.is_symlink():
            raise DownloadError("ownership", "Temporário substituído; finalização recusada.")
        assembled.unlink(missing_ok=True)
        try:
            media = task.get("media", {})
            extensions = [stream.get("ext", "mp4" if index == 0 else "m4a")
                          for index, stream in enumerate(task["streams_state"])]
            options = {} if extensions == ["mp4", "m4a"] and media.get("ext", "mp4") == "mp4" else {
                "output_ext": media.get("ext", "mp4"), "video_ext": extensions[0], "audio_ext": extensions[1]}
            mux_media(work, inputs, assembled, control.stop, **options)
        except MediaError as error:
            control.check()
            raise DownloadError("media_mux", str(error)) from None
        control.check()
        digest = hashlib.sha256()
        with assembled.open("r+b") as file:
            while chunk := file.read(1024 * 1024):
                control.check()
                digest.update(chunk)
            os.fsync(file.fileno())
        if task["sha256_expected"] and digest.hexdigest() != task["sha256_expected"]:
            raise DownloadError("integrity", "SHA-256 divergente. Arquivo não publicado; temporários preservados.")
        self._publish(task, control, assembled, assembled.stat().st_size, digest.hexdigest())

    def _finalize(self, task: dict, control: Control):
        if task.get("streaming"):
            self._publish_stream_output(task, control)
            return
        if task.get("streams_state"):
            self._finish_media(task, control)
            return
        assembled = self._owned(task) / "assembled.tmp"
        size, digest = self._assemble_parts(task, control, assembled)
        self._publish(task, control, assembled, size, digest)

    @staticmethod
    def _check_media_publication(task: dict, path: Path, control: Control):
        if task.get("expected_media") or task.get("media") or task.get("kind") in {"youtube", "media", "stream"}:
            reject_protected_mp4(path, control)

    def _publish(self, task: dict, control: Control, assembled: Path, size: int, digest: str):
        published_at = time.monotonic()
        self._check_media_publication(task, assembled, control)
        if task.get("publication") and task["publication"] != {"size": size, "sha256": digest}:
            raise DownloadError("publication", "Dados parciais divergem do registro de publicação; preservados.")
        with self.lock:
            control.check()
            # Commit the verified publication intent before the filesystem operation, so a
            # process interruption after rename can be reconciled without a second download.
            self._change(task, publication={"size": size, "sha256": digest})
            destination = Path(task["directory"]) / task["filename"]
            if os.name == "nt":
                # Windows rename is atomic on one volume and refuses an existing destination.
                os.rename(assembled, destination)
            else:
                os.link(assembled, destination)
            self._change(task, status="completed", total=size, downloaded=size, sha256=digest, error="")
        self._timing(task, "publish_s", time.monotonic() - published_at)
        cleaned_at = time.monotonic()
        try:
            self._clean(task, keep_owner=False)
        except (OSError, DownloadError):
            with self.lock:
                self._change(task, error="Concluído; alguns temporários não puderam ser removidos. Use Remover para tentar a limpeza.")
        self._timing(task, "cleanup_s", time.monotonic() - cleaned_at)
