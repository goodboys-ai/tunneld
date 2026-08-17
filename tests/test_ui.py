from io import StringIO

from rich.console import Console

from tunneld import ui


def test_render_status_defends_against_malformed_tunnel_entries(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=output, force_terminal=False))
    ui.render_status(
        {
            "daemon": {"pid": 123, "config": "/config"},
            "tunnels": [
                "invalid",
                {
                    "name": "partial",
                    "host": "host",
                    "state": "running",
                    "forwards": 1,
                },
            ],
        }
    )
    rendered = output.getvalue()
    assert "ignored malformed tunnel status entry" in rendered
    assert "partial" in rendered
