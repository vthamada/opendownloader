#!/usr/bin/env python3
"""Explicit live check: resolve YouTube, optionally read <=64 KiB per media stream."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lume.media import MediaError, resolve_media
from lume.network import Control, DownloadError, Transport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--probe", action="store_true", help="Read at most 64 KiB from the direct resource.")
    parser.add_argument("--node", type=Path)
    args = parser.parse_args()
    stop = threading.Event()
    report = {"original_url": args.url, "media_downloaded_to_disk": False}
    try:
        info = resolve_media(args.url, stop, node_runtime=args.node)
        report.update({key: info[key] for key in
                       ("video_id", "title", "filename", "format_id", "mimetype", "expected_size")})
        report["streams"] = [{key: stream[key] for key in
                              ("role", "format_id", "mimetype", "expected_size")}
                             for stream in info["streams"]]
        for summary, stream in zip(report["streams"], info["streams"]):
            summary["stream_host"] = urlsplit(stream["stream_url"]).hostname
        if args.probe:
            for summary, stream in zip(report["streams"], info["streams"]):
                control = Control()
                with Transport().open(
                    stream["stream_url"], {**stream["headers"], "Range": "bytes=0-65535"}, control
                ) as response:
                    mime = response.response.getheader("Content-Type", "").split(";", 1)[0].lower()
                    prefix = response.response.read(65536)
                    summary.update({"http_status": response.response.status,
                                    "content_type": mime, "prefix_bytes": len(prefix),
                                    "first_16_hex": prefix[:16].hex()})
                    expected = "audio/" if stream["role"] == "audio" else "video/"
                    if response.response.status not in (200, 206) or not mime.startswith(expected):
                        raise MediaError("A resposta direta não foi confirmada como mídia.")
                    if prefix.lstrip().lower().startswith((b"<!doctype", b"<html")):
                        raise MediaError("A resposta contém HTML, não um arquivo de mídia.")
        report["ok"] = True
    except (MediaError, DownloadError, OSError) as error:
        report.update({"ok": False, "code": getattr(error, "code", "network"), "error": str(error)})
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

