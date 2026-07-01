"""Tests for the panic / kill stack (P3-1).

TDD: these tests were written BEFORE the implementation and should fail until
the production code is added.
"""
import json, threading, time, urllib.request, urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

from mkcrew.panic import PanicController
from mkcrew.daemon import Mkd, _make_handler
from mkcrew import config


# ---------------------------------------------------------------------------
# Helpers shared with test_daemon.py
# ---------------------------------------------------------------------------

class FakeMux:
    def __init__(self): self.lines = []; self.enters = 0
    def send_line(self, pid, text): self.lines.append((pid, text))
    def send_enter(self, pid): self.enters += 1
    def capture(self, pid): return ""
    def kill_server(self): pass


def _start_server(mkd):
    """Start a real HTTP server on localhost:0; return (httpd, url, thread)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mkd))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}", t


def _post(url, path, payload):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# 1. PanicController unit tests
# ---------------------------------------------------------------------------

def test_panic_controller_starts_not_panicked():
    pc = PanicController()
    assert pc.is_panicked is False


def test_panic_controller_trigger_sets_panicked():
    pc = PanicController()
    pc.trigger()
    assert pc.is_panicked is True


def test_panic_controller_clear_resets_panicked():
    pc = PanicController()
    pc.trigger()
    pc.clear()
    assert pc.is_panicked is False


def test_panic_controller_wait_returns_true_immediately_when_panicked():
    """wait(timeout=0) returns immediately when already triggered."""
    pc = PanicController()
    pc.trigger()
    result = pc.wait(timeout=0)
    assert result is True


def test_panic_controller_wait_times_out_when_not_panicked():
    """wait(timeout) returns False (or equivalent) quickly when not triggered."""
    pc = PanicController()
    # Use a very short timeout — should return within ~0.1s
    result = pc.wait(timeout=0.05)
    assert not result  # Event.wait returns False on timeout


def test_panic_controller_wait_woken_by_trigger_from_other_thread():
    """A thread blocking on wait() is unblocked when another thread calls trigger()."""
    pc = PanicController()
    woken = threading.Event()

    def waiter():
        result = pc.wait(timeout=5)
        if result:
            woken.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    pc.trigger()
    woken.wait(timeout=2)
    assert woken.is_set()


# ---------------------------------------------------------------------------
# 2. Mkd.panic_now — unblocks waiting asks with PANICKED status
# ---------------------------------------------------------------------------

def test_panic_now_sets_panicked_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    assert d.panic.is_panicked is False
    d.panic_now("test")
    assert d.panic.is_panicked is True


def test_panic_now_completes_pending_job_with_panicked_status(tmp_path, monkeypatch):
    """panic_now() must set the event and mark every pending job as PANICKED."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    # Open a job and register its event manually (mimicking what ask() does)
    job = d.jobs.open(frm="main", to="worker", text="task")
    ev = threading.Event()
    d._events[job.id] = ev
    d._deliver(job)

    # Sanity: job is DELIVERED, event not set
    assert d.jobs.get(job.id).status == "DELIVERED"
    assert not ev.is_set()

    d.panic_now("test-reason")

    # Event must be set so a blocked ask() can unblock
    assert ev.is_set(), "event must be set so blocked ask() unblocks"
    # Job must have PANICKED status
    assert d.jobs.get(job.id).status == "PANICKED"
    # Reply must include [PANIC]
    assert "[PANIC]" in d.jobs.get(job.id).reply


def test_panic_now_idempotent_on_already_completed_job(tmp_path, monkeypatch):
    """panic_now() on an already-completed job must not raise."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    job = d.jobs.open(frm="main", to="worker", text="task")
    d._events[job.id] = threading.Event()
    d._deliver(job)
    # Complete the job normally first
    d.jobs.complete(job.id, reply="normal done")

    # panic_now must not raise even though job is already completed
    d.panic_now("idempotent-test")


def test_panicked_ask_unblocks_waiting_thread(tmp_path, monkeypatch):
    """A thread blocked in ask() is unblocked when panic_now() is called, returning a PANICKED reply."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    results = {}

    def caller():
        results["reply"] = d.ask(frm="main", to="worker", text="slow task", timeout=10)

    t = threading.Thread(target=caller, daemon=True)
    t.start()

    # Wait for ask() to open the job and register its event
    deadline = time.time() + 2
    while time.time() < deadline:
        if d.jobs.inflight_for("worker") is not None:
            break
        time.sleep(0.02)

    # Find the job event and verify it's registered
    inflight = d.jobs.inflight_for("worker")
    assert inflight is not None, "job must be in-flight"

    # Trigger panic
    d.panic_now("unblock-test")

    t.join(timeout=2)
    assert not t.is_alive(), "ask() thread must have unblocked"
    # The reply must contain [PANIC]
    assert "[PANIC]" in (results.get("reply") or "")


