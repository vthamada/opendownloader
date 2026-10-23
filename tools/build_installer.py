"""Build a per-user NSIS installer from a previously verified Lume bundle.

No compiler/runtime is downloaded or installed here. Optional private runtimes
must be a separately reviewed, hash-indexed input, with explicit local authority.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

project=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project))
from lume import VERSION
from lume.deployment import bundle_inventory


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        while chunk:=source.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def copy_tree(source: Path,destination: Path, expected=None):
    facts = bundle_inventory(source)
    if expected is not None and facts != expected:
        raise ValueError('O inventário diverge do conteúdo previamente validado.')
    if destination.exists():raise ValueError('O staging escolhido já existe.')
    destination.mkdir(parents=True)
    count=total=0
    for path in sorted(source.rglob('*')):
        relative=path.relative_to(source)
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
            raise ValueError('Links não são permitidos no payload.')
        if path.is_dir():
            (destination/relative).mkdir(exist_ok=True)
        elif path.is_file():
            count+=1;total+=path.stat().st_size
            if count>12000 or total>2*1024**3:raise ValueError('Payload excede o orçamento local de empacotamento.')
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
            if digest(path)!=digest(target):raise ValueError('A cópia do payload divergiu.')
        else:raise ValueError('Entrada especial no payload.')
    if bundle_inventory(destination) != facts:
        raise ValueError('O staging diverge do inventário validado antes da cópia.')
    return {'files':count,'bytes':total}


def runtime_inventory(root: Path, lock: dict):
    expected = {}
    for item in lock['files']:
        name = item['path']
        # Lock paths are POSIX relative names; deployment owns path validation.
        if not isinstance(name, str) or not name.startswith('runtimes/') or name in expected:
            raise ValueError('Caminho duplicado ou fora de runtimes no lock.')
        expected[name] = {'size': item['bytes'], 'sha256': item['sha256']}
    actual = bundle_inventory(root)
    if actual != expected:
        raise ValueError('O candidato deve conter exatamente os arquivos e hashes do lock.')
    return {name.removeprefix('runtimes/'): facts for name, facts in expected.items()}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--makensis',type=Path,required=True)
    parser.add_argument('--maintenance',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime-candidate',type=Path)
    parser.add_argument('--runtime-lock',type=Path)
    parser.add_argument('--runtime-authority',type=Path,help='Explicit local approval record; never a production signing key.')
    args=parser.parse_args()
    bundle=args.bundle.resolve();compiler=args.makensis.resolve();output=args.output.resolve()
    maintenance=args.maintenance.resolve()
    if not maintenance.is_file():raise ValueError('Informe o helper de manutenção independente.')
    if output.exists():raise ValueError('A saída já existe; preserve-a e escolha outro nome.')
    check=json.loads((project/'.cache/package-verification.json').read_text(encoding='utf-8'))
    if check['desktop_self_test']['version']!=VERSION:raise ValueError('Validação do pacote corresponde a outra versão.')
    if bundle_inventory(bundle) != check.get('files'):
        raise ValueError('O inventário completo difere do pacote validado.')
    for name,expected in check['binaries'].items():
        if digest(bundle/name)!=expected:raise ValueError('Binário difere do pacote validado: '+name)
    if not compiler.is_file():raise ValueError('Informe o compilador NSIS previamente verificado.')
    stage=project/'.cache/installer-payload'/('lume-'+VERSION+'-'+uuid.uuid4().hex)
    payload=stage/'LumeDownloads'
    stats=copy_tree(bundle,payload,check['files'])
    runtime_receipt=None
    if args.runtime_candidate is not None:
        if args.runtime_lock is None or args.runtime_authority is None:
            raise ValueError('Componentes privados exigem lock e aprovação explícita de instalação local.')
        authority=json.loads(args.runtime_authority.read_text(encoding='utf-8'))
        if (authority.get('allow_private_node_installation') is not True or
                authority.get('allow_private_ffmpeg_installation') is not True):
            raise ValueError('A exceção para o runtime Node privado não foi concedida.')
        lock=json.loads(args.runtime_lock.read_text(encoding='utf-8'))
        root=args.runtime_candidate.resolve()
        expected = runtime_inventory(root, lock)
        private_stats=copy_tree(root/'runtimes',payload/'runtimes',expected)
        (payload/'runtime-provenance.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
        runtime_receipt={'scope':'local approved use; public redistribution not cleared by this build',
                         'node':lock['node']['version'],'ffmpeg':lock['ffmpeg']['version'],**private_stats}
    shutil.copy2(project/'browser-distribution.json',payload/'browser-distribution.json')
    assembled = bundle_inventory(payload)
    if runtime_receipt is not None:
        validation_log = stage/'private-package-verification.txt'
        with validation_log.open('wb') as stream:
            validation = subprocess.run([sys.executable,str(project/'tools/verify_package.py'),
                                         '--bundle',str(payload),'--require-private-runtimes'],
                                        stdout=stream,stderr=subprocess.STDOUT)
        if validation.returncode:
            raise ValueError('O pacote com componentes privados não passou na validação: '+str(validation_log))
        verification = json.loads((project/'.cache/package-verification.json').read_text(encoding='utf-8'))
        if verification.get('files') != assembled:
            raise ValueError('O inventário validado dos componentes privados divergiu.')
        runtime_receipt['verified_checks'] = verification['checks']
    output.parent.mkdir(parents=True,exist_ok=True)
    log=stage/'nsis-build.txt'
    started=time.monotonic()
    with log.open('wb') as stream:
        result=subprocess.run([str(compiler),'/V3','/INPUTCHARSET','UTF8','/DPAYLOAD='+str(payload),'/DOUTPUT='+str(output),
                               '/DMAINTENANCE='+str(maintenance),'/DAPP_VERSION='+VERSION,str(project/'installer/LumeSetup.nsi')],
                              stdout=stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    if result.returncode:raise RuntimeError('NSIS falhou. Log: '+str(log))
    if bundle_inventory(payload) != assembled:
        raise ValueError('O staging foi alterado durante a compilação; o instalador não foi aprovado.')
    receipt={'version':VERSION,'installer':str(output),'bytes':output.stat().st_size,'sha256':digest(output),
             'payload':str(payload),'application':stats,'runtimes':runtime_receipt,'compiler':str(compiler),
             'compiler_sha256':digest(compiler),'build_wall_s':round(time.monotonic()-started,3),
             'maintenance_sha256':digest(maintenance),
             'files':assembled,
             'scope':'Built artifact, not proof of installation, runtime compatibility, or browser extension activation.'}
    output.with_suffix('.sha256').write_text(receipt['sha256']+'  '+output.name+'\n',encoding='ascii')
    (stage/'installer.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    (project/'.cache/installer-release.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
