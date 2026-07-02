"""Minimal HTTP health server for Cloud Run and local Telegram bot runs."""

from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from socketserver import BaseServer

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_PORT = 8080
HEALTH_OK_BODY = b"OK"
HEALTH_PATHS = frozenset({"/", "/health"})


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Return a plain OK response for Cloud Run health checks."""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in HEALTH_PATHS:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(HEALTH_OK_BODY)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        logger.debug("Health server: " + format, *args)


def resolve_health_port(port: int | None = None) -> int:
    """Resolve the health server port from argument or PORT env."""
    if port is not None:
        return port

    raw_port = os.environ.get("PORT", str(DEFAULT_HEALTH_PORT)).strip()
    try:
        return int(raw_port)
    except ValueError:
        logger.warning("Invalid PORT value %r; using %s.", raw_port, DEFAULT_HEALTH_PORT)
        return DEFAULT_HEALTH_PORT


def start_health_server(port: int | None = None) -> BaseServer:
    """Start a background health server and return the server instance."""
    resolved_port = resolve_health_port(port)
    server = ThreadingHTTPServer(("0.0.0.0", resolved_port), HealthCheckHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="egx-health-server",
        daemon=True,
    )
    thread.start()
    logger.info("Health server listening on 0.0.0.0:%s", resolved_port)
    return server


def health_response_body(path: str = "/health") -> bytes | None:
    """Return OK body for supported health paths, else None."""
    normalized = path.split("?", 1)[0]
    if normalized in HEALTH_PATHS:
        return HEALTH_OK_BODY
    return None
