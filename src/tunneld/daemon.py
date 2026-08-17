"""tunneld daemon: converge running tunnels to the config, supervise ssh,
and serve the control socket."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from typing import Dict, Optional

from . import state
from .config import Config, ConfigError, Tunnel, load_config
from .command import build_command
from .ipc import handle_connection
from .supervisor import Supervisor


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


class Daemon:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Optional[Config] = None
        self.config_error = ""
        self.manual_stopped: set = set()
        self.sup: Dict[str, Supervisor] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def load(self) -> bool:
        try:
            self.config = load_config(self.config_path)
            self.config_error = ""
            return True
        except (OSError, ConfigError) as exc:
            self.config_error = str(exc)
            return False

    def _find(self, name: str) -> Optional[Tunnel]:
        if self.config is None:
            return None
        for t in self.config.tunnels:
            if t.name == name:
                return t
        return None

    def _log_path(self, name: str) -> str:
        return str(state.log_dir() / f"{_safe(name)}.log")

    def apply(self) -> None:
        """Make running supervisors match the config (minus manually-stopped)."""
        cfg = self.config
        if cfg is None:
            return
        with self._lock:
            desired = {}
            for t in cfg.tunnels:
                if not t.enabled or t.name in self.manual_stopped:
                    continue
                desired[t.name] = build_command(t, cfg.keep_alive)
            for name in list(self.sup):
                if name not in desired:
                    self.sup[name].stop()
                    del self.sup[name]
            for name, argv in desired.items():
                sup = self.sup.get(name)
                if sup is None:
                    t = self._find(name)
                    sup = Supervisor(t, argv, self._log_path(name))
                    self.sup[name] = sup
                    sup.start()
                elif sup.argv != argv:
                    sup.update(argv)

    def reload(self) -> bool:
        if not self.load():
            return False
        self.apply()
        return True

    def start_all(self) -> None:
        with self._lock:
            self.manual_stopped.clear()
        self.apply()

    def start_one(self, name: str) -> None:
        if self._find(name) is None:
            raise ValueError(f"unknown tunnel {name!r}")
        with self._lock:
            self.manual_stopped.discard(name)
        self.apply()

    def stop_all(self) -> None:
        with self._lock:
            names = list(self.sup)
            self.manual_stopped.update(names)
            sups = {n: self.sup.pop(n) for n in names}
        for sup in sups.values():
            sup.stop()

    def stop_one(self, name: str) -> None:
        with self._lock:
            self.manual_stopped.add(name)
            sup = self.sup.pop(name, None)
        if sup is not None:
            sup.stop()

    def restart_all(self) -> None:
        with self._lock:
            self.manual_stopped.clear()
            names = list(self.sup)
        for name in names:
            self.restart_one(name)

    def restart_one(self, name: str) -> None:
        with self._lock:
            self.manual_stopped.discard(name)
            sup = self.sup.get(name)
        if sup is None:
            self.start_one(name)
            return
        sup.restart()

    def status_data(self) -> Dict:
        with self._lock:
            rows = [s.status() for s in self.sup.values()]
        return {
            "daemon": {
                "pid": os.getpid(),
                "config": self.config_path,
                "config_error": self.config_error,
            },
            "tunnels": rows,
        }

    def dispatch(self, op: str, args: Dict) -> Dict:
        name = args.get("name")
        if op == "status":
            return self.status_data()
        if op == "start":
            if name:
                self.start_one(name)
            else:
                self.start_all()
        elif op == "stop":
            if name:
                self.stop_one(name)
            else:
                self.stop_all()
        elif op == "restart":
            if name:
                self.restart_one(name)
            else:
                self.restart_all()
        elif op == "reload":
            if not self.reload():
                raise ValueError(self.config_error)
        elif op == "shutdown":
            threading.Thread(target=self.shutdown, daemon=True).start()
        else:
            raise ValueError(f"unknown op {op!r}")
        return self.status_data()

    def _watch_config(self) -> None:
        last: Optional[int] = None
        while not self._stop.wait(1.5):
            try:
                mtime = os.stat(self.config_path).st_mtime_ns
            except OSError:
                continue
            if last is None:
                last = mtime
                continue
            if mtime == last:
                continue
            last = mtime
            time.sleep(0.5)
            if self._stop.is_set():
                break
            if self.config is not None and not self.config.watch:
                continue
            self.reload()

    def _serve(self) -> None:
        sp = str(state.socket_path())
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(sp)
        except OSError:
            pass
        server.bind(sp)
        try:
            os.chmod(sp, 0o600)
        except OSError:
            pass
        server.listen(16)
        server.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=handle_connection, args=(conn, self.dispatch), daemon=True
            ).start()
        server.close()
        try:
            os.unlink(sp)
        except OSError:
            pass

    def run(self) -> None:
        state.ensure_runtime_dir()
        self.load()
        self.apply()
        pid_file = state.pid_path()
        pid_file.write_text(str(os.getpid()))
        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._watch_config, daemon=True).start()
        self._wait_for_signal()
        self._stop.set()
        self.shutdown_children()
        try:
            pid_file.unlink()
        except OSError:
            pass

    def shutdown(self) -> None:
        self._stop.set()

    def shutdown_children(self) -> None:
        with self._lock:
            sups = list(self.sup.values())
            self.sup.clear()
        for sup in sups:
            sup.stop()

    def _wait_for_signal(self) -> None:
        evt = threading.Event()

        def handler(sig, frame):  # noqa: ARG001
            evt.set()

        old_int = signal.signal(signal.SIGINT, handler)
        old_term = signal.signal(signal.SIGTERM, handler)
        try:
            while not self._stop.is_set() and not evt.is_set():
                evt.wait(0.5)
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            self._stop.set()
