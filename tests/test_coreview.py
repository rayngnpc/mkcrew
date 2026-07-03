import re

from mkcrew.eventlog import Event
from mkcrew.projections import JobView
from mkcrew import coreview

def _ev(seq, type, job_id="", actor="", data=None, ts=0.0):
    return Event(seq, ts, type, job_id, actor, data or {})

def _strip_ansi(s):
    """Remove ANSI colour codes so rendered text can be asserted on directly (mirrors the local
    `strip` lambda in test_render_core_mode_badge_only_when_not_standard)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_render_core_nonroster_shows_agents_and_jobs():
    agents = {"worker1": {"state": "running", "job": "job-1"},
              "worker2": {"state": "idle", "job": None}}
    jobs = [JobView(id="job-1", frm="main", to="worker1", status="DELIVERED")]
    out = coreview.render_core(agents, jobs)
    assert "worker1" in out and "running" in out
    assert "worker2" in out and "idle" in out
    assert "job-1" in out and "DELIVERED" in out          # the jobs grid


def test_render_core_empty():
    out = coreview.render_core({}, [])
    assert "(none)" in out and "(no tasks yet)" in out


def test_render_core_limits_recent_jobs():
    jobs = [JobView(id=f"job-{i}", frm="main", to="w", status="DONE") for i in range(20)]
    out = coreview.render_core({}, jobs, recent=3)
    assert "job-19" in out and "job-18" in out and "job-17" in out
    assert "job-16" not in out                            # only the last 3 jobs


def test_frame_from_events_end_to_end():
    events = [
        _ev(1, "job.created", "job-1", "main", {"frm": "main", "to": "worker1", "text": "x"}),
        _ev(2, "job.delivered", "job-1", "worker1"),
    ]
    out = coreview.frame_from_events(events)
    assert "worker1" in out and "running" in out
    assert "job-1" in out and "DELIVERED" in out


def test_status_main_prints_frame_from_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.eventlog import EventLog
    from mkcrew import config
    log = EventLog(config.event_db())          # same path status_main reads
    log.append("job.created", job_id="job-1", actor="main",
               data={"frm": "main", "to": "worker1", "text": "x"})
    log.append("job.delivered", job_id="job-1", actor="worker1")
    log.close()
    rc = coreview.status_main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "worker1" in out and "running" in out
    assert "job-1" in out and "DELIVERED" in out


def test_coreview_run_once_renders(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.eventlog import EventLog
    from mkcrew import config
    log = EventLog(config.event_db())
    log.append("job.created", job_id="job-1", actor="main",
               data={"frm": "main", "to": "worker1", "text": "x"})
    log.close()
    # one iteration, no real sleep, no real clear
    coreview.coreview_run(iterations=1, interval=0.0, clear=lambda: None)
    out = capsys.readouterr().out
    assert "MKCREW core" in out
    assert "worker1" in out


def test_render_core_jobs_newest_first_and_counts_hidden():
    """JOBS caps to the newest `recent`, lists them newest-first, and the header announces how
    many exist once older jobs roll off the bottom."""
    jobs = [JobView(id=f"job-{i}", frm="main", to="w", status="DONE") for i in range(8)]
    out = coreview.render_core({}, jobs, recent=5)
    body = out[out.index("TASKS"):]
    assert "newest 5 of 8" in body                        # header announces the hidden ones
    assert body.index("job-7") < body.index("job-3")      # newest (job-7) sits above older (job-3)
    assert "job-2" not in body                             # only the 5 newest survive


def test_render_core_horizontal_places_tables_side_by_side():
    """orient='h' renders TEAM and JOBS on the SAME lines (side by side) for wide/short strips,
    so the second table never scrolls out of a short pane."""
    roster = [{"role": "main", "provider": "claude"}]
    jobs = [JobView(id="job-1", frm="main", to="main", status="DONE")]
    out = coreview.render_core({}, jobs, roster=roster, orient="h")
    assert any("TEAM" in ln and "TASKS" in ln for ln in out.splitlines())   # both section labels on one line
    # vertical (default) keeps them on separate lines
    v = coreview.render_core({}, jobs, roster=roster)
    assert not any("TEAM" in ln and "TASKS" in ln for ln in v.splitlines())


def test_render_core_shows_full_roster_with_task_column():
    """With a roster, the core renders a readable table with role, CLI, state, and task columns;
    never collapses to '(none)'."""
    roster = [
        {"role": "main", "provider": "claude"},
        {"role": "worker1", "provider": "claude"},
        {"role": "planner", "provider": "codex"},
    ]
    agents = {"worker1": {"state": "running", "job": "job-1", "task": "write hello() in test.py"}}
    out = coreview.render_core(agents, [], roster=roster)
    for cell in ("ROLE", "CLI", "STATE", "TASK", "main", "worker1", "running", "planner", "codex", "idle", "claude"):
        assert cell in out
    assert "write hello()" in out                         # the task line shows the current job
    assert "──" in out                                    # column rule separates header from rows
    assert "│" in out                                     # records wrapped in the table frame
    assert "(none)" not in out


def test_render_core_jobs_use_professional_columns():
    jobs = [JobView(id="task-bbef25db", frm="main", to="worker2", status="DELIVERED")]
    out = coreview.render_core({}, jobs)
    body = out[out.index("TASKS"):]
    for cell in ("JOB", "FROM", "TO", "STATUS", "task-bbef25db", "main", "worker2", "DELIVERED"):
        assert cell in body
    assert body.index("JOB") < body.index("task-bbef25db")


def test_render_core_shows_hotkey_cheatsheet():
    """The core frame appends a GROUPED HOTKEYS cheatsheet BELOW TASKS, inside the box frame: a
    'Cockpit (Ctrl-b …)' group of the REAL prefix binds (a/A add + x/X close + Esc unstick rebound in
    layouts.apply_chrome, plus curated psmux defaults like d detach / z zoom) and a 'Files pane' group of
    the bare filesview.BINDINGS keys. Renders even on an empty cockpit (static chrome)."""
    out = coreview.render_core({}, [])
    assert "HOTKEYS" in out
    body = out[out.index("HOTKEYS"):]
    assert "KEY" in body and "ACTION" in body                # the KEY -> ACTION table header
    # Cockpit prefix group: rebound binds + curated psmux defaults, under a Ctrl-b group label.
    assert "Cockpit" in body and "Ctrl-b" in body
    assert "a / A" in body and "Add workspace" in body       # apply_chrome add-workspace popup (a/A)
    assert "Detach" in body and "Zoom pane" in body          # surfaced psmux defaults (d detach / z zoom)
    assert "Unstick" in body                                 # apply_chrome Esc -> send-keys -X cancel
    # Files-pane group: the bare filesview.FilesApp.BINDINGS keys.
    assert "Files pane" in body
    assert "New file" in body and "Max editor" in body and "Explorer" in body   # n / z / ] binds
    assert "Ctrl-s" in body and "Save" in body               # files-IDE save bind
    assert "Ctrl-t" in body and "Tree" in body               # files-IDE refocus-tree bind
    assert out.index("TASKS") < out.index("HOTKEYS")         # sits below the TASKS section
    assert "│" in out                                        # still wrapped in the box frame


def test_render_core_compact_fits_files_column():
    """The Files pane embeds core in a 34-column left rail, so compact mode must render a bounded
    frame instead of the full wide table being cropped."""
    roster = [
        {"role": "main", "provider": "claude"},
        {"role": "worker1_demo", "provider": "opencode"},
    ]
    agents = {"worker1_demo": {"state": "running", "job": "task-123456789", "task": "wide task text"}}
    jobs = [JobView(id="task-123456789", frm="main", to="worker1_demo", status="DELIVERED")]

    out = coreview.render_core(agents, jobs, roster=roster, compact=True)

    assert "MKCREW core" in out
    assert "TEAM" in out and "TASKS" in out and "HOTKEYS" in out
    assert "^←/^→ scroll" in out
    assert max(coreview._vlen(line) for line in out.splitlines()) <= 34


def test_render_core_compact_width_param_bounds_outer_frame():
    """Compact mode honours an explicit inner `width`; the OUTER frame is width+4 (the filesview
    column is sized to that, so the core box never wraps into the tree). Uses widths >= the compact
    content's natural minimum so the frame is exactly width+4 and rectangular."""
    for inner in (30, 36, 44):
        out = coreview.render_core({}, [], compact=True, width=inner)
        widths = {coreview._vlen(line) for line in out.splitlines() if line}
        assert widths == {inner + 4}, f"width={inner}: {sorted(widths)}"


