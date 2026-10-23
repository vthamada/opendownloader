"""Completed equal HTTP workloads with controlled per-connection setup delay."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from lume.network import Control, Transport
from test_connection_pool import PersistentServer


class Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/benchmark-connection-pool.json")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("RSS sampler in this benchmark is Windows-specific.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    process = kernel.GetCurrentProcess()
    body = bytes(range(256)) * 1024
    count = 16
    expected = hashlib.sha256(body * count).hexdigest()
    rows = []
    server = PersistentServer(connect_delay=.035, body=body)
    try:
        for reuse in (False, True):
            transport = Transport(keep_alive=reuse)
            try:
                for run in ("pool_cold", "pool_warm"):
                    before_requests = len(server.requests)
                    previous_ports = {port for _, port in server.requests}
                    stop = threading.Event()
                    samples = []
                    def sample():
                        while not stop.is_set():
                            memory = Memory(); memory.cb = ctypes.sizeof(Memory)
                            if psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
                                samples.append(memory.WorkingSetSize)
                            stop.wait(.002)
                    sampler = threading.Thread(target=sample)
                    sampler.start()
                    digest = hashlib.sha256()
                    cpu = time.process_time(); started = time.perf_counter()
                    try:
                        for index in range(count):
                            with transport.open(server.url + f"/segment-{index}", {}, Control()) as owned:
                                while chunk := owned.response.read(64 * 1024):
                                    digest.update(chunk)
                        elapsed = time.perf_counter() - started
                        cpu = time.process_time() - cpu
                    finally:
                        stop.set(); sampler.join()
                    assert digest.hexdigest() == expected
                    ports = {port for _, port in server.requests[before_requests:]}
                    rows.append({"keep_alive": reuse, "run": run, "elapsed_seconds": elapsed,
                                 "cpu_family_seconds": cpu, "sampled_peak_family_rss_bytes": max(samples) if samples else None,
                                 "rss_samples": len(samples), "bytes": len(body) * count, "requests": count,
                                 "tcp_connections_used": len(ports), "new_tcp_connections": len(ports - previous_ports),
                                 "sha256": digest.hexdigest()})
            finally:
                transport.close()
    finally:
        server.close()
    receipt = {"workload": "16 sequential HTTP responses of 256 KiB, 4 MiB total; 35 ms controlled server setup cost for each new TCP connection",
               "measurement_scope": "Single process includes client and loopback server threads; no child processes. CPU therefore covers the whole family. RSS sampled every 2 ms and may miss shorter peaks.",
               "cold_warm": "pool_cold starts a fresh transport; pool_warm reuses that transport. OS caches were not flushed.",
               "unmeasured": ["agent tokens", "real Internet throughput", "TLS handshake speed", "GUI/browser costs", "large-file disk throughput"],
               "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
