"""Runtime and config file locations."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def config_path() -> Path:
    """Return the configured TOML path using XDG defaults."""
    env = os.environ.get("TUNNELD_CONFIG")
    if env:
        return Path(env)
    xdg = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return Path(xdg) / "tunneld" / "tunneld.toml"


def runtime_dir() -> Path:
    """Return the private runtime directory for daemon state."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "tunneld"


def socket_path() -> Path:
    """Return the daemon control socket path."""
    return runtime_dir() / "tunneld.sock"


def pid_path() -> Path:
    """Return the informational daemon PID file path."""
    return runtime_dir() / "tunneld.pid"


def log_dir() -> Path:
    """Return the directory containing per-tunnel logs."""
    return runtime_dir() / "logs"


def daemon_log_path() -> Path:
    """Return the detached daemon diagnostic log path."""
    return runtime_dir() / "tunneld.log"


def ensure_runtime_dir() -> Path:
    """Create private runtime and log directories and return their root."""
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d
