"""Converge running tunnels to config, supervise SSH, and serve IPC."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from typing import Dict, Optional

from . import state
from .command import build_command, build_forward_specs
from .config import AppConfig, ConfigError, TunnelConfig, load_config
from .ipc import IPC_PROTOCOL_VERSION, handle_connection
from .supervisor import Supervisor


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


class Daemon:
    """Own configuration convergence, supervisors, and the IPC server."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Optional[AppConfig] = None
        self.config_error = ""
        self.manual_stopped: set = set()
        self.sup: Dict[str, Supervisor] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._op_lock = threading.RLock()

    def load(self) -> bool:
        """Load the config while preserving the last valid configuration."""
        try:
            config = load_config(self.config_path)
        except (OSError, ConfigError) as exc:
            with self._lock:
                self.config_error = str(exc)
            return False
        with self._lock:
            self.config = config
            self.config_error = ""
        return True

    def _find(self, name: str) -> Optional[TunnelConfig]:
        if self.config is None:
            return None
        for tunnel in self.config.tunnels:
            if tunnel.name == name:
                return tunnel
        return None

    def _log_path(self, name: str) -> str:
        return str(state.log_dir() / f"{_safe(name)}.log")

    def apply(self) -> None:
        """Converge supervisors to config without restarting unchanged commands."""
        cfg = self.config
        if cfg is None:
            return
        initial_delay = float(cfg.daemon.reconnect_initial_delay)
        max_delay = float(cfg.daemon.reconnect_max_delay)

        with self._lock:
            valid_names = {tunnel.name for tunnel in cfg.tunnels}
            self.manual_stopped.intersection_update(valid_names)
            desired = {
                tunnel.name: (tunnel, build_command(tunnel, cfg.defaults))
                for tunnel in cfg.tunnels
                if tunnel.enabled and tunnel.name not in self.manual_stopped
            }
            obsolete = [name for name in self.sup if name not in desired]
            obsolete_sups = [self.sup.pop(name) for name in obsolete]

        for supervisor in obsolete_sups:
            supervisor.stop()

        for name, (tunnel, argv) in desired.items():
            with self._lock:
                supervisor = self.sup.get(name)
                if supervisor is None:
                    supervisor = Supervisor(
                        tunnel,
                        argv,
                        self._log_path(name),
                        initial_delay,
                        max_delay,
                    )
                    self.sup[name] = supervisor
                    start = True
                else:
                    start = False
            if start:
                supervisor.start()
            else:
                supervisor.update_config(tunnel, argv, initial_delay, max_delay)

    def reload(self) -> bool:
        """Reload config and converge supervisors when validation succeeds."""
        with self._op_lock:
            if not self.load():
                return False
            self.apply()
            return True

    def start_all(self) -> None:
        """Clear manual stops and start every enabled tunnel."""
        with self._lock:
            self.manual_stopped.clear()
        self.apply()

    def start_one(self, name: str) -> None:
        """Start one enabled tunnel by name."""
        tunnel = self._find(name)
        if tunnel is None:
            raise ValueError(f"unknown tunnel {name!r}")
        if not tunnel.enabled:
            raise ValueError(f"tunnel {name!r} is disabled in the config")
        with self._lock:
            self.manual_stopped.discard(name)
        self.apply()

    def stop_all(self) -> None:
        """Stop every supervisor while retaining the daemon."""
        with self._lock:
            names = list(self.sup)
            self.manual_stopped.update(names)
            supervisors = [self.sup.pop(name) for name in names]
        for supervisor in supervisors:
            supervisor.stop()

    def stop_one(self, name: str) -> None:
        """Stop one tunnel and remember its manual-stop state."""
        if self._find(name) is None:
            raise ValueError(f"unknown tunnel {name!r}")
        with self._lock:
            self.manual_stopped.add(name)
            supervisor = self.sup.pop(name, None)
        if supervisor is not None:
            supervisor.stop()

    def restart_all(self) -> None:
        """Restart every enabled tunnel using current configuration."""
        with self._lock:
            self.manual_stopped.clear()
            supervisors = list(self.sup.values())
        for supervisor in supervisors:
            supervisor.restart()
        self.apply()

    def restart_one(self, name: str) -> None:
        """Restart one enabled tunnel by name."""
        tunnel = self._find(name)
        if tunnel is None:
            raise ValueError(f"unknown tunnel {name!r}")
        if not tunnel.enabled:
            raise ValueError(f"tunnel {name!r} is disabled in the config")
        with self._lock:
            self.manual_stopped.discard(name)
            supervisor = self.sup.get(name)
        if supervisor is None:
            self.apply()
            return
        supervisor.restart()

    def _inactive_tunnel_status(self, tunnel: TunnelConfig, tunnel_state: str) -> Dict:
        return {
            "name": tunnel.name,
            "host": tunnel.host,
            "user": tunnel.user,
            "enabled": tunnel.enabled,
            "state": tunnel_state,
            "pid": None,
            "uptime": "",
            "last_error": "",
            "forwards": [
                spec.status(tunnel_state) for spec in build_forward_specs(tunnel)
            ],
        }

    def status_data(self) -> Dict:
        """Return a protocol-versioned snapshot of daemon and tunnel state."""
        with self._lock:
            cfg = self.config
            config_error = self.config_error
            supervisors = dict(self.sup)
            manually_stopped = set(self.manual_stopped)

        rows = []
        if cfg is not None:
            for tunnel in cfg.tunnels:
                supervisor = supervisors.get(tunnel.name)
                if supervisor is not None:
                    rows.append(supervisor.status())
                elif not tunnel.enabled:
                    rows.append(self._inactive_tunnel_status(tunnel, "disabled"))
                elif tunnel.name in manually_stopped:
                    rows.append(self._inactive_tunnel_status(tunnel, "stopped"))
                else:
                    rows.append(self._inactive_tunnel_status(tunnel, "stopped"))

        return {
            "daemon": {
                "pid": os.getpid(),
                "protocol_version": IPC_PROTOCOL_VERSION,
                "config": self.config_path,
                "config_error": config_error,
            },
            "tunnels": rows,
        }

    def dispatch(self, op: str, args: Dict) -> Dict:
        """Dispatch one validated IPC operation and return current status."""
        name = args.get("name")
        if op == "status":
            return self.status_data()
        with self._op_lock:
            if op == "start":
                self.start_one(name) if name else self.start_all()
            elif op == "stop":
                self.stop_one(name) if name else self.stop_all()
            elif op == "restart":
                self.restart_one(name) if name else self.restart_all()
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
        while not self._stop.is_set():
            cfg = self.config
            interval = float(cfg.daemon.watch_interval) if cfg is not None else 1.5
            if self._stop.wait(interval):
                break
            cfg = self.config
            if cfg is not None and not cfg.daemon.watch:
                continue
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
            if not self._stop.is_set():
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
        """Run the foreground daemon until shutdown or a termination signal."""
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
        """Request an orderly daemon shutdown."""
        self._stop.set()

    def shutdown_children(self) -> None:
        """Stop and remove every owned tunnel supervisor."""
        with self._lock:
            supervisors = list(self.sup.values())
            self.sup.clear()
        for supervisor in supervisors:
            supervisor.stop()

    def _wait_for_signal(self) -> None:
        event = threading.Event()

        def handler(sig, frame):  # noqa: ARG001
            event.set()

        old_int = signal.signal(signal.SIGINT, handler)
        old_term = signal.signal(signal.SIGTERM, handler)
        try:
            while not self._stop.is_set() and not event.is_set():
                event.wait(0.5)
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            self._stop.set()
