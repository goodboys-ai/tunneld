"""Rich rendering helpers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .command import build_forward_specs, command_display

console = Console()

STATE_COLORS = {
    "active": "green",
    "running": "green",
    "reconnecting": "yellow",
    "starting": "cyan",
    "stopped": "dim",
    "disabled": "dim",
    "configured": "cyan",
    "error": "red",
}


def info(msg: str) -> None:
    """Render a successful informational message."""
    console.print(f"[green]OK[/green] {escape(str(msg))}")


def error(msg: str) -> None:
    """Render an error message."""
    console.print(f"[bold red]ERROR[/bold red] {escape(str(msg))}")


def warn(msg: str) -> None:
    """Render a warning message."""
    console.print(f"[yellow]WARN[/yellow] {escape(str(msg))}")


def _tunnel_heading(name: str, host: str, user: Optional[str]) -> str:
    heading = f"[bold]{escape(name)}[/bold]"
    if host and host != name:
        connect = f"{user}@{host}" if user else host
        heading += f" [dim]→[/dim] {escape(connect)}"
    return heading


def _route_text(entry: dict, host: str) -> str:
    """Render one entry as an entry → exit flow with server-side marker."""
    kind = str(entry.get("kind", "?"))
    listen = escape(str(entry.get("listen", "?")))
    target = escape(str(entry.get("target", "?")))
    suffix = f" [cyan]({escape(host)})[/cyan]" if host else ""
    if kind == "-D":
        via = f" [cyan]via {escape(host)}[/cyan]" if host else ""
        return f"{listen} [dim]→[/dim] dynamic (SOCKS5{via})"
    if kind == "-R":
        return f"{listen}{suffix} [dim]→[/dim] {target}"
    if kind == "-L":
        return f"{listen} [dim]→[/dim] {target}{suffix}"
    return f"{listen} [dim]→[/dim] {target}"


def _entry_table(entries, host: str = "") -> Table:
    """Render forwarding entries as a Label/Type/Route/State table."""
    table = Table()
    table.add_column("Label")
    table.add_column("Type")
    table.add_column("Route")
    table.add_column("State")
    safe_entries = entries if isinstance(entries, list) else []
    for entry in safe_entries:
        if not isinstance(entry, dict):
            continue
        entry_state = str(entry.get("state", "configured"))
        color = STATE_COLORS.get(entry_state, "white")
        table.add_row(
            escape(str(entry.get("label") or "—")),
            escape(str(entry.get("kind", "?"))),
            _route_text(entry, host),
            f"[{color}]{escape(entry_state)}[/{color}]",
        )
    return table


def render_status(data: dict) -> None:
    """Render a defensive daemon and per-forward status snapshot."""
    if not isinstance(data, dict):
        error("malformed daemon status response")
        return
    daemon = data.get("daemon", {})
    if not isinstance(daemon, dict):
        error("malformed daemon status response")
        return
    console.print(
        f"[bold]tunneld[/bold]  pid={escape(str(daemon.get('pid')))}  "
        f"config={escape(str(daemon.get('config')))}"
    )
    if daemon.get("config_error"):
        console.print(f"[red]config error:[/red] {escape(str(daemon['config_error']))}")
    tunnels = data.get("tunnels", [])
    if not isinstance(tunnels, list):
        error("malformed daemon tunnel status")
        return
    if not tunnels:
        warn("no configured tunnels")
        return
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            warn("ignored malformed tunnel status entry")
            continue
        tunnel_state = str(tunnel.get("state", "unknown"))
        color = STATE_COLORS.get(tunnel_state, "white")
        details = [
            _tunnel_heading(
                str(tunnel.get("name", "?")),
                str(tunnel.get("host", "?")),
                tunnel.get("user"),
            ),
            f"[{color}]{escape(tunnel_state)}[/{color}]",
        ]
        if tunnel.get("pid"):
            details.append(f"pid={escape(str(tunnel['pid']))}")
        if tunnel.get("uptime"):
            details.append(f"uptime={escape(str(tunnel['uptime']))}")
        console.print("  ".join(details))
        if tunnel.get("last_error"):
            console.print(f"  [red]{escape(str(tunnel['last_error']))}[/red]")
        console.print(
            _entry_table(tunnel.get("forwards", []), str(tunnel.get("host", "")))
        )


def render_list(config) -> None:
    """Render configured tunnels without contacting the daemon."""
    console.print(f"[bold]Config:[/bold] {escape(config.path)}")
    for tunnel in config.tunnels:
        state = "configured" if tunnel.enabled else "disabled"
        color = STATE_COLORS[state]
        console.print(
            f"{_tunnel_heading(tunnel.name, tunnel.host, tunnel.user)}  "
            f"[{color}]{state}[/{color}]"
        )
        entries = [spec.status(state) for spec in build_forward_specs(tunnel)]
        console.print(_entry_table(entries, tunnel.host))


def render_check(config, show_command: bool = False) -> None:
    """Render validated entries and optionally their OpenSSH commands."""
    info(f"config valid: {config.path}")
    for tunnel in config.tunnels:
        state = "configured" if tunnel.enabled else "disabled"
        console.print(_tunnel_heading(tunnel.name, tunnel.host, tunnel.user))
        entries = [spec.status(state) for spec in build_forward_specs(tunnel)]
        console.print(_entry_table(entries, tunnel.host))
        if show_command:
            command = escape(command_display(tunnel, config.defaults))
            console.print(f"  [dim]{command}[/dim]")


def tail(path: Path, lines: int, follow: bool) -> None:
    """Print the tail of a binary log and optionally follow new lines."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= lines:
            size = min(4096, pos)
            pos -= size
            fh.seek(pos)
            data = fh.read(size) + data
        console.print(data.decode(errors="replace"), end="")
        if not follow:
            return
        fh.seek(0, 2)
        while True:
            line = fh.readline()
            if line:
                console.print(line.decode(errors="replace"), end="")
            else:
                time.sleep(0.2)
