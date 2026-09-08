from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote


def cursor_exe() -> Path | None:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "Programs" / "cursor" / "Cursor.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Cursor" / "Cursor.exe",
    ]
    for exe in candidates:
        if exe.is_file():
            return exe
    return None


def simple_browser_uri(url: str) -> str:
    return f"vscode://vscode.simple-browser/show?url={quote(url, safe='')}"


def launch_app_window(url: str) -> None:
    """Open *url* in a standalone Chromium/Edge app window when possible."""
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        program_files / "Microsoft/Edge/Application/msedge.exe",
        program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
        program_files / "Google/Chrome/Application/chrome.exe",
        program_files_x86 / "Google/Chrome/Application/chrome.exe",
        local_app / "Google/Chrome/Application/chrome.exe",
    ]
    for exe in candidates:
        if exe.is_file():
            subprocess.Popen(
                [str(exe), f"--app={url}", "--window-size=900,1100", "--new-window"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    webbrowser.open(url)


def launch_in_cursor_browser(url: str) -> None:
    """Open *url* in Cursor's embedded Simple/Glass browser (no separate app window)."""
    uri = simple_browser_uri(url)
    exe = cursor_exe()
    if exe is not None:
        subprocess.Popen(
            [str(exe), uri],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if os.name == "nt":
        os.startfile(uri)  # type: ignore[attr-defined]
        return
    webbrowser.open(url)


def launch_browser(url: str, *, mode: str = "app") -> None:
    if mode == "cursor":
        launch_in_cursor_browser(url)
        return
    launch_app_window(url)
