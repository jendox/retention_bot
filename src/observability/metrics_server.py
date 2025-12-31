from __future__ import annotations

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.observability.events import EventLogger
from src.observability.metrics import REGISTRY
from src.settings import get_settings

ev = EventLogger(__name__)

_state: dict[str, object] = {"server": None, "thread": None}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        metrics_path = get_settings().metrics.path
        if self.path != metrics_path:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        payload = REGISTRY.render_prometheus().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Avoid noisy stdlib HTTP logs; we use structured logs elsewhere.
        return


def start_metrics_server() -> None:
    thread = _state.get("thread")
    if isinstance(thread, threading.Thread):
        return

    settings = get_settings().metrics
    if not settings.enabled:
        return
    if settings.exporter not in {"prometheus", "both"}:
        return

    server = ThreadingHTTPServer((settings.host, int(settings.port)), _Handler)
    _state["server"] = server

    def _run() -> None:
        try:
            ev.info("metrics.server_started", host=settings.host, port=int(settings.port), path=settings.path)
            server.serve_forever(poll_interval=0.5)
        finally:
            ev.info("metrics.server_stopped")

    server_thread = threading.Thread(target=_run, name="metrics_server", daemon=True)
    _state["thread"] = server_thread
    server_thread.start()


def stop_metrics_server() -> None:
    server = _state.get("server")
    if isinstance(server, ThreadingHTTPServer):
        server.shutdown()
        server.server_close()
    _state["server"] = None
    _state["thread"] = None