def test_render_core_box_rows_are_rectangular():
    """REGRESSION (ragged right border / merged headers): every boxed row renders to the SAME visible
    width, so the closing │ is flush for coloured rows, rule rows AND headers alike — vertical AND
    horizontal. (A coloured cell used to under-pad and the right border hugged the text.)"""
    roster = [{"role": "main", "provider": "claude"}, {"role": "worker1", "provider": "codex"}]
    agents = {"worker1": {"state": "running", "job": "j1", "task": "do a thing"}}
    jobs = [JobView(id="j1", frm="main", to="worker1", status="DELIVERED")]
    for orient in ("v", "h"):
        out = coreview.render_core(agents, jobs, roster=roster, orient=orient)
        widths = {coreview._vlen(ln) for ln in out.splitlines() if ln}
        assert len(widths) == 1, f"orient={orient} ragged frame: {sorted(widths)}"


def test_padding_carries_bold_so_a_pane_keeps_it():
    """The pad spaces are emitted BOLD (`\\033[1m … \\033[0m`). A psmux/tmux pane trims long runs of
    plain or fg-only-coloured spaces as blank trailing cells (which collapsed the right border onto the
    text); bold marks the cells non-blank, and bold on a space has no glyph so it is invisible."""
    assert coreview._fill(4) == "\033[1m    \033[0m"
    assert coreview._fill(0) == ""
    assert coreview._cell("hi", 6, coreview._CYAN).endswith("\033[1m    \033[0m")   # coloured cell pad
    assert coreview._pad_line("abc", 6).endswith("\033[1m   \033[0m")               # box-level top-up
    assert coreview._pad_line("abc", 3) == "abc"                                    # no pad needed


