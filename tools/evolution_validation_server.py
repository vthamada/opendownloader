"""Bounded loopback fixtures for real browser -> Lume 1.2 acceptance checks."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import hashlib
import json
from pathlib import Path
import re
import threading
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=root/'.cache/evolution-live')
parser.add_argument('--media', type=Path, required=True)
args = parser.parse_args()
output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
media = args.media.read_bytes()
if len(media) > 16*1024*1024 or b'ftyp' not in media[:64]:
    raise SystemExit('Use a small validated MP4 fixture (maximum 16 MiB).')
stamp = time.strftime('%Y%m%d-%H%M%S')
files = {}
for browser in ('chrome', 'edge'):
    files[f'lume-{browser}-1.2-{stamp}.bin'] = ('application/octet-stream', bytes(range(256))*8192, browser, 'file')
    files[f'lume-video-{browser}-1.2-{stamp}.mp4'] = ('video/mp4', media, browser, 'video')
lock = threading.Lock()
requests = []

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == '/':
            sections = []
            for browser in ('chrome', 'edge'):
                filename = next(name for name, (_, _, who, kind) in files.items() if who == browser and kind == 'file')
                video = next(name for name, (_, _, who, kind) in files.items() if who == browser and kind == 'video')
                sections.append(f'<section><h2>{browser.title()}</h2><p><a href="/{filename}">Arquivo novo para {browser.title()} — 2 MiB</a></p>'
                                f'<video controls preload="none" width="480" aria-label="Vídeo de teste {browser.title()}" src="/{video}"></video></section>')
            body = ('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Validar Lume 1.2 — '+stamp+'</title>'
                    '<style>body{font:17px system-ui;margin:32px;background:#f7f7f1;color:#15352c}section{margin:24px 0;padding:20px;background:white}video{max-width:100%}a{color:#065c5b}</style>'
                    '<h1>Validação Lume 1.2 — '+stamp+'</h1>'
                    '<p>Esta é a página nova da versão 1.2. Use o link e o vídeo do navegador correspondente.</p>'
                    '<ol><li>Arquivo: clique com o botão direito no link e escolha Baixar com Lume.</li>'
                    '<li>Confira a janela do Lume antes de iniciar. Teste também Só adicionar à fila e depois Continuar.</li>'
                    '<li>Vídeo: abra o popup da extensão e escolha Procurar nesta página ou Ativar botões neste site; clique no botão sobre o vídeo.</li>'
                    '<li>Repita o arquivo para verificar a nova cópia, preservando o anterior.</li></ol>' + ''.join(sections) + '</html>').encode()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        item = files.get(self.path.removeprefix('/'))
        if item is None:
            self.send_error(404); return
        mime, content, browser, kind = item
        start, end = 0, len(content)-1
        value = self.headers.get('Range')
        if value:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', value)
            if not match:
                self.send_error(416); return
            start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
        if not 0 <= start <= end < len(content):
            self.send_error(416); return
        self.send_response(206 if value else 200)
        self.send_header('Content-Type',mime); self.send_header('Content-Length',str(end-start+1))
        self.send_header('Accept-Ranges','bytes'); self.send_header('ETag','"'+hashlib.sha256(content).hexdigest()+'"')
        if value: self.send_header('Content-Range',f'bytes {start}-{end}/{len(content)}')
        self.end_headers()
        try:
            self.wfile.write(content[start:end+1])
            with lock:
                requests.append({'at':time.time(),'path':self.path,'range':value,'browser_fixture':browser,'kind':kind,'user_agent':self.headers.get('User-Agent','')[:256]})
                del requests[:-500]
                temp = output/'requests.tmp'
                temp.write_text(json.dumps(requests,indent=2),encoding='utf-8'); temp.replace(output/'requests.json')
        except OSError: pass

server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
receipt = {'page':f'http://127.0.0.1:{server.server_port}/','stamp':stamp,
           'fixtures':[{'name':name,'browser':browser,'kind':kind,'mime':mime,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
                       for name,(mime,content,browser,kind) in files.items()]}
(output/'session.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
try: server.serve_forever()
finally: server.server_close()
