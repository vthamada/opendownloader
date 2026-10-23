"""Assemble the portable distribution with documentation and runtime notices."""
from __future__ import annotations

import hashlib
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from lume import VERSION
from tools.package_extension import collect as collect_extension


def copy_extension(source: Path, destination: Path) -> dict:
    """Package exactly the validated asset closure, including nested icon files."""
    manifest, files = collect_extension(source)
    if destination.exists():
        raise ValueError('A pasta da extensão já existe; use um staging novo para preservar seus arquivos.')
    destination.mkdir(parents=True)
    for name, body in files.items():
        target=destination.joinpath(*name.split('/'))
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(body)
    shutil.copy2(source/'README.md', destination/'README.md')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle',type=Path,default=root/'dist'/'LumeDownloads')
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if not (bundle / "LumeDownloads.exe").is_file():
        raise SystemExit("Gere os executáveis antes de empacotar a distribuição.")
    for name in ("README.md", "VALIDACAO.md", "THIRD_PARTY_NOTICES.md"):
        if (root / name).exists():
            shutil.copy2(root / name, bundle / name)
    portable_readme = (bundle / "README.md").read_text(encoding="utf-8")
    portable_readme = portable_readme.replace("**`dist/LumeDownloads/LumeDownloads.exe`**, ou dê dois cliques em **`start.cmd`**.",
                                             "**`LumeDownloads.exe`** nesta pasta.")
    portable_readme = portable_readme.replace("## Executar pelo código", "## Executar pelo código (na pasta do projeto-fonte)")
    (bundle / "README.md").write_text(portable_readme, encoding="utf-8")
    (bundle / "tools").mkdir(exist_ok=True)
    shutil.copy2(root / "tools" / "register_browser.ps1", bundle / "tools" / "register_browser.ps1")
    (bundle / "docs").mkdir(exist_ok=True)
    shutil.copy2(root / 'browser-distribution.json', bundle / 'browser-distribution.json')
    shutil.copy2(root / "docs" / "UPDATES.md", bundle / "docs" / "UPDATES.md")
    shutil.copy2(root / "docs" / "STREAMING.md", bundle / "docs" / "STREAMING.md")
    shutil.copy2(root / "docs" / "DESEMPENHO.md", bundle / "docs" / "DESEMPENHO.md")
    shutil.copy2(root / "docs" / "DISTRIBUICAO-LOCAL.md", bundle / "docs" / "DISTRIBUICAO-LOCAL.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.7.2.md", bundle / "docs" / "OPENDOWNLOADER-1.7.2.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.8.0.md", bundle / "docs" / "OPENDOWNLOADER-1.8.0.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.8.1.md", bundle / "docs" / "OPENDOWNLOADER-1.8.1.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.9.0.md", bundle / "docs" / "OPENDOWNLOADER-1.9.0.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.9.1.md", bundle / "docs" / "OPENDOWNLOADER-1.9.1.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.10.0.md", bundle / "docs" / "OPENDOWNLOADER-1.10.0.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.11.0.md", bundle / "docs" / "OPENDOWNLOADER-1.11.0.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.12.0.md", bundle / "docs" / "OPENDOWNLOADER-1.12.0.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.12.1.md", bundle / "docs" / "OPENDOWNLOADER-1.12.1.md")
    shutil.copy2(root / "docs" / "OPENDOWNLOADER-1.13.0.md", bundle / "docs" / "OPENDOWNLOADER-1.13.0.md")
    copy_extension(root/'browser-extension', bundle/'browser-extension')
    extension_readme = bundle / "browser-extension" / "README.md"
    extension_readme.write_text(extension_readme.read_text(encoding="utf-8").replace(
        r".\dist\LumeDownloads\LumeNativeHost.exe", r".\LumeNativeHost.exe"), encoding="utf-8")
    notices = bundle / "notices"
    notices.mkdir(exist_ok=True)
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if license_file.exists():
        shutil.copy2(license_file, notices / "Python-LICENSE.txt")
    for library in ("tcl8.6", "tk8.6"):
        file = Path(sys.base_prefix) / "tcl" / library / "license.terms"
        if file.exists():
            shutil.copy2(file, notices / (library + "-license.terms"))
    for name in ("cryptography", "cffi", "pyinstaller", "altgraph", "pyinstaller-hooks-contrib",
                 "yt-dlp", "yt-dlp-ejs", "brotli", "certifi", "requests", "urllib3",
                 "charset-normalizer", "idna", "mutagen", "pycryptodomex", "websockets"):
        distribution = importlib.metadata.distribution(name)
        for relative in distribution.files or ():
            if relative.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    destination = notices / name / relative.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, destination)
    archive = root / "dist" / f"OpenDownloader-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for source in sorted(bundle.rglob("*")):
            if source.is_file():
                output.write(source, str(source.relative_to(bundle.parent)))
    receipt = {"version": VERSION, "archive": archive.name, "bytes": archive.stat().st_size,
               "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "files": sum(p.is_file() for p in bundle.rglob("*"))}
    archive.with_suffix(".sha256").write_text(receipt["sha256"] + "  " + archive.name + "\n", encoding="ascii")
    (root / ".cache").mkdir(exist_ok=True)
    (root / ".cache" / "release.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