# ---------------------------------------------------------------------------
# 3. POST /ask returns 409 when panicked
# ---------------------------------------------------------------------------

def test_ask_http_returns_409_when_panicked(tmp_path, monkeypatch):
    """POST /ask must return 409 with error='panicked' when the daemon is in panic state."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%1")
    d.panic_now("pre-panicked")  # panic before the request

    httpd, base_url, _ = _start_server(d)
    try:
        status, body = _post(base_url, "/ask", {"from": "main", "to": "worker", "text": "hello"})
        assert status == 409, f"Expected 409, got {status}"
        assert body.get("error") == "panicked"
    finally:
        httpd.shutdown()
        d.stop()


# ---------------------------------------------------------------------------
# 4. POST /panic endpoint triggers panic
# ---------------------------------------------------------------------------

def test_post_panic_endpoint_triggers_panic(tmp_path, monkeypatch):
    """POST /panic must call panic_now and return {ok: true}."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)

    httpd, base_url, _ = _start_server(d)
    try:
        assert not d.panic.is_panicked
        status, body = _post(base_url, "/panic", {})
        assert status == 200
        assert body.get("ok") is True
        assert d.panic.is_panicked
    finally:
        httpd.shutdown()
        d.stop()


# ---------------------------------------------------------------------------
# 5. Poll loop halts when panicked
# ---------------------------------------------------------------------------

def test_poll_once_does_nothing_when_panicked(tmp_path, monkeypatch):
    """_poll_once must return immediately without touching jobs when panicked."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    # Open + deliver a job
    job = d.jobs.open(frm="main", to="worker", text="task")
    d._deliver(job)

    # Write a completion artifact that would normally complete the job
    art = config.agent_finish_dir("worker") / "done.json"
    art.write_text(json.dumps({"job_id": job.id, "reply": "NORMAL_DONE"}), encoding="utf-8")

    # Panic before polling
    d.panic_now("halt-test")

    # Poll — must NOT process the artifact
    d._poll_once()

    # Job should still be PANICKED (not re-completed as DONE)
    assert d.jobs.get(job.id).status == "PANICKED", (
        "poll_once must skip artifact processing when panicked"
    )


# ---------------------------------------------------------------------------
# 6. Sentinel file detection triggers panic
# ---------------------------------------------------------------------------

def test_check_sentinel_triggers_panic_and_deletes_file(tmp_path, monkeypatch):
    """_check_sentinel() must trigger panic and delete the sentinel file."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)

    # Override sentinel path to use tmp_path
    sentinel = tmp_path / "PANIC"
    sentinel.write_text("", encoding="utf-8")

    assert not d.panic.is_panicked

    # Call the check method directly with our test sentinel path
    d._check_sentinel(sentinel)

    assert d.panic.is_panicked, "sentinel file must trigger panic"
    assert not sentinel.exists(), "sentinel file must be deleted after triggering panic"


def test_check_sentinel_no_op_when_file_absent(tmp_path, monkeypatch):
    """_check_sentinel() must be a no-op when the sentinel file does not exist."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)

    sentinel = tmp_path / "PANIC"  # does NOT exist

    d._check_sentinel(sentinel)

    assert not d.panic.is_panicked


def test_sentinel_file_at_config_path_triggers_panic(tmp_path, monkeypatch):
    """Using config.sentinel_file() path: writing the file must trigger panic."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)

    sentinel = config.sentinel_file()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("", encoding="utf-8")

    d._check_sentinel(sentinel)

    assert d.panic.is_panicked


# ---------------------------------------------------------------------------
# 7. config.sentinel_file() is defined
# ---------------------------------------------------------------------------

def test_config_sentinel_file_returns_path_inside_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = config.sentinel_file()
    assert isinstance(p, Path)
    assert p.name == "PANIC"
    # Must be inside runtime_root()
    assert str(p).startswith(str(config.runtime_root()))
