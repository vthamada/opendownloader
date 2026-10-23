"""Build a deterministic, minimal review ZIP; does not upload or publish.

Only the closure of current manifest/static local asset references is packed.
JavaScript literal asset names are included for dynamically registered scripts.
This is a packaging dependency check, not a JavaScript security analyzer.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
EXPECTED_PERMISSIONS = {'contextMenus', 'nativeMessaging', 'storage', 'activeTab', 'scripting'}
LOCAL_ID = 'meicfjedeegbopmeifconaiekbmppbai'
LITERAL_ASSET = re.compile(r'''["']([^"'\s]+\.(?:js|css|html|png|svg))["']''')


class HtmlReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = set()
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value and ((tag == 'script' and name == 'src') or
                          (tag == 'link' and name == 'href') or
                          (tag == 'img' and name == 'src')):
                self.references.add(value)


def extension_id(key):
    digest = hashlib.sha256(base64.b64decode(key, validate=True)).hexdigest()[:32]
    return ''.join(chr(ord('a')+int(value, 16)) for value in digest)


def collect(source):
    source = source.resolve(strict=True)
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest['manifest_version'] != 3 or manifest['name'] != 'OpenDownloader':
        raise ValueError('Unexpected extension identity or manifest version.')
    if set(manifest['permissions']) != EXPECTED_PERMISSIONS or set(manifest['optional_host_permissions']) != {'http://*/*', 'https://*/*'}:
        raise ValueError('Permissions changed: review before producing a store package.')
    if extension_id(manifest['key']) != LOCAL_ID:
        raise ValueError('The local extension key/ID changed.')
    if not re.fullmatch(r'\d+(?:\.\d+){0,3}', manifest['version']):
        raise ValueError('Invalid extension version.')
    pending = {'manifest.json', manifest['background']['service_worker'], manifest['action']['default_popup']}
    for field in (manifest.get('icons', {}), manifest['action'].get('default_icon', {})):
        pending.update(field.values() if isinstance(field, dict) else [field])
    for content in manifest.get('content_scripts', []):
        pending.update(content.get('js', [])); pending.update(content.get('css', []))
    files = {}
    while pending:
        name = min(pending); pending.remove(name)
        if name in files:
            continue
        relative = PurePosixPath(name)
        if ('\\' in name or ':' in name or relative.is_absolute() or
                relative.as_posix() != name or any(part in ('..', '.', 'tests') for part in relative.parts)):
            raise ValueError(f'Unsafe/nonlocal asset reference: {name}')
        path = source.joinpath(*relative.parts)
        if any(part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()) for part in (path, *path.parents)):
            raise ValueError(f'Asset reparse point is not allowed: {name}')
        if not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError(f'Missing local asset: {name}')
        body = path.read_bytes()
        if len(body) > 1024*1024 or len(files) >= 64:
            raise ValueError('Extension asset/count limit exceeded.')
        files[name] = body
        if path.suffix in ('.html', '.js', '.css'):
            text = body.decode('utf-8')
            references = set(LITERAL_ASSET.findall(text)) if path.suffix == '.js' else set()
            if path.suffix == '.html':
                parser = HtmlReferences(); parser.feed(text); references.update(parser.references)
            if path.suffix == '.css':
                references.update(value.strip(' \"\'') for value in re.findall(r'url\(([^)]+)\)', text))
            for ref in references:
                pending.add((relative.parent/ref).as_posix())
    for size in (16, 32, 48, 128):
        name = manifest.get('icons', {}).get(str(size))
        if not name or name not in files:
            raise ValueError(f'Missing {size}px manifest icon.')
        body = files[name]
        if body[:8] != b'\x89PNG\r\n\x1a\n' or int.from_bytes(body[16:20], 'big') != size or int.from_bytes(body[20:24], 'big') != size:
            raise ValueError(f'Icon has invalid PNG dimensions: {name}')
    return manifest, dict(sorted(files.items()))


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as output:
        for name, body in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, body)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=PROJECT/'browser-extension')
    parser.add_argument('--output', type=Path, default=PROJECT/'.cache'/'store-assets')
    args = parser.parse_args()
    manifest, files = collect(args.source)
    body = archive(files)
    if body != archive(files):
        raise ValueError('ZIP reproducibility check failed.')
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output/f"OpenDownloaderExtension-{manifest['version']}.zip"
    if target.exists() and target.read_bytes() != body:
        raise ValueError('Output already exists with different bytes; preserve and choose another output directory.')
    target.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    (target.with_suffix('.zip.sha256')).write_text(f'{digest}  {target.name}\n', encoding='ascii')
    inventory = {'name': manifest['name'], 'version': manifest['version'], 'status': 'prepared_not_published',
                 'local_extension_id': LOCAL_ID, 'store_extension_ids': {'chrome': None, 'edge': None},
                 'zip': target.name, 'bytes': len(body), 'sha256': digest,
                 'files': {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()} for name, value in files.items()}}
    target.with_suffix('.inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(inventory, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
