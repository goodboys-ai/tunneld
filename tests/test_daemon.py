import socket
import stat
import threading
import time
from types import SimpleNamespace

from tunneld import state
from tunneld.config import parse_config
from tunneld.daemon import Daemon
from tunneld.ipc import IPC_PROTOCOL_VERSION, IPC_TIMEOUT_SECONDS


def test_status_includes_stopped_and_disabled_forward_entries(tmp_path):
    config = parse_config(
        {
            "tunnels": [
                {
                    "name": "ready",
                    "host": "host",
                    "user": "henry",
                    "proxy": [{"label": "proxy", "local": 1080}],
                },
                {
                    "name": "disabled",
                    "host": "other",
                    "enabled": False,
                    "forwards": [{"local": 5432, "remote": 5432}],
                },
            ]
        }
    )
    daemon = Daemon(str(tmp_path / "tunneld.toml"))
    daemon.config = config

    status = daemon.status_data()
    assert status["daemon"]["protocol_version"] == IPC_PROTOCOL_VERSION
    rows = {row["name"]: row for row in status["tunnels"]}
    assert rows["ready"]["state"] == "stopped"
    assert rows["ready"]["user"] == "henry"
    assert rows["ready"]["forwards"][0]["label"] == "proxy"
    assert rows["ready"]["forwards"][0]["state"] == "stopped"
    assert rows["disabled"]["state"] == "disabled"
    assert rows["disabled"]["forwards"][0]["state"] == "disabled"


class FakeSupervisor:
    def __init__(self, tunnel, argv, log_path, initial_delay, max_delay):
        self.tunnel = tunnel
        self.argv = argv
        self.log_path = log_path
        self.started = 0
        self.stopped = 0
        self.updated = 0
        self.restarted = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def restart(self):
        self.restarted += 1

    def update_config(self, tunnel, argv, initial_delay, max_delay):
        self.tunnel = tunnel
        self.argv = argv
        self.updated += 1

    def begin_stop(self):
        self.stopped += 1
        return object()

    def finish_stop(self, proc, deadline):
        pass


def _one_tunnel(label="db", local=15432):
    return parse_config(
        {
            "tunnels": [
                {
                    "name": "prod",
                    "host": "prod",
                    "forwards": [{"label": label, "local": local, "remote": 5432}],
                }
            ]
        }
    )


def test_apply_converges_and_respects_manual_stop(tmp_path, monkeypatch):
    monkeypatch.setattr("tunneld.daemon.Supervisor", FakeSupervisor)
    daemon = Daemon(str(tmp_path / "tunneld.toml"))
    daemon.config = _one_tunnel()

    daemon.apply()
    first = daemon.sup["prod"]
    assert first.started == 1

    # A label-only edit updates status metadata without replacing the supervisor.
    daemon.config = _one_tunnel(label="renamed")
    daemon.apply()
    assert daemon.sup["prod"] is first
    assert first.updated == 1
    assert first.tunnel.forwards[0].label == "renamed"

    daemon.stop_one("prod")
    assert first.stopped == 1
    assert "prod" not in daemon.sup
    daemon.apply()
    assert "prod" not in daemon.sup

    daemon.start_one("prod")
    second = daemon.sup["prod"]
    assert second is not first
    assert second.started == 1

    # An empty config is valid and converges running processes to zero.
    daemon.config = parse_config({})
    daemon.apply()
    assert daemon.sup == {}
    assert second.stopped == 1


def test_failed_reload_preserves_last_good_config(tmp_path):
    path = tmp_path / "tunneld.toml"
    path.write_text(
        '[[tunnels]]\nname = "prod"\nhost = "prod"\nproxy = [{ local = 1080 }]\n'
    )
    daemon = Daemon(str(path))
    assert daemon.load()
    previous = daemon.config

    path.write_text(
        '[[tunnels]]\nname = "prod"\nhost = "prod"\nproxy = [{ loacl = 1080 }]\n'
    )
    assert not daemon.reload()
    assert daemon.config is previous
    assert "loacl" in daemon.config_error


def test_control_socket_is_private_and_removed_on_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    state.ensure_runtime_dir()
    daemon = Daemon(str(tmp_path / "config.toml"))
    thread = threading.Thread(target=daemon._serve)
    thread.start()

    socket_path = state.socket_path()
    for _ in range(50):
        if socket_path.exists():
            break
        time.sleep(0.02)
    assert socket_path.exists()
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    daemon._stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not socket_path.exists()


def test_watch_config_breaks_debounce_on_shutdown(tmp_path, monkeypatch):
    daemon = Daemon(str(tmp_path / "config.toml"))
    daemon.config = parse_config({})
    waits = iter([False, False, True])
    monkeypatch.setattr(daemon._stop, "wait", lambda timeout: next(waits))
    mtimes = iter([100, 200])
    monkeypatch.setattr(
        "tunneld.daemon.os.stat",
        lambda path: SimpleNamespace(st_mtime_ns=next(mtimes)),
    )
    reload_calls = []
    monkeypatch.setattr(daemon, "reload", lambda: reload_calls.append(1) or True)
    daemon._watch_config()
    assert reload_calls == []


