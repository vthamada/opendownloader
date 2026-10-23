"""Local, per-user deployment from an already extracted installer payload.

The installer owns its extracted source. This module owns app/ and receipts,
never queue data or Downloads. Hashes prove selected bytes, not publisher trust.
"""
from __future__ import annotations

import base64
import copy
import ctypes
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

from . import VERSION, APP_NAME
from .updates import MAX_ARCHIVE_ENTRIES, MAX_EXPANDED_BYTES, CHUNK_BYTES, _component

OWNER = "installation.json"
OWNER_ID = "lume-installation-v1"
MAX_PATH = 1024
MAX_OWNER_BYTES = MAX_ARCHIVE_ENTRIES * (MAX_PATH + 256) + 65536


def installation_paths(root: Path) -> dict[str, Path]:
    root=Path(os.path.abspath(Path(root).expanduser()))
    return {"directory":root,"app_directory":root/"app","native_directory":root/"BrowserHost",
            "uninstaller":root/"Uninstall.exe","owner":root/OWNER}


@contextmanager
def _deployment_lock(root):
    root=_plain(root,directory=True,absent=True).resolve()
    if os.name!="nt":
        yield
        return
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    for method in ("WaitForSingleObject","ReleaseMutex","CloseHandle"):
        getattr(kernel,method).argtypes=[ctypes.c_void_p,ctypes.c_uint32] if method=="WaitForSingleObject" else [ctypes.c_void_p]
    name="Local\\LumeDeployment-"+hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
    handle=kernel.CreateMutexW(None,0,name)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    acquired=False
    try:
        status=kernel.WaitForSingleObject(handle,0)
        if status not in (0,128):raise DeploymentError("Outra instalação ou remoção está em andamento para esta pasta.")
        acquired=True
        yield
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class DeploymentError(ValueError):
    def __init__(self, message, *, recovery=None):
        super().__init__(message)
        self.recovery = recovery or {}


