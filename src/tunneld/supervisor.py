"""Per-tunnel ssh subprocess supervision with auto-reconnect."""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Dict, List, Optional

from .config import Tunnel


def _fmt_dur(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class Supervisor:
    """Owns one ssh subprocess for a single [[tunnels]] block."""

    def __init__(self, tunnel: Tunnel, argv: List[str], log_path: str):
        self.tunnel = tunnel
        self.argv = argv
        self.log_path = log_path
        self.proc: Optional[subprocess.Popen] = None
        self.state = "stopped"
        self.last_error = ""
        self._started_at: Optional[float] = None
        self._stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._backoff = 1

    def start(self) -> None:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            self._stop.clear()
            self._backoff = 1
            self._spawn_locked()
        self._watch_thread = threading.Thread(target=self._watch, daemon=True)
        self._watch_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._watch_thread is not None and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=6)
        self._watch_thread = None
        self.state = "stopped"

    def restart(self) -> None:
        self.stop()
        self.start()

    def update(self, argv: List[str]) -> None:
        if argv != self.argv:
            self.argv = argv
            self.restart()

    def status(self) -> Dict:
        with self._lock:
            pid = None
            if self.proc is not None and self.proc.poll() is None:
                pid = self.proc.pid
            started = self._started_at
            uptime = (
                _fmt_dur(time.time() - started)
                if started is not None and self.state == "running"
                else ""
            )
            return {
                "name": self.tunnel.name,
                "host": self.tunnel.host,
                "forwards": len(self.tunnel.forwards),
                "state": self.state,
                "pid": pid,
                "uptime": uptime,
                "last_error": self.last_error,
            }

    def _spawn_locked(self) -> None:
        logf = open(self.log_path, "ab")
        self.state = "starting"
        self.last_error = ""
        self._started_at = time.time()
        self.proc = subprocess.Popen(
            self.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        threading.Thread(
            target=self._pump, args=(self.proc.stdout, logf), daemon=True
        ).start()
        time.sleep(0.2)
        if self.proc.poll() is None:
            self.state = "running"

    def _pump(self, stream, logf) -> None:
        try:
            for line in stream:
                logf.write(line)
                logf.flush()
        finally:
            try:
                stream.close()
            except Exception:
                pass
            try:
                logf.close()
            except Exception:
                pass

    def _watch(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                proc = self.proc
            if proc is None:
                break
            rc = proc.wait()
            if self._stop.is_set():
                break
            with self._lock:
                self.state = "reconnecting"
                self.last_error = f"ssh exited with code {rc}"
            delay = self._backoff
            if self._stop.wait(delay):
                break
            self._backoff = min(self._backoff * 2, 30)
            if self._stop.is_set():
                break
            with self._lock:
                self._spawn_locked()
        with self._lock:
            self.state = "stopped"
