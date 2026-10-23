"""Completed streaming tasks; Windows child CPU uses retained Popen handles."""
import ctypes
from ctypes import wintypes
import json,os
from pathlib import Path
import subprocess,sys,tempfile,threading,time
from unittest.mock import patch

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from lume.core import Manager
from test_streaming import Fixture,generate_media

class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('peak','rss','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
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
    return sum((v.dwHighDateTime<<32)+v.dwLowDateTime for v in values[2:])/10_000_000

results=[]
with tempfile.TemporaryDirectory(prefix='lume-stream-bench-') as directory:
    folder=Path(directory);assets=folder/'assets';assets.mkdir()
    server=Fixture(generate_media(assets));manager=Manager(folder/'data',folder/'files')
    popen=subprocess.Popen;children=[];lock=threading.Lock()
    def launch(*args,**kwargs):
        process=popen(*args,**kwargs)
        with lock:children.append(process)
        return process
    try:
        with patch('lume.mux.subprocess.Popen',side_effect=launch):
            for protocol,path in (('hls','/hls/index.m3u8'),('dash','/dash/index.mpd')):
                for iteration in (1,2):
                    done=threading.Event();memory={'sampled_peak':0,'samples':0,'errors':0}
                    def sample():
                        while not done.is_set():
                            try:
                                with lock:running=[p for p in children if p.poll() is None]
                                current=rss(parent)+sum(rss(int(p._handle)) for p in running)
                                memory['sampled_peak']=max(memory['sampled_peak'],current);memory['samples']+=1
                            except OSError:memory['errors']+=1
                            done.wait(.005)
                    with lock:before_children=len(children)
                    before_cpu=cpu(parent);started=time.perf_counter();monitor=threading.Thread(target=sample);monitor.start()
                    try:
                        count=len(server.requests)
                        identity=manager.add(server.url+path,folder/'files',filename=f'{protocol}-{iteration}.mp4')
                        task=manager.wait(identity,30)
                        assert task['status']=='completed',task['error']
                    finally:done.set();monitor.join()
                    with lock:owned=children[before_children:]
                    results.append({'protocol':protocol,'iteration':iteration,'bytes':task['total'],
                        'elapsed_s':round(time.perf_counter()-started,4),'family_cpu_s':round(cpu(parent)-before_cpu+sum(cpu(int(p._handle)) for p in owned),4),
                        'sampled_family_peak_rss_bytes':memory['sampled_peak'],'sampling_interval_ms':5,'samples':memory['samples'],
                        'sampling_errors':memory['errors'],'children':len(owned),'http_requests':len(server.requests)-count,
                        'sha256':task['sha256']})
    finally:manager.close();server.close()
report={'scope':'Source Manager + SQLite + loopback HTTP + real FFmpeg/ffprobe, 3-second generated AV fixture; no browser/GUI.',
        'cold_warm':'First/second completed task per protocol in one initialized process; not OS-cache-cold.',
        'memory':'Sampled concurrent parent+owned children RSS; estimate may miss peaks shorter than 5ms. Child identity uses retained kernel Popen handle.',
        'cpu':'Parent and all created children process CPU, including monitor overhead; no wait/human time excluded from elapsed.',
        'before':'1.2 did not implement these protocols; no equivalent completed before workflow is available.',
        'unmeasured':'Agent tokens, GUI/browser cost, remote-source/network variability, OS-cache-cold runs.', 'results':results}
(root/'.cache/benchmark-1.3.0.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
