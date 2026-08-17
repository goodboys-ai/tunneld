"""Per-tunnel SSH subprocess supervision with auto-reconnect."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Dict, List, Optional

from .command import build_forward_specs
from .config import TunnelConfig

LOG_LIMIT_BYTES = 10 * 1024 * 1024
LOG_TAIL_KEEP_BYTES = 64 * 1024
STOP_BUDGET_SECONDS = 12.0


def _truncate_log(logf, prev_path: str, keep_bytes: int = LOG_TAIL_KEEP_BYTES) -> None:
    """Keep a log's tail in its .prev sibling, then truncate it in place."""
    try:
        logf.flush()
        size = os.fstat(logf.fileno()).st_size
    except OSError:
        return
    if size == 0:
        return
    try:
        logf.seek(max(0, size - keep_bytes))
        tail = logf.read(keep_bytes)
    except OSError:
        tail = b""
    if tail:
        try:
            with open(prev_path, "wb") as prev:
                prev.write(tail)
        except OSError:
            pass
    try:
        logf.seek(0)
        os.ftruncate(logf.fileno(), 0)
        logf.write(
            (
                f"[log truncated at {time.strftime('%Y-%m-%d %H:%M:%S')}; "
                f"see {os.path.basename(prev_path)}]\n"
            ).encode("utf-8")
        )
        logf.flush()
    except OSError:
        pass


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
    """Own one SSH subprocess for one [[tunnels]] entry."""

    def __init__(
        self,
        tunnel: TunnelConfig,
        argv: List[str],
        log_path: str,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ):
        self.tunnel = tunnel
        self.argv = argv
        self.log_path = log_path
        self.reconnect_initial_delay = float(reconnect_initial_delay)
        self.reconnect_max_delay = float(reconnect_max_delay)
        self.proc: Optional[subprocess.Popen] = None
        self.state = "stopped"
        self.last_error = ""
        self._started_at: Optional[float] = None
        self._stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._backoff = self.reconnect_initial_delay

    def start(self) -> None:
        """Start SSH and its reconnect watcher if not already running."""
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            self._stop.clear()
            self._backoff = self.reconnect_initial_delay
            self._spawn_locked()
        self._watch_thread = threading.Thread(target=self._watch, daemon=True)
        self._watch_thread.start()

    def begin_stop(self) -> Optional[subprocess.Popen]:
        """Set the stop flag, detach the process, and send SIGTERM without waiting."""
        self._stop.set()
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        return proc

    def finish_stop(self, proc: Optional[subprocess.Popen], deadline: float) -> None:
        """Gracefully wait, kill survivors, and join the watcher within deadline."""
        remaining = deadline - time.monotonic()
        if proc is not None and proc.poll() is None and remaining > 0:
            try:
                proc.wait(timeout=min(remaining, 5.0))
            except Exception:
                pass
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    proc.wait(timeout=min(remaining, 1.0))
                except Exception:
                    pass
        remaining = deadline - time.monotonic()
        if (
            self._watch_thread is not None
            and self._watch_thread.is_alive()
            and remaining > 0
        ):
            self._watch_thread.join(timeout=remaining)
        if self._watch_thread is not None and not self._watch_thread.is_alive():
            self._watch_thread = None
        self.state = "stopped"

    def stop(self) -> None:
        """Stop reconnecting, terminate SSH, and join the watcher."""
        self.finish_stop(self.begin_stop(), time.monotonic() + STOP_BUDGET_SECONDS)

    def restart(self) -> None:
        """Stop and then start the supervised SSH process."""
        self.stop()
        self.start()

    def update_config(
        self,
        tunnel: TunnelConfig,
        argv: List[str],
        reconnect_initial_delay: float,
        reconnect_max_delay: float,
    ) -> None:
        """Apply config metadata and restart only when SSH argv changes."""
        restart_required = argv != self.argv
        self.tunnel = tunnel
        self.argv = argv
        self.reconnect_initial_delay = float(reconnect_initial_delay)
        self.reconnect_max_delay = float(reconnect_max_delay)
        if restart_required:
            self.restart()

    def status(self) -> Dict:
        """Return tunnel and per-forward runtime status."""
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
            state = self.state
            return {
                "name": self.tunnel.name,
                "host": self.tunnel.host,
                "user": self.tunnel.user,
                "enabled": self.tunnel.enabled,
                "state": state,
                "pid": pid,
                "uptime": uptime,
                "last_error": self.last_error,
                "forwards": [
                    spec.status(state) for spec in build_forward_specs(self.tunnel)
                ],
            }

    def _record_spawn_failure_locked(
        self, message: str, logf, terminate_process: bool = False
    ) -> None:
        proc = self.proc
        if terminate_process and proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
        try:
            self._append_log(logf, (message + "\n").encode("utf-8", errors="replace"))
        finally:
            logf.close()
        self.proc = None
        self._started_at = None
        self.state = "reconnecting"
        self.last_error = message

    def _spawn_locked(self) -> None:
        logf = open(self.log_path, "a+b")
        self.state = "starting"
        self.last_error = ""
        self._started_at = time.time()
        try:
            self.proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._record_spawn_failure_locked(f"failed to start ssh: {exc}", logf)
            return
        try:
            pump_thread = threading.Thread(
                target=self._pump, args=(self.proc.stdout, logf), daemon=True
            )
            pump_thread.start()
        except Exception as exc:
            self._record_spawn_failure_locked(
                f"failed to start log pump: {exc}", logf, terminate_process=True
            )
            return
        time.sleep(0.2)
        if self.proc.poll() is None:
            self.state = "running"

    def _append_log(self, logf, data: bytes) -> None:
        """Append log data and truncate in place once the limit is crossed."""
        with self._log_lock:
            logf.write(data)
            logf.flush()
            try:
                size = os.fstat(logf.fileno()).st_size
            except OSError:
                return
            if size >= LOG_LIMIT_BYTES:
                _truncate_log(logf, self.log_path + ".prev")

    def _pump(self, stream, logf) -> None:
        try:
            for line in stream:
                self._append_log(logf, line)
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
                started_at = self._started_at
            if proc is None:
                delay = self._backoff
                if self._stop.wait(delay):
                    break
                self._backoff = min(self._backoff * 2, self.reconnect_max_delay)
                with self._lock:
                    self._spawn_locked()
                continue
            rc = proc.wait()
            if self._stop.is_set():
                break
            if started_at is not None and time.time() - started_at >= 30:
                self._backoff = self.reconnect_initial_delay
            with self._lock:
                self.state = "reconnecting"
                self.last_error = f"ssh exited with code {rc}"
            delay = self._backoff
            if self._stop.wait(delay):
                break
            self._backoff = min(self._backoff * 2, self.reconnect_max_delay)
            if self._stop.is_set():
                break
            with self._lock:
                self._spawn_locked()
        with self._lock:
            self.state = "stopped"
