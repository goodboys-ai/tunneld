"""Re-exec ourselves as a background daemon."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from typing import Union

from . import state
from .supervisor import LOG_LIMIT_BYTES


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


def _rotate_if_oversize(
    path: Union[str, Path], generations: int = 3, limit: int = LOG_LIMIT_BYTES
) -> None:
    """Shift an oversized log into dated generations before it is reopened."""
    path = Path(path)
    try:
        oversized = path.exists() and path.stat().st_size >= limit
    except OSError:
        return
    if not oversized:
        return
    for index in range(generations, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        if source.exists():
            with contextlib.suppress(OSError):
                target.unlink()
            with contextlib.suppress(OSError):
                source.rename(target)


def spawn_daemon(config_path: str) -> None:
    """Spawn tunneld as a detached process using the active interpreter."""
    state.ensure_runtime_dir()
    _rotate_if_oversize(state.daemon_log_path())
    with open(state.daemon_log_path(), "ab") as logf:
        subprocess.Popen(
            daemon_command(config_path),
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
