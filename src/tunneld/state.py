"""Runtime and config file locations."""

from __future__ import annotations

import os
from pathlib import Path


def config_path() -> Path:
    env = os.environ.get("TUNNELD_CONFIG")
    if env:
        return Path(env)
    xdg = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return Path(xdg) / "tunneld" / "tunneld.toml"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "tunneld"


def socket_path() -> Path:
    return runtime_dir() / "tunneld.sock"


def pid_path() -> Path:
    return runtime_dir() / "tunneld.pid"


def log_dir() -> Path:
    return runtime_dir() / "logs"


def daemon_log_path() -> Path:
    return runtime_dir() / "tunneld.log"


def ensure_runtime_dir() -> Path:
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d
