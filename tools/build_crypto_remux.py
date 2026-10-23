"""Build the small local authorized-key helper against pinned private FFmpeg.

No software is installed and the global environment is not modified. The caller
selects an output directory; only the executable and its build receipt are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-build", type=Path,
                        default=project.parent / ".cache/lume-installer/ffmpeg-clean")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.private_build.resolve()
    compiler = root / "toolchain/w64devkit/bin/gcc.exe"
    runtime = root / "runtime"
    source = project / "tools/crypto_remux.c"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    executable = output / "OpenDownloaderCrypto.exe"
    if executable.exists():
        raise SystemExit("The output already exists; select a new output directory.")
    command = [str(compiler), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
               "-municode", "-static-libgcc", "-I" + str(runtime / "include"),
               str(source), "-L" + str(runtime / "lib"), "-lavformat", "-lavcodec",
               "-lavutil", "-o", str(executable)]
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    env["PATH"] = str(compiler.parent) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    started = time.perf_counter()
    result = subprocess.run(command, env=env, capture_output=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise SystemExit(result.stderr.decode("utf-8", "replace"))
    receipt = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "sample_header_sha256": hashlib.sha256((source.parent / "sample_aes_packet.h").read_bytes()).hexdigest(),
               "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
               "compiler": str(compiler), "private_build": str(root),
               "elapsed_s": round(time.perf_counter() - started, 6),
               "command": command, "stderr": result.stderr.decode("utf-8", "replace")}
    (output / "crypto-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"executable": str(executable), "sha256": receipt["executable_sha256"]}))


if __name__ == "__main__":
    main()
