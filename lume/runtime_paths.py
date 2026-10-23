"""Own application-relative runtime roots without PATH or environment changes."""
from pathlib import Path
import sys


def application_directory() -> Path:
    """Directory of the running app, or the project root in source mode."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def private_runtime_roots() -> tuple[Path, ...]:
    """Prefer sidecar runtimes, then the PyInstaller internal content directory.

    Source execution is rooted at the application package, never at the Python
    interpreter, the shell's current directory or an unrelated extraction path.
    This function locates candidates only; it never installs or executes them.
    """
    application = application_directory()
    if getattr(sys, 'frozen', False):
        internal = Path(getattr(sys, '_MEIPASS', application / '_internal')).resolve()
    else:
        internal = application / '_internal'
    return tuple(dict.fromkeys((application / 'runtimes', internal / 'runtimes')))