def _plain(path: Path, *, directory=None, absent=False):
    """Inspect lexical ancestors before resolving: reparse points are not authority."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DeploymentError(f"Links e pontos de reanálise não são permitidos: {current}")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise DeploymentError(f"O caminho contém um ancestral que não é pasta: {current}")
    if not path.exists():
        if absent:
            return path
        raise DeploymentError(f"Caminho ausente: {path}")
    if directory is True and not path.is_dir() or directory is False and not path.is_file():
        raise DeploymentError(f"Tipo de arquivo inesperado: {path}")
    return path


def _relative(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_PATH or "\\" in value:
        raise DeploymentError("Caminho de arquivo inválido no recibo.")
    parts = PurePosixPath(value).parts
    if not parts or PurePosixPath(value).is_absolute() or PurePosixPath(value).as_posix() != value:
        raise DeploymentError("Caminho relativo ambíguo no recibo.")
    for part in parts:
        try: _component(part)
        except ValueError as error: raise DeploymentError(str(error)) from error
    return parts


def _file(path):
    path = _plain(path, directory=False)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EXPANDED_BYTES:
        raise DeploymentError(f"Arquivo não regular ou fora dos limites: {path}")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise DeploymentError(f"Arquivo alterado ao abrir: {path}")
        while chunk := source.read(CHUNK_BYTES):
            total += len(chunk)
            if total > before.st_size:
                raise DeploymentError(f"Arquivo cresceu durante leitura: {path}")
            digest.update(chunk)
    if total != before.st_size:
        raise DeploymentError(f"Arquivo mudou durante leitura: {path}")
    return {"size": total, "sha256": digest.hexdigest()}


def bundle_inventory(root):
    """Bounded, format-checked file facts shared by verification and deployment."""
    root = _plain(root, directory=True)
    result = {}; total = 0; seen = set(); pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            _plain(path)
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            folded = relative.casefold()
            if folded in seen:
                raise DeploymentError("Colisão de nomes no payload.")
            seen.add(folded)
            if len(seen) > MAX_ARCHIVE_ENTRIES:
                raise DeploymentError("O payload excede o limite de entradas.")
            if path.is_dir():
                pending.append(path)
            else:
                value = _file(path); total += value["size"]
                if total > MAX_EXPANDED_BYTES:
                    raise DeploymentError("O payload excede 1 GiB expandido.")
                result[relative] = value
    return result


def _read_json(path):
    _plain(path, directory=False)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise DeploymentError("Propriedade duplicada no recibo.")
            result[key] = value
        return result
    try:
        with path.open("rb") as source: raw = source.read(MAX_OWNER_BYTES+1)
        if len(raw) > MAX_OWNER_BYTES: raise DeploymentError("Recibo excessivamente grande.")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(DeploymentError("Constante JSON inválida.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise DeploymentError("Recibo de instalação inválido; conteúdo preservado.") from error


def _owner(root):
    value = _read_json(root/OWNER)
    if (not isinstance(value, dict) or value.get("id") != OWNER_ID
            or value.get("directory") != str(root) or value.get("state") not in ("installed", "uninstalled")
            or not isinstance(value.get("instance"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["instance"])
            or not isinstance(value.get("extension_id"), str) or not re.fullmatch(r"[a-p]{32}", value["extension_id"])
            or not isinstance(value.get("files"), dict) or len(value["files"]) > MAX_ARCHIVE_ENTRIES
            or not isinstance(value.get("integration"), dict)):
        raise DeploymentError("A pasta não possui um recibo de instalação reconhecido.")
    for name, facts in [*value["files"].items(), ("Uninstall.exe", value.get("uninstaller"))]:
        _relative(name)
        if (not isinstance(facts, dict) or set(facts) != {"size", "sha256"}
                or type(facts["size"]) is not int or not 0 <= facts["size"] <= MAX_EXPANDED_BYTES
                or not isinstance(facts["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", facts["sha256"])):
            raise DeploymentError("Inventário do recibo inválido.")
    if not {"LumeDownloads.exe", "LumeNativeHost.exe"} <= value["files"].keys():
        raise DeploymentError("Executáveis principais ausentes do inventário.")
    if sum(facts["size"] for facts in value["files"].values())>MAX_EXPANDED_BYTES:
        raise DeploymentError("O inventário excede o orçamento de arquivos da instalação.")
    return value


def _matches(path, facts):
    try: return _file(path) == facts
    except (DeploymentError, OSError): return False


def _validate_existing(root, owner):
    app=_plain(root/"app",directory=True,absent=True)
    files=bundle_inventory(app) if app.exists() else {}
    uninstaller=_plain(root/"Uninstall.exe",directory=False,absent=True)
    uninstaller_facts=_file(uninstaller) if uninstaller.exists() else None
    differences={}
    for name, facts in owner["files"].items():
        if files.get(name)!=facts:
            differences["app/"+name]={"state":"missing" if name not in files else "modified",
                                        "expected":facts,"observed":files.get(name)}
    for name in files.keys()-owner["files"].keys():
        differences["app/"+name]={"state":"unknown_preserved_in_backup","observed":files[name]}
    if uninstaller_facts!=owner["uninstaller"]:
        differences["Uninstall.exe"]={"state":"missing" if uninstaller_facts is None else "modified",
                                       "expected":owner["uninstaller"],"observed":uninstaller_facts}
    return files,uninstaller_facts,differences


def _write_json(path, value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(body) > MAX_OWNER_BYTES: raise DeploymentError("Recibo excede o limite.")
    temporary = path.with_name("."+path.name+"."+uuid.uuid4().hex+".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(body); output.flush(); os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source, target, facts):
    _plain(source, directory=False); _plain(target, absent=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest=hashlib.sha256(); total=0
    owned=None
    try:
        with source.open("rb") as src, target.open("xb") as dst:
            info=os.fstat(dst.fileno());owned=(info.st_dev,info.st_ino)
            while chunk := src.read(CHUNK_BYTES):
                total += len(chunk)
                if total > facts["size"]: raise DeploymentError("O payload cresceu durante a cópia.")
                dst.write(chunk);digest.update(chunk)
            dst.flush();os.fsync(dst.fileno())
        if total != facts["size"] or digest.hexdigest() != facts["sha256"] or _file(target) != facts:
            raise DeploymentError(f"O payload mudou desde o plano: {source}")
    except BaseException:
        # Proof of exclusive creation is separate from validity of copied bytes.
        if owned is not None:
            info=target.lstat()
            if (info.st_dev,info.st_ino)==owned and not getattr(info,"st_file_attributes",0)&0x400:
                target.unlink()
        raise


def _remove_files(root, files):
    leftovers=[]
    for name, facts in files.items():
        path=root/name
        if not path.exists() and not path.is_symlink(): continue
        if not _matches(path,facts): leftovers.append(str(path));continue
        try: path.unlink()
        except OSError: leftovers.append(str(path))
    directories={root}
    for name in files:
        path=root/name
        directories.update(parent for parent in path.parents if parent==root or root in parent.parents)
    for directory in sorted(directories,key=lambda p:len(p.parts),reverse=True):
        try: _plain(directory,directory=True);directory.rmdir()
        except (OSError,DeploymentError):pass
    return leftovers


@dataclass(frozen=True)
class InstallPlan:
    source_directory: Path
    destination: Path
    uninstaller: Path
    extension_id: str
    files: dict
    uninstaller_facts: dict
    previous: dict | None
    previous_digest: dict | None
    previous_files: dict | None
    previous_uninstaller: dict | None
    repair_differences: dict
    integration_plan: object
    backend: object = field(repr=False,compare=False)
    def to_dict(self):
        return {"operation":"install", "version":VERSION,"source_directory":str(self.source_directory),
                "destination":str(self.destination),"uninstaller":str(self.uninstaller),
                "extension_id":self.extension_id,"files":copy.deepcopy(self.files),
                "uninstaller_facts":dict(self.uninstaller_facts),"update":self.previous is not None,
                "repair_differences":copy.deepcopy(self.repair_differences),
                "preserves":["queue data","downloaded files","unknown installation files","previous app backup"],
                "integration":_jsonable(self.integration_plan)}


def _jsonable(value):
    if hasattr(value,"to_dict"):return value.to_dict()
    if isinstance(value,dict):return {key:_jsonable(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [_jsonable(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class InstallReceipt:
    data: dict
    def to_dict(self): return copy.deepcopy(self.data)


def plan_install(source_directory: Path, destination: Path, uninstaller: Path, *, extension_id: str,
                 backend=None) -> InstallPlan:
    if not isinstance(extension_id,str) or not re.fullmatch(r"[a-p]{32}",extension_id):
        raise DeploymentError("Identidade da extensão inválida.")
    source=_plain(source_directory,directory=True);destination=_plain(destination,directory=True,absent=True)
    uninstaller=_plain(uninstaller,directory=False)
    if source==destination or source in destination.parents or destination in source.parents:
        raise DeploymentError("Payload e instalação devem usar pastas independentes.")
    if destination==destination.parent:
        raise DeploymentError("A raiz do volume não é destino de instalação.")
    files=bundle_inventory(source)
    if not {"LumeDownloads.exe","LumeNativeHost.exe"} <= files.keys() or not (source/"_internal").is_dir():
        raise DeploymentError("O payload não contém o aplicativo e seu runtime.")
    previous=None;previous_digest=None;previous_files=None;previous_uninstaller=None;differences={}
    if destination.exists() and any(destination.iterdir()):
        previous=_owner(destination)
        previous_files,previous_uninstaller,differences=_validate_existing(destination,previous)
        previous_digest=_file(destination/OWNER)
    backend=backend if backend is not None else WindowsBackend()
    integration=backend.plan(destination,extension_id,previous,host_source=source/"LumeNativeHost.exe") if isinstance(backend,WindowsBackend) else backend.plan(destination,extension_id,previous)
    return InstallPlan(source,destination,uninstaller,extension_id,files,_file(uninstaller),previous,
                       previous_digest,previous_files,previous_uninstaller,differences,integration,backend)


def install(plan: InstallPlan) -> InstallReceipt:
    if not isinstance(plan,InstallPlan): raise DeploymentError("Planeje a instalação antes de aplicar.")
    with _deployment_lock(plan.destination):return _install(plan)


def _install(plan):
    root=_plain(plan.destination,directory=True,absent=True)
    if bundle_inventory(plan.source_directory)!=plan.files or _file(plan.uninstaller)!=plan.uninstaller_facts:
        raise DeploymentError("O payload mudou desde o plano.")
    if plan.previous is not None:
        if not _matches(root/OWNER,plan.previous_digest):raise DeploymentError("A instalação mudou desde o plano.")
        current_files,current_uninstaller,_=_validate_existing(root,plan.previous)
        if (current_files,current_uninstaller)!=(plan.previous_files,plan.previous_uninstaller):
            raise DeploymentError("Os arquivos da instalação mudaram desde o plano.")
    elif root.exists() and any(root.iterdir()):raise DeploymentError("O destino foi ocupado desde o plano.")
    root.parent.mkdir(parents=True,exist_ok=True)
    stage=root.parent/("."+root.name+".lume-stage-"+uuid.uuid4().hex)
    stage.mkdir();(stage/"app").mkdir()
    backup=None;new=[];saved=[];integration=None
    owner={"id":OWNER_ID,"instance":uuid.uuid4().hex,"version":VERSION,"directory":str(root),
           "state":"installed","extension_id":plan.extension_id,"files":copy.deepcopy(plan.files),
           "uninstaller":dict(plan.uninstaller_facts),"integration":{}}
    try:
        for name,facts in plan.files.items():_copy(plan.source_directory/name,stage/"app"/name,facts)
        _copy(plan.uninstaller,stage/"Uninstall.exe",plan.uninstaller_facts)
        _write_json(stage/OWNER,owner)
        # Copying can take seconds. Revalidate the destination at commit too.
        _plain(root,directory=True,absent=True)
        if plan.previous is not None:
            if not _matches(root/OWNER,plan.previous_digest):raise DeploymentError("A instalação mudou antes da publicação.")
            current_files,current_uninstaller,_=_validate_existing(root,plan.previous)
            if (current_files,current_uninstaller)!=(plan.previous_files,plan.previous_uninstaller):
                raise DeploymentError("Os arquivos da instalação mudaram durante a preparação.")
        elif root.exists() and any(root.iterdir()):raise DeploymentError("O destino foi ocupado durante a preparação.")
        root.mkdir(exist_ok=True)
        if plan.previous is not None:
            backup=root/(".lume-backup-"+uuid.uuid4().hex);backup.mkdir()
            for name in ("app","Uninstall.exe",OWNER):
                if (root/name).exists():
                    os.rename(root/name,backup/name);saved.append(name)
        for name in ("app","Uninstall.exe",OWNER):
            os.rename(stage/name,root/name);new.append(name)
        integration=plan.backend.apply(plan.integration_plan)
        owner["integration"]=integration.to_dict() if hasattr(integration,"to_dict") else copy.deepcopy(integration)
        _write_json(root/OWNER,owner)
        stage.rmdir()
        return InstallReceipt({"status":"installed","directory":str(root),"version":VERSION,
                               "backup_directory":str(backup) if backup else None,
                               "owner":str(root/OWNER),"integration":owner["integration"]})
    except BaseException as error:
        recovery=[]
        if integration is not None:
            try: plan.backend.rollback(integration)
            except Exception as rollback_error:recovery.append(str(rollback_error))
        if recovery or getattr(error,"recovery",{}):
            # A surviving registry reference still owns its host. Do not erase
            # new bytes or restore another host beneath an unresolved lease.
            record={"id":"lume-installation-recovery-v1","installation":owner,
                    "integration":_jsonable(integration),"cause":str(error),"errors":recovery,
                    "backend_recovery":getattr(error,"recovery",{}),"backup":str(backup) if backup else None}
            recovery_path=root/("installation-recovery-"+uuid.uuid4().hex+".json")
            try:_write_json(recovery_path,record)
            except Exception as write_error:recovery.append("Não foi possível salvar recuperação: "+str(write_error))
            if not isinstance(error,Exception):raise
            raise DeploymentError("A integração não confirmou o rollback. Aplicativo, backup e propriedade foram preservados para recuperação.",
                                  recovery={"pending":recovery,"receipt":str(recovery_path),
                                            "backup":str(backup) if backup else None,"retained_host":True}) from error
        if "app" in new:
            recovery.extend(_remove_files(root/"app",plan.files))
            if (root/"app").exists():recovery.append(str(root/"app"))
        for name in ("Uninstall.exe",OWNER):
            if name in new:
                try:
                    if name=="Uninstall.exe" and not _matches(root/name,plan.uninstaller_facts):
                        raise DeploymentError("Desinstalador novo mudou; preservado.")
                    if name==OWNER and _owner(root)["instance"]!=owner["instance"]:
                        raise DeploymentError("Recibo novo mudou; preservado.")
                    (root/name).unlink()
                except (OSError,DeploymentError) as rollback_error:recovery.append(str(rollback_error))
        for name in saved:
            try:
                if (root/name).exists():raise DeploymentError(f"Restauração bloqueada: {root/name}")
                os.rename(backup/name,root/name)
            except (OSError,DeploymentError) as rollback_error:recovery.append(str(rollback_error))
        # Cleanup only files whose selected hashes still match; no rmtree.
        recovery.extend(_remove_files(stage/"app",plan.files))
        for name in ("Uninstall.exe",OWNER):
            path=stage/name
            try:
                if path.exists():
                    if name==OWNER and _read_json(path).get("instance")==owner["instance"]:path.unlink()
                    elif name=="Uninstall.exe" and _matches(path,plan.uninstaller_facts):path.unlink()
            except (OSError,DeploymentError):recovery.append(str(path))
        for path in (stage,backup,root if plan.previous is None else None):
            if path is not None:
                try:path.rmdir()
                except OSError:pass
        if not isinstance(error,Exception):raise
        raise DeploymentError(f"A instalação não foi aplicada; a versão anterior foi preservada. {error}",
                              recovery={"pending":recovery,"stage":str(stage),
                                        "backup":str(backup) if backup else None}) from error


def uninstall(install_directory: Path, *, backend=None) -> InstallReceipt:
    with _deployment_lock(install_directory):return _uninstall(install_directory,backend=backend)


def _uninstall(install_directory,*,backend=None):
    root=_plain(install_directory,directory=True);owner=_owner(root)
    original=_file(root/OWNER)
    backend=backend if backend is not None else WindowsBackend()
    removed=backend.remove(owner)
    retain_host=isinstance(removed,dict) and removed.get("retain_host",False)
    leftovers=list(removed.get("leftovers",[])) if isinstance(removed,dict) else list(removed)
    if not retain_host:
        leftovers.extend(_remove_files(root/"app",owner["files"]))
        leftovers.extend(_remove_files(root,{"Uninstall.exe":owner["uninstaller"]}))
    for path in root.iterdir():
        if path.name!=OWNER:leftovers.append(str(path))
    leftovers=sorted(set(leftovers))
    if not _matches(root/OWNER,original):
        leftovers.append(str(root/OWNER))
    elif leftovers:
        if not retain_host:owner["state"]="uninstalled";_write_json(root/OWNER,owner)
    else:
        (root/OWNER).unlink()
        try:root.rmdir()
        except OSError:leftovers.append(str(root))
    return InstallReceipt({"status":"recovery_pending" if retain_host else "preserved_leftovers" if leftovers else "uninstalled",
                           "directory":str(root),"leftovers":leftovers,"data_preserved":True,
                           "retained_host":bool(retain_host)})


class WindowsBackend:
    """Owned per-user integration; injectable HKCU/shortcut namespaces for tests."""
    def __init__(self, *, registry_prefix=r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                 programs_directory=None, desktop_directory=None, registration_backend=None,
                 desktop=True):
        if os.name != "nt":raise DeploymentError("Instalação integrada disponível somente no Windows.")
        if registry_prefix!=r"Software\Microsoft\Windows\CurrentVersion\Uninstall" and not registry_prefix.startswith("Software\\LumeDeploymentTests\\"):
            raise DeploymentError("Namespace privado de teste inválido.")
        self.registry_key=registry_prefix.rstrip("\\")+r"\LumeDownloads"
        self.programs=_plain(programs_directory or _known_folder("A77F5D77-2E2B-44C3-A6A2-ABA601054A51"),directory=True,absent=True)
        self.desktop=_plain(desktop_directory or _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),directory=True,absent=True) if desktop else None
        self.registration_backend=registration_backend

    def _registration(self):
        from . import registration
        return registration

    def _rollback_native(self,receipt):
        result=self._registration().rollback_registration(receipt,
            **({"_backend":self.registration_backend} if self.registration_backend is not None else {}))
        if result.rollback_pending:
            raise DeploymentError("A ponte preservou alterações posteriores; recuperação pendente.",
                                  recovery=result.to_dict())

    def _registry(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.registry_key,0,winreg.KEY_QUERY_VALUE) as key:
                result={};index=0
                while True:
                    try:name,value,kind=winreg.EnumValue(key,index);index+=1
                    except OSError as error:
                        if error.winerror==259:break
                        raise
                    if kind not in (winreg.REG_SZ,winreg.REG_DWORD) or not isinstance(value,(str,int)):
                        raise DeploymentError("A entrada de desinstalação contém valores não reconhecidos.")
                    result[name]=[value,kind]
                return result
        except FileNotFoundError:return None

    def _replace_registry(self,expected,changed):
        import winreg
        if self._registry()!=expected:raise DeploymentError("A entrada de desinstalação mudou; preservada.")
        mutations=[]
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,self.registry_key,0,winreg.KEY_SET_VALUE) as key:
                for name in sorted(set(expected or {})|set(changed or {})):
                    before=(expected or {}).get(name);after=(changed or {}).get(name)
                    if before==after:continue
                    mutations.append((name,before,after))
                    if after is None:winreg.DeleteValue(key,name)
                    else:winreg.SetValueEx(key,name,0,after[1],after[0])
            if changed is None:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,self.registry_key)
            if self._registry()!=changed:raise DeploymentError("A entrada de desinstalação não confirmou a gravação.")
        except Exception as error:
            failures=[]
            for name,before,after in reversed(mutations):
                try:
                    live=self._registry() or {}
                    if live.get(name)==before:continue
                    if live.get(name)!=after:raise DeploymentError("Valor mudou durante rollback: "+name)
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,self.registry_key,0,winreg.KEY_SET_VALUE) as key:
                        if before is None:winreg.DeleteValue(key,name)
                        else:winreg.SetValueEx(key,name,0,before[1],before[0])
                except Exception as failure:failures.append(str(failure))
            if expected is None and self._registry()=={}:
                try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,self.registry_key)
                except OSError as failure:failures.append(str(failure))
            if failures:raise DeploymentError("O registro requer recuperação; valores divergentes foram preservados.",
                                               recovery={"key":self.registry_key,"errors":failures}) from error
            raise

    def _links(self):
        paths=[self.programs/"OpenDownloader"/"OpenDownloader.lnk"]
        if self.desktop is not None:paths.append(self.desktop/"OpenDownloader.lnk")
        return paths

    def _legacy_links(self):
        """Old public shortcut names, accepted only with their recorded ownership."""
        paths=[self.programs/"Lume Downloads"/"Lume Downloads.lnk"]
        if self.desktop is not None:paths.append(self.desktop/"Lume Downloads.lnk")
        return paths

    def _owned_link_matches(self,path,facts):
        if path not in self._links()+self._legacy_links():return False
        if facts is None:return not path.exists() and not path.is_symlink()
        return _matches(path,facts)

    def plan(self,destination,extension_id,previous,*,host_source):
        reg=self._registry()
        previous_shell=(previous or {}).get("integration",{}).get("shell",{})
        registry_repair=None
        if reg is not None:
            recorded=previous_shell.get("registry",{})
            old=recorded.get("after")
            if old is None or reg!=old:
                # An older installer could leave DisplayVersion behind after
                # publishing its owned application/receipt. Only that display
                # fact may lag; it must never establish registry ownership.
                import winreg
                def version(value):
                    if (not isinstance(value,list) or len(value)!=2
                            or type(value[1]) is not int or value[1]!=winreg.REG_SZ
                            or not isinstance(value[0],str) or len(value[0])>64
                            or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",value[0])):
                        return None
                    return tuple(int(part) for part in value[0].split("."))
                observed_version=version(reg.get("DisplayVersion"))
                recorded_version=version(old.get("DisplayVersion")) if isinstance(old,dict) else None
                identity_fields={"DisplayName","Publisher","InstallLocation","DisplayIcon","UninstallString"}
                if (not isinstance(old,dict) or recorded.get("key")!=self.registry_key
                        or not identity_fields<=old.keys()
                        or {name:value for name,value in reg.items() if name!="DisplayVersion"}
                           !={name:value for name,value in old.items() if name!="DisplayVersion"}
                        or observed_version is None or recorded_version is None
                        or old["DisplayVersion"][0]!=(previous or {}).get("version")
                        or observed_version>=recorded_version):
                    raise DeploymentError("Já existe uma entrada de desinstalação sem propriedade confirmada.")
                registry_repair={"DisplayVersion":{"state":"older_display_version",
                    "observed":copy.deepcopy(reg["DisplayVersion"]),"recorded":copy.deepcopy(old["DisplayVersion"])}}
        links=[]
        old_links={item["path"]:item for item in previous_shell.get("shortcuts",[])}
        for path in self._links():
            _plain(path,directory=False,absent=True)
            before=None
            if path.exists():
                record=old_links.get(str(path))
                if record is None or not _matches(path,record["after"]):
                    raise DeploymentError(f"Atalho existente sem propriedade confirmada: {path}")
                if path.stat().st_size>65536:raise DeploymentError("Atalho excessivamente grande.")
                before=base64.b64encode(path.read_bytes()).decode("ascii")
            links.append({"path":str(path),"before":before,
                          "parent_created":bool(old_links.get(str(path),{}).get("parent_created",False))})
        for path in self._legacy_links():
            record=old_links.get(str(path))
            if record is None:continue
            _plain(path,directory=False,absent=True)
            if not path.exists():continue
            if not _matches(path,record.get("after")):
                raise DeploymentError(f"Atalho anterior foi modificado; preservado: {path}")
            if path.stat().st_size>65536:raise DeploymentError("Atalho excessivamente grande.")
            links.append({"path":str(path),"before":base64.b64encode(path.read_bytes()).decode("ascii"),
                          "parent_created":record.get("parent_created",False),"remove":True})
        module=self._registration()
        backend_args={"_backend":self.registration_backend} if self.registration_backend is not None else {}
        previous_manifest=module.discover_owned_manifest_directory(**backend_args)
        native=module.plan_registration(destination/"app"/"LumeNativeHost.exe",extension_id,
                manifest_directory=installation_paths(destination)["native_directory"],host_source=host_source,
                previous_manifest_directory=previous_manifest,**backend_args)
        shell={"registry_before":reg,"shortcuts":links}
        if registry_repair is not None:shell["registry_repair"]=registry_repair
        return {"directory":str(destination),"native":native,"shell":shell}

    def apply(self,plan):
        root=Path(plan["directory"]);shell={"shortcuts":[],"registry":{}}
        native=None
        try:
            for item in plan["shell"]["shortcuts"]:
                path=_plain(Path(item["path"]),directory=False,absent=True)
                before=base64.b64decode(item["before"]) if item["before"] is not None else None
                if (path.read_bytes() if path.exists() else None)!=before:
                    raise DeploymentError("Um atalho mudou desde o plano; preservado.")
                if item.get("remove"):
                    path.unlink()
                    shell["shortcuts"].append({**item,"after":None})
                    if item.get("parent_created") and path.parent==self.programs/"Lume Downloads":
                        try:path.parent.rmdir()
                        except OSError:pass
                    continue
                parent_created=not path.parent.exists() or item.get("parent_created",False)
                path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_name(".lume-shortcut-"+uuid.uuid4().hex+".lnk")
                try:
                    _shortcut(temporary,root/"app"/"LumeDownloads.exe")
                    if _shortcut_target(temporary)!=str(root/"app"/"LumeDownloads.exe"):
                        raise DeploymentError("O atalho não confirmou o executável selecionado.")
                    facts=_file(temporary)
                    _plain(path,directory=False,absent=True)
                    if (path.read_bytes() if path.exists() else None)!=before:
                        raise DeploymentError("O atalho mudou durante a preparação; preservado.")
                    os.replace(temporary,path)
                    shell["shortcuts"].append({**item,"after":facts,"parent_created":parent_created})
                    if not _matches(path,facts):raise DeploymentError("O atalho mudou durante publicação.")
                finally:temporary.unlink(missing_ok=True)
            import winreg
            changed=dict(plan["shell"]["registry_before"] or {})
            changed.update({"DisplayName":[APP_NAME,winreg.REG_SZ],
                            "DisplayVersion":[VERSION,winreg.REG_SZ],"Publisher":[APP_NAME,winreg.REG_SZ],
                            "InstallLocation":[str(root),winreg.REG_SZ],
                            "DisplayIcon":[f'"{root/"app"/"LumeDownloads.exe"}",0',winreg.REG_SZ],
                            "UninstallString":[f'"{root/"Uninstall.exe"}"',winreg.REG_SZ],
                            "NoModify":[1,winreg.REG_DWORD],"NoRepair":[1,winreg.REG_DWORD]})
            shell["registry"]={"key":self.registry_key,"before":plan["shell"]["registry_before"],"after":changed}
            self._replace_registry(plan["shell"]["registry_before"],changed)
            native=self._registration().apply_registration(plan["native"])
            return {"native":native.to_dict(),"shell":shell}
        except Exception as error:
            failures=[]
            native_error=getattr(error,"receipt",None)
            if native_error is not None and native_error.rollback_pending:
                failures.extend(native_error.rollback_pending)
            if native is not None:
                try:self._rollback_native(native)
                except Exception as failure:failures.append(str(failure))
            failures.extend(self._rollback_shell(shell))
            if failures:raise DeploymentError("Integração falhou; recuperação pendente.",
                                              recovery={"errors":failures,"shell":shell,
                                                        "native":native_error.to_dict() if native_error is not None else None}) from error
            raise

    def _rollback_shell(self,shell):
        failures=[]
        reg=shell.get("registry",{})
        if reg and self._registry()!=reg.get("before"):
            try:self._replace_registry(reg["after"],reg["before"])
            except Exception as error:failures.append(str(error))
        for item in reversed(shell.get("shortcuts",[])):
            path=Path(item["path"])
            try:
                if not self._owned_link_matches(path,item["after"]):
                    raise DeploymentError(f"Atalho mudou; restauração preservou {path}")
                if item["before"] is None:path.unlink()
                else:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=path.with_name(".lume-rollback-"+uuid.uuid4().hex+".lnk")
                    with temporary.open("xb") as output:output.write(base64.b64decode(item["before"]))
                    os.replace(temporary,path)
                if item.get("parent_created") and path.parent==self.programs/"OpenDownloader":
                    try:path.parent.rmdir()
                    except OSError:pass
            except Exception as error:failures.append(str(error))
        return failures

    def rollback(self,receipt):
        failures=[]
        try:self._rollback_native(receipt["native"])
        except Exception as error:failures.append(str(error))
        failures.extend(self._rollback_shell(receipt["shell"]))
        if failures:raise DeploymentError("A integração requer recuperação manual.",recovery={"errors":failures})

    def remove(self,owner):
        root=Path(owner["directory"]);leftovers=[];retain_host=False
        try:
            native=self._registration().unregister_owned(root/"app"/"LumeNativeHost.exe",installation_paths(root)["native_directory"],
                **({"_backend":self.registration_backend} if self.registration_backend is not None else {}))
            if native.rollback_pending or native.status=="partial":
                leftovers.append("Native Messaging: remoção parcial; manifesto e referências divergentes preservados.")
                retain_host=True
            elif native.status=="unchanged" and (root/"app"/"LumeNativeHost.exe").exists():
                leftovers.append("Native Messaging: não foi possível comprovar a remoção das referências ao host.")
                retain_host=True
        except Exception as error:
            leftovers.append("Native Messaging: "+str(error));retain_host=True
        if retain_host:return {"leftovers":leftovers,"retain_host":True}
        shell=owner["integration"].get("shell",{})
        reg=shell.get("registry",{})
        if reg:
            try:
                if reg.get("key")!=self.registry_key:raise DeploymentError("Registro fora do escopo esperado.")
                current=self._registry()
                if current is not None:self._replace_registry(reg["after"],None)
            except Exception as error:leftovers.append("Uninstall registry: "+str(error))
        for item in shell.get("shortcuts",[]):
            path=Path(item["path"])
            if not path.exists() and not path.is_symlink():continue
            if not self._owned_link_matches(path,item["after"]):leftovers.append(str(path));continue
            try:
                path.unlink()
                # Only our own menu subfolder, never the Desktop itself.
                if item.get("parent_created") and path.parent==self.programs/"OpenDownloader":path.parent.rmdir()
            except OSError as error:
                if path.exists():leftovers.append(str(error))
        return {"leftovers":leftovers,"retain_host":False}


def _guid(value):
    class Guid(ctypes.Structure):
        _fields_=[("raw",ctypes.c_ubyte*16)]
    return Guid.from_buffer_copy(uuid.UUID(value).bytes_le)


def _known_folder(identity):
    shell=ctypes.WinDLL("shell32");ole=ctypes.WinDLL("ole32")
    pointer=ctypes.c_void_p();guid=_guid(identity)
    shell.SHGetKnownFolderPath.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    result=shell.SHGetKnownFolderPath(ctypes.byref(guid),0,None,ctypes.byref(pointer))
    if result<0:raise DeploymentError("O Windows não informou a pasta de atalhos.")
    try:return Path(ctypes.wstring_at(pointer))
    finally:
        ole.CoTaskMemFree.argtypes=[ctypes.c_void_p];ole.CoTaskMemFree(pointer)


def _com_call(pointer,index,*argtypes):
    table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*argtypes)(table[index])


def _shell_link(path,target=None):
    """Use public IShellLinkW/IPersistFile, without PowerShell or script hosts."""
    ole=ctypes.WinDLL("ole32");ole.CoInitializeEx.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:raise DeploymentError("COM indisponível para o atalho.")
    link=ctypes.c_void_p();persist=ctypes.c_void_p()
    try:
        clsid=_guid("00021401-0000-0000-C000-000000000046")
        iid=_guid("000214F9-0000-0000-C000-000000000046")
        ole.CoCreateInstance.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
        if ole.CoCreateInstance(ctypes.byref(clsid),None,1,ctypes.byref(iid),ctypes.byref(link))<0:
            raise DeploymentError("O Windows não criou o objeto de atalho.")
        persist_iid=_guid("0000010B-0000-0000-C000-000000000046")
        if _com_call(link,0,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p))(link,ctypes.byref(persist_iid),ctypes.byref(persist))<0:
            raise DeploymentError("O atalho não expôs IPersistFile.")
        if target is not None:
            for index,value in ((20,str(target)),(9,str(target.parent)),(7,"OpenDownloader")):
                if _com_call(link,index,ctypes.c_wchar_p)(link,value)<0:raise DeploymentError("O atalho recusou seus dados.")
            result=_com_call(persist,6,ctypes.c_wchar_p,ctypes.c_int)(persist,str(path),1)
            if result<0:raise DeploymentError("O Windows não salvou o atalho.")
            return None
        if _com_call(persist,5,ctypes.c_wchar_p,ctypes.c_uint32)(persist,str(path),0)<0:
            raise DeploymentError("O Windows não abriu o atalho para conferência.")
        value=ctypes.create_unicode_buffer(32768)
        if _com_call(link,3,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32)(link,value,len(value),None,4)<0:
            raise DeploymentError("O atalho não confirmou seu destino.")
        return value.value
    finally:
        for pointer in (persist,link):
            if pointer.value:_com_call(pointer,2)(pointer)
        if initialized>=0:ole.CoUninitialize()


def _shortcut(path,target):return _shell_link(path,target)
def _shortcut_target(path):return _shell_link(path)
