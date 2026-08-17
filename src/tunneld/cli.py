"""tunneld command line interface."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import typer

from . import __version__, daemonize, state, ui
from .config import ConfigError, load_config
from .ipc import IPCError, send_request

app = typer.Typer(
    no_args_is_help=True,
    help="Config-driven SSH tunnel manager (one ssh connection per host).",
)

TEMPLATE = """\
# tunneld configuration
# One [[tunnels]] block == one target host == one SSH connection.
# Each [[tunnels.forwards]] == one -L / -D / -R channel on that connection.

[defaults]
keep_alive = 30   # seconds -> ssh ServerAliveInterval
watch = true      # auto-reload config on change

[[tunnels]]
name = "example"
host = "example.com"        # ~/.ssh/config alias or literal hostname
# user = "root"             # optional
# port = 22                 # optional
# identity = "~/.ssh/id_ed25519"   # optional
# ssh_options = ["Compression=yes"]

  [[tunnels.forwards]]
  mode = "local"            # local(-L) | socks(-D) | remote(-R)
  local = "5432"
  remote = "db.internal:5432"

  [[tunnels.forwards]]
  mode = "socks"            # -D
  local = "1080"
"""


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
    version: bool = typer.Option(
        False, "--version", is_eager=True, help="Show version and exit"
    ),
):
    if version:
        typer.echo(f"tunneld {__version__}")
        raise typer.Exit()
    ctx.obj = {"config": config}


def _cfg(ctx: typer.Context) -> Path:
    return ctx.obj["config"] or state.config_path()


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _ensure_daemon(ctx: typer.Context) -> None:
    path = _cfg(ctx)
    try:
        send_request("status")
        return
    except IPCError:
        pass
    daemonize.spawn_daemon(str(path))
    for _ in range(50):
        time.sleep(0.1)
        try:
            send_request("status")
            return
        except IPCError:
            continue
    raise RuntimeError(
        "daemon did not come up; check " + str(state.daemon_log_path())
    )


def _wait_for_up(names: Optional[List[str]], seconds: int) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            data = send_request("status")
        except IPCError:
            time.sleep(0.2)
            continue
        rows = data.get("tunnels", [])
        target = set(names) if names else None
        relevant = [r for r in rows if target is None or r["name"] in target]
        if relevant and all(r["state"] == "running" for r in relevant):
            return
        time.sleep(0.3)
    ui.warn("timeout waiting for tunnels to come up")


@app.command()
def up(
    ctx: typer.Context,
    names: List[str] = typer.Argument(None, help="Tunnel names (default: all)"),
    wait: int = typer.Option(0, "--wait", help="Wait up to N seconds for tunnels"),
):
    """Start tunnels (launching the daemon if needed)."""
    path = _cfg(ctx)
    try:
        load_config(path)
    except (OSError, ConfigError) as exc:
        ui.error(f"invalid config: {exc}")
        raise typer.Exit(1)
    _ensure_daemon(ctx)
    try:
        if names:
            for n in names:
                send_request("start", name=n)
        else:
            send_request("reload")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)
    if wait:
        _wait_for_up(names, wait)
    ui.info("tunnels up")


@app.command()
def down(
    ctx: typer.Context,
    names: List[str] = typer.Argument(None, help="Tunnel names (default: all)"),
    kill_daemon: bool = typer.Option(
        False, "--kill-daemon", help="Also stop the daemon"
    ),
):
    """Stop tunnels."""
    if kill_daemon:
        try:
            send_request("shutdown")
            ui.info("daemon stopped")
        except IPCError:
            ui.warn("daemon not running")
        return
    try:
        if names:
            for n in names:
                send_request("stop", name=n)
        else:
            send_request("stop")
        ui.info("tunnels stopped")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


@app.command()
def restart(
    ctx: typer.Context,
    names: List[str] = typer.Argument(None, help="Tunnel names (default: all)"),
):
    """Restart tunnels."""
    try:
        if names:
            for n in names:
                send_request("restart", name=n)
        else:
            send_request("restart")
        ui.info("restarted")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


@app.command()
def reload(ctx: typer.Context):
    """Re-read the config and converge (automatic when watch=true)."""
    try:
        send_request("reload")
        ui.info("reloaded")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


@app.command()
def status(ctx: typer.Context):
    """Show running tunnels."""
    try:
        data = send_request("status")
    except IPCError:
        ui.error("daemon not running (try 'tunneld up')")
        raise typer.Exit(1)
    ui.render_status(data)


@app.command("list")
def list_cmd(ctx: typer.Context):
    """Show tunnels defined in the config (does not touch the daemon)."""
    try:
        cfg = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        ui.error(f"invalid config: {exc}")
        raise typer.Exit(1)
    ui.render_list(cfg)


@app.command()
def check(ctx: typer.Context):
    """Validate the config and print the ssh command each tunnel would run."""
    try:
        cfg = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        ui.error(f"invalid config: {exc}")
        raise typer.Exit(1)
    ui.render_check(cfg)


@app.command()
def logs(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Tunnel name"),
    follow: bool = typer.Option(False, "--follow", "-f"),
    lines: int = typer.Option(50, "--lines", "-n"),
):
    """Tail the ssh output of one tunnel."""
    path = state.log_dir() / f"{_safe(name)}.log"
    if not path.exists():
        ui.error(f"no log for {name!r}")
        raise typer.Exit(1)
    ui.tail(path, lines, follow)


@app.command()
def init(ctx: typer.Context, force: bool = typer.Option(False, "--force")):
    """Write a commented example config."""
    path = _cfg(ctx)
    if path.exists() and not force:
        ui.error(f"{path} already exists (use --force to overwrite)")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE)
    ui.info(f"wrote {path}")


@app.command()
def edit(ctx: typer.Context):
    """Open the config in $EDITOR."""
    path = _cfg(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(TEMPLATE)
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(path)])


@app.command()
def doctor(ctx: typer.Context):
    """Diagnose common problems."""
    problems = []
    if shutil.which("ssh") is None:
        problems.append("ssh not found in PATH")
    try:
        cfg = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        problems.append(f"config invalid: {exc}")
        cfg = None
    if cfg is not None:
        for t in cfg.tunnels:
            r = subprocess.run(
                ["ssh", "-G", t.host], capture_output=True, text=True
            )
            if r.returncode != 0:
                problems.append(
                    f"host {t.name!r} ({t.host}) not resolvable by ssh: "
                    f"{r.stderr.strip()}"
                )
    if not problems:
        ui.info("all checks passed")
    else:
        for p in problems:
            ui.error(p)
        raise typer.Exit(1)


@app.command(hidden=True)
def daemon(
    ctx: typer.Context,
    foreground: bool = typer.Option(
        True, "--foreground", help="Run in foreground"
    ),
):
    """Run the daemon (internal; used by 'up' and systemd)."""
    from .daemon import Daemon

    Daemon(str(_cfg(ctx))).run()
