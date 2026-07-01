# tests/test_jobs.py
import time
import pytest
from mkcrew.jobs import JobStore
from mkcrew.eventlog import EventLog
from mkcrew import projections as proj

def test_open_and_complete_job():
    js = JobStore()
    job = js.open(frm="main", to="worker", text="do X")
    assert job.status == "PENDING"
    assert js.inflight_for("worker").id == job.id
    js.mark_delivered(job.id)
    assert js.get(job.id).status == "DELIVERED"
    js.complete(job.id, reply="DONE")
    assert js.get(job.id).status == "DONE"
    assert js.get(job.id).reply == "DONE"
    assert js.inflight_for("worker") is None

def test_rejects_second_inflight_for_same_agent():
    js = JobStore()
    js.open(frm="main", to="worker", text="a")
    with pytest.raises(ValueError):
        js.open(frm="main", to="worker", text="b")

def test_mark_delivered_sets_delivered_at():
    js = JobStore()
    before = time.time()
    job = js.open(frm="main", to="worker", text="x")
    assert job.delivered_at == 0.0
    js.mark_delivered(job.id)
    after = time.time()
    assert before <= js.get(job.id).delivered_at <= after


# ---------------------------------------------------------------------------
# P1-3: complete() accepts optional status
# ---------------------------------------------------------------------------

def test_complete_with_custom_status():
    """complete() should accept an optional status parameter."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="[delivery_stale_giveup] no response", status="INCOMPLETE")
    assert js.get(job.id).status == "INCOMPLETE"
    assert js.get(job.id).reply == "[delivery_stale_giveup] no response"


def test_complete_default_status_is_done():
    """complete() with no status defaults to DONE."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="ok")
    assert js.get(job.id).status == "DONE"


# ---------------------------------------------------------------------------
# P1-4: events, record_event, list_jobs
# ---------------------------------------------------------------------------

def test_open_records_created_event():
    """open() must append a 'created' event to the new job."""
    js = JobStore()
    before = time.time()
    job = js.open(frm="main", to="worker", text="x")
    after = time.time()
    assert len(job.events) == 1
    ev = job.events[0]
    assert ev["label"] == "created"
    assert before <= ev["ts"] <= after


def test_mark_delivered_records_delivered_event():
    """mark_delivered() must append a 'delivered' event."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.mark_delivered(job.id)
    labels = [e["label"] for e in job.events]
    assert "delivered" in labels


def test_complete_records_completed_event():
    """complete() must append a 'completed:<status>' event."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="done", status="DONE")
    labels = [e["label"] for e in job.events]
    assert "completed:DONE" in labels


def test_complete_incomplete_records_event():
    """complete() with INCOMPLETE status records 'completed:INCOMPLETE' event."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="[delivery_stale_giveup] no response", status="INCOMPLETE")
    labels = [e["label"] for e in job.events]
    assert "completed:INCOMPLETE" in labels


def test_record_event_appends():
    """record_event() must append a timestamped event to the job."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    before = time.time()
    js.record_event(job.id, "repair-resubmit")
    after = time.time()
    labels = [e["label"] for e in job.events]
    assert "repair-resubmit" in labels
    ev = next(e for e in job.events if e["label"] == "repair-resubmit")
    assert before <= ev["ts"] <= after


def test_list_jobs_returns_all_jobs():
    """list_jobs() must return all jobs in the store."""
    js = JobStore()
    job1 = js.open(frm="main", to="worker1", text="a")
    job2 = js.open(frm="main", to="worker2", text="b")
    jobs = js.list_jobs()
    ids = {j.id for j in jobs}
    assert job1.id in ids
    assert job2.id in ids
    assert len(jobs) == 2


def test_list_jobs_empty():
    """list_jobs() returns empty list when no jobs exist."""
    js = JobStore()
    assert js.list_jobs() == []


# ---------------------------------------------------------------------------
# Review-fixes: correctness guards
# ---------------------------------------------------------------------------

