# tests/test_config.py
from pathlib import Path
from mkcrew import config


def test_runtime_root_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert config.runtime_root() == tmp_path / "mkcrew"


def test_agent_dirs_are_created(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inbox = config.agent_inbox_dir("worker")
    finish = config.agent_finish_dir("worker")
    assert inbox.is_dir() and finish.is_dir()
    assert inbox == tmp_path / "mkcrew" / "runtime" / "worker" / "inbox"


def test_pid_file_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    pf = config.pid_file()
    assert pf == tmp_path / "mkcrew" / "runtime" / "mkd.pid"


def test_event_db_under_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = config.event_db()
    assert p.name == "events.db"
    assert p.parent.parent.name == "projects"   # per-project: runtime/projects/<key>/events.db
    assert "runtime" in p.parts
    assert p.parent.exists()  # _ensure created the per-project dir
    # different projects -> different DBs (so one project's tasks never show in another's core)
    assert config.event_db(tmp_path / "projA") != config.event_db(tmp_path / "projB")


def test_default_account_bin_resolves_bare_provider(monkeypatch, tmp_path):
    """A bare built-in provider resolves to the DEFAULT account wrapper (flagged default:true, else the
    first listed for that provider). No account for a provider -> None (the provider stays bare)."""
    import json
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config.runtime_root().mkdir(parents=True, exist_ok=True)
    (config.runtime_root() / "accounts.json").write_text(json.dumps([
        {"provider": "claude", "bin": "~/bin/claudew"},                 # first for claude
        {"provider": "claude", "bin": "/x/claude", "default": True},    # explicit default wins over first
        {"provider": "codex",  "bin": "/x/codex"},                      # only one -> it's the default
    ]), encoding="utf-8")
    assert config.default_account_bin("claude") == str(Path("/x/claude"))          # default:true beats first-listed
    assert config.default_account_bin("codex") == str(Path("/x/codex"))
    assert config.default_account_bin("opencode") is None               # no account -> bare
