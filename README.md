# tunneld

Config-driven SSH tunnel manager for Python 3.9+. Define local, dynamic
proxy, and remote forwards in TOML; tunneld runs one system OpenSSH process per
`[[tunnels]]` entry, keeps it connected, and reloads changes automatically.

One `[[tunnels]]` entry means one SSH connection. Every entry in its
`forwards`, `proxy`, and `remote_forwards` arrays shares that connection.

## Features

- Local (`-L`), dynamic SOCKS5 (`-D`), and remote (`-R`) forwarding.
- Automatic reconnect with bounded exponential backoff.
- Automatic, non-destructive config reload.
- Strict Pydantic validation with unknown-key rejection and useful field paths.
- Per-forward route rows with server-side markers, optional labels, listener conflict checks, and JSON Schema.
- Uses system `ssh`, preserving `~/.ssh/config`, ssh-agent, ProxyJump, and known_hosts.
- Ships a PEP 561 `py.typed` marker for type-checking library consumers.

## Requirements

- Python 3.9+
- OpenSSH client (`ssh` on `PATH`)
- Linux or macOS (the daemon control channel is a Unix domain socket)

Python 3.11+ uses `tomllib`; Python 3.9/3.10 use `tomli`.

## Install

Requires Python 3.9+ and an OpenSSH client on PATH.

### PyPI (recommended)

~~~console
uv tool install tunneld
uv tool upgrade tunneld
~~~

### pipx

~~~console
pipx install tunneld
pipx upgrade tunneld
~~~

### pip

~~~console
python -m pip install --user tunneld
python -m pip install --user --upgrade tunneld
~~~

### Pin a GitHub release

~~~console
uv tool install --force \
  "tunneld @ git+https://github.com/goodboys-ai/tunneld@v0.3.1"
~~~

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

### Endpoint shorthand

An integer means `localhost:<port>` on the corresponding side:

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

### Forward direction

| Config array | OpenSSH | Listener | Target reached from |
|---|---|---|---|
| `forwards` | `-L` | local machine | SSH server side |
| `proxy` | `-D` | local machine | dynamic through SSH |
| `remote_forwards` | `-R` | SSH server | local machine side |

`label` is optional. If present, it must be unique across all forwarding arrays
within that tunnel. It affects status and errors, not the SSH command.

### Strict validation

Pydantic rejects unknown keys, invalid endpoints, duplicate tunnel names,
duplicate labels, conflicting enabled local listeners, managed SSH options, and
tunnels without forwarding entries. Tunnel names must start with an ASCII letter
or digit and may also contain `.`, `_`, and `-`. Disabled tunnels are excluded
from listener-conflict checks. `keep_alive_count = 0` is accepted and preserves
OpenSSH's no-termination behavior. `host` and `user` are passed directly to the
system `ssh` argv; invalid aliases or names are diagnosed by OpenSSH and
`tunneld doctor`, without shell interpretation.

~~~console
tunneld check
tunneld check --show-command
tunneld schema
tunneld schema --output tunneld.schema.json
~~~

## Status model

`status` groups rows by tunnel and displays every forwarding entry:

~~~text
tunneld  pid=12844  config=~/.config/tunneld/tunneld.toml

prod  running  pid=12844  uptime=2h13m
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
sides stay distinguishable. When `name == host`, the header shows the name
once; otherwise it shows `name → [user@]host`. `-D` rows use `via host`
instead of a suffix because SOCKS destinations are dynamic.

All entries in a tunnel share one SSH process, so their state inherits the
tunnel state. `active` means OpenSSH created the listener successfully; it is
not a destination-service health check.

## Commands

~~~text
tunneld up [NAMES...]             start enabled tunnels
tunneld down [NAMES...]           stop named tunnels; with no names, stop daemon
tunneld down --keep-daemon        stop all tunnels but keep the daemon
tunneld down --kill-daemon        explicitly stop tunnels and daemon
tunneld restart [NAMES...]        restart tunnels
tunneld reload                    reload and converge immediately
tunneld status                    show tunnel and forwarding-entry state
tunneld list                      inspect config without the daemon
tunneld check [--show-command]    strictly validate and normalize config
tunneld logs NAME [--follow]      read one tunnel's SSH output
tunneld init [--full]             write a config template
tunneld edit                      open config in $EDITOR
tunneld doctor                    check config, ssh, and host aliases
tunneld schema [-o PATH]          emit JSON Schema
tunneld --version
~~~

## Daemon behavior

- `up` starts a detached daemon if necessary and replaces a daemon using an
  incompatible IPC protocol after an upgrade.
- Every SSH process uses `ExitOnForwardFailure=yes` and configured keepalives.
- A dropped process reconnects using the configured delay range.
- The watcher performs one `stat` at the configured interval and only parses
  when mtime changes.
- Invalid edits leave current tunnels running and appear as a config error.
- `down NAME` stays stopped until `up NAME` or `restart NAME`; it leaves other
  tunnels and the daemon running. Plain `down` stops everything and exits the
  daemon; use `down --keep-daemon` to retain the watcher.
- IPC requests and responses are capped at 1 MiB. The runtime directory is
  mode `0700` and the control socket is mode `0600`.
- Per-tunnel logs are truncated in place at 10 MiB; the last 64 KiB is
  preserved in `<name>.log.prev`, and `logs -f` continues across truncation.
  The daemon log rotates into three generations when it exceeds the limit at
  daemon startup.
- Logs live under `$XDG_RUNTIME_DIR/tunneld/logs/`, falling back to
  `~/.cache/tunneld/logs/`.
- The PID file is informational and overwritten at startup; daemon liveness is
  determined through IPC. Like any Unix process, `SIGKILL` can leave a stale
  PID file, which does not prevent the next start.

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

## Development

~~~console
git clone https://github.com/goodboys-ai/tunneld
cd tunneld
uv sync --extra dev
pre-commit install
uv run pytest          # coverage report included; CI enforces >=70%
uv run ruff check src tests
uv run pyright
~~~

Pre-commit runs Ruff lint/format, Pyright, and codespell. CI runs the same
checks plus the coverage gate on Python 3.9, 3.11, and 3.12.

## License

MIT. See [LICENSE](LICENSE).
