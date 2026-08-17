import os

from tunneld.command import build_command, command_hash, forward_arg
from tunneld.config import Forward, Tunnel


def test_local_and_socks_argv():
    t = Tunnel(
        name="prod",
        host="prod.example.com",
        user="root",
        port=22,
        forwards=[
            Forward(mode="local", local="5432", remote="db:5432"),
            Forward(mode="socks", local="1080"),
        ],
    )
    argv = build_command(t, 30)
    assert argv[0] == "ssh"
    assert "-N" in argv and "-T" in argv
    assert "-L 5432:db:5432" in argv
    assert "-D 1080" in argv
    assert "-p" in argv and "22" in argv
    assert "ServerAliveInterval=30" in argv
    assert "ServerAliveCountMax=3" in argv
    assert argv[-1] == "root@prod.example.com"


def test_remote_argv():
    t = Tunnel(
        name="r",
        host="h",
        forwards=[Forward(mode="remote", local="127.0.0.1:8080", remote="0.0.0.0:18080")],
    )
    assert "-R 0.0.0.0:18080:127.0.0.1:8080" in build_command(t, 30)


def test_identity_expansion():
    t = Tunnel(
        name="t",
        host="h",
        identity="~/key",
        forwards=[Forward(local="1", remote="r:1")],
    )
    argv = build_command(t, 30)
    assert os.path.expanduser("~/key") in argv


def test_keep_alive_override():
    t = Tunnel(
        name="t",
        host="h",
        keep_alive=15,
        forwards=[Forward(local="1", remote="r:1")],
    )
    assert "ServerAliveInterval=15" in build_command(t, 30)


def test_command_hash_changes_with_args():
    base = Tunnel(name="t", host="h", forwards=[Forward(local="1", remote="r:1")])
    changed = Tunnel(
        name="t", host="h", forwards=[Forward(local="2", remote="r:2")]
    )
    assert command_hash(base, 30) != command_hash(changed, 30)


def test_forward_arg_strings():
    assert forward_arg(Forward(mode="local", local="1", remote="r:1")) == "-L 1:r:1"
    assert forward_arg(Forward(mode="socks", local="1080")) == "-D 1080"
    assert forward_arg(Forward(mode="remote", local="1", remote="r:1")) == "-R r:1:1"
