"""Re-exec ourselves as a background daemon."""

from __future__ import annotations

import subprocess
import sys

from . import state


def daemon_command(config_path: str) -> list[str]:
    """Build the detached daemon command.

    Typer global options must precede the subcommand name.
    """
    return [
        sys.executable,
        "-m",
        "tunneld",
        "--config",
        str(config_path),
        "daemon",
        "--foreground",
    ]


def spawn_daemon(config_path: str) -> None:
    """Spawn tunneld as a detached process using the active interpreter."""
    state.ensure_runtime_dir()
    logf = open(str(state.daemon_log_path()), "ab")
    subprocess.Popen(
        daemon_command(config_path),
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    logf.close()
