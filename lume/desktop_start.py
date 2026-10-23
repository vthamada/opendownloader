"""Request the fixed desktop companion after a browser link is durably queued."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

_WINDOWS = os.name == "nt"


def request_desktop(data_dir: Path) -> None:
    """Hand off to a user-owned desktop process; never pass a URL or wait.

    OSError is deliberately propagated so the host can report a launch warning
    while retaining the already-persisted queue entry.
    """
    directory = Path(data_dir).absolute()
    interpreter = Path(sys.executable)
    if getattr(sys, "frozen", False):
        desktop = interpreter.with_name("LumeDownloads.exe")
        command = [str(desktop), "--from-browser", "--data-dir", str(directory)]
        working_directory = desktop.parent
    else:
        pythonw = interpreter.with_name("pythonw.exe")
        launcher = pythonw if _WINDOWS and pythonw.is_file() else interpreter
        command = [str(launcher), "-m", "lume", "--from-browser", "--data-dir", str(directory)]
        working_directory = Path(__file__).resolve().parents[1]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "close_fds": True,
        "cwd": str(working_directory),
    }
    if _WINDOWS:
        # Break away from a browser/host job when permitted. Detached/group flags
        # alone would not protect the GUI from a kill-on-close inherited job.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)

