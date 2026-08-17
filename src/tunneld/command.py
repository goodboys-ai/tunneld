"""Turn a Tunnel into an ssh argv list."""

from __future__ import annotations

import hashlib
import os
from typing import List

from .config import Forward, Tunnel


def forward_arg(fwd: Forward) -> str:
    if fwd.mode == "local":
        return f"-L {fwd.local}:{fwd.remote}"
    if fwd.mode == "socks":
        return f"-D {fwd.local}"
    return f"-R {fwd.remote}:{fwd.local}"


def build_command(tunnel: Tunnel, default_keep_alive: int) -> List[str]:
    keep_alive = (
        tunnel.keep_alive if tunnel.keep_alive is not None else default_keep_alive
    )
    cmd = [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ServerAliveInterval={keep_alive}",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if tunnel.port is not None:
        cmd += ["-p", str(tunnel.port)]
    if tunnel.identity:
        cmd += ["-i", os.path.expanduser(tunnel.identity)]
    for opt in tunnel.ssh_options:
        cmd += ["-o", opt]
    for fwd in tunnel.forwards:
        cmd.append(forward_arg(fwd))
    target = f"{tunnel.user}@{tunnel.host}" if tunnel.user else tunnel.host
    cmd.append(target)
    return cmd


def command_hash(tunnel: Tunnel, default_keep_alive: int) -> str:
    """Stable identity for a tunnel's argv, used to detect config changes."""
    h = hashlib.sha1()
    for part in build_command(tunnel, default_keep_alive):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()
