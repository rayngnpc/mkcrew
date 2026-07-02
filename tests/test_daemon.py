import json, os, time, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from mkcrew.daemon import Mkd, _make_handler, serve
from mkcrew import config


class FakeMux:
    def __init__(self): self.lines = []; self.enters = 0
    def send_line(self, pid, text): self.lines.append((pid, text))
    def send_enter(self, pid): self.enters += 1
    def capture(self, pid): return ""


def test_ask_delivers_then_completes_on_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9")
    d.start_poller()

    result = {}
    def caller():
        result["reply"] = d.ask(frm="main", to="worker", text="do the thing")
    t = threading.Thread(target=caller); t.start()

    time.sleep(0.3)
    # delivery happened: inbox file written + worker woken with a short nudge (task body NOT typed)
    assert mux.lines and mux.lines[0][0] == "%9"
    assert all("do the thing" not in line for _, line in mux.lines)
    inbox = list(config.agent_inbox_dir("worker").glob("*.md"))
    assert len(inbox) == 1 and "do the thing" in inbox[0].read_text(encoding="utf-8")

    # Determine the job id that was assigned (first inflight job is job1)
    inflight = d.jobs.inflight_for("worker")
    assert inflight is not None
    job_id = inflight.id

    # simulate the worker running mk-done which writes a job_id-tagged artifact
    art = config.agent_finish_dir("worker") / "done-1.json"
    art.write_text(json.dumps({"job_id": job_id, "actor": "worker", "reply": "ALL_DONE", "ts": time.time()}), encoding="utf-8")

    t.join(timeout=5)
    assert result["reply"] == "ALL_DONE"
    d.stop()


def test_artifact_without_matching_job_id_does_not_complete(tmp_path, monkeypatch):
    """An artifact whose job_id doesn't match the inflight job must NOT complete it."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9")
    d.start_poller()

    result = {}
    completed = threading.Event()
    def caller():
        result["reply"] = d.ask(frm="main", to="worker", text="do the thing", timeout=2)
        completed.set()
    t = threading.Thread(target=caller); t.start()

    time.sleep(0.3)

    # Write a heartbeat artifact (no job_id)
    heartbeat = config.agent_finish_dir("worker") / "heartbeat.json"
    heartbeat.write_text(
        json.dumps({"actor": "worker", "reply": "", "ts": time.time()}),
        encoding="utf-8"
    )

    # Write an artifact with the wrong job_id
    wrong_job = config.agent_finish_dir("worker") / "wrong.json"
    wrong_job.write_text(
        json.dumps({"job_id": "other-job-999", "actor": "worker", "reply": "WRONG", "ts": time.time()}),
        encoding="utf-8"
    )

    # Give the poller a couple cycles
    time.sleep(0.4)

    # Job should NOT be done yet
    job = d.jobs.inflight_for("worker")
    assert job is not None, "artifacts without matching job_id must not complete the job"

    # Now write the correct artifact
    inflight = d.jobs.inflight_for("worker")
    assert inflight is not None
    good_art = config.agent_finish_dir("worker") / "good.json"
    good_art.write_text(
        json.dumps({"job_id": inflight.id, "actor": "worker", "reply": "CORRECT", "ts": time.time()}),
        encoding="utf-8"
    )

    t.join(timeout=5)
    assert result["reply"] == "CORRECT"
    d.stop()


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


def test_ask_409_on_duplicate_inflight(tmp_path, monkeypatch):
    """Second /ask for the same agent while one is in-flight returns 409."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=0.05)
    d.register_agent("worker", pane_id="%9")
    d.start_poller()

    httpd, base_url, _ = _start_server(d)
    t1 = threading.Thread(
        target=lambda: _post(base_url, "/ask", {"from": "main", "to": "worker", "text": "task1"}),
        daemon=True,
    )
    try:
        # first ask (blocks until the job completes)
        t1.start()
        time.sleep(0.2)  # let delivery happen

        # second ask — should get 409
        status, body = _post(base_url, "/ask", {"from": "main", "to": "worker", "text": "task2"})
        assert status == 409
        assert "error" in body
    finally:
        # Unblock the first (blocking) ask so its background thread returns cleanly.
        # Otherwise its urlopen hits the client timeout and leaks a TimeoutError,
        # which pytest intermittently escalates to a failure (flaky).
        inflight = d.jobs.inflight_for("worker")
        if inflight:
            ev = d._events.get(inflight.id)
            d.jobs.complete(inflight.id, reply="test-cleanup")
            if ev:
                ev.set()
        t1.join(timeout=5)
        httpd.shutdown()
        d.stop()


