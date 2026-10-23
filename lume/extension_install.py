"""Owned HKCU external-extension announcements, separate from Native Messaging.

An announcement requests the browser's normal consent flow; it does not grant
consent, install policy, or prove browser acceptance. Inputs are parsed store
distribution facts. The journal retains unresolved ownership after failures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Iterable, Protocol
import uuid

from .distribution import PublishedExtension, CHROME_UPDATE_URL, EDGE_UPDATE_URL

OWNER = "opendownloader-external-extension-v1"
VERSION = 1
UPDATE_URLS = {
    "chrome": CHROME_UPDATE_URL,
    "edge": EDGE_UPDATE_URL,
}
BASES = {"chrome": r"Software\Google\Chrome\Extensions",
         "edge": r"Software\Microsoft\Edge\Extensions"}
MAX_RECEIPT = 1024 * 1024


class ExtensionInstallError(RuntimeError):
    def __init__(self, code, message, receipt=None):
        super().__init__(message)
        self.code, self.receipt = code, receipt


@dataclass(frozen=True)
class RegistryTarget:
    browser: str
    extension_id: str
    path: str


@dataclass(frozen=True)
class RegistryState:
    key_exists: bool = False
    values: tuple[tuple[str, object, int], ...] = ()
    subkeys: tuple[str, ...] = ()


class RegistryBackend(Protocol):
    def target(self, browser: str, extension_id: str) -> RegistryTarget: ...
    def read(self, target: RegistryTarget) -> RegistryState: ...
    def write(self, target: RegistryTarget, update_url: str) -> None: ...
    def remove(self, target: RegistryTarget) -> None: ...


class WindowsRegistry:
    """HKCU only; neither policies nor browser profiles are touched.

    These HKCU Software keys are shared across Windows registry views. One
    explicit 64-bit view avoids treating aliased writes as separate ownership.
    """
    def __init__(self, *, namespace: str | None = None):
        if os.name != "nt":
            raise ExtensionInstallError("platform", "A integração exige Windows.")
        if namespace is not None and not re.fullmatch(
                r"Software\\OpenDownloaderExtensionTests\\[0-9a-f]{32}", namespace):
            raise ValueError("Private extension test namespace required")
        self.namespace = namespace

    def target(self, browser, extension_id):
        _identity(browser, extension_id)
        base = self.namespace + "\\" + browser if self.namespace else BASES[browser]
        return RegistryTarget(browser, extension_id, base + "\\" + extension_id)

    def read(self, target):
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, target.path, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            return RegistryState()
        with key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
            if values > 32 or subkeys > 32:
                raise ExtensionInstallError("capacity", "Registro excede o limite de inspeção; preservado.")
            return RegistryState(True,
                tuple(sorted((winreg.EnumValue(key, i) for i in range(values)), key=lambda row: row[0])),
                tuple(sorted(winreg.EnumKey(key, i) for i in range(subkeys))))

    def write(self, target, update_url):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, target.path, 0,
                               winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, "update_url", 0, winreg.REG_SZ, update_url)

    def remove(self, target):
        import winreg
        # Delete only our field; a newly added unrelated value is never erased.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, target.path, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                try:
                    winreg.DeleteValue(key, "update_url")
                except FileNotFoundError:
                    pass
            current = self.read(target)
            if current.key_exists and not current.values and not current.subkeys:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, target.path, winreg.KEY_WOW64_64KEY, 0)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class ExtensionInstallPlan:
    receipt_file: Path
    changes: tuple[dict, ...]
    preserved: tuple[str, ...]
    pending: tuple[str, ...]
    _owned: tuple[dict, ...] = field(repr=False)
    _previous_owned: tuple[dict, ...] = field(repr=False)
    _before: bytes | None = field(repr=False)
    _backend: RegistryBackend = field(repr=False, compare=False)

    @property
    def changed(self):
        return bool(self.changes)

    def to_dict(self):
        return {"owner": OWNER, "version": VERSION, "receipt_file": str(self.receipt_file),
                "changed": self.changed, "changes": list(self.changes),
                "preserved": list(self.preserved), "pending": list(self.pending),
                "browser_acceptance": "unverified", "requires_browser_consent": True}


@dataclass(frozen=True)
class ExtensionInstallReceipt:
    receipt_file: Path
    status: str
    applied: bool
    preserved: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    backup_directory: Path | None = None
    _document: dict = field(default_factory=dict, repr=False, compare=False)
    _backend: RegistryBackend | None = field(default=None, repr=False, compare=False)

    @property
    def rollback_pending(self):
        return self.pending

    def to_dict(self):
        return {**self._document, "receipt_file": str(self.receipt_file),
                "status": self.status, "applied": self.applied,
                "preserved": list(self.preserved), "pending": list(self.pending),
                "backup_directory": str(self.backup_directory) if self.backup_directory else None,
                "browser_acceptance": "unverified", "requires_browser_consent": True}


def _identity(browser, extension_id):
    if browser not in BASES or not isinstance(extension_id, str) or not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ExtensionInstallError("identity", "Navegador ou ID de extensão inválido.")


def _label(target):
    return target.browser + ":" + target.extension_id


def _state_json(state):
    # Owned states are deliberately tiny: an absent key, or our sole string.
    return {"key_exists": state.key_exists, "values": [list(row) for row in state.values],
            "subkeys": list(state.subkeys)}


def _state(raw, *, browser=None, owned=False):
    if not isinstance(raw, dict) or set(raw) != {"key_exists", "values", "subkeys"}:
        raise ValueError("Invalid state")
    if type(raw["key_exists"]) is not bool or not isinstance(raw["values"], list) or raw["subkeys"] != []:
        raise ValueError("Invalid state")
    absent = {"key_exists": False, "values": [], "subkeys": []}
    expected = {"key_exists": True, "values": [["update_url", UPDATE_URLS.get(browser), 1]], "subkeys": []}
    if raw != absent and raw != expected:
        raise ValueError("Foreign state in ownership journal")
    if owned and raw != expected:
        raise ValueError("Missing owned state")
    return RegistryState(raw["key_exists"], tuple(tuple(row) for row in raw["values"]), ())


def _path(value):
    path = Path(os.path.abspath(value))
    for part in (*reversed(path.parents), path):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ExtensionInstallError("ownership", "Recibo não pode atravessar links.")
    return path


def _read(path):
    _path(path)
    try:
        if path.stat().st_size > MAX_RECEIPT:
            raise ExtensionInstallError("capacity", "Recibo excede o limite de tamanho.")
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _encode(document):
    data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_RECEIPT:
        raise ExtensionInstallError("capacity", "Recibo excede o limite de tamanho.")
    return data


def _save(path, document, expected):
    data = _encode(document)
    if _read(path) != expected:
        raise ExtensionInstallError("changed", "Recibo mudou; configuração preservada.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path) != expected:
            raise ExtensionInstallError("changed", "Recibo mudou durante a gravação.")
        os.replace(temporary, path)
        if _read(path) != data:
            raise ExtensionInstallError("readback", "A leitura do recibo divergiu.")
        return data
    finally:
        temporary.unlink(missing_ok=True)


def _decode(data, backend):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate journal key")
            result[key] = value
        return result
    try:
        doc = json.loads(data, object_pairs_hook=unique)
        if doc.get("owner") != OWNER or type(doc.get("version")) is not int or doc["version"] != VERSION:
            raise ValueError("Unknown owner")
        if doc.get("status") not in {"ready", "applying", "recovery_pending", "removed", "rolled_back"}:
            raise ValueError("Unknown status")
        for name in ("owned", "previous_owned", "operations"):
            rows = doc[name]
            if not isinstance(rows, list) or len(rows) > 8:
                raise ValueError("Invalid target budget")
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"target", "before", "after"}:
                    raise ValueError("Invalid journal row")
                target = RegistryTarget(**row["target"])
                if target != backend.target(target.browser, target.extension_id) or target in seen:
                    raise ValueError("Foreign or duplicate target")
                seen.add(target)
                _state(row["before"], browser=target.browser)
                _state(row["after"], browser=target.browser, owned=name != "operations")
                if name != "operations" and _state(row["before"]).key_exists:
                    raise ValueError("Foreign original state")
                if name == "operations" and row["before"] == row["after"]:
                    raise ValueError("Empty operation")
        if any(not isinstance(doc.get(name), list) or len(doc[name]) > 16 or
               any(not isinstance(item, str) or len(item) > 256 for item in doc[name])
               for name in ("preserved", "pending")):
            raise ValueError("Invalid labels")
        return doc
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ExtensionInstallError("receipt", "Recibo inválido; nenhuma alteração autorizada.") from error


def _row(target, before, after):
    return {"target": {"browser": target.browser, "extension_id": target.extension_id, "path": target.path},
            "before": _state_json(before), "after": _state_json(after)}


def _target(row):
    return RegistryTarget(**row["target"])


def plan_extension_install(entries: Iterable[PublishedExtension], receipt_file: Path, *,
                           _backend: RegistryBackend | None = None) -> ExtensionInstallPlan:
    backend = _backend or WindowsRegistry()
    path = _path(receipt_file)
    before = _read(path)
    prior = _decode(before, backend) if before is not None else None
    if prior and prior["status"] in {"applying", "recovery_pending"}:
        raise ExtensionInstallError("recovery_pending", "Finalize a recuperação do anúncio anterior.")
    old = tuple(prior["owned"]) if prior else ()
    desired = {}
    for entry in entries:
        if not isinstance(entry, PublishedExtension):
            raise TypeError("Only parsed PublishedExtension facts are accepted")
        _identity(entry.browser, entry.extension_id)
        if entry.update_url != UPDATE_URLS[entry.browser] or entry.browser in desired:
            raise ExtensionInstallError("distribution", "Canal de extensão inválido ou repetido.")
        desired[entry.browser] = entry
    if len(desired) > 2:
        raise ExtensionInstallError("capacity", "No máximo um anúncio por navegador.")
    previous = {_target(row): row for row in old}
    changes, owned, preserved, pending = [], [], [], []
    wanted = set()
    for entry in desired.values():
        target = backend.target(entry.browser, entry.extension_id)
        wanted.add(target)
        current = backend.read(target)
        after = RegistryState(True, (("update_url", entry.update_url, 1),))
        old_row = previous.get(target)
        if old_row and current == _state(old_row["after"], browser=target.browser):
            owned.append(old_row)
        elif not current.key_exists:
            new = _row(target, current, after)
            changes.append(new)
            owned.append(new)
        else:
            preserved.append(_label(target))
            if old_row:
                owned.append(old_row)
                pending.append(_label(target))
    for target, row in previous.items():
        if target in wanted:
            continue
        current = backend.read(target)
        if current == _state(row["after"], browser=target.browser):
            changes.append(_row(target, current, RegistryState()))
        elif current != _state(row["before"], browser=target.browser):
            owned.append(row)
            preserved.append(_label(target))
            pending.append(_label(target))
    return ExtensionInstallPlan(path, tuple(changes), tuple(preserved), tuple(pending),
                                tuple(owned), old, before, backend)


def _receipt(path, doc, backend, *, applied=False, backup=None):
    return ExtensionInstallReceipt(path, doc["status"], applied, tuple(doc["preserved"]),
                                   tuple(doc["pending"]), backup, doc, backend)


def _document(owned, previous, operations, status, preserved=(), pending=()):
    return {"owner": OWNER, "version": VERSION, "status": status, "owned": list(owned),
            "previous_owned": list(previous), "operations": list(operations),
            "preserved": list(preserved), "pending": list(pending)}


def _restore(backend, operations):
    pending = []
    for row in reversed(operations):
        target = _target(row)
        before = _state(row["before"], browser=target.browser)
        after = _state(row["after"], browser=target.browser)
        try:
            current = backend.read(target)
            if current == before:
                continue
            if current != after:
                pending.append(_label(target))
                continue
            if before.key_exists:
                backend.write(target, UPDATE_URLS[target.browser])
            else:
                backend.remove(target)
            if backend.read(target) != before:
                pending.append(_label(target))
        except Exception:
            pending.append(_label(target))
    return pending


def apply_extension_install(plan: ExtensionInstallPlan) -> ExtensionInstallReceipt:
    backend, path = plan._backend, plan.receipt_file
    if _read(path) != plan._before:
        raise ExtensionInstallError("changed", "Recibo mudou após o planejamento.")
    for row in plan.changes:
        target = _target(row)
        if backend.read(target) != _state(row["before"], browser=target.browser):
            raise ExtensionInstallError("changed", "Anúncio mudou após o planejamento.")
    if not plan.changed and plan._owned == plan._previous_owned and not plan.pending:
        doc = _decode(plan._before, backend) if plan._before else _document((), (), (), "ready", plan.preserved)
        doc = {**doc, "preserved": list(plan.preserved)}
        return _receipt(path, doc, backend)
    backup = path.parent / ("extension-backup-" + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    (backup / "registry-before.json").write_bytes(_encode(list(plan.changes)))
    if plan._before is not None:
        (backup / "receipt-before.json").write_bytes(plan._before)
    # Intent is durable before registry mutation; failed cleanup keeps its owner.
    all_owned = {_target(row): row for row in (*plan._previous_owned, *plan._owned)}
    doc = _document(all_owned.values(), plan._previous_owned, plan.changes, "applying",
                    plan.preserved, plan.pending)
    expected = _save(path, doc, plan._before)
    attempted = []
    try:
        for row in plan.changes:
            target = _target(row)
            if backend.read(target) != _state(row["before"], browser=target.browser):
                raise ExtensionInstallError("changed", "Anúncio mudou durante a configuração.")
            attempted.append(row)
            after = _state(row["after"], browser=target.browser)
            if after.key_exists:
                backend.write(target, UPDATE_URLS[target.browser])
            else:
                backend.remove(target)
            if backend.read(target) != after:
                raise ExtensionInstallError("readback", "A leitura do anúncio divergiu.")
        # Catch a late change to an earlier announcement before success.
        for row in plan._owned:
            target = _target(row)
            if backend.read(target) != _state(row["after"], browser=target.browser) and _label(target) not in plan.pending:
                raise ExtensionInstallError("readback", "Anúncio mudou na verificação final.")
        doc["owned"] = list(plan._owned)
        doc["status"] = "recovery_pending" if plan.pending else "ready"
        _save(path, doc, expected)
    except Exception as error:
        pending = _restore(backend, attempted)
        doc["owned"] = list(all_owned.values())
        doc["status"] = "recovery_pending" if pending else "rolled_back"
        doc["pending"] = pending
        if not pending:
            doc["owned"] = list(plan._previous_owned)
        try:
            _save(path, doc, expected)
        except Exception:
            # Original durable intent still names all potentially owned targets.
            pending = list(dict.fromkeys((*pending, "receipt")))
        receipt = _receipt(path, doc, backend, backup=backup)
        if tuple(pending) != receipt.pending:
            receipt = ExtensionInstallReceipt(path, "recovery_pending", False, receipt.preserved,
                tuple(pending), backup, doc, backend)
        raise ExtensionInstallError("apply", "Não foi possível concluir os anúncios; recuperação preservada.", receipt) from error
    return _receipt(path, doc, backend, applied=bool(plan.changes), backup=backup)


def rollback_extension_install(receipt: ExtensionInstallReceipt, *,
                               _backend: RegistryBackend | None = None) -> ExtensionInstallReceipt:
    backend = _backend or receipt._backend or WindowsRegistry()
    path = _path(receipt.receipt_file)
    before = _read(path)
    if before is None:
        raise ExtensionInstallError("receipt", "Recibo de anúncio ausente.")
    doc = _decode(before, backend)
    # A stale receipt may not roll back a newer successful operation.
    if doc != receipt._document:
        raise ExtensionInstallError("changed", "Recibo mudou; gere uma nova operação.")
    pending = _restore(backend, doc["operations"])
    doc["pending"] = pending
    doc["status"] = "recovery_pending" if pending else "rolled_back"
    if not pending:
        doc["owned"] = doc["previous_owned"]
    _save(path, doc, before)
    return _receipt(path, doc, backend, backup=receipt.backup_directory)


def remove_owned_extensions(receipt_file: Path, *,
                            _backend: RegistryBackend | None = None) -> ExtensionInstallReceipt:
    backend, path = _backend or WindowsRegistry(), _path(receipt_file)
    before = _read(path)
    if before is None:
        return _receipt(path, _document((), (), (), "removed"), backend)
    doc = _decode(before, backend)
    pending = _restore(backend, doc["owned"])
    doc["pending"], doc["status"] = pending, "recovery_pending" if pending else "removed"
    doc["owned"] = [row for row in doc["owned"] if _label(_target(row)) in pending]
    # Keep operation records on failure so retry and explicit rollback retain proof.
    _save(path, doc, before)
    return _receipt(path, doc, backend, applied=True)
