"""Explicit caller-owned content keys; never a persistence or license client."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re

from .network import DownloadError

MAX_AUTHORIZED_KEYS = 16
MAX_KEY_TEXT = 8192
_HEX = re.compile(r"[0-9a-fA-F]{32}")


def _invalid() -> DownloadError:
    # Never interpolate caller input: both parse errors and repr cross UI/log seams.
    return DownloadError("authorized_keys", "Informe até 16 pares KID:CHAVE de 32 caracteres hexadecimais cada, sem KIDs repetidos.")


@dataclass(frozen=True, repr=False)
class AuthorizedKeys:
    """Immutable in-memory key set. Public methods never expose an internal map."""
    _entries: tuple[tuple[str, bytes], ...] = field(default=(), repr=False)

    def __post_init__(self):
        if type(self._entries) is not tuple or len(self._entries) > MAX_AUTHORIZED_KEYS:
            raise _invalid()
        normalized = []
        seen = set()
        for item in self._entries:
            if (type(item) is not tuple or len(item) != 2 or not isinstance(item[0], str)
                    or not _HEX.fullmatch(item[0]) or type(item[1]) is not bytes or len(item[1]) != 16):
                raise _invalid()
            kid = item[0].lower()
            if kid in seen:
                raise _invalid()
            seen.add(kid)
            normalized.append((kid, item[1]))
        object.__setattr__(self, "_entries", tuple(sorted(normalized)))

    def __repr__(self):
        return f"AuthorizedKeys(count={len(self._entries)})"

    def __bool__(self):
        return bool(self._entries)

    def by_kid(self) -> dict[str, bytes]:
        return dict(self._entries)

    def fingerprint(self) -> str:
        digest = hashlib.sha256(b"OpenDownloader-authorized-keys-v1\0")
        for kid, key in self._entries:
            digest.update(bytes.fromhex(kid))
            digest.update(key)
        return digest.hexdigest()


def parse_authorized_keys(text: str) -> AuthorizedKeys:
    if not isinstance(text, str) or len(text) > MAX_KEY_TEXT:
        raise _invalid()
    if not text.strip():
        return AuthorizedKeys()
    entries = []
    for line in re.split(r"[\r\n,;]+", text.strip()):
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*([0-9a-fA-F]{32})\s*:\s*([0-9a-fA-F]{32})\s*", line)
        if match is None:
            raise _invalid()
        entries.append((match[1], bytes.fromhex(match[2])))
        if len(entries) > MAX_AUTHORIZED_KEYS:
            raise _invalid()
    if not entries:
        raise _invalid()
    return AuthorizedKeys(tuple(entries))


def validate_authorized_keys(value: AuthorizedKeys | None) -> None:
    if value is not None and type(value) is not AuthorizedKeys:
        raise _invalid()
