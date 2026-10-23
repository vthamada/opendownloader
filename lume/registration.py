"""Owned, per-user Native Messaging registration for the portable OpenDownloader host.

This module never changes browser policies or profiles. A plan is a snapshot,
not permission to overwrite later changes; apply rechecks it before every write.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol
import uuid

HOST_NAME = "app.lume.downloads"
OWNER = "lume-native-host-registration-v1"
MANIFEST_NAME = HOST_NAME + ".json"
OWNER_NAME = HOST_NAME + ".owner.json"
ORIGIN = re.compile(r"chrome-extension://[a-p]{32}/\Z")


@dataclass(frozen=True)
class RegistryTarget:
    browser: str
    view: int
    path: str


@dataclass(frozen=True)
class RegistryState:
    key_exists: bool = False
    default_exists: bool = False
    value: Any = None
    kind: int | None = None
    other_values: tuple = ()
    subkeys: tuple[str, ...] = ()


class RegistryBackend(Protocol):
    def targets(self) -> tuple[RegistryTarget, ...]: ...
    def read(self, target: RegistryTarget) -> RegistryState: ...
    def write(self, target: RegistryTarget, value: str, kind: int = 1) -> None: ...
    def delete_default(self, target: RegistryTarget) -> None: ...
    def delete_empty_key(self, target: RegistryTarget) -> None: ...


class WindowsRegistry:
    """HKCU only. namespace is for private integration-test keys, never policies."""
    def __init__(self, *, namespace: str | None = None):
        if os.name != "nt":
            raise RegistrationError("platform", "A integração com navegadores exige Windows.")
        if namespace is not None and (not namespace.startswith("Software\\LumeRegistrationTests\\")
                                      or ".." in namespace.split("\\")):
            raise ValueError("Private registration test namespace required")
        self.namespace = namespace

    def targets(self):
        bases = (("Chrome", r"Software\Google\Chrome\NativeMessagingHosts"),
                 ("Edge", r"Software\Microsoft\Edge\NativeMessagingHosts"))
        return tuple(RegistryTarget(browser, view,
                                   (self.namespace + "\\" + browser if self.namespace else base) + "\\" + HOST_NAME)
                     for browser, base in bases for view in (32, 64))

    @staticmethod
    def _access(view):
        import winreg
        return winreg.KEY_WOW64_32KEY if view == 32 else winreg.KEY_WOW64_64KEY

    def read(self, target):
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, target.path, 0,
                                 winreg.KEY_READ | self._access(target.view))
        except FileNotFoundError:
            return RegistryState()
        with key:
            count_keys, count_values, _ = winreg.QueryInfoKey(key)
            values = [winreg.EnumValue(key, index) for index in range(count_values)]
            default = next((row for row in values if row[0] == ""), None)
            return RegistryState(True, default is not None, default[1] if default else None,
                                 default[2] if default else None,
                                 tuple(sorted((row for row in values if row[0]), key=lambda row: row[0])),
                                 tuple(sorted(winreg.EnumKey(key, index) for index in range(count_keys))))

    def write(self, target, value, kind=1):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, target.path, 0,
                                winreg.KEY_SET_VALUE | self._access(target.view)) as key:
            winreg.SetValueEx(key, "", 0, kind, value)

    def delete_default(self, target):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, target.path, 0,
                                winreg.KEY_SET_VALUE | self._access(target.view)) as key:
                try:
                    winreg.DeleteValue(key, "")
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass

    def delete_empty_key(self, target):
        import winreg
        state = self.read(target)
        if state.key_exists and not state.default_exists and not state.other_values and not state.subkeys:
            try:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, target.path, self._access(target.view), 0)
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class FileState:
    content: bytes | None = None
    identity: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class RegistrationReceipt:
    status: str
    applied: bool
    manifest: Path
    backup_directory: Path | None = None
    changes: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    rollback_pending: tuple[str, ...] = ()
    _mutations: tuple = field(default=(), repr=False, compare=False)
    _backend: RegistryBackend | None = field(default=None, repr=False, compare=False)

    def to_dict(self):
        return {"status": self.status, "applied": self.applied, "manifest": str(self.manifest),
                "backup_directory": str(self.backup_directory) if self.backup_directory else None,
                "changes": list(self.changes), "preserved": list(self.preserved),
                "rollback_pending": list(self.rollback_pending),
                "owner": OWNER, "rollback": [_mutation_json(row) for row in self._mutations]}


class RegistrationError(RuntimeError):
    def __init__(self, code: str, message: str, receipt: RegistrationReceipt | None = None):
        super().__init__(message)
        self.code = code
        self.receipt = receipt


@dataclass(frozen=True)
class RegistrationPlan:
    host_executable: Path
    manifest: Path
    owner_file: Path
    extension_id: str
    allowed_origins: tuple[str, ...]
    backup_directory: Path | None
    changes: tuple[str, ...]
    _host_digest: str = field(repr=False)
    _files: tuple[tuple[Path, FileState], ...] = field(repr=False)
    _registry: tuple[tuple[RegistryTarget, RegistryState], ...] = field(repr=False)
    _manifest_bytes: bytes = field(repr=False)
    _owner_bytes: bytes = field(repr=False)
    _backend: RegistryBackend = field(repr=False, compare=False)
    host_source: Path | None = None
    previous_manifest_directory: Path | None = None

    @property
    def changed(self):
        return bool(self.changes)

    def to_dict(self):
        return {"host_executable": str(self.host_executable), "manifest": str(self.manifest),
                "host_source": str(self.host_source) if self.host_source else None,
                "host_sha256": self._host_digest,
                "previous_manifest_directory": str(self.previous_manifest_directory) if self.previous_manifest_directory else None,
                "owner_file": str(self.owner_file), "extension_id": self.extension_id,
                "allowed_origins": list(self.allowed_origins), "changed": self.changed,
                "changes": list(self.changes),
                "backup_directory": str(self.backup_directory) if self.backup_directory else None,
                "registry": [{"browser": target.browser, "view": target.view, "hive": "HKCU",
                              "path": target.path, "before": _state_json(state)}
                             for target, state in self._registry]}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _path(value):
    path = Path(os.path.abspath(Path(value).expanduser()))
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise RegistrationError("ownership", "A configuração não pode atravessar links de diretório ou arquivo.")
    return path


def _same_path(first, second):
    return isinstance(first, (str, Path)) and os.path.normcase(os.path.abspath(first)) == os.path.normcase(str(second))


def _read_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return FileState()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise RegistrationError("ownership", "A configuração existente não é um arquivo regular pertencente ao OpenDownloader.")
    content = path.read_bytes()
    after = path.stat()
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RegistrationError("changed", "O arquivo mudou durante a leitura; nenhuma alteração autorizada.")
    return FileState(content, identity)


def _owned_manifest(directory, *, expected_host=None):
    manifest, owner = directory / MANIFEST_NAME, directory / OWNER_NAME
    manifest_state, owner_state = _read_file(manifest), _read_file(owner)
    if (manifest_state.content is None) != (owner_state.content is None):
        raise RegistrationError("ownership", "Manifesto e recibo de propriedade estão incompletos; arquivos preservados.")
    if manifest_state.content is None:
        return None, ((manifest, manifest_state), (owner, owner_state))
    try:
        proof = json.loads(owner_state.content.decode("utf-8-sig"))
        value = json.loads(manifest_state.content.decode("utf-8-sig"))
        valid = (isinstance(proof, dict) and proof.get("owner") == OWNER
                 and _same_path(proof.get("manifest"), manifest)
                 and proof.get("sha256") == _digest(manifest_state.content)
                 and isinstance(value, dict) and value.get("name") == HOST_NAME
                 and value.get("type") == "stdio" and isinstance(value.get("path"), str)
                 and Path(value["path"]).is_absolute() and Path(value["path"]).suffix.lower() == ".exe"
                 and isinstance(value.get("allowed_origins"), list) and bool(value["allowed_origins"])
                 and all(isinstance(origin, str) and ORIGIN.fullmatch(origin) for origin in value["allowed_origins"]))
        if not valid or (expected_host is not None and not _same_path(value["path"], expected_host)):
            raise ValueError("Unproven owner or incompatible manifest")
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise RegistrationError("ownership", "A configuração existente não corresponde ao recibo do OpenDownloader; preservada.") from error
    return value, ((manifest, manifest_state), (owner, owner_state))


def _state_json(state):
    def encode(value):
        return {"base64": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value
    return {"key_exists": state.key_exists, "default_exists": state.default_exists,
            "value": encode(state.value), "kind": state.kind,
            "other_values": [[name, encode(value), kind] for name, value, kind in state.other_values],
            "subkeys": list(state.subkeys)}


def _file_json(state):
    return {"content_base64": base64.b64encode(state.content).decode("ascii") if state.content is not None else None,
            "identity": list(state.identity) if state.identity is not None else None}


def _mutation_json(row):
    kind, target, before, after = row
    return {"kind": kind,
            "target": str(target) if kind == "file" else {"browser": target.browser, "view": target.view, "path": target.path},
            "before": _file_json(before) if kind == "file" else _state_json(before),
            "after": _file_json(after) if kind == "file" else _state_json(after)}


def discover_owned_manifest_directory(*, _backend: RegistryBackend | None = None) -> Path | None:
    """Discover proven configuration, independently of the old host's readiness.

    The legacy executable is neither read nor run. Its absence or corruption
    does not invalidate the manifest's owner receipt; the new host is verified
    separately by plan_registration and apply_registration.
    """
    backend = _backend or WindowsRegistry()
    selected = None
    for target in backend.targets():
        state = backend.read(target)
        if not state.default_exists:
            if state.other_values or state.subkeys:
                raise RegistrationError("foreign", "Há uma ponte de navegador incompleta; descoberta recusada.")
            continue
        if state.kind != 1 or not isinstance(state.value, str) or not Path(state.value).is_absolute():
            raise RegistrationError("foreign", "Há uma ponte de navegador sem propriedade comprovada.")
        manifest_path = _path(state.value)
        if manifest_path.name != MANIFEST_NAME:
            raise RegistrationError("foreign", "O registro aponta para um manifesto desconhecido.")
        if selected is not None and not _same_path(manifest_path, selected):
            raise RegistrationError("foreign", "Chrome e Edge possuem pontes diferentes; migração automática recusada.")
        value, _ = _owned_manifest(manifest_path.parent)
        if value is None:
            raise RegistrationError("ownership", "A configuração anterior não tem propriedade comprovada.")
        selected = manifest_path
    return selected.parent if selected is not None else None


def plan_registration(host_executable: Path, extension_id: str, manifest_directory: Path | None = None,
                      previous_manifest_directory: Path | None = None, *,
                      host_source: Path | None = None,
                      additional_extension_ids: tuple[str, ...] = (),
                      _backend: RegistryBackend | None = None) -> RegistrationPlan:
    if not isinstance(extension_id, str) or not re.fullmatch(r"[a-p]{32}", extension_id):
        raise RegistrationError("extension", "Informe um ID de extensão com 32 letras de a até p.")
    if (not isinstance(additional_extension_ids, (tuple, list)) or
            any(not isinstance(value, str) or not re.fullmatch(r"[a-p]{32}", value)
                for value in additional_extension_ids)):
        raise RegistrationError("extension", "Os IDs adicionais das lojas devem ter 32 letras de a até p.")
    host = _path(host_executable)
    source = _path(host_source) if host_source is not None else host
    host_state = _read_file(source)
    if host.suffix.lower() != ".exe" or host_state.content is None:
        raise RegistrationError("host", "Escolha o executável LumeNativeHost.exe existente.")
    directory = _path(manifest_directory or host.parent / "BrowserHost")
    current, files = _owned_manifest(directory)
    origins = {"chrome-extension://" + extension_id + "/"}
    origins.update("chrome-extension://" + value + "/" for value in additional_extension_ids)
    if current:
        origins.update(current["allowed_origins"])
    previous_manifest = None
    previous_dir = None
    if previous_manifest_directory is not None:
        previous_dir = _path(previous_manifest_directory)
        previous, previous_files = _owned_manifest(previous_dir)
        if previous is None:
            raise RegistrationError("ownership", "A configuração anterior não tem propriedade comprovada; migração recusada.")
        previous_manifest = previous_dir / MANIFEST_NAME
        origins.update(previous["allowed_origins"])
        if previous_dir != directory:
            files += previous_files
    backend = _backend or WindowsRegistry()
    registry = tuple((target, backend.read(target)) for target in backend.targets())
    for target, before in registry:
        if before.default_exists:
            aligned = current is not None and _same_path(before.value, directory / MANIFEST_NAME)
            migration = previous_manifest is not None and _same_path(before.value, previous_manifest)
            if before.kind != 1 or not isinstance(before.value, str) or not (aligned or migration):
                raise RegistrationError("foreign", f"Registro HKCU {target.browser} ({target.view} bits) pertence a outra configuração; nada foi gravado.")
        elif before.key_exists and (before.other_values or before.subkeys):
            raise RegistrationError("foreign", f"Registro HKCU {target.browser} ({target.view} bits) tem conteúdo sem propriedade comprovada.")
    manifest = directory / MANIFEST_NAME
    manifest_bytes = _json({"name": HOST_NAME, "description": "OpenDownloader - caixa de entrada local",
                            "path": str(host), "type": "stdio", "allowed_origins": sorted(origins)})
    owner_bytes = _json({"owner": OWNER, "manifest": str(manifest), "sha256": _digest(manifest_bytes)})
    changes = []
    if files[0][1].content != manifest_bytes:
        changes.append("manifest")
    if files[1][1].content != owner_bytes:
        changes.append("owner")
    changes.extend(f"{target.browser}:{target.view}" for target, before in registry
                   if not before.default_exists or not _same_path(before.value, manifest))
    backup = directory / "backups" / ("registration-" + uuid.uuid4().hex) if changes else None
    return RegistrationPlan(host, manifest, directory / OWNER_NAME, extension_id, tuple(sorted(origins)), backup,
                            tuple(changes), _digest(host_state.content), files, registry,
                            manifest_bytes, owner_bytes, backend, source, previous_dir)


def _assert_file(path, expected):
    if _read_file(_path(path)) != expected:
        raise RegistrationError("changed", "A configuração mudou após o planejamento; preserve os dados e gere um novo plano.")


def _atomic_write(path, content, *, prepared=None):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        info = temporary.stat()
        state = FileState(content, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
        if prepared is not None:
            prepared(state)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _backup(directory, files, registry):
    _path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for path, before in files:
        if before.content is not None:
            # Names are unique for current manifest/owner; previous state is
            # retained with its source path in files-before.json.
            name = _digest(str(path).encode("utf-8"))[:12] + "-" + path.name
            _atomic_write(directory / name, before.content)
    _atomic_write(directory / "files-before.json", _json([
        {"path": str(path), "exists": before.content is not None,
         "sha256": _digest(before.content) if before.content is not None else None,
         "identity": before.identity} for path, before in files]))
    _atomic_write(directory / "registry-before.json", _json([
        {"browser": target.browser, "view": target.view, "path": target.path, "state": _state_json(before)}
        for target, before in registry]))


def _rollback(backend, mutations):
    pending = []
    for kind, target, before, after in reversed(mutations):
        try:
            if kind == "file":
                current = _read_file(target)
                if current == before or current.content == before.content:
                    continue
                if current != after:
                    pending.append(str(target))
                    continue
                if before.content is None:
                    target.unlink()
                else:
                    _atomic_write(target, before.content)
            else:
                current = backend.read(target)
                if (current.default_exists, current.value, current.kind) == (before.default_exists, before.value, before.kind):
                    continue
                # Restore only our exact field; preserve unrelated values that
                # may have been added by another program while we worked.
                if (current.default_exists, current.value, current.kind) != (after.default_exists, after.value, after.kind):
                    pending.append(f"{target.browser}:{target.view}")
                    continue
                if before.default_exists:
                    backend.write(target, before.value, before.kind)
                else:
                    backend.delete_default(target)
                    if not before.key_exists:
                        backend.delete_empty_key(target)
        except Exception:
            pending.append(str(target) if kind == "file" else f"{target.browser}:{target.view}")
    return tuple(pending)


def _our_aliased_write(target, current, mutations):
    # HKCU Software keys can be shared between WOW64 views. Detect the result
    # of our own previous write instead of treating that alias as a third party.
    return any(kind == "registry" and prior.browser == target.browser and prior.path == target.path
               and after == current for kind, prior, _before, after in mutations)


def apply_registration(plan: RegistrationPlan) -> RegistrationReceipt:
    backend = plan._backend
    for path, before in plan._files:
        _assert_file(path, before)
    if _digest(_read_file(_path(plan.host_executable)).content or b"") != plan._host_digest:
        raise RegistrationError("changed", "O executável mudou após o planejamento; gere um novo plano.")
    for target, before in plan._registry:
        if backend.read(target) != before:
            raise RegistrationError("changed", "O registro mudou após o planejamento; nenhuma alteração feita.")
    if not plan.changed:
        return RegistrationReceipt("unchanged", False, plan.manifest)
    mutations = []
    try:
        _backup(plan.backup_directory, plan._files, plan._registry)
        for path, content in ((plan.manifest, plan._manifest_bytes), (plan.owner_file, plan._owner_bytes)):
            before = next(state for candidate, state in plan._files if candidate == path)
            if before.content == content:
                continue
            _assert_file(path, before)
            _atomic_write(path, content, prepared=lambda after, target=path, old=before:
                          mutations.append(("file", target, old, after)))
            after = _read_file(path)
            if after.content != content:
                raise RegistrationError("readback", "A leitura do arquivo gravado divergiu do plano.")
        for target, before in plan._registry:
            if before.default_exists and _same_path(before.value, plan.manifest):
                continue
            current = backend.read(target)
            if current != before and _our_aliased_write(target, current, mutations):
                continue
            if current != before:
                raise RegistrationError("changed", "O registro mudou durante a instalação.")
            after = RegistryState(True, True, str(plan.manifest), 1, before.other_values, before.subkeys)
            mutations.append(("registry", target, before, after))
            backend.write(target, str(plan.manifest), 1)
            if backend.read(target) != after:
                raise RegistrationError("readback", "A leitura do registro gravado divergiu do plano.")
        # Read back all target values and exact owned files after the full write.
        _owned_manifest(plan.manifest.parent, expected_host=plan.host_executable)
        if (_read_file(plan.manifest).content != plan._manifest_bytes
                or _read_file(plan.owner_file).content != plan._owner_bytes):
            raise RegistrationError("readback", "Os arquivos mudaram durante a verificação final.")
        for target, _ in plan._registry:
            state = backend.read(target)
            if not state.default_exists or state.kind != 1 or not _same_path(state.value, plan.manifest):
                raise RegistrationError("readback", "A configuração mudou durante a verificação final.")
    except Exception as error:
        pending = _rollback(backend, mutations)
        receipt = RegistrationReceipt("failed", False, plan.manifest, plan.backup_directory,
                                      plan.changes, rollback_pending=pending,
                                      _mutations=tuple(mutations), _backend=backend)
        raise RegistrationError("apply", "Não foi possível concluir a integração. O backup foi preservado; alterações concorrentes não foram substituídas.", receipt) from error
    return RegistrationReceipt("applied", True, plan.manifest, plan.backup_directory, plan.changes,
                               _mutations=tuple(mutations), _backend=backend)


def _receipt_from_dict(value: dict, backend) -> RegistrationReceipt:
    """Decode only the exact owned journal schema used by the local installer."""
    if value.get("owner") != OWNER:
        raise RegistrationError("receipt", "Recibo de integração sem proprietário compatível.")
    if not isinstance(value.get("manifest"), str):
        raise RegistrationError("receipt", "O recibo não identifica o manifesto.")
    manifest = _path(value["manifest"])
    if manifest.name != MANIFEST_NAME:
        raise RegistrationError("receipt", "Recibo aponta para um manifesto desconhecido.")
    allowed_files = {manifest, manifest.parent / OWNER_NAME}
    targets = set(backend.targets())
    mutations = []
    def file_state(raw):
        encoded, identity = raw["content_base64"], raw["identity"]
        if encoded is not None and not isinstance(encoded, str):
            raise ValueError("Invalid journal content")
        if encoded is not None and len(encoded) > 2 * 1024 * 1024:
            raise ValueError("Registration journal exceeds resource budget")
        if identity is not None and (not isinstance(identity, list) or len(identity) != 4
                                     or any(type(part) is not int for part in identity)):
            raise ValueError("Invalid journal identity")
        return FileState(base64.b64decode(encoded, validate=True) if encoded is not None else None,
                         tuple(identity) if identity is not None else None)
    def registry_state(raw):
        def decode(item):
            return base64.b64decode(item["base64"], validate=True) if isinstance(item, dict) else item
        if type(raw["key_exists"]) is not bool or type(raw["default_exists"]) is not bool:
            raise ValueError("Invalid registry journal state")
        if (raw["default_exists"] and (raw["kind"] != 1 or not isinstance(raw["value"], str))) or (
                not raw["default_exists"] and (raw["kind"] is not None or raw["value"] is not None)):
            raise ValueError("Invalid owned registry value")
        return RegistryState(raw["key_exists"], raw["default_exists"], decode(raw["value"]), raw["kind"],
                             tuple((name, decode(content), kind) for name, content, kind in raw["other_values"]),
                             tuple(raw["subkeys"]))
    try:
        for row in value["rollback"]:
            kind = row["kind"]
            if kind == "file":
                target = _path(row["target"])
                if target not in allowed_files:
                    raise ValueError("Foreign journal file")
                before, after = file_state(row["before"]), file_state(row["after"])
            elif kind == "registry":
                target = RegistryTarget(**row["target"])
                if target not in targets:
                    raise ValueError("Foreign registry journal target")
                before, after = registry_state(row["before"]), registry_state(row["after"])
            else:
                raise ValueError("Unknown journal operation")
            mutations.append((kind, target, before, after))
    except (KeyError, ValueError, TypeError) as error:
        raise RegistrationError("receipt", "Recibo de integração inválido; nenhuma alteração feita.") from error
    return RegistrationReceipt(value.get("status", "applied"), bool(value.get("applied")), manifest,
                               _path(value["backup_directory"]) if value.get("backup_directory") else None,
                               tuple(value.get("changes", ())), tuple(value.get("preserved", ())),
                               tuple(value.get("rollback_pending", ())), tuple(mutations), backend)


def rollback_registration(receipt: RegistrationReceipt | dict, *,
                          _backend: RegistryBackend | None = None) -> RegistrationReceipt:
    if isinstance(receipt, dict):
        receipt = _receipt_from_dict(receipt, _backend or WindowsRegistry())
    if not isinstance(receipt, RegistrationReceipt):
        raise TypeError("rollback_registration requires a registration receipt or its serialized dictionary")
    pending = _rollback(receipt._backend, receipt._mutations)
    return RegistrationReceipt("partial" if pending else "rolled_back", False, receipt.manifest,
                               receipt.backup_directory, receipt.changes, receipt.preserved, pending,
                               receipt._mutations, receipt._backend)


def unregister_owned(host_executable: Path, manifest_directory: Path, *,
                     _backend: RegistryBackend | None = None) -> RegistrationReceipt:
    host, directory = _path(host_executable), _path(manifest_directory)
    manifest, files = _owned_manifest(directory, expected_host=host)
    if manifest is None:
        return RegistrationReceipt("unchanged", False, directory / MANIFEST_NAME)
    path = directory / MANIFEST_NAME
    backend = _backend or WindowsRegistry()
    registry = tuple((target, backend.read(target)) for target in backend.targets())
    owned = [(target, state) for target, state in registry
             if state.default_exists and state.kind == 1 and _same_path(state.value, path)]
    preserved = [f"{target.browser}:{target.view}" for target, state in registry
                 if state.key_exists and (target, state) not in owned]
    uncertain_reference = any(state.default_exists and _same_path(state.value, path) and state.kind != 1
                              for _, state in registry)
    backup = directory / "backups" / ("unregister-" + uuid.uuid4().hex)
    mutations = []
    try:
        for candidate, before in files:
            _assert_file(candidate, before)
        _backup(backup, files, registry)
        for target, before in owned:
            current = backend.read(target)
            if current != before and _our_aliased_write(target, current, mutations):
                continue
            if current != before:
                raise RegistrationError("changed", "O registro mudou antes da remoção.")
            after = RegistryState(True, False, None, None, before.other_values, before.subkeys)
            mutations.append(("registry", target, before, after))
            backend.delete_default(target)
            current = backend.read(target)
            if current.default_exists:
                raise RegistrationError("readback", "O valor de registro não foi removido.")
        for target, before in registry:
            current = backend.read(target)
            if current != before and not _our_aliased_write(target, current, mutations):
                raise RegistrationError("changed", "O registro mudou durante a remoção; arquivos preservados.")
        if not uncertain_reference:
            for candidate, before in files:
                _assert_file(candidate, before)
                mutations.append(("file", candidate, before, FileState()))
                candidate.unlink()
        else:
            preserved += [str(candidate) for candidate, _ in files]
        for target, before in owned:
            if not before.key_exists or (not before.other_values and not before.subkeys):
                backend.delete_empty_key(target)
        for target, _before in owned:
            if backend.read(target).default_exists:
                raise RegistrationError("changed", "O registro foi alterado durante a verificação da remoção.")
    except Exception as error:
        pending = _rollback(backend, mutations)
        receipt = RegistrationReceipt("failed", False, path, backup, preserved=tuple(preserved), rollback_pending=pending)
        raise RegistrationError("unregister", "A remoção não concluiu; backup e dados de terceiros foram preservados.", receipt) from error
    return RegistrationReceipt("partial" if uncertain_reference else "removed", True, path, backup,
                               tuple(f"{target.browser}:{target.view}" for target, _ in owned), tuple(preserved),
                               _mutations=tuple(mutations), _backend=backend)