def test_ask_400_on_missing_fields(tmp_path, monkeypatch):
    """POST /ask without required fields returns 400."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    httpd, base_url, _ = _start_server(d)
    try:
        status, body = _post(base_url, "/ask", {"from": "main"})  # missing 'to' and 'text'
        assert status == 400
        assert body.get("error") == "missing field"
    finally:
        httpd.shutdown()
        d.stop()


def test_register_400_on_missing_fields(tmp_path, monkeypatch):
    """POST /register without required fields returns 400."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    httpd, base_url, _ = _start_server(d)
    try:
        status, body = _post(base_url, "/register", {"agent": "worker"})  # missing pane_id
        assert status == 400
        assert body.get("error") == "missing field"
    finally:
        httpd.shutdown()
        d.stop()


def test_serve_writes_pid_file(tmp_path, monkeypatch):
    """serve() must write its PID to config.pid_file() before accepting connections."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    t = threading.Thread(target=serve, args=(d,), daemon=True)
    t.start()

    # Wait for both files to appear (port + pid)
    deadline = time.time() + 5
    while time.time() < deadline:
        if config.port_file().exists() and config.pid_file().exists():
            break
        time.sleep(0.05)

    assert config.pid_file().exists(), "mkd.pid not written by serve()"
    pid_str = config.pid_file().read_text(encoding="utf-8").strip()
    assert pid_str.isdigit(), f"pid file contents not a number: {pid_str!r}"
    assert int(pid_str) == os.getpid()  # serve() runs in this process (via thread)
    d.stop()


# ---------------------------------------------------------------------------
# P1-2: routing guard + concurrency
# ---------------------------------------------------------------------------

def test_ask_404_on_unknown_role(tmp_path, monkeypatch):
    """POST /ask to an unregistered role returns 404 with an error key."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    # NOTE: no register_agent call — "ghost" is unknown
    httpd, base_url, _ = _start_server(d)
    try:
        status, body = _post(base_url, "/ask", {"from": "main", "to": "ghost", "text": "hello"})
        assert status == 404
        assert "error" in body
        assert "ghost" in body["error"]
    finally:
        httpd.shutdown()
        d.stop()


def test_concurrent_jobs_different_agents_complete_independently(tmp_path, monkeypatch):
    """Two in-flight jobs across different agents each complete with their own reply."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=0.05)
    d.register_agent("opus1", pane_id="%1")
    d.register_agent("sonnet4", pane_id="%2")
    d.start_poller()

    results = {}

    def ask_opus():
        results["opus1"] = d.ask(frm="main", to="opus1", text="opus task")

    def ask_sonnet():
        results["sonnet4"] = d.ask(frm="main", to="sonnet4", text="sonnet task")

    t1 = threading.Thread(target=ask_opus)
    t2 = threading.Thread(target=ask_sonnet)
    t1.start()
    t2.start()

    # Let delivery happen
    time.sleep(0.3)

    # Both jobs should be in-flight simultaneously
    opus_job = d.jobs.inflight_for("opus1")
    sonnet_job = d.jobs.inflight_for("sonnet4")
    assert opus_job is not None, "opus1 job must be in-flight"
    assert sonnet_job is not None, "sonnet4 job must be in-flight"
    assert opus_job.id != sonnet_job.id, "jobs must have distinct IDs"

    # Complete each with its own matching artifact
    art_opus = config.agent_finish_dir("opus1") / "done-opus.json"
    art_opus.write_text(
        json.dumps({"job_id": opus_job.id, "reply": "OPUS_DONE"}),
        encoding="utf-8",
    )
    art_sonnet = config.agent_finish_dir("sonnet4") / "done-sonnet.json"
    art_sonnet.write_text(
        json.dumps({"job_id": sonnet_job.id, "reply": "SONNET_DONE"}),
        encoding="utf-8",
    )

    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results.get("opus1") == "OPUS_DONE"
    assert results.get("sonnet4") == "SONNET_DONE"
    d.stop()


# ---------------------------------------------------------------------------
# P1-3: delivery watchdog tests — use injectable clock, no sleeping
# ---------------------------------------------------------------------------

class ScriptedCaptureMux(FakeMux):
    """FakeMux whose capture() returns successive scripted strings per pane."""
    def __init__(self, captures_by_pane: dict[str, list[str]]):
        super().__init__()
        self._captures = captures_by_pane
        self._calls: dict[str, int] = {}

    def capture(self, pid):
        self._calls.setdefault(pid, 0)
        seq = self._captures.get(pid, [""])
        idx = min(self._calls[pid], len(seq) - 1)
        result = seq[idx]
        self._calls[pid] += 1
        return result


def _setup_watchdog_daemon(tmp_path, monkeypatch, captures_by_pane):
    """Helper: create a Mkd with a scripted mux and a controllable clock."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = ScriptedCaptureMux(captures_by_pane)
    d = Mkd(mux=mux, poll_interval=9999)  # poll loop won't fire on its own

    clock_value = [0.0]
    d._now = lambda: clock_value[0]
    d._last_wd = 0.0

    return d, mux, clock_value


