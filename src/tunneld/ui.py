"""Rich rendering helpers."""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .command import build_command, forward_arg

console = Console()

STATE_COLORS = {
    "running": "green",
    "reconnecting": "yellow",
    "starting": "cyan",
    "stopped": "dim",
    "error": "red",
}


def info(msg: str) -> None:
    console.print(f"[green]OK[/green] {msg}")


def error(msg: str) -> None:
    console.print(f"[bold red]ERROR[/bold red] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]WARN[/yellow] {msg}")


def render_status(data: dict) -> None:
    d = data.get("daemon", {})
    console.print(
        f"[bold]tunneld[/bold]  pid={d.get('pid')}  config={d.get('config')}"
    )
    if d.get("config_error"):
        console.print(f"[red]config error:[/red] {d['config_error']}")
    table = Table(title="Tunnels")
    for col in ("Name", "Host", "Fwd", "State", "PID", "Uptime", "Last error"):
        table.add_column(col)
    for t in data.get("tunnels", []):
        state = t["state"]
        color = STATE_COLORS.get(state, "white")
        table.add_row(
            t["name"],
            t["host"],
            str(t["forwards"]),
            f"[{color}]{state}[/{color}]",
            str(t.get("pid") or "-"),
            t.get("uptime") or "-",
            t.get("last_error") or "-",
        )
    console.print(table)


def render_list(cfg) -> None:
    table = Table(title=f"Config: {cfg.path}")
    table.add_column("Tunnel")
    table.add_column("Host")
    table.add_column("Forwards")
    for t in cfg.tunnels:
        fwds = ", ".join(forward_arg(f) for f in t.forwards)
        table.add_row(t.name, t.host, fwds)
    console.print(table)


def render_check(cfg) -> None:
    for t in cfg.tunnels:
        argv = build_command(t, cfg.keep_alive)
        console.print(f"[bold]{t.name}[/bold]  ({t.host})")
        console.print(f"  [dim]{' '.join(argv)}[/dim]")


def tail(path: Path, lines: int, follow: bool) -> None:
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
