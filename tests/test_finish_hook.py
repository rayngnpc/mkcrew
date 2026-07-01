# tests/test_finish_hook.py
import json, io, os
from mkcrew import finish_hook, config

def test_hook_writes_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MK_ACTOR", "worker")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"DONE_X"}]}}', encoding="utf-8")
    stdin = io.StringIO(json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript)}))
    rc = finish_hook.run(stdin)
    assert rc == 0
    files = list(config.agent_finish_dir("worker").glob("*.json"))
    assert len(files) == 1
    art = json.loads(files[0].read_text(encoding="utf-8"))
    assert art["actor"] == "worker" and art["reply"] == "DONE_X"
