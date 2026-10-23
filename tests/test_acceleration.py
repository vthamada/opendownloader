"""Public HTTP/queue checks for reusable workers and durable range blocks."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest

from lume.core import Manager


class BalancedServer:
    def __init__(self, data, *, gate=False, delay=0):
        self.data = data
        self.requests = []
        self.clients = set()
        self.release = threading.Event()
        self.reassigned = threading.Event()
        self.started = threading.Event()
        self.lock = threading.Lock()
        outer = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_): pass
            def handle(self):
                try: super().handle()
                except (ConnectionAbortedError, ConnectionResetError): pass
            def do_GET(self):
                with outer.lock: outer.clients.add(self.client_address)
                first, last = map(int, self.headers['Range'][6:].split('-'))
                length = last-first+1
                self.send_response(206)
                self.send_header('Content-Length', str(length))
                self.send_header('Content-Range', f'bytes {first}-{last}/{len(data)}')
                self.send_header('ETag', '"balanced-v1"')
                self.end_headers()
                if length > 1:
                    with outer.lock:
                        outer.requests.append((first,last))
                        if len(outer.requests)>4: outer.reassigned.set()
                    if first==0:
                        outer.started.set()
                        if gate: outer.release.wait(8)
                try:
                    for offset in range(first,last+1,65536):
                        self.wfile.write(data[offset:min(last+1,offset+65536)])
                        if length>1 and first==0 and delay: time.sleep(delay)
                except OSError: pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/large.bin'
    def close(self):
        self.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join()


class AccelerationTests(unittest.TestCase):
    def test_free_worker_claims_more_ranges_while_first_is_busy(self):
        data = bytes(range(256)) * (28*1024*1024//256)
        with tempfile.TemporaryDirectory(prefix='lume-balanced-') as folder:
            root = Path(folder); server = BalancedServer(data,gate=True)
            manager = Manager(root/'data',root/'files')
            try:
                identity = manager.add(server.url,root/'files',parts=4)
                self.assertTrue(server.started.wait(5))
                self.assertTrue(server.reassigned.wait(5), 'Free workers must claim pending ranges before the slow range finishes')
                server.release.set()
                task=manager.wait(identity,15)
                self.assertEqual(task['status'],'completed',task['error'])
                self.assertEqual(task['sha256'],hashlib.sha256(data).hexdigest())
                self.assertEqual(task['error'],'')
                self.assertFalse(list((root/'files').glob('.lume-*')))
                self.assertEqual((root/'files'/'large.bin').read_bytes(),data)
                intervals=sorted(server.requests)
                self.assertEqual(intervals[0][0],0)
                self.assertEqual(intervals[-1][1],len(data)-1)
                self.assertTrue(all(left[1]+1==right[0] for left,right in zip(intervals,intervals[1:])))
            finally:
                server.release.set(); manager.close(); server.close()

    def test_pause_restart_keeps_block_map_and_hash(self):
        data = b'0123456789abcdef'*(12*1024*1024//16)
        with tempfile.TemporaryDirectory(prefix='lume-balanced-resume-') as folder:
            root=Path(folder); server=BalancedServer(data,gate=True)
            manager=Manager(root/'data',root/'files')
            try:
                identity=manager.add(server.url,root/'files',parts=4)
                self.assertTrue(server.started.wait(5))
                manager.pause(identity)
                server.release.set()
                task=manager.wait(identity,15)
                self.assertEqual(task['status'],'paused')
                ranges=[(p['start'],p['end']) for p in task['parts_state']]
                manager.close()
                manager=Manager(root/'data',root/'files')
                manager.resume(identity)
                task=manager.wait(identity,15)
                self.assertEqual(task['status'],'completed',task['error'])
                self.assertEqual(ranges,[(p['start'],p['end']) for p in task['parts_state']])
                self.assertEqual(task['sha256'],hashlib.sha256(data).hexdigest())
            finally:
                server.release.set(); manager.close(); server.close()


if __name__=='__main__': unittest.main()
