# tunneld

Config-driven SSH tunnel manager for Python 3.9+. Define local, SOCKS, and
remote forwards in TOML; tunneld runs one system OpenSSH process per target,
keeps it connected, and reloads changes automatically.

One `[[tunnels]]` entry means one SSH connection. Every entry in its
`forwards`, `socks`, and `remote_forwards` arrays shares that connection.

## Features

- Local (`-L`), dynamic SOCKS5 (`-D`), and remote (`-R`) forwarding.
- One SSH process per tunnel, regardless of forwarding-entry count.
- Automatic reconnect with bounded exponential backoff.
- Automatic, non-destructive config reload.
- Strict Pydantic validation with unknown-key rejection and useful field paths.
- Per-forward status rows, optional labels, listener conflict checks, and JSON Schema.
- Uses system `ssh`, preserving `~/.ssh/config`, ssh-agent, ProxyJump, and known_hosts.

## Requirements

- Python 3.9+
- OpenSSH client (`ssh` on `PATH`)
- Linux or macOS (the daemon control channel is a Unix domain socket)

Python 3.11+ uses `tomllib`; Python 3.9/3.10 use `tomli`.

## Install

~~~console
uv tool install "tunneld @ git+https://github.com/goodboys-ai/tunneld"
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

# Dynamic SOCKS5 forwarding (-D).
socks = [
  { label = "proxy", local = 1080 },
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

socks = [
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

Binding a local or SOCKS listener to `0.0.0.0` exposes it to other machines.
A remote `0.0.0.0` listener also requires the SSH server to permit `GatewayPorts`.

### Forward direction

| Config array | OpenSSH | Listener | Target reached from |
|---|---|---|---|
| `forwards` | `-L` | local machine | SSH server side |
| `socks` | `-D` | local machine | dynamic through SSH |
| `remote_forwards` | `-R` | SSH server | local machine side |

`label` is optional. If present, it must be unique across all forwarding arrays
within that tunnel. It affects status and errors, not the SSH command.

### Strict validation

Pydantic rejects unknown keys, invalid endpoints, duplicate tunnel names,
duplicate labels, conflicting enabled local listeners, managed SSH options, and
tunnels without forwarding entries. Tunnel names must start with an ASCII letter
or digit and may also contain `.`, `_`, and `-`. Disabled tunnels are excluded
from listener-conflict checks. `keep_alive_count = 0` is accepted and preserves
OpenSSH's no-termination behavior.

~~~console
tunneld check
tunneld check --show-command
tunneld schema
tunneld schema --output tunneld.schema.json
~~~

## Status model

`status` groups rows by tunnel and displays every forwarding entry:

~~~text
prod  prod  running  pid=12844  uptime=2h13m

LABEL     TYPE  SIDE    LISTEN          TARGET          STATE
postgres  -L    local   localhost:5432  localhost:5432  active
proxy     -D    local   localhost:1080  SOCKS5          active
webhook   -R    remote  localhost:18080 localhost:8080  active
~~~

All entries in a tunnel share one SSH process, so their state inherits the
tunnel state. `active` means OpenSSH created the listener successfully; it is
not a destination-service health check.

## Commands

~~~text
tunneld up [NAMES...]             start enabled tunnels
tunneld down [NAMES...]           stop tunnels but keep the daemon
tunneld down --kill-daemon        stop tunnels and daemon
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

- `up` starts a detached daemon if necessary.
- Every SSH process uses `ExitOnForwardFailure=yes` and configured keepalives.
- A dropped process reconnects using the configured delay range.
- The watcher performs one `stat` at the configured interval and only parses
  when mtime changes.
- Invalid edits leave current tunnels running and appear as a config error.
- `down NAME` stays stopped until `up NAME` or `restart NAME`.
- Logs live under `$XDG_RUNTIME_DIR/tunneld/logs/`, falling back to
  `~/.cache/tunneld/logs/`.

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
uv run pytest
~~~

## License

MIT. See [LICENSE](LICENSE).
