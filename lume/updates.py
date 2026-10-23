"""Offline signed-release verification and bounded, non-executing ZIP staging."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile
import unicodedata
import zipfile

APP_ID = "lume-downloads"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_BYTES = 1024**3
MAX_EXPANDED_BYTES = 1024**3
MAX_ARCHIVE_ENTRIES = 4096
MAX_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
_VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{i}" for i in "123456789¹²³"),
    *(f"LPT{i}" for i in "123456789¹²³"),
}


class UpdateError(ValueError):
    """An update cannot be authenticated or staged safely."""


@dataclass(frozen=True)
class VerifiedUpdate:
    version: str
    filename: str
    sha256: str
    size: int
    package_path: Path
    current_version: str
    _manifest_bytes: bytes = field(repr=False)
    _trusted_public_key: bytes = field(repr=False)


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise UpdateError("Versão inválida: use três números, por exemplo 1.0.1.")
    return tuple(int(piece) for piece in value.split("."))


def _component(value: str) -> None:
    if (
        not value or len(value) > 240 or value in (".", "..")
        or value[-1] in (" ", ".")
        or any(ord(ch) < 32 or ord(ch) == 127 or ch in '<>:"/\\|?*' for ch in value)
        or value.split(".", 1)[0].rstrip(" .").upper() in _RESERVED
    ):
        raise UpdateError("Nome de arquivo inválido ou reservado no Windows.")


def _payload(value: object) -> dict:
    required = {"app", "version", "sha256", "size", "filename"}
    if not isinstance(value, dict) or set(value) != required:
        raise UpdateError("O payload da atualização não corresponde ao esquema esperado.")
    if value["app"] != APP_ID:
        raise UpdateError("Este manifesto pertence a outro aplicativo.")
    _version(value["version"])
    if not isinstance(value["sha256"], str) or not _DIGEST.fullmatch(value["sha256"]):
        raise UpdateError("SHA-256 inválido no manifesto.")
    if type(value["size"]) is not int or not 1 <= value["size"] <= MAX_PACKAGE_BYTES:
        raise UpdateError("Tamanho do pacote fora do limite de 1 GiB.")
    name = value["filename"]
    if not isinstance(name, str):
        raise UpdateError("Nome do pacote inválido.")
    _component(name)
    if not name.lower().endswith(".zip"):
        raise UpdateError("A atualização deve ser um pacote ZIP.")
    return value


def canonical_payload(payload: dict) -> bytes:
    """The single signing/verification representation; validates the payload."""
    return json.dumps(
        _payload(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise UpdateError("O manifesto contém uma propriedade JSON duplicada.")
        obj[key] = value
    return obj


def _invalid_constant(value):
    raise UpdateError("O manifesto contém um número JSON inválido.")


def _envelope(raw: bytes) -> tuple[dict, bytes]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("O manifesto excede o limite de 1 MiB.")
    try:
        obj = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise UpdateError("O manifesto não é um JSON UTF-8 válido.") from error
    if not isinstance(obj, dict) or set(obj) != {"payload", "signature"}:
        raise UpdateError("Envelope de atualização inválido.")
    payload = _payload(obj["payload"])
    signature = _decode_base64(obj["signature"], 64, "Assinatura Ed25519")
    return payload, signature


def _decode_base64(value: object, size: int, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise UpdateError(f"{label} ausente; atualização bloqueada.")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise UpdateError(f"{label} não está em base64 válido.") from error
    if len(raw) != size:
        raise UpdateError(f"{label} tem tamanho inválido.")
    return raw


def _authenticate(raw_manifest: bytes, public_key: bytes, current_version: str) -> dict:
    payload, signature = _envelope(raw_manifest)
    if _version(payload["version"]) <= _version(current_version):
        raise UpdateError("Versão repetida ou antiga: atualização bloqueada.")
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as error:
        raise UpdateError("Biblioteca cryptography indisponível; atualização bloqueada.") from error
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical_payload(payload)
        )
    except UnsupportedAlgorithm as error:
        raise UpdateError("Ed25519 indisponível neste ambiente; atualização bloqueada.") from error
    except (InvalidSignature, ValueError, TypeError) as error:
        raise UpdateError("Assinatura da atualização inválida.") from error
    return payload


def _digest(stream, expected_size: int) -> str:
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise UpdateError("O pacote deve ser um arquivo regular.")
    if os.fstat(stream.fileno()).st_size != expected_size:
        raise UpdateError("O tamanho do pacote difere do manifesto.")
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(min(CHUNK_BYTES, expected_size - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > expected_size:
            raise UpdateError("O pacote cresceu durante a leitura.")
        digest.update(chunk)
    if total != expected_size:
        raise UpdateError("Leitura incompleta do pacote.")
    return digest.hexdigest()


def release_payload(package_path: str | Path, version: str) -> dict:
    """Produce metadata for the release signing tool, without trusting a package."""
    path = Path(package_path)
    _component(path.name)
    _version(version)
    try:
        with path.open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            if not 1 <= size <= MAX_PACKAGE_BYTES:
                raise UpdateError("Tamanho do pacote fora do limite de 1 GiB.")
            digest = _digest(source, size)
    except OSError as error:
        raise UpdateError(f"Não foi possível ler o pacote: {error}") from error
    return _payload(
        {"app": APP_ID, "version": version, "filename": path.name, "size": size, "sha256": digest}
    )


def verify_update(
    manifest_path: str | Path,
    package_path: str | Path,
    trusted_public_key_b64: str,
    current_version: str = "1.0.0",
) -> VerifiedUpdate:
    """Authenticate signed metadata and stream-check a local package. No network."""
    key = _decode_base64(trusted_public_key_b64, 32, "Chave pública confiável")
    path = Path(package_path)
    try:
        with Path(manifest_path).open("rb") as source:
            raw_manifest = source.read(MAX_MANIFEST_BYTES + 1)
        payload = _authenticate(raw_manifest, key, current_version)
        if path.name != payload["filename"]:
            raise UpdateError("O nome do pacote difere do manifesto assinado.")
        with path.open("rb") as source:
            if _digest(source, payload["size"]) != payload["sha256"]:
                raise UpdateError("O SHA-256 do pacote difere do manifesto.")
    except OSError as error:
        raise UpdateError(f"Não foi possível ler os arquivos de atualização: {error}") from error
    return VerifiedUpdate(
        payload["version"], payload["filename"], payload["sha256"], payload["size"],
        path, current_version, raw_manifest, key,
    )


def _zip_directory_count(stream) -> int:
    """Bound the central-directory allocation before constructing ZipFile."""
    stream.seek(0, os.SEEK_END)
    length = stream.tell()
    tail_size = min(length, 65535 + 22)
    stream.seek(length - tail_size)
    tail = stream.read(tail_size)
    position = len(tail)
    while True:
        position = tail.rfind(b"PK\x05\x06", 0, position)
        if position < 0:
            raise UpdateError("O pacote não possui diretório ZIP válido.")
        if position + 22 <= len(tail):
            header = struct.unpack_from("<4s4H2IH", tail, position)
            if position + 22 + header[-1] == len(tail):
                break
    _, disk, directory_disk, disk_count, total_count, directory_size, offset, _ = header
    end_offset = length - tail_size + position
    if (
        disk or directory_disk or disk_count != total_count
        or total_count == 0xFFFF or directory_size == 0xFFFFFFFF or offset == 0xFFFFFFFF
        or directory_size > MAX_CENTRAL_DIRECTORY_BYTES
        or offset + directory_size != end_offset
    ):
        raise UpdateError("ZIP64, múltiplos volumes ou diretório ZIP fora dos limites.")
    if total_count > MAX_ARCHIVE_ENTRIES:
        raise UpdateError("O ZIP excede o limite de entradas.")
    # Count the actual records as well: the EOCD count is untrusted and ZipFile
    # otherwise allocates every record before the application can reject it.
    stream.seek(offset)
    remaining = directory_size
    actual_count = 0
    while remaining:
        fixed = stream.read(46)
        if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
            raise UpdateError("Registro de diretório ZIP inválido.")
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", fixed, 28)
        variable_size = name_size + extra_size + comment_size
        if 46 + variable_size > remaining or name_size > 4096:
            raise UpdateError("Registro de diretório ZIP fora dos limites.")
        actual_count += 1
        if actual_count > MAX_ARCHIVE_ENTRIES:
            raise UpdateError("O ZIP excede o limite de entradas.")
        stream.seek(variable_size, os.SEEK_CUR)
        remaining -= 46 + variable_size
    if actual_count != total_count:
        raise UpdateError("Contagem inconsistente de entradas ZIP.")
    stream.seek(0)
    return total_count


def _key(parts: tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)


def _archive_plan(archive, max_entries: int, max_expanded_bytes: int):
    entries = archive.infolist()
    if len(entries) > max_entries:
        raise UpdateError("O ZIP excede o limite de entradas.")
    seen = {}
    explicit = set()
    plan = []
    expanded = 0
    for info in entries:
        name = info.orig_filename
        if "\x00" in name or "\\" in name or name.startswith("/"):
            raise UpdateError("Caminho absoluto ou ambíguo no ZIP.")
        is_dir = info.is_dir()
        parts = tuple((name[:-1] if is_dir else name).split("/"))
        if sum(len(part) + 1 for part in parts) > 1024:
            raise UpdateError("Caminho excessivamente longo no ZIP.")
        for part in parts:
            _component(part)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
            raise UpdateError("Links e arquivos especiais não são permitidos no ZIP.")
        if kind == stat.S_IFDIR and not is_dir:
            raise UpdateError("Tipo de diretório inconsistente no ZIP.")
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise UpdateError("ZIP criptografado ou compressão não suportada.")
        if info.file_size < 0 or (is_dir and info.file_size):
            raise UpdateError("Tamanho de entrada ZIP inválido.")
        expanded += info.file_size
        if expanded > max_expanded_bytes:
            raise UpdateError("O conteúdo expandido excede o limite permitido.")
        for length in range(1, len(parts)):
            prefix = parts[:length]
            prefix_key = _key(prefix)
            previous = seen.get(prefix_key)
            if previous is not None and previous != (prefix, True):
                raise UpdateError("Colisão de nomes ou arquivo/diretório no ZIP.")
            seen[prefix_key] = (prefix, True)
        path_key = _key(parts)
        previous = seen.get(path_key)
        if path_key in explicit or previous is not None and previous != (parts, is_dir):
            raise UpdateError("Colisão de nomes ou arquivo/diretório no ZIP.")
        seen[path_key] = (parts, is_dir)
        explicit.add(path_key)
        plan.append((info, parts, is_dir))
    return plan


def _extract(stream, destination: Path, max_entries: int, max_expanded_bytes: int) -> None:
    count = _zip_directory_count(stream)
    if count > max_entries:
        raise UpdateError("O ZIP excede o limite de entradas.")
    with zipfile.ZipFile(stream, "r") as archive:
        if len(archive.infolist()) != count:
            raise UpdateError("Contagem inconsistente de entradas ZIP.")
        plan = _archive_plan(archive, max_entries, max_expanded_bytes)
        total = 0
        for info, parts, is_dir in plan:
            target = destination.joinpath(*parts)
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info, "r") as source, target.open("xb") as output:
                while True:
                    chunk = source.read(min(CHUNK_BYTES, info.file_size - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > info.file_size or total > max_expanded_bytes:
                        raise UpdateError("A expansão real do ZIP excedeu o limite.")
                    output.write(chunk)
            if written != info.file_size:
                raise UpdateError("Conteúdo ZIP incompleto.")


def _cleanup_owned(stage: Path, parent: Path) -> None:
    if stage.parent.resolve() != parent or not stage.name.startswith("lume-update-") or stage.is_symlink():
        raise UpdateError(f"Limpeza não autorizada: verifique a pasta {stage}.")
    shutil.rmtree(stage)


def stage_update(
    verified: VerifiedUpdate,
    staging_parent: str | Path,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES,
) -> Path:
    """Copy, reauthenticate and extract into a new folder. Never applies/runs code.

    Optional quotas may tighten the built-in ceilings; they cannot raise them.
    The selected staging parent must be controlled by the current user.
    """
    if not isinstance(verified, VerifiedUpdate):
        raise UpdateError("A atualização precisa ser verificada antes do staging.")
    if (
        type(max_entries) is not int or not 1 <= max_entries <= MAX_ARCHIVE_ENTRIES
        or type(max_expanded_bytes) is not int
        or not 1 <= max_expanded_bytes <= MAX_EXPANDED_BYTES
    ):
        raise UpdateError("Limites de staging inválidos.")
    payload = _authenticate(
        verified._manifest_bytes, verified._trusted_public_key, verified.current_version
    )
    if (verified.version, verified.filename, verified.sha256, verified.size) != (
        payload["version"], payload["filename"], payload["sha256"], payload["size"]
    ) or verified.package_path.name != payload["filename"]:
        raise UpdateError("Os dados verificados foram alterados.")
    stage = None
    try:
        parent = Path(staging_parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="lume-update-", dir=parent))
        payload_dir = stage / "payload"
        payload_dir.mkdir()
        # The hash is computed over exactly the bytes copied to a private file.
        # The same private handle is checked again and used by ZipFile.
        with verified.package_path.open("rb") as source, (stage / "release.zip").open("x+b") as private:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise UpdateError("O pacote deve ser um arquivo regular.")
            if os.fstat(source.fileno()).st_size != verified.size:
                raise UpdateError("O pacote mudou desde a verificação.")
            digest = hashlib.sha256()
            copied = 0
            while True:
                chunk = source.read(min(CHUNK_BYTES, verified.size - copied + 1))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > verified.size:
                    raise UpdateError("O pacote mudou durante a cópia.")
                private.write(chunk)
                digest.update(chunk)
            if copied != verified.size or digest.hexdigest() != verified.sha256:
                raise UpdateError("O pacote mudou desde a verificação.")
            private.flush()
            os.fsync(private.fileno())
            private.seek(0)
            if _digest(private, verified.size) != verified.sha256:
                raise UpdateError("A cópia privada do pacote não passou na verificação.")
            _extract(private, payload_dir, max_entries, max_expanded_bytes)
        with (stage / "manifest.json").open("xb") as output:
            output.write(verified._manifest_bytes)
        return stage
    except BaseException as error:
        if stage is not None:
            try:
                _cleanup_owned(stage, parent)
            except Exception as cleanup_error:
                raise UpdateError(
                    f"Staging falhou e a limpeza ficou pendente em {stage}: {cleanup_error}"
                ) from error
        if isinstance(error, UpdateError):
            raise
        if isinstance(error, Exception):
            raise UpdateError(f"Não foi possível preparar a atualização: {error}") from error
        raise

