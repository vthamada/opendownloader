"""Equivalent 32 MiB HTTP tasks, reconstructed legacy vs bounded block scheduling.

Both use four workers and the same new transport with keepalive disabled, to
isolate scheduling. The legacy byte partition is reproduced from Lume 1.3;
this is not execution of an old binary or an IDM comparison.
"""
import ctypes
from ctypes import wintypes
import hashlib,json
from pathlib import Path
import sys,tempfile,threading,time
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from lume.core import Manager
from lume.network import Transport
from tests.test_acceleration import BalancedServer

class StaticManager(Manager):
    def _plan(self,task,identity,control):
        new=not task['parts_state'];super()._plan(task,identity,control)
        if new and task['mode']=='range':
            total=task['total'];count=min(task['parts'],max(1,total//(256*1024))) if total>=512*1024 else 1
            with self.lock:
                self._change(task,parts_state=[dict(start=total*i//count,end=total*(i+1)//count-1,done=0) for i in range(count)])

class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
        ('peak','rss','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
kernel.GetCurrentProcess.restype=wintypes.HANDLE
psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
handle=kernel.GetCurrentProcess()
def rss():
    value=Counters();value.cb=ctypes.sizeof(value)
    if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):raise ctypes.WinError(ctypes.get_last_error())
    return value.rss

data=bytes(range(256))*(32*1024*1024//256);expected=hashlib.sha256(data).hexdigest();rows=[]
with tempfile.TemporaryDirectory(prefix='lume-balance-benchmark-') as folder:
    folder=Path(folder)
    for iteration in range(1,4):
        for name,manager_type in (('legacy_partition_reconstructed',StaticManager),('bounded_work_blocks',Manager)):
            server=BalancedServer(data,delay=.004);transport=Transport(keep_alive=False,max_connections_per_host=4)
            manager=manager_type(folder/f'{name}-{iteration}-data',folder/f'{name}-{iteration}-files',transport=transport)
            done=threading.Event();memory=[0]
            def sample():
                while not done.is_set():memory[0]=max(memory[0],rss());done.wait(.005)
            sampler=threading.Thread(target=sample);sampler.start();start=time.perf_counter();cpu=time.process_time()
            try:
                identity=manager.add(server.url,folder/f'{name}-{iteration}-files',parts=4)
                task=manager.wait(identity,30)
                assert task['status']=='completed',task['error']
                assert task['error']==''
                assert task['sha256']==expected
                rows.append(dict(mode=name,iteration=iteration,bytes=task['total'],sha256=task['sha256'],
                    elapsed_s=round(time.perf_counter()-start,4),cpu_s=round(time.process_time()-cpu,4),
                    sampled_peak_rss_bytes=memory[0],sampling_interval_ms=5,ranges=len(server.requests),children=0))
            finally:done.set();sampler.join();manager.close();transport.close();server.close()
receipt=dict(workload='32 MiB, first range throttled 4 ms/64 KiB; other ranges unthrottled; four workers; keepalive disabled in both',
    baseline='Reconstructed Lume 1.3 partition on same current runtime; not an old binary or IDM run',
    cache='Fresh Manager and transport each run; initialized Python/OS caches, no cold-OS run',
    measurement='CPU and 5ms-sampled RSS of whole source process including loopback server; no children',
    unmeasured=['agent tokens','GUI and browser','real network variance'],results=rows)
(root/'.cache/benchmark-balanced-1.4.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))
