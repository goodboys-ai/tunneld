import pytest

from tunneld.config import ConfigError, load_config, parse_config
from tunneld.templates import FULL_CONFIG, MINIMAL_CONFIG


def valid_document():
    return {
        "daemon": {
            "watch": True,
            "watch_interval": 1.5,
            "reconnect_initial_delay": 1.0,
            "reconnect_max_delay": 30.0,
        },
        "defaults": {"keep_alive": 30, "keep_alive_count": 3},
        "tunnels": [
            {
                "name": "prod",
                "host": "prod",
                "forwards": [
                    {"label": "db", "local": 5432, "remote": 5432},
                    {"local": 4321, "remote": "db.internal:4321"},
                ],
                "socks": [{"label": "proxy", "local": 1080}],
                "remote_forwards": [
                    {"label": "webhook", "local": 8080, "remote": 18080}
                ],
            }
        ],
    }


def test_new_schema_parses_all_forward_types():
    config = parse_config(valid_document())
    tunnel = config.tunnels[0]
    assert tunnel.forwards[0].label == "db"
    assert tunnel.forwards[0].remote == 5432
    assert tunnel.socks[0].local == 1080
    assert tunnel.remote_forwards[0].remote == 18080
    assert config.defaults.keep_alive_count == 3
    assert config.daemon.watch_interval == 1.5


def test_empty_config_is_valid_for_converging_to_zero_tunnels():
    assert parse_config({}).tunnels == []


def test_keep_alive_count_zero_is_valid_openssh_behavior():
    document = valid_document()
    document["defaults"]["keep_alive_count"] = 0
    assert parse_config(document).defaults.keep_alive_count == 0


def test_minimal_and_full_templates_are_valid(tmp_path):
    for name, text in (("minimal", MINIMAL_CONFIG), ("full", FULL_CONFIG)):
        path = tmp_path / f"{name}.toml"
        path.write_text(text)
        config = load_config(path)
        assert config.path == str(path)
        assert config.tunnels


def test_utf8_bom_is_accepted(tmp_path):
    path = tmp_path / "bom.toml"
    path.write_bytes(b"\xef\xbb\xbf" + MINIMAL_CONFIG.encode("utf-8"))
    assert load_config(path).tunnels[0].name == "example"


def test_unknown_and_legacy_mode_fields_are_rejected():
    document = valid_document()
    document["tunnels"][0]["forwards"][0]["mode"] = "local"
    with pytest.raises(ConfigError, match=r"tunnels\[0\]\.forwards\[0\]\.mode"):
        parse_config(document)


def test_numeric_string_endpoint_is_rejected():
    document = valid_document()
    document["tunnels"][0]["forwards"][0]["local"] = "5432"
    with pytest.raises(ConfigError, match="use an integer for a bare port"):
        parse_config(document)


@pytest.mark.parametrize("endpoint", [0, 65536, "host:0", "host:65536", "::1:80"])
def test_invalid_endpoints_are_rejected(endpoint):
    document = valid_document()
    document["tunnels"][0]["forwards"][0]["remote"] = endpoint
    with pytest.raises(ConfigError):
        parse_config(document)


def test_duplicate_labels_across_forward_kinds_are_rejected():
    document = valid_document()
    document["tunnels"][0]["socks"][0]["label"] = "db"
    with pytest.raises(ConfigError, match="duplicate label"):
        parse_config(document)


def test_duplicate_tunnel_names_are_rejected():
    document = valid_document()
    duplicate = {
        "name": "prod",
        "host": "other",
        "socks": [{"local": 1081}],
    }
    document["tunnels"].append(duplicate)
    with pytest.raises(ConfigError, match="duplicate tunnel name"):
        parse_config(document)


def test_enabled_local_listener_conflicts_are_rejected():
    document = valid_document()
    document["tunnels"].append(
        {"name": "other", "host": "other", "socks": [{"local": 5432}]}
    )
    with pytest.raises(ConfigError, match="local listener"):
        parse_config(document)


def test_disabled_tunnel_listener_may_overlap():
    document = valid_document()
    document["tunnels"].append(
        {
            "name": "disabled",
            "host": "other",
            "enabled": False,
            "socks": [{"local": 5432}],
        }
    )
    assert len(parse_config(document).tunnels) == 2


def test_tunnel_requires_at_least_one_entry():
    with pytest.raises(ConfigError, match="at least one"):
        parse_config({"tunnels": [{"name": "empty", "host": "host"}]})


def test_reconnect_delay_order_is_validated():
    document = valid_document()
    document["daemon"]["reconnect_initial_delay"] = 31.0
    with pytest.raises(ConfigError, match="must not exceed"):
        parse_config(document)


def test_managed_ssh_options_are_rejected():
    document = valid_document()
    document["tunnels"][0]["ssh_options"] = ["LocalForward=1:host:1"]
    with pytest.raises(ConfigError, match="managed by tunneld"):
        parse_config(document)
