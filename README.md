# tunneld

[![CI](https://github.com/goodboys-ai/tunneld/actions/workflows/ci.yml/badge.svg)](https://github.com/goodboys-ai/tunneld/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/tunneld)](https://pypi.org/project/tunneld/)
[![Python versions](https://img.shields.io/pypi/pyversions/tunneld)](https://pypi.org/project/tunneld/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Config-driven SSH tunnel manager for Python 3.9+. Define local, dynamic
proxy, and remote forwards in TOML; tunneld runs one system OpenSSH process per
`[[tunnels]]` entry, keeps it connected, and reloads changes automatically.

Every running, enabled `[[tunnels]]` entry uses one SSH process. Every entry
in its `forwards`, `proxy`, and `remote_forwards` arrays shares that connection.

## Features

- Local (`-L`), dynamic SOCKS5 (`-D`), and remote (`-R`) forwarding.
- Automatic reconnect with bounded exponential backoff.
- Automatic, non-destructive config reload.
- Strict Pydantic validation: unknown keys rejected with field paths, listener
  conflicts detected.
- Per-forward route rows with server-side markers and optional labels.
- Uses system `ssh`, preserving `~/.ssh/config`, ssh-agent, ProxyJump, and known_hosts.
- Ships a PEP 561 `py.typed` marker for type-checking library consumers.

## Requirements

- Python 3.9+
- OpenSSH client (`ssh` on `PATH`)
- Linux or macOS (the daemon control channel is a Unix domain socket)

Python 3.11+ uses `tomllib`; Python 3.9/3.10 use `tomli`.

## Install

### PyPI (recommended)

~~~console
uv tool install tunneld
~~~

### pipx

~~~console
pipx install tunneld
~~~

### pip

~~~console
python -m pip install --user tunneld
~~~

Update with the matching tool's upgrade command (`uv tool upgrade tunneld`,
`pipx upgrade tunneld`, or `pip install --user --upgrade tunneld`).

## Quick start

~~~console
tunneld init
tunneld edit
tunneld check --show-command
tunneld up
tunneld status
~~~

The default config is `$XDG_CONFIG_HOME/tunneld/tunneld.toml`, falling back to
`~/.config/tunneld/tunneld.toml`. Override it with a global option:

~~~console
tunneld --config ./tunneld.toml check
~~~

Global options must appear before the subcommand.

## Configuration

`tunneld init` writes a concise config. `tunneld init --full` writes a fully
commented example containing every supported setting.

~~~toml
#:schema https://raw.githubusercontent.com/goodboys-ai/tunneld/main/tunneld.schema.json

[daemon]
watch = true
watch_interval = 1.5
reconnect_initial_delay = 1.0
reconnect_max_delay = 30.0

[defaults]
keep_alive = 30
keep_alive_count = 3

[[tunnels]]
name = "prod"
host = "prod"                  # ~/.ssh/config alias or hostname
enabled = true
user = "root"                  # optional
# port = 22
# identity = "~/.ssh/id_ed25519"
ssh_options = ["Compression=yes"]

# Local forwarding (-L): listen here, connect from the SSH server side.
forwards = [
  { label = "postgres", local = 5432, remote = 5432 },
  { label = "service_b", local = 4321, remote = "db.internal:4321" },
  { local = "127.0.0.1:9090", remote = "metrics.internal:9090" },
]

# Dynamic SOCKS5 proxy (-D).
proxy = [
  { label = "browser", local = 1080 },
]

# Remote forwarding (-R): listen on the SSH server, connect back here.
remote_forwards = [
  { label = "webhook", local = 8080, remote = 18080 },
]

[[tunnels]]
name = "staging"
host = "staging"

forwards = [
  { local = 15432, remote = "postgres.internal:5432" },
]

proxy = [
  { local = 1081 },
]
~~~

This creates two SSH processes. The `prod` command contains three `-L`
arguments, one `-D`, and one `-R`.

### Forwarding semantics

| Config array | OpenSSH | Listener | Target reached from |
|---|---|---|---|
| `forwards` | `-L` | local machine | SSH server side |
| `proxy` | `-D` | local machine | dynamic through SSH |
| `remote_forwards` | `-R` | SSH server | local machine side |

`label` is optional. If present, it must be unique across all forwarding arrays
within that tunnel. It affects status and errors, not the SSH command.

An integer endpoint means `localhost:<port>` on the corresponding side:

~~~toml
{ local = 5432, remote = 5432 }
~~~

becomes `-L 5432:localhost:5432`. Use a string for an explicit address:

~~~toml
{ local = "127.0.0.1:5432", remote = "db.internal:5432" }
~~~

Bare ports written as strings (`"5432"`) are rejected; write `5432` instead.
Ports must be in `1..65535`. IPv6 endpoints use `[address]:port`.

Binding a local or proxy listener to `0.0.0.0` exposes it to other machines.
A remote `0.0.0.0` listener also requires the SSH server to permit `GatewayPorts`.

### Strict validation

Pydantic rejects unknown keys, invalid endpoints, duplicate tunnel names,
duplicate labels, conflicting enabled local listeners, managed SSH options, and
tunnels without forwarding entries. Tunnel names must start with an ASCII letter
or digit and may also contain `.`, `_`, and `-`. Disabled tunnels are excluded
from listener-conflict checks. `keep_alive_count = 0` is accepted and preserves
OpenSSH's no-termination behavior. `host` and `user` are passed directly to the
system `ssh` argv; connection or resolution errors are reported by OpenSSH.
`tunneld doctor` checks the config, SSH availability, and `ssh -G` expansion.

~~~console
tunneld check
tunneld check --show-command
tunneld schema
tunneld schema --output tunneld.schema.json
~~~

## Status model

`status` groups rows by tunnel and displays every forwarding entry:

~~~text
tunneld  pid=12844  config=/home/alice/.config/tunneld/tunneld.toml

prod  running  pid=12857  uptime=2h13m
┏━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Label    ┃ Type ┃ Route                                      ┃ State  ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ postgres │ -L   │ localhost:5432 → localhost:5432 (prod)     │ active │
│ browser  │ -D   │ localhost:1080 → dynamic (SOCKS5 via prod) │ active │
│ webhook  │ -R   │ localhost:18080 (prod) → localhost:8080    │ active │
└──────────┴──────┴────────────────────────────────────────────┴────────┘
~~~

The Route column always reads entry → exit; a `(host)` suffix marks the
endpoint resolved on the SSH server side, so identical address strings on both
sides stay distinguishable. `-D` rows use `via host` because SOCKS
destinations are dynamic. `active` means OpenSSH created the listener
successfully; it is not a destination-service health check.

## Commands

~~~text
tunneld up [NAMES...] [--wait N]   start enabled tunnels
tunneld down [NAMES...]            stop named tunnels; with no names, stop daemon
tunneld down --keep-daemon         stop all tunnels but keep the daemon
tunneld down --kill-daemon         explicitly stop tunnels and daemon
tunneld restart [NAMES...]         restart tunnels
tunneld reload                     reload and converge immediately
tunneld status                     show tunnel and forwarding-entry state
tunneld list                       inspect config without the daemon
tunneld check [--show-command]     strictly validate and normalize config
tunneld logs NAME [-n N] [--follow]  read one tunnel's SSH output
tunneld init [--full] [--force]    write a config template
tunneld edit                       open config in $EDITOR
tunneld doctor                     check config, ssh, and host aliases
tunneld schema [-o PATH]           emit JSON Schema
tunneld --version
~~~

All commands accept a global `-c/--config PATH` option before the subcommand.

## Daemon behavior

- `up` starts a detached daemon if necessary and replaces a daemon using an
  incompatible IPC protocol after an upgrade.
- Every SSH process uses `ExitOnForwardFailure=yes` and configured keepalives.
- A dropped process reconnects using the configured delay range.
- Invalid edits leave current tunnels running and appear as a config error.
- `down NAME` stays stopped until `up NAME` or `restart NAME`; it leaves other
  tunnels and the daemon running. Plain `down` stops everything and exits the
  daemon; use `down --keep-daemon` to retain the watcher.
- IPC requests and responses are capped at 1 MiB. The runtime directory is
  mode `0700` and the control socket is mode `0600`.
- Per-tunnel logs live under `$XDG_RUNTIME_DIR/tunneld/logs/` (falling back to
  `~/.cache/tunneld/logs/`), truncated in place at 10 MiB with the last 64 KiB
  kept in `<name>.log.prev`. The daemon log rotates into three generations when
  `tunneld up` spawns the detached daemon.
- The PID file is informational; daemon liveness is determined through IPC.

## systemd user service

~~~ini
# ~/.config/systemd/user/tunneld.service
[Unit]
Description=tunneld SSH tunnel manager
After=network-online.target

[Service]
ExecStart=%h/.local/bin/tunneld daemon --foreground
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
~~~

~~~console
systemctl --user daemon-reload
systemctl --user enable --now tunneld
~~~

With `Restart=always`, systemd restarts the daemon after a plain `tunneld down`;
use `systemctl --user stop tunneld` or `tunneld down --keep-daemon` instead.

## Development

~~~console
git clone https://github.com/goodboys-ai/tunneld
cd tunneld
uv sync --extra dev
uv run pre-commit install
uv run pytest          # coverage report included; CI gates on it
uv run ruff check src tests
uv run pyright
~~~

Pre-commit runs Ruff, Pyright, and codespell. CI runs Ruff, Pyright, and
coverage-gated tests across Python 3.9-3.14 (see
[.github/workflows/ci.yml](.github/workflows/ci.yml)).

## License

MIT. See [LICENSE](LICENSE).
