"""Thrift services this process hosts for the backend to call back into.

The backend expects a frontend to provide a few services: a debug-event
listener (``debugger.eventhandler``, which some backends require before
configure/start succeeds), terminal I/O (``libsupport``), and a listwindow
frontend for list/trace windows to post notifications to.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

from thriftpy2.rpc import make_server

from ._process import is_reachable


class _SuppressConnectionResetFilter(logging.Filter):
    """Drop expected connection-reset tracebacks from thriftpy2 callback servers.

    When a debug session or the managed backend process stops, its open
    connections to our listeners reset. thriftpy2's TThreadedServer only
    swallows TTransportException and logs a full traceback for
    ConnectionResetError; that is normal teardown, not an error worth surfacing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError))


logging.getLogger("thriftpy2.server").addFilter(_SuppressConnectionResetFilter())


def pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class CallbackServer:
    """A thriftpy2 server on a free local port, restarted if it goes away."""

    host = "127.0.0.1"

    def __init__(self, name: str, handler: object, client_timeout_ms: int) -> None:
        self.name = name
        self.handler = handler
        self.client_timeout_ms = client_timeout_ms
        self.lock = threading.RLock()
        self.port = 0
        self._server: Any | None = None
        self._thread: threading.Thread | None = None

    @property
    def started(self) -> bool:
        return self._server is not None

    def ensure(self, service_cls: Any) -> tuple[str, int]:
        """Start the server if it is not running and reachable; return its endpoint."""
        with self.lock:
            if self._server is None or not is_reachable(self.host, self.port, timeout_s=0.5):
                self.stop()
                port = pick_free_port(self.host)
                server = make_server(
                    service_cls, self.handler, self.host, port, client_timeout=self.client_timeout_ms
                )
                # Per-connection handler threads must be daemon threads, or a
                # backend connection blocked in recv (client_timeout up to 1h)
                # prevents interpreter exit before atexit cleanup can run.
                server.daemon = True
                thread = threading.Thread(target=server.serve, daemon=True, name=f"iar-cspy-{self.name}")
                thread.start()
                time.sleep(0.2)
                self._server = server
                self._thread = thread
                self.port = port
            return self.host, self.port

    def stop(self) -> None:
        with self.lock:
            server, self._server = self._server, None
            thread, self._thread = self._thread, None
            if server is None:
                return
            server.close()
            # serve() only checks its closed flag between accepts; one dummy
            # connection lets it see the flag and return. Closing the socket
            # under a blocked accept() instead would log a traceback.
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    pass
            except Exception:
                pass
            if thread is not None:
                thread.join(timeout=1.0)
            try:
                server.trans.close()
            except Exception:
                pass
