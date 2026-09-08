from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from avatar.config import project_root

_oracle_lock = threading.Lock()
_latest_oracle: dict[str, Any] = {}


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


class _OracleHandler(_QuietHandler):
    """Static 2048 files plus POST/GET /__oracle for OS-harness scoring."""

    def do_GET(self) -> None:
        if _oracle_path(self.path):
            self._send_oracle()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not _oracle_path(self.path):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(max(0, length))
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return
        if not isinstance(payload, dict):
            self.send_error(400, "expected object")
            return
        with _oracle_lock:
            _latest_oracle.clear()
            _latest_oracle.update(payload)
        self.send_response(204)
        self.end_headers()

    def _send_oracle(self) -> None:
        with _oracle_lock:
            body = json.dumps(_latest_oracle).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _oracle_path(path: str) -> bool:
    return urlparse(path).path.rstrip("/") == "/__oracle"


def latest_oracle() -> dict[str, Any]:
    with _oracle_lock:
        return dict(_latest_oracle)


def reset_oracle() -> None:
    with _oracle_lock:
        _latest_oracle.clear()


def serve_directory(path: Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(_QuietHandler, directory=str(path))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, bound = server.server_address[:2]
    return server, f"http://{host}:{bound}/"


def serve_2048(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    reset_oracle()
    root = project_root() / "games" / "2048"
    handler = partial(_OracleHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, bound = server.server_address[:2]
    return server, f"http://{host}:{bound}/"


def serve_tetris_hint() -> str:
    return (
        "Tetris is a Vite app. From games/tetris run `npm install` then "
        "`npm run dev -- --host 127.0.0.1 --port 5173` and open "
        "http://127.0.0.1:5173/?pause_and_decide=1"
    )
