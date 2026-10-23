#!/usr/bin/env python3
"""Release-maintainer tool. Creates keys only with --generate-key; never applies updates."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lume.updates import UpdateError, canonical_payload, release_payload


def generate_key(directory: Path) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as error:
        raise UpdateError("Biblioteca cryptography indisponível.") from error
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode("ascii")
    # Exclusive creation preserves any pre-existing release key. On Windows the
    # maintainer must additionally use a directory protected by account ACLs.
    fd = os.open(directory / "private-key.pem", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(private)
    with (directory / "public-key.txt").open("x", encoding="ascii") as output:
        output.write(public + "\n")
    return public


def sign_release(package: Path, version: str, private_key: Path, output: Path) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as error:
        raise UpdateError("Biblioteca cryptography indisponível.") from error
    with private_key.open("rb") as source:
        raw_key = source.read(65537)
    if len(raw_key) > 65536:
        raise UpdateError("Arquivo de chave privada excessivamente grande.")
    key = serialization.load_pem_private_key(raw_key, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateError("A chave privada deve ser Ed25519.")
    payload = release_payload(package, version)
    signature = base64.b64encode(key.sign(canonical_payload(payload))).decode("ascii")
    envelope = {"payload": payload, "signature": signature}
    with output.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(envelope, destination, sort_keys=True, indent=2, ensure_ascii=True)
        destination.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-key", type=Path, metavar="DIRECTORY")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--output", type=Path, help="Novo manifesto JSON; nunca sobrescreve.")
    args = parser.parse_args(argv)
    signing_args = (args.package, args.version, args.private_key, args.output)
    if args.generate_key and any(value is not None for value in signing_args):
        parser.error("--generate-key deve ser usado separadamente da assinatura.")
    if not args.generate_key and not all(value is not None for value in signing_args):
        parser.error("Informe --package, --version, --private-key e --output.")
    try:
        if args.generate_key:
            public = generate_key(args.generate_key)
            print(f"Chave privada criada em {args.generate_key / 'private-key.pem'}.")
            print("Proteja-a; não distribua a chave privada com o aplicativo.")
            print(f"Chave pública Ed25519 (base64): {public}")
        else:
            sign_release(args.package, args.version, args.private_key, args.output)
            print(f"Manifesto assinado criado em {args.output}.")
    except (OSError, ValueError, TypeError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

