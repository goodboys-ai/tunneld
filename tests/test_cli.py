import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from tunneld import cli
from tunneld.cli import app
from tunneld.config import config_schema, load_config
from tunneld.ipc import IPC_PROTOCOL_VERSION, IPCError

runner = CliRunner()


def test_init_minimal_and_check_show_command(tmp_path):
    path = tmp_path / "tunneld.toml"
    result = runner.invoke(app, ["--config", str(path), "init"])
    assert result.exit_code == 0, result.output
    config = load_config(path)
    assert config.tunnels[0].forwards

    result = runner.invoke(app, ["--config", str(path), "check", "--show-command"])
    assert result.exit_code == 0, result.output
    assert "config valid" in result.output
    assert "ssh -N -T" in result.output
    assert "-L" in result.output


def test_init_full_is_valid(tmp_path):
    path = tmp_path / "full.toml"
    result = runner.invoke(app, ["--config", str(path), "init", "--full"])
    assert result.exit_code == 0, result.output
    config = load_config(path)
    assert config.tunnels[0].socks
    assert config.tunnels[0].remote_forwards


def test_schema_outputs_json_schema():
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["title"] == "AppConfig"
    assert "tunnels" in schema["properties"]


def test_version_works_without_a_subcommand():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "tunneld 0.2.1" in result.output


def test_committed_schema_covers_current_models():
    path = Path(__file__).parents[1] / "tunneld.schema.json"
    committed = json.loads(path.read_text())
    generated = config_schema()
    assert committed["properties"].keys() == generated["properties"].keys()
    assert committed["$defs"].keys() == generated["$defs"].keys()


def test_up_reloads_disk_before_starting(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda path: object())
    monkeypatch.setattr(cli, "_ensure_daemon", lambda ctx: None)
    monkeypatch.setattr(
        cli, "send_request", lambda op, **args: calls.append((op, args)) or {}
    )

    result = runner.invoke(app, ["--config", str(tmp_path / "config.toml"), "up"])
    assert result.exit_code == 0, result.output
    assert calls == [("reload", {}), ("start", {})]


def _compatible_status():
    return {
        "daemon": {"protocol_version": IPC_PROTOCOL_VERSION},
        "tunnels": [],
    }


def test_status_reports_incompatible_daemon_without_traceback(monkeypatch):
    monkeypatch.setattr(
        cli,
        "send_request",
        lambda op, **args: {
            "daemon": {"pid": 123, "config": "/old/config"},
            "tunnels": [{"name": "old", "forwards": 1}],
        },
    )
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "daemon protocol is incompatible" in result.output
    assert "Traceback" not in result.output


def test_ensure_daemon_restarts_incompatible_process(monkeypatch, tmp_path):
    calls = []
    status_count = 0

    def fake_request(op, **args):
        nonlocal status_count
        calls.append((op, args))
        if op == "status":
            status_count += 1
            if status_count == 1:
                return {"daemon": {"pid": 123}, "tunnels": []}
            if status_count == 2:
                raise IPCError("daemon stopped")
            return _compatible_status()
        return {}

    spawned = []
    monkeypatch.setattr(cli, "send_request", fake_request)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli.daemonize, "spawn_daemon", spawned.append)
    context = SimpleNamespace(obj={"config": tmp_path / "config.toml"})

    cli._ensure_daemon(context)

    assert calls[:3] == [("status", {}), ("shutdown", {}), ("status", {})]
    assert spawned == [str(tmp_path / "config.toml")]
    assert calls[-1] == ("status", {})


def test_down_without_names_stops_daemon_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, "send_request", lambda op, **args: calls.append((op, args)) or {}
    )
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0, result.output
    assert calls == [("shutdown", {})]


def test_down_can_keep_daemon_or_stop_one_name(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, "send_request", lambda op, **args: calls.append((op, args)) or {}
    )
    result = runner.invoke(app, ["down", "--keep-daemon"])
    assert result.exit_code == 0, result.output
    assert calls == [("stop", {})]

    calls.clear()
    result = runner.invoke(app, ["down", "prod"])
    assert result.exit_code == 0, result.output
    assert calls == [("stop", {"name": "prod"})]


def test_package_declares_pep561_typing_support():
    marker = Path(__file__).parents[1] / "src" / "tunneld" / "py.typed"
    assert marker.is_file()


def test_down_kill_daemon_warns_that_names_are_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, "send_request", lambda op, **args: calls.append((op, args)) or {}
    )
    result = runner.invoke(app, ["down", "prod", "--kill-daemon"])
    assert result.exit_code == 0, result.output
    assert "ignores tunnel names" in result.output
    assert calls == [("shutdown", {})]


def test_down_rejects_conflicting_daemon_options():
    result = runner.invoke(app, ["down", "--kill-daemon", "--keep-daemon"])
    assert result.exit_code == 2
    assert "cannot be used together" in result.output
