import os
import time

from tunneld.command import build_command
from tunneld.config import Forward, Tunnel
from tunneld.supervisor import Supervisor


def _fake_ssh(tmp_path):
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/bin/sh\nsleep 0.2\nexit 0\n")
    ssh.chmod(0o755)
    return ssh


def test_respawns_on_exit(tmp_path, monkeypatch):
    _fake_ssh(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    tunnel = Tunnel(name="t", host="h", forwards=[Forward(local="1", remote="r:1")])
    sup = Supervisor(tunnel, build_command(tunnel, 30), str(tmp_path / "log"))
    sup.start()
    time.sleep(0.4)
    first_pid = sup.proc.pid
    for _ in range(40):
        if sup.proc is not None and sup.proc.pid != first_pid:
            break
        time.sleep(0.2)
    assert sup.proc is not None
    assert sup.proc.pid != first_pid
    sup.stop()
    assert sup.state == "stopped"
