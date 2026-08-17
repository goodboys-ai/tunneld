import os
import time

from tunneld.command import build_command
from tunneld.config import DefaultsConfig, LocalForwardConfig, TunnelConfig
from tunneld.supervisor import Supervisor


def _fake_ssh(tmp_path):
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/bin/sh\nsleep 0.2\nexit 0\n")
    ssh.chmod(0o755)
    return ssh


def test_respawns_and_reports_individual_entries(tmp_path, monkeypatch):
    _fake_ssh(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    tunnel = TunnelConfig(
        name="t",
        host="h",
        forwards=[LocalForwardConfig(label="db", local=15432, remote=5432)],
    )
    supervisor = Supervisor(
        tunnel,
        build_command(tunnel, DefaultsConfig()),
        str(tmp_path / "log"),
        reconnect_initial_delay=0.1,
        reconnect_max_delay=0.2,
    )
    supervisor.start()
    time.sleep(0.35)
    first_pid = supervisor.proc.pid
    for _ in range(40):
        if supervisor.proc is not None and supervisor.proc.pid != first_pid:
            break
        time.sleep(0.1)
    assert supervisor.proc is not None
    assert supervisor.proc.pid != first_pid
    status = supervisor.status()
    assert status["forwards"][0]["label"] == "db"
    assert status["forwards"][0]["kind"] == "-L"
    supervisor.stop()
    assert supervisor.state == "stopped"


def test_spawn_failure_enters_reconnect_instead_of_raising(tmp_path, monkeypatch):
    tunnel = TunnelConfig(
        name="t",
        host="h",
        socks=[],
        forwards=[LocalForwardConfig(local=15432, remote=5432)],
    )

    def fail(*args, **kwargs):
        raise FileNotFoundError("ssh missing")

    monkeypatch.setattr("tunneld.supervisor.subprocess.Popen", fail)
    supervisor = Supervisor(
        tunnel,
        build_command(tunnel, DefaultsConfig()),
        str(tmp_path / "log"),
        reconnect_initial_delay=10,
        reconnect_max_delay=10,
    )
    supervisor.start()
    assert supervisor.state == "reconnecting"
    assert supervisor.proc is None
    assert "ssh missing" in supervisor.last_error
    assert "failed to start ssh" in (tmp_path / "log").read_text()
    supervisor.stop()
