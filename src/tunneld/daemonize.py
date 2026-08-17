"""Re-exec ourselves as a background daemon."""

from __future__ import annotations

import subprocess
import sys

from . import state


def spawn_daemon(config_path: str) -> None:
    state.ensure_runtime_dir()
    logf = open(str(state.daemon_log_path()), "ab")
    cmd = [
        sys.executable,
        "-m",
        "tunneld",
        "daemon",
        "--foreground",
        "--config",
        str(config_path),
    ]
    subprocess.Popen(
        cmd,
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    logf.close()
