"""Measure completed direct and confirmed-browser jobs without human wait time."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from lume.browser import enqueue_link
from lume.core import Manager
from tests.test_downloads import FixtureServer, DATA, DIGEST
from benchmark import peak_memory

results = []
with tempfile.TemporaryDirectory(prefix='lume-evolution-bench-') as directory:
    profile = Path(directory)
    server = FixtureServer(profile)
    manager = Manager(profile/'data', profile/'files')
    try:
        for mode in ('direct', 'browser-confirmed'):
            for iteration in (1, 2):
                before_cpu = os.times(); wall = time.perf_counter()
                name = f'{mode}-{iteration}.bin'
                if mode == 'direct':
                    identity = manager.add(server.url+'/file', profile/'files', name, sha256=DIGEST)
                else:
                    requests_before = len(server.requests)
                    identity = enqueue_link(profile/'data', server.url+'/file').replace('-', '')
                    until = time.monotonic()+5
                    while not any(row['id']==identity for row in manager.list_requests()):
                        if time.monotonic()>until: raise TimeoutError('Browser request was not imported')
                        time.sleep(.01)
                    assert len(server.requests)==requests_before, 'Transferred before confirmation'
                    manager.confirm_request(identity, profile/'files', name)
                completed = manager.wait(identity, 15)
                assert completed['status']=='completed' and completed['sha256']==DIGEST, completed['error']
                after_cpu=os.times()
                results.append({'mode':mode,'iteration':iteration,'bytes':len(DATA),
                                'elapsed_s':round(time.perf_counter()-wall,4),
                                'cpu_s':round(after_cpu.user+after_cpu.system-before_cpu.user-before_cpu.system,4),
                                'peak_process_family_memory_bytes':peak_memory(),'sha256':completed['sha256']})
    finally:
        manager.close(); server.close()
report={'scope':'One Python process: source engine, SQLite and loopback fixture. No GUI/browser/child processes.',
        'cold_warm':'First/second transfer per mode in one initialized process; not OS-cache-cold.',
        'human_confirmation_wait':'Excluded: verifier confirms immediately after observing durable pending request.',
        'unmeasured':'Agent tokens; GUI/browser overhead; Node/FFmpeg family cost; source extraction network variance.',
        'historic_baseline':'.cache/benchmark-1.1.0.json (different run; no universal performance comparison claimed)',
        'results':results}
(root/'.cache/benchmark-1.2.0.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
