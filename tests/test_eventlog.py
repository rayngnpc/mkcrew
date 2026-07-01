from mkcrew.eventlog import EventLog, Event

def test_append_returns_increasing_seq(tmp_path):
    log = EventLog(tmp_path / "e.db")
    s1 = log.append("job.created", job_id="job1", actor="main", data={"to": "opus1"})
    s2 = log.append("job.delivered", job_id="job1", actor="opus1")
    assert s1 == 1 and s2 == 2
    log.close()

def test_replay_returns_all_in_order(tmp_path):
    log = EventLog(tmp_path / "e.db")
    log.append("job.created", job_id="job1", data={"text": "x"})
    log.append("job.done", job_id="job1", data={"status": "DONE", "reply": "ok"})
    evs = log.replay()
    assert [e.type for e in evs] == ["job.created", "job.done"]
    assert evs[0].data["text"] == "x"
    assert evs[1].data["reply"] == "ok"
    assert isinstance(evs[0], Event)
    log.close()

def test_since_returns_tail(tmp_path):
    log = EventLog(tmp_path / "e.db")
    log.append("a"); log.append("b"); s3 = log.append("c")
    tail = log.since(2)
    assert [e.type for e in tail] == ["c"]
    assert tail[0].seq == s3
    log.close()

def test_persists_across_reopen(tmp_path):
    p = tmp_path / "e.db"
    log = EventLog(p); log.append("job.created", job_id="job1"); log.close()
    log2 = EventLog(p)
    assert [e.type for e in log2.replay()] == ["job.created"]
    log2.close()