def test_watchdog_cleanup_on_normal_completion(tmp_path, monkeypatch):
    """When a job completes normally via _poll_once, its _wd entry is removed."""
    captures = {"%1": ["idle"]}
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)
    # Use real poll_interval so we can call _poll_once directly
    d.register_agent("worker", "%1")
    job = d.jobs.open(frm="main", to="worker", text="task")
    d._deliver(job)

    assert job.id in d._wd, "watchdog entry must exist after delivery"

    # Simulate worker writing the done artifact
    art = config.agent_finish_dir("worker") / "done.json"
    art.write_text(json.dumps({"job_id": job.id, "reply": "DONE"}), encoding="utf-8")

    d._poll_once()

    assert job.id not in d._wd, "_wd entry must be cleaned up on normal completion"
    assert d.jobs.get(job.id).status == "DONE"


# ---------------------------------------------------------------------------
# Post-pickup stall watchdog — a worker that INJECTS then HANGS must not freeze
# the lead's ask() for the full 1800s timeout (the ~30-min-frozen-main bug).
# ---------------------------------------------------------------------------

def _deliver_and_pickup(d, agent="worker"):
    """Deliver a job to `agent` and mark it 'injected' (as if its Stop hook pulled it),
    registering an event so ask()-unblock can be asserted.  Returns (job, event)."""
    job = d.jobs.open(frm="main", to=agent, text="task")
    ev = threading.Event()
    d._events[job.id] = ev
    d._deliver(job)
    d.next_for(agent)            # worker's hook pulled the task -> 'injected'
    assert any(e.get("label") == "injected" for e in d.jobs.get(job.id).events)
    return job, ev


def test_watchdog_gives_up_when_injected_worker_stalls(tmp_path, monkeypatch):
    """A worker that picks up (injected) then HANGS with a frozen pane is given up after the
    post-pickup stall window — unblocking the lead's ask() far below the 1800s ceiling."""
    from mkcrew.daemon import POST_PICKUP_STALL_SECONDS
    captures = {"%1": ["working..."]}   # non-blank AND unchanging -> no progress (frozen heartbeat)
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)
    d.register_agent("worker", "%1")
    job, ev = _deliver_and_pickup(d)

    # First tick seeds the post-pickup progress clock AT pickup time -> not given up yet.
    clock[0] = 1.0
    d._watchdog_tick()
    assert d.jobs.get(job.id).status == "DELIVERED", "must not give up on the pickup tick"
    assert not ev.is_set()

    # Advance well past the stall window with an unchanging pane -> stall give-up.
    clock[0] = POST_PICKUP_STALL_SECONDS + 5.0
    d._watchdog_tick()

    j = d.jobs.get(job.id)
    assert j.status == "INCOMPLETE", f"stalled pickup must be given up, got {j.status}"
    assert "[stall_giveup]" in j.reply
    assert ev.is_set(), "the blocked ask() must be unblocked by the stall give-up"
    assert job.id not in d._wd, "watchdog state must be cleaned up"
    assert clock[0] < 1800, "the lead is freed within the bounded window, NOT the 1800s ask timeout"


