"""Rich rendering helpers."""

from __future__ import annotations

import time
from pathlib import Path

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


def _entry_table(entries, title: str = "") -> Table:
    table = Table(title=title or None)
    table.add_column("Label")
    table.add_column("Type")
    table.add_column("Side")
    table.add_column("Listen")
    table.add_column("Target")
    table.add_column("State")
    safe_entries = entries if isinstance(entries, list) else []
    for entry in safe_entries:
        if not isinstance(entry, dict):
            continue
        entry_state = entry.get("state", "configured")
        color = STATE_COLORS.get(entry_state, "white")
        table.add_row(
            escape(entry.get("label") or "—"),
            str(entry.get("kind", "?")),
            str(entry.get("listen_side", "?")),
            escape(str(entry.get("listen", "?"))),
            escape(str(entry.get("target", "?"))),
            f"[{color}]{entry_state}[/{color}]",
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
        f"[bold]tunneld[/bold]  pid={daemon.get('pid')}  "
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
            f"[bold]{escape(str(tunnel.get('name', '?')))}[/bold]",
            escape(str(tunnel.get("host", "?"))),
            f"[{color}]{tunnel_state}[/{color}]",
        ]
        if tunnel.get("pid"):
            details.append(f"pid={tunnel['pid']}")
        if tunnel.get("uptime"):
            details.append(f"uptime={tunnel['uptime']}")
        console.print("  ".join(details))
        if tunnel.get("last_error"):
            console.print(f"  [red]{escape(str(tunnel['last_error']))}[/red]")
        console.print(_entry_table(tunnel.get("forwards", [])))


def render_list(config) -> None:
    """Render configured tunnels without contacting the daemon."""
    console.print(f"[bold]Config:[/bold] {escape(config.path)}")
    for tunnel in config.tunnels:
        state = "configured" if tunnel.enabled else "disabled"
        color = STATE_COLORS[state]
        console.print(
            f"[bold]{escape(tunnel.name)}[/bold]  {escape(tunnel.host)}  "
            f"[{color}]{state}[/{color}]"
        )
        entries = [spec.status(state) for spec in build_forward_specs(tunnel)]
        console.print(_entry_table(entries))


def render_check(config, show_command: bool = False) -> None:
    """Render validated entries and optionally their OpenSSH commands."""
    info(f"config valid: {config.path}")
    for tunnel in config.tunnels:
        state = "configured" if tunnel.enabled else "disabled"
        console.print(f"[bold]{escape(tunnel.name)}[/bold]  ({escape(tunnel.host)})")
        entries = [spec.status(state) for spec in build_forward_specs(tunnel)]
        console.print(_entry_table(entries))
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
