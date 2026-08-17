"""Configuration model and loader for tunneld."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # Python 3.9 / 3.10
    import tomlkit as _toml  # type: ignore

FORWARD_MODES = ("local", "socks", "remote")


class ConfigError(ValueError):
    """Raised when the configuration is invalid."""


@dataclass
class Forward:
    """A single -L / -D / -R forward on a tunnel."""

    mode: str = "local"
    local: str = ""
    remote: Optional[str] = None

    def validate(self, tunnel_name: str) -> None:
        if self.mode not in FORWARD_MODES:
            raise ConfigError(
                f"{tunnel_name}: unknown forward mode {self.mode!r} "
                f"(use one of {', '.join(FORWARD_MODES)})"
            )
        if not self.local:
            raise ConfigError(f"{tunnel_name}: forward 'local' is required")
        if self.mode in ("local", "remote") and not self.remote:
            raise ConfigError(f"{tunnel_name}: {self.mode} forward needs 'remote'")


@dataclass
class Tunnel:
    """One [[tunnels]] block == one target host == one ssh connection."""

    name: str
    host: str
    user: Optional[str] = None
    port: Optional[int] = None
    identity: Optional[str] = None
    keep_alive: Optional[int] = None
    enabled: bool = True
    ssh_options: List[str] = field(default_factory=list)
    forwards: List[Forward] = field(default_factory=list)

    def validate(self) -> None:
        if not self.name:
            raise ConfigError("tunnel 'name' is required")
        if not self.host:
            raise ConfigError(f"{self.name}: 'host' is required")
        if not self.forwards:
            raise ConfigError(
                f"{self.name}: at least one [[tunnels.forwards]] is required"
            )
        for fwd in self.forwards:
            fwd.validate(self.name)


@dataclass
class Config:
    """Top-level config ([defaults] + [[tunnels]])."""

    keep_alive: int = 30
    watch: bool = True
    tunnels: List[Tunnel] = field(default_factory=list)
    path: str = ""

    def validate(self) -> None:
        names = set()
        for t in self.tunnels:
            if t.name in names:
                raise ConfigError(f"duplicate tunnel name {t.name!r}")
            names.add(t.name)
            t.validate()


def _get_str(table, key: str, default: str = "") -> str:
    value = table.get(key, default)
    return str(value).strip() if value is not None else default


def _parse_forward(table, tunnel_name: str) -> Forward:
    mode = str(table.get("mode", "local")).strip().lower()
    local = _get_str(table, "local")
    remote = table.get("remote")
    remote = str(remote).strip() if remote is not None else None
    fwd = Forward(mode=mode, local=local, remote=remote)
    fwd.validate(tunnel_name)
    return fwd


def _parse_tunnel(table) -> Tunnel:
    name = _get_str(table, "name")
    host = _get_str(table, "host")
    user = _get_str(table, "user") or None
    port = table.get("port")
    port = int(port) if port is not None else None
    identity = _get_str(table, "identity") or None
    keep_alive = table.get("keep_alive")
    keep_alive = int(keep_alive) if keep_alive is not None else None
    enabled = bool(table.get("enabled", True))
    ssh_options = [str(o) for o in table.get("ssh_options", [])]
    forwards = [_parse_forward(f, name) for f in table.get("forwards", [])]
    tunnel = Tunnel(
        name=name,
        host=host,
        user=user,
        port=port,
        identity=identity,
        keep_alive=keep_alive,
        enabled=enabled,
        ssh_options=ssh_options,
        forwards=forwards,
    )
    tunnel.validate()
    return tunnel


def parse(doc) -> Config:
    defaults = doc.get("defaults", {})
    keep_alive = int(defaults.get("keep_alive", 30))
    watch = bool(defaults.get("watch", True))
    tunnels = [_parse_tunnel(t) for t in doc.get("tunnels", [])]
    cfg = Config(keep_alive=keep_alive, watch=watch, tunnels=tunnels)
    cfg.validate()
    return cfg


def load_config(path) -> Config:
    with open(path, "rb") as fh:
        data = fh.read()
    cfg = parse(_toml.loads(data.decode("utf-8")))
    cfg.path = str(path)
    return cfg
