"""Small repeatable task measurement; fixture server is in the measured process."""
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lume.core import Manager
import lume.core as core
from tests.test_downloads import DATA, DIGEST, FixtureServer


def peak_memory():
    if os.name != "nt":
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    *[(key, ctypes.c_size_t) for key in ("peak", "working", "pp", "p", "np", "n", "page", "peakpage")]]
    counter = Counters()
    counter.cb = ctypes.sizeof(counter)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
        return counter.peak
    return None


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="lume-bench-") as temp:
        root = Path(temp)
        server = FixtureServer(root)
        manager = Manager(root / "data", root / "files")
        try:
            for label, checkpoint in (("64KiB-cold-transfer", 64*1024), ("64KiB-warm-transfer", 64*1024),
                                      ("1MiB-cold-transfer", 1024*1024), ("1MiB-warm-transfer", 1024*1024)):
                core.CHECKPOINT_BYTES = checkpoint
                wall = time.perf_counter()
                cpu = os.times()
                task_id = manager.add(server.url + "/file", root / "files", label + ".bin", parts=4, sha256=DIGEST)
                result = manager.wait(task_id, 45)
                assert result["status"] == "completed", result["error"]
                end_cpu = os.times()
                elapsed = time.perf_counter() - wall
                results.append({"run": label, "checkpoint_bytes": checkpoint, "bytes": len(DATA), "wall_seconds": round(elapsed, 4),
                                "cpu_seconds": round(end_cpu.user + end_cpu.system - cpu.user - cpu.system, 4),
                                "peak_process_working_set_bytes": peak_memory(),
                                "MiB_per_second": round(len(DATA) / 1048576 / elapsed, 3), "sha256": result["sha256"]})
        finally:
            manager.close()
            server.close()
    receipt = {"scope": "single Python process including engine, SQLite and loopback fixture server; no GUI or children",
               "cold_definition": "first transfer with each checkpoint policy; process already initialized, not an OS-cache-cold claim",
               "warm_definition": "second equivalent transfer with same checkpoint policy in same process/profile",
               "comparison": "controlled 64KiB versus 1MiB durable-checkpoint policy on identical IPv4 loopback transfers; not a historic released-app comparison",
               "previous_app_baseline": "unavailable: new application",
               "agent_tokens": "not measured / usage counter unavailable",
               "process_family_memory": "equals measured process: no child processes in this benchmark",
               "runs": results}
    output = Path(__file__).resolve().parents[1] / ".cache" / "benchmark.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
