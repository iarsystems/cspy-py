"""Supervision of one backend process that publishes a service registry port.

Both CSpyServer2 and IarServiceLauncher print the same banner once their
registry is up::

    Service registry running on local socket on port: 41821

so one supervisor serves both: start the program in a private working
directory with its output captured to a log file, poll the log for that
banner, and keep the tail of the output around for diagnostics.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import TextIO

PORT_PATTERN = re.compile(r"Service registry running on local socket on port:\s*(\d+)")


def is_reachable(host: str, port: int, timeout_s: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except Exception:
        return False


class ManagedProcess:
    """One supervised backend program.

    ``command`` is called at every (re)start with the extra context the caller
    needs (for CSpyServer2: the registry port to join, if any).
    """

    def __init__(
        self,
        name: str,
        *,
        host: str = "127.0.0.1",
        start_timeout_ms: int = 20000,
        restart_on_failure: bool = True,
        terminate_timeout_s: float = 3.0,
    ) -> None:
        self.name = name
        self.host = host
        self.start_timeout_ms = start_timeout_ms
        self.restart_on_failure = restart_on_failure
        self.terminate_timeout_s = terminate_timeout_s

        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.registry_port: int | None = None
        self.log_file: TextIO | None = None
        self.log_path: Path | None = None
        self.work_dir: Path | None = None
        self.last_cmd: list[str] = []
        self.stdout_tail: deque[str] = deque(maxlen=200)
        self.last_process_diagnostics = ""

    # -- state ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def healthy(self) -> bool:
        with self.lock:
            proc = self.process
            if proc is None or proc.poll() is not None or self.registry_port is None:
                return False
            return is_reachable(self.host, int(self.registry_port))

    def status(self) -> dict[str, object]:
        with self.lock:
            proc = self.process
            running = proc is not None and proc.poll() is None
            if proc is not None:
                self._refresh_tail()
            return {
                "running": bool(running),
                "pid": int(proc.pid) if running and proc is not None else None,
                "registry_port": self.registry_port,
                "work_dir": str(self.work_dir) if self.work_dir else None,
                "log_path": str(self.log_path) if self.log_path else None,
                "command": list(self.last_cmd),
                "recent_output": list(self.stdout_tail)[-20:],
                "last_process_diagnostics": self.last_process_diagnostics,
            }

    def diagnostics(self, max_lines: int = 40) -> str:
        """Best-effort context for a failed RPC: exit code and recent output.

        If the process is gone, returns the snapshot taken when it was cleaned
        up, so a crash that already triggered a restart is still explained.
        """
        with self.lock:
            proc = self.process
            if proc is None:
                return self.last_process_diagnostics

            self._refresh_tail()
            tail = "\n".join(list(self.stdout_tail)[-max(1, int(max_lines)):]) or "<no output captured>"
            cmd = " ".join(self.last_cmd) if self.last_cmd else "<unknown>"
            log_path = str(self.log_path) if self.log_path else "<none>"

            if proc.poll() is None:
                return (
                    f"{self.name} is still running, but a Thrift call failed. "
                    f"PID: {proc.pid}. Command: {cmd}. Log: {log_path}. Recent output:\n{tail}"
                )
            return (
                f"{self.name} appears to have exited. "
                f"Exit code: {proc.returncode}. Command: {cmd}. Log: {log_path}. Recent output:\n{tail}"
            )

    # -- lifecycle -----------------------------------------------------------

    def ensure(self, executable: Path, args: list[str]) -> int:
        """Return the registry port, (re)starting the process if needed."""
        with self.lock:
            if self.healthy():
                return int(self.registry_port)  # type: ignore[arg-type]

            if self.process is not None:
                if self.process.poll() is None and not self.restart_on_failure:
                    raise RuntimeError(f"Managed {self.name} is unhealthy and restart is disabled")
                self._cleanup()

            last_error: Exception | None = None
            for _ in range(2 if self.restart_on_failure else 1):
                try:
                    self._start(executable, args)
                    return self._wait_for_registry_port()
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    self._cleanup()
            raise RuntimeError(f"Failed to start {self.name}: {last_error}")

    def shutdown(self) -> None:
        with self.lock:
            self._cleanup()

    def _start(self, executable: Path, args: list[str]) -> None:
        if not executable.exists():
            raise RuntimeError(f"{self.name} executable not found: {executable}")

        # A private working directory keeps the CSpyServer2-ServiceRegistry.txt
        # these programs write out of the (possibly read-only, possibly shared
        # with another backend) installation directory.
        work_dir = Path(tempfile.mkdtemp(prefix=f"{self.name.lower()}-"))
        cmd = [str(executable), *args]

        tmp = tempfile.NamedTemporaryFile(prefix=f"{self.name.lower()}-", suffix=".log", delete=False)
        log_path = Path(tmp.name)
        tmp.close()
        log_file = log_path.open("w", encoding="utf-8", errors="replace")

        self.process = subprocess.Popen(cmd, cwd=str(work_dir), stdout=log_file, stderr=subprocess.STDOUT)
        self.registry_port = None
        self.log_file = log_file
        self.log_path = log_path
        self.work_dir = work_dir
        self.last_cmd = cmd

    def _wait_for_registry_port(self) -> int:
        deadline = time.time() + max(1.0, self.start_timeout_ms / 1000.0)
        while True:
            self._refresh_tail()
            if self.registry_port is not None:
                break

            proc = self.process
            if proc is not None and proc.poll() is not None:
                self._refresh_tail()
                tail = "\n".join(list(self.stdout_tail)[-20:]) or "<no output captured>"
                raise RuntimeError(
                    f"{self.name} exited before publishing a registry port. "
                    f"Exit code: {proc.returncode}. Command: {' '.join(self.last_cmd)}. "
                    f"Log: {self.log_path or ''}. Recent output:\n{tail}"
                )
            if time.time() >= deadline:
                tail = "\n".join(list(self.stdout_tail)[-20:]) or "<no output captured>"
                raise RuntimeError(
                    f"Timed out waiting for the {self.name} registry port. "
                    f"Command: {' '.join(self.last_cmd)}. Log: {self.log_path or ''}. Recent output:\n{tail}"
                )
            time.sleep(0.1)

        port = int(self.registry_port)
        if not is_reachable(self.host, port):
            raise RuntimeError(f"{self.name} reported registry port {port}, but {self.host}:{port} is unreachable")
        return port

    def _refresh_tail(self) -> None:
        log_path = self.log_path
        if log_path is None or not log_path.exists():
            return
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        self.stdout_tail.clear()
        self.stdout_tail.extend(text.splitlines()[-200:])
        matches = list(PORT_PATTERN.finditer(text))
        if matches:
            self.registry_port = int(matches[-1].group(1))

    def _cleanup(self) -> None:
        proc = self.process
        if proc is not None:
            self._refresh_tail()
            cmd = " ".join(self.last_cmd) if self.last_cmd else "<unknown>"
            log_path = str(self.log_path) if self.log_path else "<none>"

            if proc.poll() is None:
                # Both programs handle SIGTERM; IarServiceLauncher uses it to
                # shut its services down in reverse order before exiting.
                try:
                    proc.terminate()
                    proc.wait(timeout=self.terminate_timeout_s)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            self._refresh_tail()
            tail = "\n".join(list(self.stdout_tail)[-40:]) or "<no output captured>"
            self.last_process_diagnostics = (
                f"Most recent {self.name} process snapshot. "
                f"Exit code: {proc.returncode}. Command: {cmd}. Log: {log_path}. "
                f"Recent output:\n{tail}"
            )

        if self.log_file is not None:
            try:
                self.log_file.close()
            except Exception:
                pass

        if self.work_dir is not None:
            # Only the registry file the program wrote; the log is kept for diagnostics.
            shutil.rmtree(self.work_dir, ignore_errors=True)

        self.process = None
        self.registry_port = None
        self.log_file = None
        self.log_path = None
        self.work_dir = None
        self.last_cmd = []