def test_render_core_blocked_asker_shows_waiting_not_idle():
    """A role with an in-flight OUTGOING ask renders 'waiting→<to>', never 'idle' — mk ask blocks the
    asker, and labelling a blocked lead 'idle' made a working cockpit look dead to the user."""
    agents = {"worker2": {"state": "running", "task": "hero imagery"}}
    roster = [{"role": "main", "provider": "claude"}, {"role": "worker2", "provider": "codex"}]
    inflight = [JobView(id="j1", frm="main", to="worker2", status="DELIVERED")]
    out = coreview.render_core(agents, inflight, roster=roster)
    assert "waiting→worker2" in out                      # the blocked lead says WHO it waits on
    done = [JobView(id="j1", frm="main", to="worker2", status="DONE")]
    out2 = coreview.render_core(agents, done, roster=roster)
    assert "waiting→" not in out2                        # terminal job -> back to idle
    outh = coreview.render_core(agents, inflight, roster=roster, orient="h")
    assert "waiting→worker2" in outh                     # side-by-side strip too


def test_render_core_mode_badge_only_when_not_standard():
    """Non-standard modes show a 'mode <m>' badge in the header; standard renders BYTE-IDENTICAL to
    a mode-less call (regression guard: existing cockpits must not change appearance)."""
    import re
    strip = lambda s: re.sub(r"\x1b\[[0-9;]*m", "", s)
    assert coreview.render_core({}, [], mode="standard") == coreview.render_core({}, [])
    assert "mode thorough" in strip(coreview.render_core({}, [], mode="thorough"))
    assert "mode thorough" not in strip(coreview.render_core({}, []))
    assert "mode plan-first" in strip(coreview.render_core({}, [], mode="plan-first", orient="h"))


# ---------------------------------------------------------------------------
# Tower liveness: 'working <age>' / 'late-work <age>' STATE from the daemon's optional
# /status "activity" map -- the truth the 49-minute-worker incident needed.
# ---------------------------------------------------------------------------

