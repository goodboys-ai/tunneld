"""Strict Pydantic models and TOML loader for tunneld."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Dict, Iterator, List, Optional, Tuple, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # Python 3.9 / 3.10
    import tomli as _toml  # type: ignore


class ConfigError(ValueError):
    """A user-facing TOML or schema validation error."""


def _endpoint_parts(value: Union[int, str]) -> Tuple[Optional[str], int]:
    if isinstance(value, int):
        return None, value

    if value.isdigit():
        raise ValueError("use an integer for a bare port, for example local = 5432")

    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)]:(\d+)", value)
        if not match:
            raise ValueError("IPv6 endpoints must use [address]:port")
        host, port_text = match.groups()
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator or not host or not port_text:
            raise ValueError("endpoint must be a port integer or host:port string")
        if ":" in host:
            raise ValueError("IPv6 endpoints must use [address]:port")

    port = int(port_text) if port_text.isdigit() else 0
    if not 1 <= port <= 65535:
        raise ValueError("endpoint port must be between 1 and 65535")
    return host, port


def _validate_endpoint(value: Union[int, str]) -> Union[int, str]:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("endpoint must not be empty")
    _endpoint_parts(value)
    return value


Port = Annotated[StrictInt, Field(ge=1, le=65535)]
Endpoint = Annotated[Union[Port, StrictStr], AfterValidator(_validate_endpoint)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveSeconds = Union[
    Annotated[StrictInt, Field(gt=0)],
    Annotated[float, Field(strict=True, gt=0)],
]
Name = Annotated[
    StrictStr, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class DaemonConfig(StrictModel):
    watch: StrictBool = True
    watch_interval: PositiveSeconds = 1.5
    reconnect_initial_delay: PositiveSeconds = 1.0
    reconnect_max_delay: PositiveSeconds = 30.0

    @model_validator(mode="after")
    def validate_delays(self) -> "DaemonConfig":
        if self.reconnect_initial_delay > self.reconnect_max_delay:
            raise ValueError(
                "reconnect_initial_delay must not exceed reconnect_max_delay"
            )
        return self


class DefaultsConfig(StrictModel):
    keep_alive: NonNegativeInt = 30
    keep_alive_count: NonNegativeInt = 3


class LabeledForward(StrictModel):
    label: Optional[NonEmptyString] = None


class LocalForwardConfig(LabeledForward):
    local: Endpoint
    remote: Endpoint


class SocksForwardConfig(LabeledForward):
    local: Endpoint


class RemoteForwardConfig(LabeledForward):
    local: Endpoint
    remote: Endpoint


_MANAGED_SSH_OPTIONS = {
    "controlmaster",
    "controlpath",
    "controlpersist",
    "dynamicforward",
    "exitonforwardfailure",
    "forkafterauthentication",
    "localforward",
    "remoteforward",
    "requesttty",
    "serveralivecountmax",
    "serveraliveinterval",
    "sessiontype",
}


class TunnelConfig(StrictModel):
    name: Name
    host: NonEmptyString
    enabled: StrictBool = True

    user: Optional[NonEmptyString] = None
    port: Optional[Port] = None
    identity: Optional[NonEmptyString] = None
    keep_alive: Optional[NonNegativeInt] = None
    keep_alive_count: Optional[NonNegativeInt] = None
    ssh_options: List[NonEmptyString] = Field(default_factory=list)

    forwards: List[LocalForwardConfig] = Field(default_factory=list)
    socks: List[SocksForwardConfig] = Field(default_factory=list)
    remote_forwards: List[RemoteForwardConfig] = Field(default_factory=list)

    @field_validator("ssh_options")
    @classmethod
    def validate_ssh_options(cls, options: List[str]) -> List[str]:
        for option in options:
            if "=" not in option:
                raise ValueError(f"ssh option {option!r} must use Key=value syntax")
            key = option.split("=", 1)[0].strip().lower()
            if key in _MANAGED_SSH_OPTIONS:
                raise ValueError(
                    f"ssh option {key!r} is managed by tunneld and cannot be overridden"
                )
        return options

    @model_validator(mode="after")
    def validate_forward_entries(self) -> "TunnelConfig":
        entries = list(self.iter_forward_entries())
        if not entries:
            raise ValueError(
                "at least one forwards, socks, or remote_forwards entry is required"
            )

        labels: Dict[str, str] = {}
        for kind, entry in entries:
            if entry.label is None:
                continue
            if entry.label in labels:
                raise ValueError(
                    f"duplicate label {entry.label!r} in {kind} and "
                    f"{labels[entry.label]}"
                )
            labels[entry.label] = kind

        remote_listeners: List[Tuple[str, Endpoint]] = []
        for entry in self.remote_forwards:
            for previous_label, previous in remote_listeners:
                if _listeners_conflict(previous, entry.remote):
                    label = entry.label or "unlabeled remote forward"
                    raise ValueError(
                        f"remote listener for {label!r} conflicts with "
                        f"{previous_label!r}"
                    )
            remote_listeners.append(
                (entry.label or "unlabeled remote forward", entry.remote)
            )
        return self

    def iter_forward_entries(self) -> Iterator[Tuple[str, LabeledForward]]:
        for entry in self.forwards:
            yield "forwards", entry
        for entry in self.socks:
            yield "socks", entry
        for entry in self.remote_forwards:
            yield "remote_forwards", entry

    def effective_keep_alive(self, defaults: DefaultsConfig) -> int:
        return self.keep_alive if self.keep_alive is not None else defaults.keep_alive

    def effective_keep_alive_count(self, defaults: DefaultsConfig) -> int:
        return (
            self.keep_alive_count
            if self.keep_alive_count is not None
            else defaults.keep_alive_count
        )


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_WILDCARD_HOSTS = {"*", "0.0.0.0", "::"}


def _listener_identity(endpoint: Endpoint) -> Tuple[str, int]:
    host, port = _endpoint_parts(endpoint)
    if host is None:
        return "loopback", port
    normalized = host.strip("[]").lower()
    if normalized in _LOOPBACK_HOSTS:
        return "loopback", port
    if normalized in _WILDCARD_HOSTS:
        return "wildcard", port
    return normalized, port


def _listeners_conflict(left: Endpoint, right: Endpoint) -> bool:
    left_host, left_port = _listener_identity(left)
    right_host, right_port = _listener_identity(right)
    if left_port != right_port:
        return False
    if "wildcard" in (left_host, right_host):
        return True
    return left_host == right_host


class AppConfig(StrictModel):
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    tunnels: List[TunnelConfig] = Field(default_factory=list)

    _path: str = PrivateAttr(default="")

    @property
    def path(self) -> str:
        return self._path

    @model_validator(mode="after")
    def validate_tunnels(self) -> "AppConfig":
        names: Dict[str, int] = {}
        local_listeners: List[Tuple[str, str, Endpoint]] = []
        remote_listeners: List[Tuple[str, str, Endpoint]] = []

        for index, tunnel in enumerate(self.tunnels):
            if tunnel.name in names:
                raise ValueError(
                    f"duplicate tunnel name {tunnel.name!r} at indexes "
                    f"{names[tunnel.name]} and {index}"
                )
            names[tunnel.name] = index
            if not tunnel.enabled:
                continue

            for entry in [*tunnel.forwards, *tunnel.socks]:
                label = entry.label or "unlabeled forward"
                for previous_tunnel, previous_label, previous in local_listeners:
                    if _listeners_conflict(previous, entry.local):
                        raise ValueError(
                            f"local listener for {tunnel.name}/{label} conflicts with "
                            f"{previous_tunnel}/{previous_label}"
                        )
                local_listeners.append((tunnel.name, label, entry.local))

            for entry in tunnel.remote_forwards:
                label = entry.label or "unlabeled remote forward"
                for previous_host, previous_label, previous in remote_listeners:
                    if previous_host == tunnel.host and _listeners_conflict(
                        previous, entry.remote
                    ):
                        raise ValueError(
                            f"remote listener for {tunnel.name}/{label} conflicts with "
                            f"{previous_label} on host {tunnel.host!r}"
                        )
                remote_listeners.append(
                    (tunnel.host, f"{tunnel.name}/{label}", entry.remote)
                )
        return self


def _format_location(location: Tuple[Any, ...]) -> str:
    result = ""
    for part in location:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += ("." if result else "") + str(part)
    return result or "config"


def _format_validation_error(error: ValidationError) -> str:
    lines = ["invalid configuration:"]
    for item in error.errors(include_url=False):
        location = _format_location(tuple(item["loc"]))
        lines.append(f"  {location}: {item['msg']}")
    return "\n".join(lines)


def parse_config(document: Dict[str, Any], path: str = "") -> AppConfig:
    try:
        config = AppConfig.model_validate(document)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from None
    config._path = path
    return config


def load_config(path: Union[str, Path]) -> AppConfig:
    path = Path(path)
    try:
        document = _toml.loads(path.read_text(encoding="utf-8-sig"))
    except _toml.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from None
    return parse_config(document, str(path))


def config_schema() -> Dict[str, Any]:
    schema = AppConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://raw.githubusercontent.com/goodboys-ai/tunneld/main/tunneld.schema.json"
    )
    return schema
