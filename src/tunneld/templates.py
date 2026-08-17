"""Built-in TOML templates used by tunneld init/edit."""

MINIMAL_CONFIG = """\
#:schema https://raw.githubusercontent.com/goodboys-ai/tunneld/main/tunneld.schema.json

[daemon]
watch = true

[defaults]
keep_alive = 30
keep_alive_count = 3

[[tunnels]]
name = "example"
host = "example.com"  # ~/.ssh/config alias or hostname

forwards = [
  { label = "database", local = 5432, remote = "db.internal:5432" },
]

socks = [
  { local = 1080 },
]
"""


FULL_CONFIG = """\
#:schema https://raw.githubusercontent.com/goodboys-ai/tunneld/main/tunneld.schema.json
# Complete tunneld configuration example.
# One [[tunnels]] entry creates one SSH process and one SSH connection.

[daemon]
watch = true
watch_interval = 1.5
reconnect_initial_delay = 1.0
reconnect_max_delay = 30.0

[defaults]
keep_alive = 30          # ssh ServerAliveInterval
keep_alive_count = 3     # ssh ServerAliveCountMax


# Explicit SSH connection settings.
[[tunnels]]
name = "prod"
host = "prod.example.com"
enabled = true

user = "root"
port = 22
identity = "~/.ssh/id_ed25519"
# Optional per-tunnel keepalive overrides:
# keep_alive = 15
# keep_alive_count = 4
ssh_options = [
  "Compression=yes",
]

# Local forwards (-L): listen locally, connect from the SSH server side.
forwards = [
  # Integer endpoints mean localhost:<port>.
  { label = "postgres", local = 5432, remote = 5432 },
  { label = "service_b", local = 4321, remote = "db.internal:4321" },
  # label is optional; a string specifies an explicit address.
  { local = "127.0.0.1:9090", remote = "metrics.internal:9090" },
]

# Dynamic SOCKS5 forwards (-D).
socks = [
  { label = "default_proxy", local = 1080 },
  # WARNING: 0.0.0.0 exposes the proxy to other machines.
  { label = "shared_proxy", local = "0.0.0.0:1081" },
]

# Remote forwards (-R): listen on the SSH server, connect back locally.
remote_forwards = [
  { label = "webhook", local = 8080, remote = 18080 },
  { local = "127.0.0.1:9091", remote = "127.0.0.1:19091" },
]


# Use an ~/.ssh/config alias for all SSH connection settings.
[[tunnels]]
name = "staging"
host = "staging"

forwards = [
  { local = 8080, remote = 8080 },
  { label = "database", local = 15432, remote = "postgres.internal:5432" },
]

socks = [
  { local = 1082 },
]


# Disabled tunnels remain visible in list/status but are not started.
[[tunnels]]
name = "legacy"
host = "legacy.example.com"
enabled = false

forwards = [
  { label = "legacy_db", local = 13306, remote = 3306 },
]
"""