def test_fmt_age_formatting_examples():
    """Compact age strings: minutes under an hour, 'HhMM' past an hour."""
    assert coreview._fmt_age(12 * 60) == "12m"
    assert coreview._fmt_age(49 * 60) == "49m"
    assert coreview._fmt_age(65 * 60) == "1h05"    # 1h05 == 1 hour 5 minutes


def test_render_core_working_state_with_fresh_activity():
    """A role with an in-flight job AND fresh daemon 'activity' renders 'working <age>' instead of
    the plain 'running' label -- age comes from the job's created_ts."""
    now = 10_000.0
    agents = {"worker1": {"state": "running"}}
    jobs = [JobView(id="job-1", frm="main", to="worker1", status="DELIVERED",
                    created_ts=now - 12 * 60)]           # started 12 minutes ago
    activity = {"worker1": 5.0}                          # pane changed 5s ago -- fresh
    out = _strip_ansi(coreview.render_core(agents, jobs, activity=activity, now=now))
    assert "working 12m" in out
    assert "running" not in out                          # replaced, not appended


def test_render_core_late_work_state_when_incomplete_but_still_active():
    """A role whose LATEST job is INCOMPLETE (the ask() ceiling gave up) but whose pane STILL has
    fresh daemon 'activity' renders 'late-work <age>' -- INCOMPLETE must not silently read as
    'stopped' (the exact truth the real incident needed)."""
    now = 20_000.0
    jobs = [JobView(id="job-1", frm="main", to="worker2", status="INCOMPLETE",
                    created_ts=now - 49 * 60)]            # the 49-minute incident
    activity = {"worker2": 30.0}                          # pane changed 30s ago -- fresh
    roster = [{"role": "worker2", "provider": "codex"}]
    out = _strip_ansi(coreview.render_core({}, jobs, roster=roster, activity=activity, now=now))
    assert "late-work 49m" in out


def test_render_core_activity_stale_or_absent_falls_back_to_incomplete():
    """Stale (>=90s) or missing activity for an INCOMPLETE job's agent must NOT show 'late-work' --
    it falls back to the plain terminal state exactly as today."""
    now = 20_000.0
    jobs = [JobView(id="job-1", frm="main", to="worker2", status="INCOMPLETE",
                    created_ts=now - 49 * 60)]
    roster = [{"role": "worker2", "provider": "codex"}]
    stale = _strip_ansi(coreview.render_core({}, jobs, roster=roster,
                                             activity={"worker2": 90.0}, now=now))
    missing = _strip_ansi(coreview.render_core({}, jobs, roster=roster,
                                               activity={}, now=now))
    assert "late-work" not in stale
    assert "late-work" not in missing


def test_waiting_wins_over_late_work_state():
    """A role whose latest INCOMING job is INCOMPLETE (late-work eligible, fresh activity) but who
    is ALSO a currently-blocked asker (non-terminal outgoing job) still shows 'waiting-><to>' --
    that existing precedence (checked first, returns early) must not change now that 'late-work' is
    a candidate too.  ('working' requires the running-state branch, which never even reaches the
    waiting-> check -- see test_render_core_blocked_asker_shows_waiting_not_idle for that
    pre-existing, unchanged rule.)"""
    now = 5000.0
    jobs = [
        JobView(id="in", frm="someone", to="main", status="INCOMPLETE", created_ts=now - 600),
        JobView(id="out", frm="main", to="worker2", status="DELIVERED", created_ts=now - 60),
    ]
    activity = {"main": 1.0}
    roster = [{"role": "main", "provider": "claude"}, {"role": "worker2", "provider": "codex"}]
    out = _strip_ansi(coreview.render_core({}, jobs, roster=roster, activity=activity, now=now))
    assert "waiting→worker2" in out
    assert "late-work" not in out


