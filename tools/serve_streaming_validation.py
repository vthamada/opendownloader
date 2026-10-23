"""Loopback-only browser acceptance fixtures; all assets are generated locally."""
import argparse,hashlib,json
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import re,sys,threading,time
from urllib.parse import urlsplit

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from test_streaming import generate_media
from lume.core import Manager
from lume import VERSION

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
assets=out/'assets';assets.mkdir(exist_ok=True)
resources=generate_media(assets)
master='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000,RESOLUTION=96x64,CODECS="avc1.42C00A,mp4a.40.2"\nindex.m3u8\n'
resources['/hls/master.m3u8']=master.encode()
resources['/dash/manifest.mpd']=resources['/dash/index.mpd']
stamp=datetime.now().strftime('%Y%m%d-%H%M%S');phase='preparation';events=[];lock=threading.Lock()
binary = bytes(range(256)) * 8192
binary_names = {browser: f'lume-{browser}-{stamp}.bin' for browser in ('chrome', 'edge')}
for name in binary_names.values():
    resources['/' + name] = binary
template=(root/'browser-extension/tests/streaming-player-fixture.html').read_text(encoding='utf-8')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        global events
        parsed=urlsplit(self.path);parts=parsed.path.split('/');browser=parts[1] if len(parts)>1 else ''
        if browser not in ('chrome','edge','reference'):
            self.send_error(404);return
        # Keep relative component/manifest URLs inside their browser namespace
        # when the address was typed without its trailing slash.
        if parsed.path=='/'+browser:
            self.send_response(308)
            self.send_header('Location',parsed.path+'/'+('?' + parsed.query if parsed.query else ''))
            self.send_header('Content-Length','0');self.end_headers();return
        path='/'+('/'.join(parts[2:]))
        if path in ('/','/index.html'):
            text=template.replace('Lume — fixture de descoberta de streaming',f'Validar Lume {VERSION} — {browser} — {stamp}')
            text=text.replace('<h1>Descoberta de fontes HLS/DASH</h1>',f'<h1>Lume {VERSION} — {browser} — {stamp}</h1>')
            text=text.replace("new URL('hls/master.m3u8',location.href)",f"new URL('manifest?kind=hls&fixture={stamp}',location.href)")
            if browser in binary_names:
                text = text.replace('</h1>', '</h1>' + f'<p><a href="{binary_names[browser]}">Arquivo de teste {browser.title()} — 2 MiB</a>. Use o clique direito: Baixar com Lume.</p>', 1)
            content=text.encode('utf-8');mime='text/html; charset=utf-8'
        elif path=='/manifest':
            content=master.replace('\nindex.m3u8\n','\nhls/index.m3u8\n').encode();mime='application/vnd.apple.mpegurl'
        else:
            content=resources.get(path)
            if content is None:self.send_error(404);return
            mime=('application/vnd.apple.mpegurl' if path.endswith('.m3u8') else 'application/dash+xml' if path.endswith('.mpd') else 'application/octet-stream')
        first,last=0,len(content)-1;interval=self.headers.get('Range')
        if interval:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',interval)
            if not match:self.send_error(416);return
            first=int(match[1]);last=min(int(match[2]) if match[2] else last,last)
            if not 0<=first<=last<len(content):self.send_error(416);return
        self.send_response(206 if interval else 200);self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(last-first+1));self.send_header('ETag','"'+hashlib.sha256(content).hexdigest()+'"')
        self.send_header('Accept-Ranges','bytes')
        if interval:self.send_header('Content-Range',f'bytes {first}-{last}/{len(content)}')
        self.end_headers()
        try:self.wfile.write(content[first:last+1])
        except OSError:return
        with lock:
            events.append({'at':time.time(),'phase':phase,'browser_fixture':browser,'path':self.path,'range':interval,
                           'user_agent':self.headers.get('User-Agent','')[:256]})
            del events[:-1000]
            temporary=out/'requests.tmp';temporary.write_text(json.dumps(events,indent=2),encoding='utf-8');temporary.replace(out/'requests.json')

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);address=f'http://127.0.0.1:{server.server_port}'
worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
manager=Manager(out/'reference-data',out/'reference-files');references=[]
try:
    for protocol,path in (('hls','/hls/master.m3u8'),('dash','/dash/manifest.mpd')):
        identity=manager.add(address+'/reference'+path,out/'reference-files',filename=protocol+'.mp4')
        task=manager.wait(identity,25)
        if task['status']!='completed':raise RuntimeError(task['error'])
        references.append({'protocol':protocol,'bytes':task['total'],'sha256':task['sha256'],'probe':task['media_probe']})
finally:manager.close()
phase='browser-validation'
receipt={'stamp':stamp,'version':VERSION,'chrome':address+'/chrome/','edge':address+'/edge/','references':references,
         'binary':{'bytes':len(binary),'sha256':hashlib.sha256(binary).hexdigest(),
                   'links':{browser:address+'/'+browser+'/'+name for browser,name in binary_names.items()}},
         'scope':'Synthetic blob/open-shadow discovery; real 3-second generated HLS/DASH transfers. Not production-site playback proof.'}
(out/'session.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt),flush=True)
try:worker.join()
finally:server.shutdown();server.server_close()
