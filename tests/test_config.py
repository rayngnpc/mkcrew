# tests/test_config.py
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
