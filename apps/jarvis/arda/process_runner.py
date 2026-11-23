"""Run-owned subprocesses with bounded cancellation and deadline cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


class ManagedProcessRunner:
    """Execute only this run's children and terminate them within a bound."""

    def __init__(
        self,
        *,
        should_cancel: Callable[[], bool] | None = None,
        deadline_exceeded: Callable[[], bool] | None = None,
        poll_interval: float = 0.05,
        grace_period: float = 0.5,
    ) -> None:
        self._should_cancel = should_cancel or (lambda: False)
        self._deadline_exceeded = deadline_exceeded or (lambda: False)
        self._poll_interval = max(0.01, poll_interval)
        self._grace_period = max(0.05, grace_period)
        self._lock = threading.Lock()
        self._owned: set[subprocess.Popen] = set()

    def _register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._owned.add(proc)

    def _unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._owned.discard(proc)

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), sig)
            else:
                proc.send_signal(sig)
        except (OSError, ProcessLookupError):
            pass

    def _terminate(self, proc: subprocess.Popen) -> None:
        self._signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=self._grace_period)
        except subprocess.TimeoutExpired:
            self._signal_group(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=self._grace_period)
            except subprocess.TimeoutExpired:
                # The process is run-owned; leave no unbounded wait in cleanup.
                pass

    def run(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        kwargs = {
            "args": args,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "stdin": subprocess.DEVNULL,
            "start_new_session": os.name == "posix",
            "cwd": str(cwd) if cwd else None,
        }
        proc = subprocess.Popen(**kwargs)
        self._register(proc)
        interrupted = False
        try:
            while proc.poll() is None:
                if self._should_cancel() or self._deadline_exceeded():
                    interrupted = True
                    self._terminate(proc)
                    break
                time.sleep(self._poll_interval)
            try:
                stdout, stderr = proc.communicate(timeout=self._grace_period)
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)
                stdout, stderr = proc.communicate()
            if interrupted:
                reason = "cancelled" if self._should_cancel() else "timed_out"
                stderr = (stderr or "") + f"\nprocess terminated: {reason}"
            return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        finally:
            self._unregister(proc)

    def close(self) -> None:
        """Boundedly terminate every child still owned by this run."""
        with self._lock:
            owned = tuple(self._owned)
        for proc in owned:
            self._terminate(proc)