def test_serve_sets_timeout_and_bounds_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    state.ensure_runtime_dir()
    daemon = Daemon(str(tmp_path / "config.toml"))
    handled = []
    release = threading.Event()

    def fake_handler(conn, dispatch):
        handled.append(conn)
        release.wait()

    monkeypatch.setattr("tunneld.daemon.handle_connection", fake_handler)
    thread = threading.Thread(target=daemon._serve)
    thread.start()
    sp = state.socket_path()
    for _ in range(50):
        if sp.exists():
            break
        time.sleep(0.02)
    assert sp.exists()

    clients = []
    try:
        for index in range(32):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(sp))
            clients.append(client)
            for _ in range(100):
                if len(handled) >= index + 1:
                    break
                time.sleep(0.01)
        assert len(handled) == 32
        for conn in handled:
            assert conn.gettimeout() == IPC_TIMEOUT_SECONDS
        extra = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        extra.settimeout(2)
        extra.connect(str(sp))
        assert extra.recv(1) == b""
        extra.close()
    finally:
        release.set()
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(2)
        probe.connect(str(sp))
        for _ in range(100):
            if len(handled) >= 33:
                break
            time.sleep(0.01)
        assert len(handled) == 33
        probe.close()
        for client in clients:
            client.close()
        daemon._stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_serve_survives_worker_thread_start_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    state.ensure_runtime_dir()
    daemon = Daemon(str(tmp_path / "config.toml"))
    handled = []
    release = threading.Event()

    def fake_handler(conn, dispatch):
        handled.append(conn)
        release.wait()

    monkeypatch.setattr("tunneld.daemon.handle_connection", fake_handler)
    real_thread = threading.Thread
    attempts = {"count": 0}

    class FlakyThread:
        def __init__(self, *args, **kwargs):
            self._real = real_thread(*args, **kwargs)
            attempts["count"] += 1
            self._should_fail = attempts["count"] == 1

        def start(self):
            if self._should_fail:
                raise RuntimeError("can't start new thread")
            self._real.start()

    monkeypatch.setattr("tunneld.daemon.threading.Thread", FlakyThread)
    serve_thread = real_thread(target=daemon._serve)
    serve_thread.start()
    sp = state.socket_path()
    for _ in range(50):
        if sp.exists():
            break
        time.sleep(0.02)

    first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    first.settimeout(2)
    first.connect(str(sp))
    assert first.recv(1) == b""
    first.close()

    second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    second.connect(str(sp))
    for _ in range(200):
        if len(handled) >= 1:
            break
        time.sleep(0.01)
    assert len(handled) == 1

    release.set()
    second.close()
    daemon._stop.set()
    serve_thread.join(timeout=3)
    assert not serve_thread.is_alive()


def _two_tunnel_config():
    return parse_config(
        {
            "tunnels": [
                {
                    "name": "a",
                    "host": "h1",
                    "forwards": [{"local": 15432, "remote": 5432}],
                },
                {
                    "name": "b",
                    "host": "h2",
                    "forwards": [{"local": 15433, "remote": 5433}],
                },
            ]
        }
    )


def test_batch_stop_begins_all_before_finishing(tmp_path, monkeypatch):
    events = []

    class OrderSupervisor(FakeSupervisor):
        def begin_stop(self):
            events.append(("begin", self.tunnel.name))
            return object()

        def finish_stop(self, proc, deadline):
            events.append(("finish", self.tunnel.name, deadline))

    monkeypatch.setattr("tunneld.daemon.Supervisor", OrderSupervisor)
    monkeypatch.setattr("tunneld.daemon.time.monotonic", lambda: 0.0)
    daemon = Daemon(str(tmp_path / "config.toml"))
    daemon.config = _two_tunnel_config()
    daemon.apply()
    daemon.stop_all()
    assert events == [
        ("begin", "a"),
        ("begin", "b"),
        ("finish", "a", 12.0),
        ("finish", "b", 12.0),
    ]
    assert daemon.sup == {}


def test_apply_batch_stops_obsolete_supervisors(tmp_path, monkeypatch):
    events = []

    class OrderSupervisor(FakeSupervisor):
        def begin_stop(self):
            events.append(("begin", self.tunnel.name))
            return object()

        def finish_stop(self, proc, deadline):
            events.append(("finish", self.tunnel.name))

    monkeypatch.setattr("tunneld.daemon.Supervisor", OrderSupervisor)
    monkeypatch.setattr("tunneld.daemon.time.monotonic", lambda: 0.0)
    daemon = Daemon(str(tmp_path / "config.toml"))
    daemon.config = _two_tunnel_config()
    daemon.apply()
    daemon.config = parse_config({})
    daemon.apply()
    assert events == [
        ("begin", "a"),
        ("begin", "b"),
        ("finish", "a"),
        ("finish", "b"),
    ]
    assert daemon.sup == {}
