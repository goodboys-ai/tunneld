import os

from tunneld.command import build_command, build_forward_specs
from tunneld.config import (
    DefaultsConfig,
    LocalForwardConfig,
    ProxyForwardConfig,
    RemoteForwardConfig,
    TunnelConfig,
)


def tunnel():
    return TunnelConfig(
        name="prod",
        host="prod.example.com",
        user="root",
        port=22,
        forwards=[
            LocalForwardConfig(label="db", local=5432, remote=5432),
            LocalForwardConfig(local=4321, remote="db.internal:4321"),
        ],
        proxy=[ProxyForwardConfig(label="proxy", local=1080)],
        remote_forwards=[
            RemoteForwardConfig(label="webhook", local=8080, remote=18080)
        ],
    )


def option_values(argv, option):
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == option]


def test_build_command_uses_separate_ssh_argv_elements():
    argv = build_command(tunnel(), DefaultsConfig())
    assert argv[0:3] == ["ssh", "-N", "-T"]
    assert option_values(argv, "-L") == [
        "5432:localhost:5432",
        "4321:db.internal:4321",
    ]
    assert option_values(argv, "-D") == ["1080"]
    assert option_values(argv, "-R") == ["18080:localhost:8080"]
    assert not any(" " in value and value.startswith("-") for value in argv)
    assert argv[-1] == "root@prod.example.com"


def test_forward_specs_describe_individual_entries():
    specs = build_forward_specs(tunnel())
    assert [(spec.label, spec.kind) for spec in specs] == [
        ("db", "-L"),
        (None, "-L"),
        ("proxy", "-D"),
        ("webhook", "-R"),
    ]
    assert specs[0].listen == "localhost:5432"
    assert specs[0].target == "localhost:5432"
    assert specs[-1].listen_side == "remote"
    assert specs[-1].status("running")["state"] == "active"


def test_identity_and_keep_alive_overrides():
    value = tunnel().model_copy(
        update={"identity": "~/key", "keep_alive": 15, "keep_alive_count": 4}
    )
    argv = build_command(value, DefaultsConfig())
    assert os.path.expanduser("~/key") in argv
    assert "ServerAliveInterval=15" in argv
    assert "ServerAliveCountMax=4" in argv


def test_ipv6_endpoints_preserve_openssh_bracket_syntax():
    value = TunnelConfig(
        name="ipv6",
        host="host",
        forwards=[
            LocalForwardConfig(local="[::1]:15432", remote="[2001:db8::10]:5432")
        ],
        remote_forwards=[RemoteForwardConfig(local="[::1]:8080", remote="[::1]:18080")],
    )
    argv = build_command(value, DefaultsConfig())
    assert option_values(argv, "-L") == ["[::1]:15432:[2001:db8::10]:5432"]
    assert option_values(argv, "-R") == ["[::1]:18080:[::1]:8080"]