def test_watchdog_does_not_give_up_progressing_worker(tmp_path, monkeypatch):
    """A picked-up worker whose pane keeps changing (real progress) is NEVER cut off, even after
    the cumulative elapsed time passes the stall window."""
    from mkcrew.daemon import POST_PICKUP_STALL_SECONDS
    # A new, distinct capture each tick = the worker is actively producing output.
    captures = {"%1": [f"building step {i}\n" for i in range(12)]}
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)
    d.register_agent("worker", "%1")
    job, ev = _deliver_and_pickup(d)

    # Each gap is under the window, but the pane changes every tick so the clock keeps re-arming;
    # cumulative elapsed deliberately exceeds POST_PICKUP_STALL_SECONDS.
    step = POST_PICKUP_STALL_SECONDS * 0.75
    for i in range(6):
        clock[0] += step
        d._watchdog_tick()
        assert d.jobs.get(job.id).status == "DELIVERED", f"progressing worker killed early at tick {i}"

    assert clock[0] > POST_PICKUP_STALL_SECONDS, "test must run past the window to be meaningful"
    assert not ev.is_set()
    assert job.id in d._wd, "still-progressing job stays watched, not given up"


def test_watchdog_injected_worker_completes_via_artifact_unaffected(tmp_path, monkeypatch):
    """The normal inject -> work -> done flow is unaffected by the stall guard: a finish artifact
    within the window completes the job cleanly."""
    captures = {"%1": ["working..."]}
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)
    d.register_agent("worker", "%1")
    job, ev = _deliver_and_pickup(d)

    # A couple of watchdog ticks while it works (below the stall window) -> stays in-flight.
    clock[0] = 5.0
    d._watchdog_tick()
    assert d.jobs.get(job.id).status == "DELIVERED"

    # Worker reports completion; the poller consumes the artifact and completes the job.
    art = config.agent_finish_dir("worker") / "done.json"
    art.write_text(json.dumps({"job_id": job.id, "reply": "ALL_DONE"}), encoding="utf-8")
    d._poll_once()

    j = d.jobs.get(job.id)
    assert j.status == "DONE" and j.reply == "ALL_DONE"
    assert ev.is_set()
    assert job.id not in d._wd


def test_watchdog_stalled_codex_pickup_is_given_up(tmp_path, monkeypatch):
    """Codex is exempt from the blank-pane zombie check, so a codex worker that picks up (injected
    at delivery) then hangs used to be watched by NOTHING.  The stall guard must still catch it
    (this is the live main->worker2 stall pattern)."""
    from mkcrew.daemon import POST_PICKUP_STALL_SECONDS
    captures = {"%1": [""]}   # codex can capture blank while alive; zombie check skips it entirely
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)
    d.register_agent("worker2", "%1", provider="codex")
    job = d.jobs.open(frm="main", to="worker2", text="task")
    ev = threading.Event()
    d._events[job.id] = ev
    d._deliver(job)   # codex: doorbell IS delivery -> marks 'injected' immediately
    assert any(e.get("label") == "injected" for e in d.jobs.get(job.id).events)

    clock[0] = 1.0
    d._watchdog_tick()
    assert d.jobs.get(job.id).status == "DELIVERED"

    clock[0] = POST_PICKUP_STALL_SECONDS + 5.0
    d._watchdog_tick()

    j = d.jobs.get(job.id)
    assert j.status == "INCOMPLETE"
    assert "[stall_giveup]" in j.reply
    assert ev.is_set()


# ---------------------------------------------------------------------------
# P1-4: /jobs, /jobs/<id>, /repair endpoints
# ---------------------------------------------------------------------------

def _get(url, path):
    req = urllib.request.Request(url + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_get_jobs_lists_open_jobs(tmp_path, monkeypatch):
    """/jobs returns a list containing all open jobs with expected shape."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="hello")
    d._deliver(job)

    httpd, base_url, _ = _start_server(d)
    try:
        status, data = _get(base_url, "/jobs")
        assert status == 200
        assert "jobs" in data
        ids = [j["id"] for j in data["jobs"]]
        assert job.id in ids
        job_entry = next(j for j in data["jobs"] if j["id"] == job.id)
        assert job_entry["from"] == "main"
        assert job_entry["to"] == "worker"
        assert job_entry["status"] == "DELIVERED"
        assert "retry_count" in job_entry
    finally:
        httpd.shutdown()
        d.stop()


def test_get_jobs_id_returns_detail_with_events(tmp_path, monkeypatch):
    """/jobs/<id> returns full job detail including events (created + delivered)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="hello")
    d._deliver(job)

    httpd, base_url, _ = _start_server(d)
    try:
        status, data = _get(base_url, f"/jobs/{job.id}")
        assert status == 200
        assert data["id"] == job.id
        assert data["from"] == "main"
        assert data["to"] == "worker"
        assert data["status"] == "DELIVERED"
        assert "reply" in data
        assert "retry_count" in data
        assert "events" in data
        labels = [e["label"] for e in data["events"]]
        assert "created" in labels
        assert "delivered" in labels
    finally:
        httpd.shutdown()
        d.stop()


