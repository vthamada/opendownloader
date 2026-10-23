"""Open a chosen browser's extension manager without changing its configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess


_BROWSERS = {
    'chrome': ('chrome.exe', 'chrome://extensions/', 'Google/Chrome/Application/chrome.exe'),
    'edge': ('msedge.exe', 'edge://extensions/', 'Microsoft/Edge/Application/msedge.exe'),
}


def extensions_url(browser: str) -> str:
    if not isinstance(browser, str) or browser not in _BROWSERS:
        raise ValueError('Browser must be chrome or edge')
    return _BROWSERS[browser][1]


def _registry_paths(executable: str):
    """Read executable App Paths only; never interpret a command or write a key."""
    if os.name != 'nt':
        return ()
    import winreg
    paths = []
    key_name = r'Software\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + executable
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    value, kind = winreg.QueryValueEx(key, None)
                if isinstance(value, str) and kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                    paths.append(os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value)
            except OSError:
                continue
    return tuple(paths)


def _known_paths(browser: str):
    relative = _BROWSERS[browser][2]
    return tuple(Path(root) / relative for name in ('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)')
                 if (root := os.environ.get(name)))


def find_browser(browser: str) -> Path | None:
    """Select only a regular absolute chrome.exe/msedge.exe from known locations."""
    extensions_url(browser)
    executable = _BROWSERS[browser][0]
    for candidate in (*_registry_paths(executable), *_known_paths(browser)):
        # App Paths may surround the whole path with quotes; arguments are never parsed.
        value = str(candidate)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        path = Path(value)
        try:
            if not path.is_absolute() or path.name.casefold() != executable or not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if resolved.name.casefold() == executable and resolved.is_file():
                return resolved
        except (OSError, ValueError):
            continue
    return None


@dataclass(frozen=True)
class BrowserLaunchResult:
    browser: str
    url: str
    executable: Path | None
    requested: bool
    reason: str | None = None


def open_extensions(browser: str) -> BrowserLaunchResult:
    """Request a normal browser launch; success does not prove the page opened."""
    url = extensions_url(browser)
    executable = find_browser(browser)
    if executable is None:
        return BrowserLaunchResult(browser, url, None, False, 'Navegador não encontrado nas pastas conhecidas.')
    try:
        subprocess.Popen([str(executable), url], shell=False, close_fds=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as error:
        return BrowserLaunchResult(browser, url, executable, False, str(error))
    return BrowserLaunchResult(browser, url, executable, True)
