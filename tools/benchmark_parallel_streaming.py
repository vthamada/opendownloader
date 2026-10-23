"""Compare completed serial/parallel streaming under controlled request latency.

The HTTP/1.0 fixture closes every connection, isolating segment scheduling from
the independently measured keep-alive change. No external media is downloaded.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from lume.network import Control,Transport
from lume.streaming import resolve_stream,download_stream
from test_streaming import Fixture,generate_media

parser=argparse.ArgumentParser()
parser.add_argument('--latency-ms',type=float,default=120)
args=parser.parse_args()
if not 0<=args.latency_ms<=1000:parser.error('latency must be 0..1000 ms')

class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('peak','rss','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
psapi=ctypes.WinDLL('psapi',use_last_error=True)
kernel.GetCurrentProcess.restype=wintypes.HANDLE
kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
parent=kernel.GetCurrentProcess()

def rss(handle):
    value=Counters();value.cb=ctypes.sizeof(value)
    if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):raise ctypes.WinError(ctypes.get_last_error())
    return value.rss

def cpu(handle):
    values=[wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle,*map(ctypes.byref,values)):raise ctypes.WinError(ctypes.get_last_error())
    return sum((value.dwHighDateTime<<32)+value.dwLowDateTime for value in values[2:])/10_000_000

results=[]
with tempfile.TemporaryDirectory(prefix='lume-parallel-bench-') as directory:
    folder=Path(directory);assets=folder/'assets';assets.mkdir()
    server=Fixture(generate_media(assets))
    def delay(name,interval):
        if not name.endswith(('.m3u8','.mpd')):time.sleep(args.latency_ms/1000)
    server.on_get=delay
    original_popen=subprocess.Popen;children=[];lock=threading.Lock()
    def launch(*command,**kwargs):
        process=original_popen(*command,**kwargs)
        with lock:children.append(process)
        return process
    try:
        with patch('lume.mux.subprocess.Popen',side_effect=launch):
            for protocol,path in (('hls','/hls/index.m3u8'),('dash','/dash/index.mpd')):
                for iteration in (1,2):
                    for concurrency in (1,4):
                        work=folder/f'{protocol}-{iteration}-{concurrency}';work.mkdir()
                        done=threading.Event();memory={'peak':0,'samples':0,'errors':0}
                        def sample():
                            while not done.is_set():
                                try:
                                    with lock:running=[p for p in children if p.poll() is None]
                                    current=rss(parent)+sum(rss(int(p._handle)) for p in running)
                                    memory['peak']=max(memory['peak'],current);memory['samples']+=1
                                except OSError:memory['errors']+=1
                                done.wait(.005)
                        with lock:before_children=len(children)
                        before_cpu=cpu(parent);before_requests=len(server.requests)
                        started=time.perf_counter();monitor=threading.Thread(target=sample);monitor.start()
                        transport=Transport(max_connections_per_host=4)
                        try:
                            control=Control();plan=resolve_stream(server.url+path,control,transport=transport)
                            output=download_stream(plan,work,control,transport=transport,concurrency=concurrency)
                            digest=hashlib.sha256(output['path'].read_bytes()).hexdigest()
                        finally:
                            transport.close();done.set();monitor.join()
                        with lock:owned=children[before_children:]
                        results.append({'protocol':protocol,'iteration':iteration,'concurrency':concurrency,
                            'elapsed_s':round(time.perf_counter()-started,4),
                            'family_cpu_s':round(cpu(parent)-before_cpu+sum(cpu(int(p._handle)) for p in owned),4),
                            'sampled_family_peak_rss_bytes':memory['peak'],'sampling_interval_ms':5,
                            'sampling_errors':memory['errors'],'samples':memory['samples'],'children':len(owned),
                            'http_requests':len(server.requests)-before_requests,'bytes':output['bytes'],'sha256':digest})
    finally:server.close()
for protocol in ('hls','dash'):
    assert len({r['sha256'] for r in results if r['protocol']==protocol})==1
report={'scope':'Real loopback HTTP/1.0 + generated 3-second AV + FFmpeg/ffprobe; metadata, transfer, remux, final hash and transport cleanup included.',
    'comparison':'Same current source with concurrency=1 vs4; isolates serial/parallel scheduling, not a full 1.3 vs1.4 application comparison or IDM comparison.',
    'latency_ms_per_segment_request':args.latency_ms,
    'cold_warm':'Two passes per protocol in an initialized process; no OS-cache-cold measurement.',
    'cpu':'Parent (including fixture and monitor threads) plus owned FFmpeg/ffprobe children measured via retained kernel process handles.',
    'memory':'Concurrent family RSS sampled at5ms; estimate can miss shorter peaks.',
    'unmeasured':'Agent tokens, GUI/browser, real remote-source variability, bandwidth-limited behavior, OS-cache-cold runs.',
    'results':results}
destination=root/'.cache/benchmark-parallel-streaming.json'
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
