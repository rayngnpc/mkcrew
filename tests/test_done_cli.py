import json, os
from mkcrew import done_cli, config


def test_run_writes_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MK_ACTOR", "worker")
    rc = done_cli.run(["job7", "did", "the", "thing"])
    assert rc == 0
    files = list(config.agent_finish_dir("worker").glob("*.json"))
    assert len(files) == 1
    art = json.loads(files[0].read_text(encoding="utf-8"))
    assert art["job_id"] == "job7"
    assert art["actor"] == "worker"
    assert art["reply"] == "did the thing"


def test_run_no_args_returns_2(capsys):
    rc = done_cli.run([])
    assert rc == 2


def test_run_only_job_id_empty_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MK_ACTOR", "worker")
    rc = done_cli.run(["job99"])
    assert rc == 0
    files = list(config.agent_finish_dir("worker").glob("*.json"))
    assert len(files) == 1
    art = json.loads(files[0].read_text(encoding="utf-8"))
    assert art["job_id"] == "job99"
    assert art["reply"] == ""
