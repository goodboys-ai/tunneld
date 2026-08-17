import os
import tempfile

import pytest

from tunneld.config import ConfigError, load_config


def _write(tmp, text):
    p = os.path.join(tmp, "tunneld.toml")
    with open(p, "w") as fh:
        fh.write(text)
    return p


def test_minimal_local_forward():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[[tunnels]]
name = "prod"
host = "prod"
  [[tunnels.forwards]]
  mode = "local"
  local = "5432"
  remote = "db:5432"
""")
        cfg = load_config(p)
        assert len(cfg.tunnels) == 1
        t = cfg.tunnels[0]
        assert t.name == "prod"
        assert t.host == "prod"
        assert t.forwards[0].mode == "local"
        assert t.forwards[0].remote == "db:5432"


def test_socks_forward_needs_no_remote():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[[tunnels]]
name = "s"
host = "h"
  [[tunnels.forwards]]
  mode = "socks"
  local = "1080"
""")
        cfg = load_config(p)
        assert cfg.tunnels[0].forwards[0].mode == "socks"


def test_defaults():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[defaults]
keep_alive = 45
watch = false

[[tunnels]]
name = "t"
host = "h"
  [[tunnels.forwards]]
  local = "1"
  remote = "r:1"
""")
        cfg = load_config(p)
        assert cfg.keep_alive == 45
        assert cfg.watch is False


def test_missing_host_fails():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[[tunnels]]
name = "x"
""")
        with pytest.raises(ConfigError):
            load_config(p)


def test_duplicate_names_fail():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[[tunnels]]
name = "x"
host = "a"
  [[tunnels.forwards]]
  local = "1"
  remote = "r:1"
[[tunnels]]
name = "x"
host = "b"
  [[tunnels.forwards]]
  local = "2"
  remote = "r:2"
""")
        with pytest.raises(ConfigError):
            load_config(p)


def test_no_forwards_fails():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, """
[[tunnels]]
name = "x"
host = "h"
""")
        with pytest.raises(ConfigError):
            load_config(p)
