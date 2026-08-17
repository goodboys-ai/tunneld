# tunneld

Config-driven SSH tunnel manager. Define your tunnels in one TOML file; tunneld
keeps them connected in the background, auto-reconnects on drops, and
multiplexes every forward to the same host onto a single ssh connection.

One [[tunnels]] block = one target host = one ssh connection.
Each [[tunnels.forwards]] block = one -L / -D / -R channel on that connection.

This is a public repository.

## Why tunneld

- **Multiplexing.** Unlike tools that open one ssh per forward, tunneld groups
  forwards by host so you authenticate once and run a single connection per
  host, carrying all of its -L / -D / -R channels.
- **Auto-reconnect.** Each connection is supervised and respawned with
  exponential backoff (1s .. 30s) when it drops.
- **Auto-reload.** Edit the config and tunneld converges to it (watch = true).
  Only changed tunnels are restarted; a parse error leaves running tunnels alone.
- **Zero magic.** It drives your system ssh, so ~/.ssh/config, ssh-agent,
  ProxyJump and known_hosts all behave exactly as you expect.

## Requirements

- Python 3.9+ (3.11+ uses the stdlib tomllib; 3.9/3.10 fall back to tomlkit)
- OpenSSH client (the ssh binary) on PATH
- Linux / macOS (control channel uses a Unix domain socket)

## Install

    uv tool install "tunneld @ git+https://github.com/goodboys-ai/tunneld"

Upgrade:

    uv tool upgrade tunneld

## Quickstart

    tunneld init          # write a commented example config
    tunneld edit          # open it in $EDITOR
    tunneld up            # start everything (daemon auto-starts in background)
    tunneld status        # show running tunnels

The config lives at $XDG_CONFIG_HOME/tunneld/tunneld.toml
(default ~/.config/tunneld/tunneld.toml). Override it with --config.

## Config example

    [defaults]
    keep_alive = 30   # seconds -> ssh ServerAliveInterval
    watch = true      # auto-reload config on change

    [[tunnels]]
    name = "prod"
    host = "prod"              # ~/.ssh/config alias or literal hostname
    user = "root"              # optional
    # port = 22                # optional
    # identity = "~/.ssh/id_ed25519"   # optional
    # ssh_options = ["Compression=yes"]

      [[tunnels.forwards]]
      mode = "local"           # -L
      local = "5432"
      remote = "db.internal:5432"

      [[tunnels.forwards]]
      mode = "local"           # -L
      local = "6379"
      remote = "redis.internal:6379"

      [[tunnels.forwards]]
      mode = "socks"           # -D
      local = "1080"

    [[tunnels]]
    name = "staging"
    host = "staging"

      [[tunnels.forwards]]
      mode = "socks"
      local = "1081"

This yields ONE ssh process for prod (carrying -L 5432, -L 6379 and -D 1080)
and one for staging. Run tunneld check to print the exact commands.

## Forward modes

- local  -> ssh -L (forwards local:remote)
- socks  -> ssh -D (dynamic SOCKS5 proxy)
- remote -> ssh -R (forwards remote:local)

The local field accepts a bare port (binds 127.0.0.1) or host:port (binds that
interface), matching OpenSSH's [bind:] syntax.

## Commands

    tunneld up [names...]            start (default: all enabled)
    tunneld down [names...]          stop (default: all)
    tunneld restart [names...]       restart (default: all)
    tunneld reload                   re-read config and converge
    tunneld status                   running state
    tunneld list                     show the config
    tunneld check                    print the ssh command per tunnel
    tunneld logs NAME --follow       tail a tunnel's ssh output
    tunneld init / edit / doctor     config helpers
    tunneld --version

## Behavior

- up launches a daemon in the background if one is not already running.
- The daemon starts every enabled tunnel and supervises it; if ssh exits it
  respawns with exponential backoff.
- The config is watched with a ~1.5s stat poll (negligible cost). On change
  only affected tunnels restart; a bad edit never tears down running tunnels.
- down NAME stops a tunnel and marks it manually-stopped until up NAME or
  restart NAME. down --kill-daemon stops everything including the daemon.
- Per-tunnel logs: $XDG_RUNTIME_DIR/tunneld/logs/NAME.log (or ~/.cache/tunneld).

## systemd (service-managed foreground daemon)

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

    systemctl --user enable --now tunneld

## Development

    git clone https://github.com/goodboys-ai/tunneld
    cd tunneld
    uv sync --extra dev          # or: python -m venv .venv && pip install -e '.[dev]'
    pytest

## License

MIT. See LICENSE.