def test_get_jobs_unknown_id_returns_404(tmp_path, monkeypatch):
    """/jobs/<unknown> returns 404 with error key."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    httpd, base_url, _ = _start_server(d)
    try:
        status, data = _get(base_url, "/jobs/jobXXXnone")
        assert status == 404
        assert "error" in data
    finally:
        httpd.shutdown()
        d.stop()


def test_repair_on_inflight_job_resubmits(tmp_path, monkeypatch):
    """/repair on an in-flight job triggers a second send and returns ok:true."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="task")
    d._deliver(job)
    wakes_after_deliver = len(mux.lines)

    httpd, base_url, _ = _start_server(d)
    try:
        status, data = _post(base_url, "/repair", {"job_id": job.id})
        assert status == 200
        assert data.get("ok") is True
        # repair re-wakes the worker (one more nudge)
        assert len(mux.lines) == wakes_after_deliver + 1
        # wake-retry count reset
        assert d._wd[job.id]["wakes"] == 0
        # repair-rewake event recorded
        labels = [e["label"] for e in d.jobs.get(job.id).events]
        assert "repair-rewake" in labels
    finally:
        httpd.shutdown()
        d.stop()


def test_repair_on_non_inflight_job_returns_error(tmp_path, monkeypatch):
    """/repair on a completed (non-inflight) job returns ok:false."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="task")
    d._deliver(job)
    # Complete the job so it's no longer in-flight
    d.jobs.complete(job.id, reply="done")

    httpd, base_url, _ = _start_server(d)
    try:
        status, data = _post(base_url, "/repair", {"job_id": job.id})
        assert status == 200
        assert data.get("ok") is False
        assert "error" in data
    finally:
        httpd.shutdown()
        d.stop()


# ---------------------------------------------------------------------------
# P3-2: Pause state, budget, consecutive failures, deadlock, zombie
# ---------------------------------------------------------------------------

def _setup_safety_daemon(tmp_path, monkeypatch, captures_by_pane=None):
    """Helper: create a Mkd with an injectable clock and scripted mux."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    if captures_by_pane is not None:
        mux = ScriptedCaptureMux(captures_by_pane)
    else:
        mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)  # poll loop won't fire on its own
    clock_value = [0.0]
    d._now = lambda: clock_value[0]
    d._last_wd = 0.0
    return d, mux, clock_value


def _start_server2(mkd):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mkd))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}", t


# --- pause / resume ---

def test_pause_blocks_ask_with_409(tmp_path, monkeypatch):
    """/ask returns 409 with 'paused' error when daemon is paused."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%1")
    d.pause("test reason")

    httpd, base_url, _ = _start_server2(d)
    try:
        status, body = _post(base_url, "/ask", {"from": "main", "to": "worker", "text": "hello"})
        assert status == 409
        assert body.get("error", "").startswith("paused")
    finally:
        httpd.shutdown()
        d.stop()


def test_resume_clears_pause(tmp_path, monkeypatch):
    """/resume clears the paused state."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%1")
    d.pause("test reason")
    assert d._paused is True

    httpd, base_url, _ = _start_server2(d)
    try:
        status, body = _post(base_url, "/resume", {})
        assert status == 200
        assert d._paused is False
        # Now /ask should not get 409 for pause (may get 200 or block, but not paused-409)
        # We just check the daemon state
    finally:
        httpd.shutdown()
        d.stop()


