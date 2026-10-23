"""Explicit local maintenance commands; never dispatched from native message bodies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

MODES = {'--browser-setup', '--install-local', '--uninstall-local'}
DEFAULT_EXTENSION_ID = 'meicfjedeegbopmeifconaiekbmppbai'


def _emit(value: dict, report: Path | None) -> None:
    text = json.dumps(value, ensure_ascii=True, indent=2)
    if report is not None:
        report = report.absolute()
        temporary = report.with_name(report.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, report)
        finally:
            temporary.unlink(missing_ok=True)
    if sys.stdout is not None:
        if report is None:
            print(text)
        else:
            summary = {key: value[key] for key in ('ok', 'status', 'version', 'error', 'directory', 'maintenance_host') if key in value}
            summary['report'] = str(report)
            print(json.dumps(summary, ensure_ascii=True))


def main(argv: list[str]) -> int:
    mode, *arguments = argv
    parser = argparse.ArgumentParser(prog='OpenDownloader ' + mode[2:])
    parser.add_argument('--report', type=Path)
    if mode == '--browser-setup':
        parser.add_argument('--extension-id', default=DEFAULT_EXTENSION_ID)
        parser.add_argument('--manifest-directory', type=Path)
        parser.add_argument('--previous-manifest-directory', type=Path)
        parser.add_argument('--host-executable', type=Path)
        parser.add_argument('--apply', action='store_true')
    elif mode == '--install-local':
        parser.add_argument('--source-directory', required=True, type=Path)
        parser.add_argument('--destination', required=True, type=Path)
        parser.add_argument('--uninstaller', required=True, type=Path)
        parser.add_argument('--extension-id', default=DEFAULT_EXTENSION_ID)
        parser.add_argument('--apply', action='store_true')
    elif mode == '--uninstall-local':
        parser.add_argument('--install-directory', required=True, type=Path)
        parser.add_argument('--apply', action='store_true')
    else:
        raise ValueError('Modo de manutenção desconhecido.')
    args = parser.parse_args(arguments)
    try:
        if mode == '--browser-setup':
            from .registration import plan_registration, apply_registration
            host = args.host_executable
            if host is None:
                if not getattr(sys, 'frozen', False):
                    raise ValueError('Informe --host-executable ao usar o código-fonte.')
                host = Path(sys.executable).with_name('LumeNativeHost.exe')
            plan = plan_registration(host, args.extension_id, args.manifest_directory,
                                     args.previous_manifest_directory)
            result = apply_registration(plan) if args.apply else plan
            value = result.to_dict()
        elif mode == '--install-local':
            from .deployment import plan_install, install
            plan = plan_install(args.source_directory, args.destination, args.uninstaller,
                                extension_id=args.extension_id)
            value = (install(plan) if args.apply else plan).to_dict()
        else:
            from .deployment import uninstall
            if not args.apply:
                raise ValueError('A remoção exige --apply após confirmação no desinstalador.')
            value = uninstall(args.install_directory).to_dict()
            if value.get('status') not in ('uninstalled', 'preserved_leftovers') or value.get('retained_host'):
                _emit({**value, 'ok': False, 'error': 'A remoção mantém recursos para recuperação; consulte o relatório.'}, args.report)
                return 1
        _emit({'ok': True, **value}, args.report)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        value = {'ok': False, 'error': str(error)}
        recovery = getattr(error, 'recovery', None)
        if recovery:
            value['recovery'] = recovery
        receipt = getattr(error, 'receipt', None)
        if receipt is not None:
            value['receipt'] = receipt.to_dict() if hasattr(receipt, 'to_dict') else receipt
        try:
            _emit(value, args.report)
        except OSError:
            if sys.stderr is not None:
                print(value['error'], file=sys.stderr)
        return 1
