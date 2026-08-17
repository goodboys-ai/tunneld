"""tunneld command line interface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import typer

from . import __version__, daemonize, state, ui
from .config import ConfigError, config_schema, load_config
from .ipc import IPC_PROTOCOL_VERSION, IPCError, send_request
from .templates import FULL_CONFIG, MINIMAL_CONFIG

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help="Config-driven SSH tunnel manager (one SSH connection per tunnel).",
)


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
    """Initialize global CLI state and handle the eager version option."""
    if version:
        typer.echo(f"tunneld {__version__}")
        raise typer.Exit()
    ctx.obj = {"config": config}


def _cfg(ctx: typer.Context) -> Path:
    return ctx.obj["config"] or state.config_path()


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _daemon_compatible(data: dict) -> bool:
    daemon = data.get("daemon")
    return (
        isinstance(daemon, dict)
        and daemon.get("protocol_version") == IPC_PROTOCOL_VERSION
    )


def _incompatible_daemon_message() -> str:
    return (
        "daemon protocol is incompatible with this CLI; run "
        "'tunneld down --kill-daemon' and then 'tunneld up'"
    )


def _stop_incompatible_daemon() -> None:
    try:
        send_request("shutdown")
    except IPCError as exc:
        raise RuntimeError(_incompatible_daemon_message()) from exc
    for _ in range(50):
        time.sleep(0.1)
        try:
            send_request("status")
        except IPCError:
            return
    raise RuntimeError(
        _incompatible_daemon_message() + " (daemon did not stop after shutdown)"
    )


def _ensure_daemon(ctx: typer.Context) -> None:
    path = _cfg(ctx)
    try:
        data = send_request("status")
    except IPCError:
        data = None
    if data is not None:
        if _daemon_compatible(data):
            return
        _stop_incompatible_daemon()

    daemonize.spawn_daemon(str(path))
    for _ in range(50):
        time.sleep(0.1)
        try:
            data = send_request("status")
        except IPCError:
            continue
        if _daemon_compatible(data):
            return
        raise RuntimeError(_incompatible_daemon_message())
    raise RuntimeError("daemon did not come up; check " + str(state.daemon_log_path()))


def _wait_for_up(names: Optional[List[str]], seconds: int) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            data = send_request("status")
        except IPCError:
            time.sleep(0.2)
            continue
        if not _daemon_compatible(data):
            ui.error(_incompatible_daemon_message())
            return
        rows = data.get("tunnels", [])
        target = set(names) if names else None
        relevant = [
            row
            for row in rows
            if row.get("enabled", True) and (target is None or row["name"] in target)
        ]
        if not relevant and target is None:
            ui.warn("no enabled tunnels configured")
            return
        if relevant and all(row["state"] == "running" for row in relevant):
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
    try:
        load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        ui.error(str(exc))
        raise typer.Exit(1)
    try:
        _ensure_daemon(ctx)
        # Always reread disk so up converges even when daemon.watch is false.
        send_request("reload")
        if names:
            for name in names:
                send_request("start", name=name)
        else:
            send_request("start")
    except (IPCError, RuntimeError) as exc:
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
        False, "--kill-daemon", help="Stop the daemon (retained for compatibility)"
    ),
    keep_daemon: bool = typer.Option(
        False, "--keep-daemon", help="Keep the daemon after stopping all tunnels"
    ),
):
    """Stop named tunnels, or stop everything and the daemon by default."""
    if kill_daemon and keep_daemon:
        ui.error("--kill-daemon and --keep-daemon cannot be used together")
        raise typer.Exit(2)
    should_shutdown = kill_daemon or (not names and not keep_daemon)
    if kill_daemon and names:
        ui.warn("--kill-daemon ignores tunnel names; stopping everything")
    if should_shutdown:
        try:
            send_request("shutdown")
            ui.info("tunnels and daemon stopped")
        except IPCError:
            ui.warn("daemon not running")
        return
    try:
        if names:
            for name in names:
                send_request("stop", name=name)
        else:
            send_request("stop")
        ui.info("tunnels stopped; daemon kept running")
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
            for name in names:
                send_request("restart", name=name)
        else:
            send_request("restart")
        ui.info("restarted")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


@app.command()
def reload(ctx: typer.Context):
    """Re-read the config and converge (automatic when daemon.watch=true)."""
    try:
        send_request("reload")
        ui.info("reloaded")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


@app.command()
def status(ctx: typer.Context):
    """Show tunnels and every configured forwarding entry."""
    try:
        data = send_request("status")
    except IPCError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)
    if not _daemon_compatible(data):
        ui.error(_incompatible_daemon_message())
        raise typer.Exit(1)
    ui.render_status(data)


@app.command("list")
def list_cmd(ctx: typer.Context):
    """Show tunnels and forwarding entries without contacting the daemon."""
    try:
        config = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        ui.error(str(exc))
        raise typer.Exit(1)
    ui.render_list(config)


@app.command()
def check(
    ctx: typer.Context,
    show_command: bool = typer.Option(
        False, "--show-command", help="Show the exact SSH argv for each tunnel"
    ),
):
    """Strictly validate and normalize the config."""
    try:
        config = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        ui.error(str(exc))
        raise typer.Exit(1)
    ui.render_check(config, show_command=show_command)


@app.command()
def schema(
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write JSON Schema to this path"
    ),
):
    """Print the JSON Schema used to validate tunneld.toml."""
    text = json.dumps(config_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(text, nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    ui.info(f"wrote {output}")


@app.command()
def logs(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Tunnel name"),
    follow: bool = typer.Option(False, "--follow", "-f"),
    lines: int = typer.Option(50, "--lines", "-n"),
):
    """Tail the SSH output of one tunnel."""
    path = state.log_dir() / f"{_safe(name)}.log"
    if not path.exists():
        ui.error(f"no log for {name!r}")
        raise typer.Exit(1)
    ui.tail(path, lines, follow)


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force"),
    full: bool = typer.Option(False, "--full", help="Write every supported option"),
):
    """Write a valid commented configuration template."""
    path = _cfg(ctx)
    if path.exists() and not force:
        ui.error(f"{path} already exists (use --force to overwrite)")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FULL_CONFIG if full else MINIMAL_CONFIG, encoding="utf-8")
    ui.info(f"wrote {path}")


@app.command()
def edit(ctx: typer.Context):
    """Open the config in $EDITOR."""
    path = _cfg(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(path)])


@app.command()
def doctor(ctx: typer.Context):
    """Diagnose config, OpenSSH, and host alias problems."""
    problems = []
    if shutil.which("ssh") is None:
        problems.append("ssh not found in PATH")
    try:
        config = load_config(_cfg(ctx))
    except (OSError, ConfigError) as exc:
        problems.append(str(exc))
        config = None
    if config is not None and shutil.which("ssh") is not None:
        for tunnel in config.tunnels:
            if not tunnel.enabled:
                continue
            result = subprocess.run(
                ["ssh", "-G", tunnel.host], capture_output=True, text=True
            )
            if result.returncode != 0:
                problems.append(
                    f"host {tunnel.name!r} ({tunnel.host}) not resolvable by ssh: "
                    f"{result.stderr.strip()}"
                )
    if not problems:
        ui.info("all checks passed")
    else:
        for problem in problems:
            ui.error(problem)
        raise typer.Exit(1)


@app.command(hidden=True)
def daemon(
    ctx: typer.Context,
    foreground: bool = typer.Option(True, "--foreground", help="Run in foreground"),
):
    """Run the daemon (internal; used by up and systemd)."""
    from .daemon import Daemon

    Daemon(str(_cfg(ctx))).run()