def test_pause_not_panic(tmp_path, monkeypatch):
    """Pausing does NOT trigger panic."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.pause("soft stop")
    assert d._paused is True
    assert d.panic.is_panicked is False


# --- consecutive failures ---

def test_consecutive_failures_pause_daemon(tmp_path, monkeypatch):
    """After MAX_CONSECUTIVE_FAILURES INCOMPLETE completions, daemon becomes paused."""
    from mkcrew.daemon import MAX_CONSECUTIVE_FAILURES
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    for i in range(MAX_CONSECUTIVE_FAILURES):
        job = d.jobs.open(frm="main", to=f"worker", text=f"task{i}")
        ev = threading.Event()
        d._events[job.id] = ev
        d._deliver(job)
        d.jobs.complete(job.id, reply=f"fail{i}", status="INCOMPLETE")
        d._on_job_completed(job.id, status="INCOMPLETE")

    assert d._paused is True
    assert d._pause_reason != ""


def test_done_resets_consecutive_failure_counter(tmp_path, monkeypatch):
    """A successful DONE completion resets the consecutive failure counter."""
    from mkcrew.daemon import MAX_CONSECUTIVE_FAILURES
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    # 2 failures (below threshold)
    for i in range(MAX_CONSECUTIVE_FAILURES - 1):
        job = d.jobs.open(frm="main", to="worker", text=f"task{i}")
        ev = threading.Event()
        d._events[job.id] = ev
        d._deliver(job)
        d.jobs.complete(job.id, reply="fail", status="INCOMPLETE")
        d._on_job_completed(job.id, status="INCOMPLETE")

    assert d._paused is False

    # 1 success — resets counter
    job = d.jobs.open(frm="main", to="worker", text="success_task")
    ev = threading.Event()
    d._events[job.id] = ev
    d._deliver(job)
    d.jobs.complete(job.id, reply="done", status="DONE")
    d._on_job_completed(job.id, status="DONE")

    # Now another failure — counter restarted, not paused
    job2 = d.jobs.open(frm="main", to="worker", text="task_after")
    ev2 = threading.Event()
    d._events[job2.id] = ev2
    d._deliver(job2)
    d.jobs.complete(job2.id, reply="fail2", status="INCOMPLETE")
    d._on_job_completed(job2.id, status="INCOMPLETE")

    assert d._paused is False


# --- budget ---

def test_budget_pause_after_job_cap(tmp_path, monkeypatch):
    """When completed jobs exceed MAX_TEAM_JOBS, daemon pauses (not panics)."""
    from mkcrew.safety import MAX_TEAM_JOBS
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%1")

    # Manually set the counter past the cap
    d._jobs_completed = MAX_TEAM_JOBS
    d._check_budget()

    assert d._paused is True
    assert d.panic.is_panicked is False  # soft stop, not panic


def test_budget_pause_after_time_cap(tmp_path, monkeypatch):
    """When elapsed minutes exceed MAX_TEAM_MINUTES, daemon pauses."""
    from mkcrew.safety import MAX_TEAM_MINUTES
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)

    clock_value = [0.0]
    d._now = lambda: clock_value[0]
    # Set team start time to now, then advance clock past cap
    d._team_start = 0.0
    clock_value[0] = (MAX_TEAM_MINUTES + 1) * 60.0
    d._check_budget()

    assert d._paused is True
    assert d.panic.is_panicked is False


# --- deadlock ---

def test_watchdog_breaks_2cycle_deadlock(tmp_path, monkeypatch):
    """Watchdog detects a 2-cycle in-flight deadlock and breaks the oldest job."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)

    clock_value = [100.0]
    d._now = lambda: clock_value[0]
    d._last_wd = 0.0

    d.register_agent("agentA", pane_id="%1")
    d.register_agent("agentB", pane_id="%2")

    # agentA asks agentB (older — lower clock)
    clock_value[0] = 10.0
    jobAB = d.jobs.open(frm="agentA", to="agentB", text="A→B task")
    evAB = threading.Event()
    d._events[jobAB.id] = evAB
    d._deliver(jobAB)

    # agentB asks agentA (newer — higher clock)
    clock_value[0] = 20.0
    jobBA = d.jobs.open(frm="agentB", to="agentA", text="B→A task")
    evBA = threading.Event()
    d._events[jobBA.id] = evBA
    d._deliver(jobBA)

    # Advance clock to trigger watchdog
    clock_value[0] = 200.0
    d._watchdog_tick()

    # At least one job in the cycle must have been broken
    ab_done = d.jobs.get(jobAB.id).status in ("INCOMPLETE", "DONE")
    ba_done = d.jobs.get(jobBA.id).status in ("INCOMPLETE", "DONE")
    assert ab_done or ba_done, "watchdog must break the deadlock cycle"

    # The broken job must have the deadlock reply
    broken_reply = ""
    if d.jobs.get(jobAB.id).status == "INCOMPLETE":
        broken_reply = d.jobs.get(jobAB.id).reply
    elif d.jobs.get(jobBA.id).status == "INCOMPLETE":
        broken_reply = d.jobs.get(jobBA.id).reply
    assert "[deadlock]" in broken_reply


