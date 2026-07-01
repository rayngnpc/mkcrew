# tests/test_ask_cli.py
import json
import pytest
from unittest.mock import patch, MagicMock
import urllib.error, urllib.request
from mkcrew import ask_cli

def test_parse_args_callback():
    ns = ask_cli.parse_args(["--callback", "worker", "do X"])
    assert ns.role == "worker" and ns.message == "do X" and ns.callback is True

def test_parse_args_silence():
    ns = ask_cli.parse_args(["--silence", "reviewer", "look"])
    assert ns.role == "reviewer" and ns.silence is True


def test_main_prints_error_on_unknown_role(monkeypatch, capsys):
    """ask_cli prints the error message when daemon returns an error response."""
    # Patch _post to return an error dict (simulating daemon 404 response)
    monkeypatch.setattr(ask_cli, "_post", lambda path, payload: {"error": "unknown role: ghost"})

    ret = ask_cli.main(["ghost", "hello"])

    captured = capsys.readouterr()
    assert "unknown role: ghost" in captured.out
    assert ret == 0


def test_post_exits_friendly_when_port_file_missing(monkeypatch, tmp_path):
    """`mk ask` with the daemon down must sys.exit a friendly 'mkd not reachable'
    message instead of leaking a FileNotFoundError traceback to the lead."""
    from mkcrew import config as _cfg
    monkeypatch.setattr(_cfg, "port_file", lambda: tmp_path / "nonexistent.port")
    with pytest.raises(SystemExit) as exc:
        ask_cli._post("/ask", {"from": "main", "to": "opus1", "text": "hi"})
    msg = str(exc.value)
    assert "mkd not reachable" in msg
    assert "mk start" in msg


def test_post_exits_friendly_on_connection_refused(monkeypatch, tmp_path):
    """_post sys.exits friendly (not URLError) when the port file exists but nothing answers."""
    from mkcrew import config as _cfg
    pf = tmp_path / "mkd.port"
    pf.write_text("19999", encoding="utf-8")
    monkeypatch.setattr(_cfg, "port_file", lambda: pf)

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(SystemExit) as exc:
        ask_cli._post("/ask", {"from": "main", "to": "opus1", "text": "hi"})
    assert "mkd not reachable" in str(exc.value)
