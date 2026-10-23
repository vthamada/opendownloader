"""Opt-in loopback fixture for a human/browser extension -> desktop check."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sys
import time
import argparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=root / ".cache" / "browser-live")
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y%m%d-%H%M%S")
payload = bytes(range(256)) * 8192
names = {browser: f"lume-{browser}-{stamp}.bin" for browser in ("chrome", "edge")}
receipt = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "requests": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            body = ("<!doctype html><meta charset=utf-8><title>Teste Lume</title>"
                    "<h1>Teste da extensão Lume</h1><p>Clique com o botão direito no link do seu navegador "
                    "e escolha <strong>Baixar com Lume</strong>.</p>" + "".join(
                        f'<p><a href="/{name}">Arquivo de teste para {browser.title()} (2 MiB)</a></p>'
                        for browser, name in names.items())).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path[1:] not in names.values():
            self.send_error(404)
            return
        start, end = 0, len(payload)-1
        value = self.headers.get("Range")
        if value:
            import re
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
            if not match:
                self.send_error(416)
                return
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, end)
        if not 0 <= start <= end < len(payload):
            self.send_error(416)
            return
        self.send_response(206 if value else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("ETag", '"lume-browser-fixture-v1"')
        if value:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        self.end_headers()
        try:
            self.wfile.write(payload[start:end+1])
            receipt["requests"].append({"path": self.path, "range": value,
                                        "at": time.time(), "user_agent": self.headers.get("User-Agent", "")[:256]})
            (output / "requests.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        except OSError:
            pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
receipt["page"] = f"http://127.0.0.1:{server.server_port}/"
receipt["links"] = {browser: receipt["page"] + name for browser, name in names.items()}
(output / "session.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
print(json.dumps(receipt), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
