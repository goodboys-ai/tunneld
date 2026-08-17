import sys

from tunneld import daemonize
from tunneld.daemonize import daemon_command


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