def test_watchdog_deadlock_sets_event(tmp_path, monkeypatch):
    """The event for the broken deadlock job must be set so ask() unblocks."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)

    clock_value = [0.0]
    d._now = lambda: clock_value[0]
    d._last_wd = 0.0

    d.register_agent("agentA", pane_id="%1")
    d.register_agent("agentB", pane_id="%2")

    clock_value[0] = 1.0
    jobAB = d.jobs.open(frm="agentA", to="agentB", text="A→B")
    evAB = threading.Event()
    d._events[jobAB.id] = evAB
    d._deliver(jobAB)

    clock_value[0] = 2.0
    jobBA = d.jobs.open(frm="agentB", to="agentA", text="B→A")
    evBA = threading.Event()
    d._events[jobBA.id] = evBA
    d._deliver(jobBA)

    clock_value[0] = 100.0
    d._watchdog_tick()

    # Whichever job was broken, its event must be set
    if d.jobs.get(jobAB.id).status == "INCOMPLETE":
        assert evAB.is_set()
    if d.jobs.get(jobBA.id).status == "INCOMPLETE":
        assert evBA.is_set()


# --- zombie ---

def test_watchdog_zombie_giveup_after_ticks(tmp_path, monkeypatch):
    """A job with consistently blank capture for ZOMBIE_TICKS ticks is given up as zombie."""
    from mkcrew.daemon import ZOMBIE_TICKS
    # Use blank captures (zombie condition: empty AND no hash progress)
    captures = {"%1": [""]}  # always empty
    d, mux, clock = _setup_watchdog_daemon(tmp_path, monkeypatch, captures)

    d.register_agent("worker", "%1")
    job = d.jobs.open(frm="main", to="worker", text="task")
    ev = threading.Event()
    d._events[job.id] = ev
    d._deliver(job)

    # Drive ZOMBIE_TICKS ticks with blank capture
    # Each tick we must advance clock past stale threshold to avoid the regular
    # stale/retry path kicking in first; we set retries to MAX_RETRY so the
    # stale path would give up, but we want zombie to kick in first.
    # Instead, keep clock below STALE_NO_PROGRESS_SECONDS to avoid stale path;
    # zombie checks blank ticks independently.
    for i in range(ZOMBIE_TICKS):
        clock[0] = float(i + 1)  # small — below STALE_NO_PROGRESS_SECONDS
        d._watchdog_tick()

    completed_job = d.jobs.get(job.id)
    assert completed_job.status == "INCOMPLETE", f"Expected INCOMPLETE, got {completed_job.status}"
    assert "[zombie]" in completed_job.reply
    assert ev.is_set()


# --- mk resume CLI ---

def test_resume_endpoint_clears_pause(tmp_path, monkeypatch):
    """POST /resume clears the paused state and returns ok."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.pause("testing")
    assert d._paused is True

    httpd, base_url, _ = _start_server2(d)
    try:
        status, body = _post(base_url, "/resume", {})
        assert status == 200
        assert body.get("ok") is True
        assert d._paused is False
        assert d._pause_reason == ""
    finally:
        httpd.shutdown()
        d.stop()


# ---------------------------------------------------------------------------
# Review-fixes: defensive hardening tests
# ---------------------------------------------------------------------------

