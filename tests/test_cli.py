import json
from pathlib import Path

from typer.testing import CliRunner

from tunneld import cli
from tunneld.cli import app
from tunneld.config import config_schema, load_config

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
    assert "tunneld 0.1.0" in result.output


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