def test_complete_no_op_on_terminal_job():
    """complete() must not overwrite a job already in a terminal state."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    # Mark PANICKED first
    js.complete(job.id, reply="[PANIC] halted", status="PANICKED")
    assert js.get(job.id).status == "PANICKED"
    # Subsequent complete() call (e.g. from late poll thread) must be a no-op
    js.complete(job.id, reply="late reply", status="DONE")
    assert js.get(job.id).status == "PANICKED", "terminal status must not be overwritten"
    assert js.get(job.id).reply == "[PANIC] halted", "reply must not be overwritten"


def test_complete_no_op_on_done():
    """complete() must not overwrite a DONE job (idempotent for terminal states)."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="first", status="DONE")
    js.complete(job.id, reply="second", status="INCOMPLETE")
    assert js.get(job.id).reply == "first"
    assert js.get(job.id).status == "DONE"


def test_complete_no_op_cleans_inflight():
    """complete() no-op on terminal job still ensures _inflight is cleared."""
    js = JobStore()
    job = js.open(frm="main", to="worker", text="x")
    js.complete(job.id, reply="first", status="DONE")
    assert js.inflight_for("worker") is None
    # Second call should also leave inflight clear
    js.complete(job.id, reply="second", status="INCOMPLETE")
    assert js.inflight_for("worker") is None


def test_jobstore_emits_events_to_log(tmp_path):
    log = EventLog(tmp_path / "e.db")
    js = JobStore(eventlog=log)
    job = js.open(frm="main", to="opus1", text="do X")
    js.mark_delivered(job.id)
    js.complete(job.id, reply="did X", status="DONE")
    types = [e.type for e in log.replay()]
    assert types == ["job.created", "job.delivered", "job.done"]
    log.close()

def test_jobstore_events_rebuild_via_projection(tmp_path):
    log = EventLog(tmp_path / "e.db")
    js = JobStore(eventlog=log)
    job = js.open(frm="main", to="opus1", text="do X")
    js.complete(job.id, reply="ok")
    view = proj.jobs(log.replay())[job.id]
    assert view.status == "DONE" and view.reply == "ok" and view.to == "opus1"
    log.close()

def test_jobstore_no_log_still_works():
    js = JobStore()   # no eventlog — existing behaviour
    job = js.open(frm="main", to="w", text="x")
    js.complete(job.id, reply="ok")
    assert js.get(job.id).status == "DONE"

def test_jobstore_terminal_complete_emits_no_extra_event(tmp_path):
    log = EventLog(tmp_path / "e.db")
    js = JobStore(eventlog=log)
    job = js.open(frm="main", to="w", text="x")
    js.complete(job.id, reply="first", status="DONE")
    js.complete(job.id, reply="second", status="INCOMPLETE")  # no-op
    assert [e.type for e in log.replay()] == ["job.created", "job.done"]
    log.close()


def test_jobstore_emits_inside_lock():
    """Review I1: events must be appended while the JobStore lock is held, so the
    log's append-order can never diverge from the in-memory mutation-order."""
    seen = []

    class ProbeLog:
        def append(self, type, **kw):
            seen.append(("locked" if js._lock.locked() else "unlocked", type))
            return len(seen)

    js = JobStore(eventlog=ProbeLog())
    job = js.open(frm="main", to="w", text="x")
    js.mark_delivered(job.id)
    js.complete(job.id, reply="ok")
    js.record_event(job.id, "note")
    assert all(state == "locked" for state, _ in seen), seen
    assert [t for _, t in seen] == ["job.created", "job.delivered", "job.done", "job.event"]


def test_job_ids_are_random_and_collision_free_across_stores(tmp_path):
    """Job ids are random, so a restarted daemon OR a second project's daemon never collides on
    the same id (a collision makes `mk ask` hand back another job's reply)."""
    js1 = JobStore(eventlog=EventLog(tmp_path / "a.db"))
    js2 = JobStore(eventlog=EventLog(tmp_path / "b.db"))   # a different project's daemon
    ids = [js1.open(frm="main", to="w1", text="x").id,
           js1.open(frm="main", to="w2", text="y").id,
           js2.open(frm="main", to="w1", text="z").id]
    assert len(set(ids)) == 3                        # all distinct
    assert all(i.startswith("task-") for i in ids)   # task-<random>
