"""Target terminal I/O through the ``libsupport`` service.

The backend calls ``libsupport`` for the target program's stdout/stdin, exit
and failed asserts (semihosting and the like). This process hosts that service
and buffers the traffic.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from ._callbacks import CallbackServer
from .types import TerminalOutput

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger("iar_cspy.rpc")

LIBSUPPORT_SERVICE = "libsupport"
MAX_BUFFER_BYTES = 256 * 1024


class _LibSupportHandler:
    def __init__(self, terminal: Terminal) -> None:
        self._terminal = terminal

    def requestInputBinary(self, length: int) -> bytes:
        t = self._terminal
        n = max(0, int(length))
        with t._lock:
            chunk = bytes(t._input[:n])
            del t._input[:n]
        log.debug("libsupport requestInputBinary(len=%d) -> %d bytes", n, len(chunk))
        return chunk

    def requestInput(self, length: int) -> str:
        return self.requestInputBinary(length).decode("utf-8", errors="replace")

    def printOutputBinary(self, data: bytes) -> None:
        payload = bytes(data)
        self._terminal._append_output(payload)
        log.debug("libsupport printOutputBinary(%d bytes)", len(payload))

    def printOutput(self, data: str) -> None:
        payload = str(data).encode("utf-8", errors="replace")
        self._terminal._append_output(payload)
        log.debug("libsupport printOutput(%d bytes)", len(payload))

    def exit(self, code: int) -> None:
        with self._terminal._changed:
            self._terminal._exit_code = int(code)
            self._terminal._changed.notify_all()
        log.debug("libsupport exit(code=%d)", int(code))

    def reportAssert(self, file: str, line: str, message: str) -> None:
        entry = {"file": str(file), "line": str(line), "message": str(message)}
        with self._terminal._lock:
            self._terminal._asserts.append(entry)
        log.debug("libsupport reportAssert(%r)", entry)


class Terminal:
    """The target program's console (``client.terminal``).

    Output is captured as soon as a session is configured; :meth:`send`
    queues input for the program's next stdin read.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._text: deque[str] = deque(maxlen=5000)
        self._bytes = bytearray()
        self._input = bytearray()
        self._exit_code: int | None = None
        self._asserts: deque[dict[str, str]] = deque(maxlen=100)
        self.server = CallbackServer("libsupport", _LibSupportHandler(self), client.config.libsupport_client_timeout_ms)

    def _append_output(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        with self._changed:
            self._text.append(text)
            self._bytes.extend(data)
            if len(self._bytes) > MAX_BUFFER_BYTES:
                del self._bytes[: len(self._bytes) - MAX_BUFFER_BYTES]
            self._changed.notify_all()

    def output(self, clear: bool = False) -> TerminalOutput:
        """Everything printed so far, the exit code and failed asserts."""
        with self._lock:
            out = TerminalOutput(
                text="".join(self._text),
                data=bytes(self._bytes),
                exit_code=self._exit_code,
                asserts=list(self._asserts),
            )
            if clear:
                self._clear_locked()
        return out

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._text)

    @property
    def exit_code(self) -> int | None:
        """The program's exit code, once it has called exit()."""
        with self._lock:
            return self._exit_code

    def wait_for_exit(self, timeout: float = 30.0) -> int | None:
        """Wait for the program to call exit(); returns the exit code, or None on timeout.

        On a simulator the core keeps running after the program exits, so
        this, rather than waiting for the core to stop, is how to run a
        program to completion. The exit code is forgotten when a new session
        is configured.
        """
        with self._changed:
            self._changed.wait_for(lambda: self._exit_code is not None, timeout=max(0.0, timeout))
            return self._exit_code

    def wait_for_text(self, text: str, timeout: float = 30.0) -> bool:
        """Wait until the captured output contains ``text``; False on timeout.

        For firmware that never exits, this is how to run it "until it
        prints X". Call :meth:`clear` first to ignore earlier output.
        """
        with self._changed:
            return self._changed.wait_for(lambda: text in "".join(self._text), timeout=max(0.0, timeout))

    def clear(self) -> None:
        """Forget captured output and asserts."""
        with self._lock:
            self._clear_locked()

    def _clear_locked(self) -> None:
        self._text.clear()
        self._bytes.clear()
        self._asserts.clear()

    def send(self, text: str | bytes, newline: bool = False) -> int:
        """Queue input for the program; returns the number of bytes now queued."""
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        if newline:
            data += b"\n"
        with self._lock:
            self._input.extend(data)
            return len(self._input)

    def forget_exit_code(self) -> None:
        with self._lock:
            self._exit_code = None

    def clear_input(self) -> None:
        with self._lock:
            self._input.clear()

    def request_input(self, length: int) -> str:
        """Call the registered libsupport service's requestInput (a round trip)."""
        return str(self._client.call("libsupport", "requestInput", int(length)))

    def request_input_binary(self, length: int) -> bytes:
        return bytes(self._client.call("libsupport", "requestInputBinary", int(length)))

    def ensure_registered(self) -> dict[str, Any]:
        """Host and register the ``libsupport`` service so output is captured."""
        if not self._client.config.auto_libsupport:
            return {"ok": False, "reason": "auto-libsupport-disabled"}
        if self._client.backend.registry_endpoint() is None:
            return {"ok": False, "reason": "no-registry-port"}
        host, port = self.server.ensure(self._client.idl.service("libsupport.thrift", "LibSupportService2"))
        self._client.registry.register(LIBSUPPORT_SERVICE, host, port)
        return {"ok": True, "status": "registered", "host": host, "port": port}
