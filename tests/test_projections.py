from mkcrew.eventlog import Event
from mkcrew import projections as proj

def _ev(seq, type, job_id="", actor="", data=None, ts=0.0):
    return Event(seq, ts, type, job_id, actor, data or {})

def _flow():
    # main delegates to opus1; opus1 finishes
    return [
        _ev(1, "job.created", "job1", "main", {"frm": "main", "to": "opus1", "text": "do X"}, ts=10.0),
        _ev(2, "job.delivered", "job1", "opus1", ts=11.0),
        _ev(3, "job.done", "job1", "opus1", {"status": "DONE", "reply": "did X"}, ts=20.0),
    ]

def test_jobs_projection_builds_done_view():
    jv = proj.jobs(_flow())
    j = jv["job1"]
    assert j.frm == "main" and j.to == "opus1" and j.text == "do X"
    assert j.status == "DONE" and j.reply == "did X"
    assert j.delivered_at == 11.0

def test_jobs_projection_done_does_not_override_terminal():
    evs = _flow() + [_ev(4, "job.done", "job1", "opus1", {"status": "INCOMPLETE", "reply": "late"})]
    assert proj.jobs(evs)["job1"].reply == "did X"   # first terminal wins

def test_agents_projection_marks_busy_then_idle():
    created_only = _flow()[:2]   # created + delivered, not done
    assert proj.agents(created_only)["opus1"]["state"] == "running"
    assert proj.agents(_flow())["opus1"]["state"] == "idle"

def test_activity_returns_recent_tail():
    acts = proj.activity(_flow(), limit=2)
    assert [a["type"] for a in acts] == ["job.delivered", "job.done"]
