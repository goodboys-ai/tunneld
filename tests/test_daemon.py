import stat
import threading
import time

from tunneld import state
from tunneld.config import parse_config
from tunneld.daemon import Daemon
from tunneld.ipc import IPC_PROTOCOL_VERSION


def test_status_includes_stopped_and_disabled_forward_entries(tmp_path):
    config = parse_config(
        {
            "tunnels": [
                {
                    "name": "ready",
                    "host": "host",
                    "socks": [{"label": "proxy", "local": 1080}],
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
        '[[tunnels]]\nname = "prod"\nhost = "prod"\nsocks = [{ local = 1080 }]\n'
    )
    daemon = Daemon(str(path))
    assert daemon.load()
    previous = daemon.config

    path.write_text(
        '[[tunnels]]\nname = "prod"\nhost = "prod"\nsocks = [{ loacl = 1080 }]\n'
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
