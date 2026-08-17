"""Normalize forwarding entries and build the OpenSSH argv."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Optional

from .config import DefaultsConfig, Endpoint, TunnelConfig


@dataclass(frozen=True)
class ForwardSpec:
    """Normalized OpenSSH forwarding argument and display metadata."""

    label: Optional[str]
    kind: str
    option: str
    argument: str
    listen_side: str
    listen: str
    target: str

    def status(self, state: str) -> dict:
        """Return this forwarding entry as a daemon status mapping."""
        return {
            "label": self.label,
            "kind": self.kind,
            "listen_side": self.listen_side,
            "listen": self.listen,
            "target": self.target,
            "state": _entry_state(state),
        }


def _entry_state(tunnel_state: str) -> str:
    if tunnel_state == "running":
        return "active"
    return tunnel_state


def listener_argument(endpoint: Endpoint) -> str:
    """Convert a validated listener endpoint to OpenSSH syntax."""
    return str(endpoint)


def target_argument(endpoint: Endpoint) -> str:
    """Convert a validated target endpoint to OpenSSH syntax."""
    if isinstance(endpoint, int):
        return f"localhost:{endpoint}"
    return endpoint


def display_endpoint(endpoint: Endpoint) -> str:
    """Return a normalized endpoint for human-readable status."""
    if isinstance(endpoint, int):
        return f"localhost:{endpoint}"
    return endpoint


def build_forward_specs(tunnel: TunnelConfig) -> list[ForwardSpec]:
    """Build normalized forwarding specifications for one tunnel."""
    specs: list[ForwardSpec] = []
    for entry in tunnel.forwards:
        listen_arg = listener_argument(entry.local)
        target_arg = target_argument(entry.remote)
        specs.append(
            ForwardSpec(
                label=entry.label,
                kind="-L",
                option="-L",
                argument=f"{listen_arg}:{target_arg}",
                listen_side="local",
                listen=display_endpoint(entry.local),
                target=display_endpoint(entry.remote),
            )
        )
    for entry in tunnel.proxy:
        listen_arg = listener_argument(entry.local)
        specs.append(
            ForwardSpec(
                label=entry.label,
                kind="-D",
                option="-D",
                argument=listen_arg,
                listen_side="local",
                listen=display_endpoint(entry.local),
                target="SOCKS5",
            )
        )
    for entry in tunnel.remote_forwards:
        listen_arg = listener_argument(entry.remote)
        target_arg = target_argument(entry.local)
        specs.append(
            ForwardSpec(
                label=entry.label,
                kind="-R",
                option="-R",
                argument=f"{listen_arg}:{target_arg}",
                listen_side="remote",
                listen=display_endpoint(entry.remote),
                target=display_endpoint(entry.local),
            )
        )
    return specs


def build_command(tunnel: TunnelConfig, defaults: DefaultsConfig) -> list[str]:
    """Build the shell-free OpenSSH argv for one tunnel."""
    cmd = [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ServerAliveInterval={tunnel.effective_keep_alive(defaults)}",
        "-o",
        f"ServerAliveCountMax={tunnel.effective_keep_alive_count(defaults)}",
    ]
    if tunnel.port is not None:
        cmd += ["-p", str(tunnel.port)]
    if tunnel.identity:
        cmd += ["-i", os.path.expanduser(tunnel.identity)]
    for option in tunnel.ssh_options:
        cmd += ["-o", option]
    for spec in build_forward_specs(tunnel):
        cmd += [spec.option, spec.argument]
    target = f"{tunnel.user}@{tunnel.host}" if tunnel.user else tunnel.host
    cmd.append(target)
    return cmd


def command_display(tunnel: TunnelConfig, defaults: DefaultsConfig) -> str:
    """Return a shell-escaped display form of the OpenSSH argv."""
    return shlex.join(build_command(tunnel, defaults))
