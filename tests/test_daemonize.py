import sys

import pytest

from tunneld import daemonize
from tunneld.daemonize import _rotate_if_oversize, daemon_command


def test_daemon_command_places_global_config_before_subcommand():
    command = daemon_command("/tmp/tunneld.toml")
    assert command == [
        sys.executable,
        "-m",
        "tunneld",
        "--config",
        "/tmp/tunneld.toml",
        "daemon",
        "--foreground",
    ]
    assert command.index("--config") < command.index("daemon")


def test_spawn_daemon_passes_one_positional_command(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(daemonize.state, "ensure_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(
        daemonize.state, "daemon_log_path", lambda: tmp_path / "daemon.log"
    )
    monkeypatch.setattr(
        daemonize.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    daemonize.spawn_daemon("/tmp/tunneld.toml")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (daemon_command("/tmp/tunneld.toml"),)
    assert kwargs["start_new_session"] is True


def test_rotate_if_oversize_shifts_three_generations(tmp_path):
    log = tmp_path / "tunneld.log"
    log.write_bytes(b"main" * 100)
    (tmp_path / "tunneld.log.1").write_bytes(b"one")
    (tmp_path / "tunneld.log.2").write_bytes(b"two")
    (tmp_path / "tunneld.log.3").write_bytes(b"three")

    _rotate_if_oversize(log, generations=3, limit=100)

    assert not log.exists()
    assert (tmp_path / "tunneld.log.1").read_bytes() == b"main" * 100
    assert (tmp_path / "tunneld.log.2").read_bytes() == b"one"
    assert (tmp_path / "tunneld.log.3").read_bytes() == b"two"


def test_rotate_if_oversize_leaves_small_logs_alone(tmp_path):
    log = tmp_path / "tunneld.log"
    log.write_bytes(b"tiny")
    _rotate_if_oversize(log, generations=3, limit=100)
    assert log.read_bytes() == b"tiny"


def test_spawn_daemon_closes_log_when_popen_fails(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(daemonize.state, "ensure_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(
        daemonize.state, "daemon_log_path", lambda: tmp_path / "daemon.log"
    )

    def fail_to_spawn(*args, **kwargs):
        captured["logf"] = kwargs["stdout"]
        raise OSError("spawn failed")

    monkeypatch.setattr(daemonize.subprocess, "Popen", fail_to_spawn)

    with pytest.raises(OSError, match="spawn failed"):
        daemonize.spawn_daemon("/tmp/tunneld.toml")

    assert captured["logf"].closed