def _post_raw(url, path, raw_body: bytes, content_type="application/json"):
    """Post raw bytes — used to send malformed bodies."""
    import urllib.request
    req = urllib.request.Request(
        url + path,
        data=raw_body,
        headers={"Content-Type": content_type, "Content-Length": str(len(raw_body))},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_do_post_bad_json_returns_400(tmp_path, monkeypatch):
    """do_POST with invalid JSON body must return 400, not crash the handler thread."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    httpd, base_url, _ = _start_server(d)
    try:
        status, body = _post_raw(base_url, "/ask", b"this is not json")
        assert status == 400
        assert body.get("error") == "bad request"
        # Server must still be alive after the bad request
        status2, body2 = _post_raw(base_url, "/register", json.dumps({"agent": "w", "pane_id": "%1"}).encode())
        assert status2 == 200
    finally:
        httpd.shutdown()
        d.stop()


def test_do_post_non_numeric_content_length_returns_400(tmp_path, monkeypatch):
    """do_POST with non-numeric Content-Length must return 400, not crash."""
    import http.client
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    httpd, base_url, _ = _start_server(d)
    port = httpd.server_address[1]
    try:
        # Send a raw request with a bad Content-Length header using http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body_bytes = b'{"from":"a","to":"b","text":"c"}'
        conn.request("POST", "/ask", body=body_bytes,
                     headers={"Content-Length": "not-a-number", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 400
        assert data.get("error") == "bad request"
        conn.close()
    finally:
        httpd.shutdown()
        d.stop()


def test_ask_timeout_returns_timeout_reply(tmp_path, monkeypatch):
    """ask() with a very short timeout must return '[timeout] no response' instead of hanging."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)  # no poller — job will never complete naturally
    d.register_agent("worker", pane_id="%9")

    start = time.monotonic()
    reply = d.ask(frm="main", to="worker", text="task", timeout=0.05)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, "ask() must not hang past the timeout"
    assert "[timeout]" in reply, f"Expected '[timeout]' in reply, got: {reply!r}"
    # Job must be INCOMPLETE (not still DELIVERED/PENDING)
    job = d.jobs.list_jobs()[0]
    assert job.status == "INCOMPLETE"
    # _events must be cleaned up
    assert job.id not in d._events


def test_ask_with_panicked_job_does_not_hang(tmp_path, monkeypatch):
    """ask() on a job panicked immediately after open() must return without waiting."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux, poll_interval=9999)
    d.register_agent("worker", pane_id="%9")

    # Trigger panic immediately — before any ask
    d.panic_now("test")

    start = time.monotonic()
    # ask() should still be callable; job gets panicked immediately by panic_now's
    # job loop (or the pre-wait status check catches it after delivery).
    # Either way it must not hang for timeout duration.
    try:
        reply = d.ask(frm="main", to="worker", text="task", timeout=5.0)
    except ValueError:
        # panic also causes open() to fail if inflight check fires — either is acceptable
        reply = "[panicked via ValueError]"
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, "ask() must not hang when job is panicked"


def test_daemon_accepts_injected_eventlog(tmp_path):
    from mkcrew.eventlog import EventLog
    from mkcrew.daemon import Mkd
    log = EventLog(tmp_path / "e.db")
    d = Mkd(eventlog=log)      # injectable for tests
    assert d.jobs._log is log


# --- core mode: live switch via POST /mode + thorough watchdog patience ---

def test_http_mode_switch_updates_daemon_and_notifies_lead(tmp_path, monkeypatch):
    """POST /mode sets the daemon's posture live, tells the RUNNING lead its new posture (one line
    into the main pane), and /status exposes it (tower/tools can read it)."""
    import threading, urllib.request, json as _json
    from http.server import ThreadingHTTPServer
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    mkd = Mkd(mux=mux)
    mkd.register_agent("main", pane_id="%1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mkd))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mode", data=b'{"mode": "thorough"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert _json.loads(r.read())["mode"] == "thorough"
        assert mkd.mode == "thorough"
        assert any(pid == "%1" and "thorough" in line for pid, line in mux.lines)   # lead notified
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5) as r:
            assert _json.loads(r.read())["mode"] == "thorough"
    finally:
        httpd.shutdown()
        httpd.server_close()   # release the listening socket NOW (a lingering one can flake later tests)


def test_thorough_mode_widens_stall_patience(tmp_path, monkeypatch):
    """A picked-up job whose heartbeat freezes past POST_PICKUP_STALL_SECONDS is given up in
    standard mode but NOT in thorough (3x patience: deep work legitimately goes quiet for long)."""
    from mkcrew import daemon as dmod
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    results = {}
    for mode in ("standard", "thorough"):
        clock = [1000.0]
        mux = FakeMux(); mux.capture = lambda pid: "frozen frame"    # heartbeat never changes
        d = Mkd(mux=mux, mode=mode); d._now = lambda: clock[0]
        d.register_agent("worker", pane_id="%9")
        job = d.jobs.open(frm="main", to="worker", text="deep task")
        d._deliver(job)
        d.jobs.record_event(job.id, "injected")                      # worker picked it up
        d._poll_once()                                               # arm the progress heartbeat
        clock[0] += dmod.POST_PICKUP_STALL_SECONDS + 60              # past 1x patience, under 3x
        d._poll_once()
        results[mode] = d.jobs.get(job.id).status
    assert results["standard"] in ("INCOMPLETE",), results           # standard gave up
    assert results["thorough"] == "DELIVERED", results               # thorough is still patient
