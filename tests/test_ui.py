from io import StringIO

from rich.console import Console

from tunneld import ui


def _render(function, *args, **kwargs):
    output = StringIO()
    saved = ui.console
    ui.console = Console(file=output, force_terminal=False)
    try:
        result = function(*args, **kwargs)
        if result is not None:
            ui.console.print(result)
    finally:
        ui.console = saved
    return output.getvalue()


def test_entry_table_uses_route_column_with_server_side_markers():
    entries = [
        {
            "label": "dsh",
            "kind": "-L",
            "listen_side": "local",
            "listen": "localhost:18308",
            "target": "localhost:18308",
            "state": "active",
        },
        {
            "label": "socks",
            "kind": "-D",
            "listen_side": "local",
            "listen": "localhost:1080",
            "target": "SOCKS5",
            "state": "active",
        },
        {
            "label": "web",
            "kind": "-R",
            "listen_side": "remote",
            "listen": "0.0.0.0:18080",
            "target": "localhost:3000",
            "state": "active",
        },
    ]
    rendered = _render(ui._entry_table, entries, "home6")
    for header in ("Label", "Type", "Route", "State"):
        assert header in rendered
    for legacy in ("Side", "Listen", "Target"):
        assert legacy not in rendered
    assert "localhost:18308 → localhost:18308 (home6)" in rendered
    assert "dynamic (SOCKS5 via home6)" in rendered
    assert "0.0.0.0:18080 (home6) → localhost:3000" in rendered


def test_tunnel_heading_omits_identical_host_and_shows_mapping():
    assert ui._tunnel_heading("home6", "home6", None).count("home6") == 1
    mapping = ui._tunnel_heading("web", "prod.example.com", "root")
    assert "root@prod.example.com" in mapping
    assert "→" in mapping


def test_render_status_does_not_duplicate_identical_name_and_host():
    rendered = _render(
        ui.render_status,
        {
            "daemon": {"pid": 123, "config": "/config"},
            "tunnels": [
                {
                    "name": "home6",
                    "host": "home6",
                    "state": "running",
                    "forwards": [
                        {
                            "label": "dsh",
                            "kind": "-L",
                            "listen_side": "local",
                            "listen": "localhost:18308",
                            "target": "localhost:18308",
                            "state": "active",
                        }
                    ],
                }
            ],
        },
    )
    assert "home6  home6" not in rendered
    assert "localhost:18308 (home6)" in rendered


def test_render_status_defends_against_malformed_tunnel_entries():
    rendered = _render(
        ui.render_status,
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
        },
    )
    assert "ignored malformed tunnel status entry" in rendered
    assert "partial" in rendered


def test_markup_in_daemon_fields_is_escaped():
    evil = "[bold red]EVIL[/bold red]"
    output = StringIO()
    saved = ui.console
    ui.console = Console(file=output, force_terminal=True, width=200)
    try:
        ui.render_status(
            {
                "daemon": {"pid": 123, "config": "/config"},
                "tunnels": [
                    {
                        "name": evil,
                        "host": "h",
                        "state": "running",
                        "forwards": [
                            {
                                "label": "l",
                                "kind": evil,
                                "listen_side": "local",
                                "listen": "localhost:1",
                                "target": "localhost:1",
                                "state": evil,
                            }
                        ],
                    }
                ],
            }
        )
    finally:
        ui.console = saved
    rendered = output.getvalue()
    assert "EVIL" in rendered
    assert "\x1b[1;31mEVIL" not in rendered