def test_activity_state_caps_at_16_chars():
    """STATE text for 'working <age>'/'late-work <age>' never exceeds the 16-char STATE column
    budget, even for an extreme (many-hour) age, and the rendered frame stays rectangular."""
    now = 1_000_000.0
    huge_age = 999 * 3600 + 59 * 60                        # deliberately absurd: ~999h59
    jobs = [JobView(id="job-1", frm="main", to="worker1", status="DELIVERED",
                    created_ts=now - huge_age)]
    working = coreview._activity_state("worker1", "running", jobs, {"worker1": 1.0}, now)
    assert working is not None and len(working) <= 16

    jobs_incomplete = [JobView(id="job-1", frm="main", to="worker1", status="INCOMPLETE",
                               created_ts=now - huge_age)]
    late = coreview._activity_state("worker1", "idle", jobs_incomplete, {"worker1": 1.0}, now)
    assert late is not None and len(late) <= 16

    agents = {"worker1": {"state": "running"}}
    out = coreview.render_core(agents, jobs, activity={"worker1": 1.0}, now=now)
    widths = {coreview._vlen(ln) for ln in out.splitlines() if ln}
    assert len(widths) == 1                                # every row still the same visible width


def test_render_core_backward_compat_no_activity_matches_today():
    """No daemon 'activity' data (call omitted, explicit None, or an empty map) renders BYTE-
    IDENTICAL to today's render_core call -- the hard backward-compat guarantee for a tower pointed
    at an older daemon whose /status has no 'activity' key."""
    roster = [{"role": "main", "provider": "claude"}, {"role": "worker2", "provider": "codex"}]
    agents = {"worker2": {"state": "running", "task": "hero imagery"}}
    jobs = [JobView(id="j1", frm="main", to="worker2", status="DELIVERED", created_ts=1000.0)]

    baseline = coreview.render_core(agents, jobs, roster=roster)   # today's call signature, untouched
    assert coreview.render_core(agents, jobs, roster=roster, activity=None) == baseline
    assert coreview.render_core(agents, jobs, roster=roster, activity={}) == baseline

    # Also true for the empty/terminal scenes already covered above.
    assert coreview.render_core({}, []) == coreview.render_core({}, [], activity=None)
    inflight = [JobView(id="j1", frm="main", to="worker2", status="DELIVERED")]
    assert (coreview.render_core(agents, inflight, roster=roster)
            == coreview.render_core(agents, inflight, roster=roster, activity=None))


def test_coreview_run_fetches_live_activity_from_daemon(tmp_path, monkeypatch, capsys):
    """coreview_run best-effort fetches the daemon's /status 'activity' map each tick and feeds it
    into the rendered frame: with a reachable daemon reporting fresh activity, an in-flight job
    renders 'working <age>' in the live pane, end to end."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    import json as _json
    import threading as _threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from mkcrew.eventlog import EventLog
    from mkcrew import config

    now = _time.time()
    log = EventLog(config.event_db())
    log.append("job.created", job_id="job-1", actor="main",
               data={"frm": "main", "to": "worker1", "text": "x"}, ts=now - 12 * 60)
    log.append("job.delivered", job_id="job-1", actor="worker1")
    log.close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = _json.dumps({"activity": {"worker1": 1.0}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    _threading.Thread(target=httpd.serve_forever, daemon=True).start()
    config.port_file().write_text(str(port), encoding="utf-8")
    try:
        coreview.coreview_run(iterations=1, interval=0.0, clear=lambda: None)
        out = _strip_ansi(capsys.readouterr().out)
        assert "working 12m" in out
    finally:
        httpd.shutdown()
        httpd.server_close()   # release the listening socket now (a lingering one can flake later tests)


def test_coreview_run_without_daemon_matches_today(tmp_path, monkeypatch, capsys):
    """With no daemon reachable (no port file -- the common case in these tests / a cockpit-less
    event-log read), coreview_run's output is BYTE-IDENTICAL to before this feature existed."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.eventlog import EventLog
    from mkcrew import config
    log = EventLog(config.event_db())
    log.append("job.created", job_id="job-1", actor="main",
               data={"frm": "main", "to": "worker1", "text": "x"})
    log.close()

    coreview.coreview_run(iterations=1, interval=0.0, clear=lambda: None)
    out = capsys.readouterr().out
    assert "MKCREW core" in out
    assert "worker1" in out
    assert "working" not in out
