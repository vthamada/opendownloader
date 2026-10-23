"""Receive-buffer resource and cancellation regressions at real HTTP seams."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lume.network import Control, DownloadError, Interrupted, Transport
from lume.streaming import Segment, StreamPlan, Track, download_stream
from test_streaming import Fixture


class StreamingBufferTests(unittest.TestCase):
    def test_cancellation_during_partial_body_settles_without_waiting_for_full_segment(self):
        first=threading.Event();release=threading.Event();finish=threading.Event()
        reading=threading.Event();done=threading.Event();cancelled=threading.Event();errors=[]

        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'

            def log_message(self,*_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length',str(2*1024*1024))
                self.send_header('Content-Type','video/mp2t')
                self.end_headers()
                try:
                    self.wfile.write(b'x'*8192);self.wfile.flush();first.set()
                    if release.wait(5):
                        # A bounded prefix becomes available after cancellation.
                        # The remaining declared body intentionally stays blocked.
                        self.wfile.write(b'x'*(256*1024-8192));self.wfile.flush()
                        finish.wait(5)
                except OSError:
                    pass
                self.close_connection=True

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads=True
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        control=Control();transport=Transport(max_connections_per_host=1)
        url=f'http://127.0.0.1:{server.server_port}/slow.ts'
        plan=StreamPlan(url,'hls','buffer','output.mp4','mp4',False,'Slow fixture','a'*64,
                        (Track('muxed','mpegts',(Segment(url),)),))
        original_read=http.client.HTTPResponse.read

        def observed_read(response,amount=None):
            reading.set()
            return original_read(response,amount)

        with tempfile.TemporaryDirectory(prefix='lume-buffer-cancel-') as temporary:
            work=Path(temporary)

            def transfer():
                try:
                    download_stream(plan,work,control,transport=transport,concurrency=1)
                except BaseException as error:
                    errors.append(error)
                finally:
                    done.set()

            worker=threading.Thread(target=transfer)
            def interrupt():
                try:
                    control.interrupt()
                finally:
                    cancelled.set()
            interrupter=threading.Thread(target=interrupt)
            try:
                with patch.object(http.client.HTTPResponse,'read',observed_read):
                    worker.start()
                    self.assertTrue(first.wait(5),'Server did not begin the body')
                    self.assertTrue(reading.wait(5),'Client did not enter a body read')
                    interrupter.start()
                    self.assertTrue(control.stop.wait(1),'Interruption was not signalled')
                    release.set()
                    settled=done.wait(1) and cancelled.wait(1)
            finally:
                release.set();finish.set();control.stop.set()
                worker.join(8);transport.close()
                if interrupter.ident is not None:
                    interrupter.join(8)
                server.shutdown();server.server_close();serving.join()
            self.assertFalse(worker.is_alive(),'A transfer worker survived finalization')
            self.assertFalse(interrupter.is_alive(),'The interrupt call survived finalization')
            self.assertTrue(settled,'Cancellation waited for the intentionally blocked body')
            self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],Interrupted)
            self.assertFalse(list(work.glob('streaming-seg-*.bin')))
            self.assertFalse(list(work.glob('streaming-seg-*.json')))
            self.assertFalse((work/'streaming-output.mp4').exists())

    def test_low_disk_budget_reserves_all_inflight_receive_buffers_before_writing(self):
        server=Fixture({'/segment.ts':b'x'*(2*1024*1024)})
        self.addCleanup(server.close)
        url=server.url+'/segment.ts'
        plan=StreamPlan(url,'hls','budget','output.mp4','mp4',False,'Space fixture','b'*64,
                        (Track('muxed','mpegts',(Segment(url),)),))
        with tempfile.TemporaryDirectory(prefix='lume-buffer-space-') as temporary:
            work=Path(temporary)
            # 10 MiB cannot cover the check interval, eight in-flight receive
            # buffers and the existing margin. The real disk is untouched.
            usage=SimpleNamespace(total=100*1024*1024,used=90*1024*1024,free=10*1024*1024)
            with patch('lume.streaming.shutil.disk_usage',return_value=usage):
                with self.assertRaises(DownloadError) as caught:
                    download_stream(plan,work,Control(),concurrency=8)
            self.assertEqual(caught.exception.code,'space')
            self.assertFalse(list(work.glob('streaming-seg-*.bin')))
            self.assertFalse(list(work.glob('streaming-seg-*.json')))
            self.assertFalse((work/'streaming-output.mp4').exists())


if __name__=='__main__':
    unittest.main()
