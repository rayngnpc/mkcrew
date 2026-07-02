# tests/test_forward_delivery.py
# Invisible forward delivery: instead of the daemon TYPING a doorbell into the worker
# pane, the daemon hands the worker its queued job via next_for() (served at
# GET /next?role=), so the worker's Stop hook can inject it into context (zero visible
# keystrokes). These tests cover the daemon-side job-handout logic.
import base64, json, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from mkcrew.daemon import Mkd, _make_handler
from mkcrew.finish_hook import decide_block
from mkcrew import config


class FakeMux:
    def __init__(self): self.lines = []; self.wake_submits = []; self.enters = 0
    def send_line(self, pid, text): self.lines.append((pid, text))
    def send_wake_submit(self, pid, text): self.wake_submits.append((pid, text))
    def send_enter(self, pid): self.enters += 1
    def capture(self, pid): return ""


def test_next_for_returns_delivered_job_doorbell(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="do the thing")
    d.jobs.mark_delivered(job.id)

    nxt = d.next_for("worker")

    assert nxt is not None
    assert nxt["job_id"] == job.id
    assert job.id in nxt["reason"]                 # doorbell names the job id
    assert "mk-done" in nxt["reason"].lower()      # ...and how to report completion


def test_next_for_returns_none_when_no_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker", pane_id="%9")

    assert d.next_for("worker") is None


def test_next_for_hands_out_a_job_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="do the thing")
    d.jobs.mark_delivered(job.id)

    first = d.next_for("worker")
    second = d.next_for("worker")

    assert first is not None                       # delivered once
    assert second is None                          # not re-injected on the next turn-end


def test_next_for_ignores_not_yet_delivered_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker", pane_id="%9")
    d.jobs.open(frm="main", to="worker", text="do the thing")   # PENDING, not delivered

    assert d.next_for("worker") is None            # inbox not written yet — don't inject early


# --- Stop-hook decision logic (decide_block): at turn-end, inject a queued task into
#     the agent's own context via {"decision":"block","reason":...} — zero keystrokes. ---

def test_decide_block_injects_queued_job():
    nxt = {"job_id": "job-1", "reason": "do the task, then run mk-done"}
    out = decide_block({}, "worker", lambda role: nxt)
    assert out == {"decision": "block", "reason": "do the task, then run mk-done"}


def test_decide_block_none_when_no_job():
    assert decide_block({}, "worker", lambda role: None) is None


def test_decide_block_respects_stop_hook_active_loop_guard():
    # Even with a job queued, if we're already in a hook-driven continuation, don't
    # block again — otherwise the agent can never end its turn (infinite loop).
    nxt = {"job_id": "job-1", "reason": "do it"}
    out = decide_block({"stop_hook_active": True}, "worker", lambda role: nxt)
    assert out is None


def test_decide_block_never_raises_on_fetch_error():
    def boom(role):
        raise RuntimeError("daemon down")
    assert decide_block({}, "worker", boom) is None   # a delivery hiccup must never break the turn


# --- HTTP glue: GET /next?role= is what the worker's Stop hook actually calls. ---

def test_next_http_route_returns_queued_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="do the thing")
    d.jobs.mark_delivered(job.id)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(d))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/next?role=worker") as r:
            payload = json.loads(r.read())
        assert payload["job_id"] == job.id
        assert "mk-done" in payload["reason"].lower()

        # unknown / idle role -> empty object (hook treats it as "no job")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/next?role=nobody") as r:
            assert json.loads(r.read()) == {}
    finally:
        httpd.shutdown()


# --- run(): the hook's stdin/stdout wiring (Claude reads the block JSON from stdout). ---

def test_run_emits_block_decision_to_stdout_when_job_queued(tmp_path, monkeypatch, capsys):
    import io
    from mkcrew import finish_hook
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MK_ACTOR", "worker")
    monkeypatch.setattr(finish_hook, "_fetch_next",
                        lambda role: {"job_id": "job-1", "reason": "do it, then run mk-done"},
                        raising=False)
    tp = tmp_path / "t.jsonl"; tp.write_text("", encoding="utf-8")   # Claude always sends a transcript

    rc = finish_hook.run(io.StringIO(json.dumps(
        {"stop_hook_active": False, "transcript_path": str(tp)})))
    out = capsys.readouterr().out

    assert rc == 0
    assert "decision" in out, "run() must emit the block decision to stdout"
    assert json.loads(out.strip()) == {"decision": "block", "reason": "do it, then run mk-done"}


def test_run_treats_empty_stdin_as_stop_event(monkeypatch, capsys):
    import io
    from mkcrew import finish_hook
    monkeypatch.setenv("MK_ACTOR", "worker2")
    monkeypatch.setattr(finish_hook, "_fetch_next",
                        lambda role: {"job_id": "job-1", "reason": f"do it for {role}"},
                        raising=False)

    rc = finish_hook.run(io.StringIO(""))
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out.strip()) == {"decision": "block", "reason": "do it for worker2"}


def test_run_strips_codex_stdin_bom(monkeypatch, capsys):
    import io
    from mkcrew import finish_hook
    monkeypatch.setenv("MK_ACTOR", "worker2")
    monkeypatch.setattr(finish_hook, "_fetch_next",
                        lambda role: {"job_id": "job-1", "reason": "do it"},
                        raising=False)

    rc = finish_hook.run(io.StringIO('ï»¿{"stop_hook_active": false}'))
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out.strip()) == {"decision": "block", "reason": "do it"}


# --- _deliver: write the inbox + a short wake nudge; the TASK is never typed (hook injects it). ---

def test_deliver_writes_inbox_and_wakes_with_nudge_not_task(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="do the thing")

    d._deliver(job)

    inbox = list(config.agent_inbox_dir("worker").glob("*.md"))
    assert len(inbox) == 1 and "do the thing" in inbox[0].read_text(encoding="utf-8")
    assert mux.lines, "worker should be woken with a nudge"
    assert mux.wake_submits == []
    # the wake is a short nudge — the TASK body is never typed (it rides the inbox + the hook)
    assert all("do the thing" not in line for _, line in mux.lines)
    assert d.jobs.get(job.id).status == "DELIVERED"


def test_watchdog_rewakes_default_job_not_yet_picked_up(tmp_path, monkeypatch):
    from mkcrew.daemon import WAKE_RETRY_SECONDS
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux(); mux.capture = lambda pid: "idle prompt"   # non-blank -> not a zombie
    clock = [1000.0]
    d = Mkd(mux=mux); d._now = lambda: clock[0]
    d.register_agent("worker", pane_id="%9")
    d._deliver(d.jobs.open(frm="main", to="worker", text="t"))
    nwakes = len(mux.lines)                                    # the initial wake nudge

    clock[0] += WAKE_RETRY_SECONDS + 1.0
    d._watchdog_tick()                                         # still not injected -> re-wake
    assert len(mux.lines) == nwakes + 1
    assert mux.wake_submits == []


def test_deliver_codex_types_doorbell_pointer_not_task(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9", provider="codex")
    job = d.jobs.open(frm="main", to="worker", text="do the codex thing")

    d._deliver(job)

    inbox = list(config.agent_inbox_dir("worker").glob("*.md"))
    assert len(inbox) == 1 and "do the codex thing" in inbox[0].read_text(encoding="utf-8")
    typed = "\n".join(line for _, line in mux.lines)
    assert mux.wake_submits == []
    assert job.id in typed and "mk-done" in typed.lower()
    assert "do the codex thing" not in typed
    j = d.jobs.get(job.id)
    assert j.status == "DELIVERED" and any(e.get("label") == "injected" for e in j.events)


# --- watchdog re-keyed off the 'injected' job event (not pane-text hashing). ---

def test_watchdog_does_not_rewake_codex_after_visible_doorbell(tmp_path, monkeypatch):
    from mkcrew.daemon import WAKE_RETRY_SECONDS
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux(); mux.capture = lambda pid: "idle prompt"   # non-blank -> not a zombie
    clock = [1000.0]
    d = Mkd(mux=mux); d._now = lambda: clock[0]
    d.register_agent("worker", pane_id="%9", provider="codex")
    d._deliver(d.jobs.open(frm="main", to="worker", text="t"))
    nlines = len(mux.lines)                                    # the initial doorbell

    clock[0] += WAKE_RETRY_SECONDS + 1.0
    d._watchdog_tick()                                         # already injected -> no retype
    assert len(mux.lines) == nlines
    assert mux.wake_submits == []


def test_watchdog_does_not_mark_blank_codex_pane_zombie(tmp_path, monkeypatch):
    from mkcrew.daemon import WATCHDOG_INTERVAL_SECONDS, ZOMBIE_TICKS
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux(); mux.capture = lambda pid: ""
    clock = [1000.0]
    d = Mkd(mux=mux); d._now = lambda: clock[0]
    d.register_agent("worker", pane_id="%9", provider="codex")
    job = d.jobs.open(frm="main", to="worker", text="t")
    d._deliver(job)

    for _ in range(ZOMBIE_TICKS + 1):
        clock[0] += WATCHDOG_INTERVAL_SECONDS + 0.1
        d._watchdog_tick()

    assert d.jobs.get(job.id).status == "DELIVERED"


def test_watchdog_stops_rewaking_once_injected(tmp_path, monkeypatch):
    from mkcrew.daemon import WAKE_RETRY_SECONDS
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux(); mux.capture = lambda pid: "working"
    clock = [1000.0]
    d = Mkd(mux=mux); d._now = lambda: clock[0]
    d.register_agent("worker", pane_id="%9")
    d._deliver(d.jobs.open(frm="main", to="worker", text="t"))
    d.next_for("worker")                                       # worker's hook pulled it -> 'injected'
    nwakes = len(mux.lines)

    clock[0] += WAKE_RETRY_SECONDS * 5
    d._watchdog_tick()
    assert len(mux.lines) == nwakes                            # picked up -> never re-woken


def test_watchdog_gives_up_when_never_picked_up(tmp_path, monkeypatch):
    from mkcrew.daemon import WAKE_RETRY_SECONDS, MAX_RETRY
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux(); mux.capture = lambda pid: "idle"
    clock = [1000.0]
    d = Mkd(mux=mux); d._now = lambda: clock[0]
    d.register_agent("worker", pane_id="%9")
    job = d.jobs.open(frm="main", to="worker", text="t")
    d._deliver(job)
    for _ in range(MAX_RETRY + 1):
        clock[0] += WAKE_RETRY_SECONDS + 1.0
        d._watchdog_tick()

    j = d.jobs.get(job.id)
    assert j.status == "INCOMPLETE"
    assert "pick up" in j.reply.lower() or "giveup" in j.reply.lower()


# --- codex pull path: register the same forward-delivery hook as a codex Stop hook. ---

def test_ensure_codex_hook_registers_stop_hook(tmp_path, monkeypatch):
    from mkcrew.agent import ensure_codex_hook, _codex_hook_trusted_hash
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    hp = ensure_codex_hook(tmp_path, "worker2")
    assert hp == tmp_path / ".codex" / "hooks.json"
    cfg = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[features]\nhooks = true" in cfg
    data = json.loads(hp.read_text(encoding="utf-8"))
    stop = data["hooks"]["Stop"]
    assert len(stop) == 1
    cmds = [str(h.get("commandWindows", "")) for g in stop for h in g["hooks"]]
    assert all("-EncodedCommand" in c for c in cmds), cmds
    decoded = [base64.b64decode(c.split()[-1]).decode("utf-16le") for c in cmds]
    assert any("finish_hook" in c.replace("-", "_") for c in decoded), decoded  # runs our hook
    assert all("MK_ACTOR='worker2'" in c for c in decoded), decoded              # bakes the actor (codex won't pass it)
    assert all("PYTHONIOENCODING='utf-8'" in c for c in decoded), decoded        # decode Codex hook JSON bytes as UTF-8
    assert all("[Console]::In.ReadToEnd()" in c for c in decoded), decoded       # forward raw hook stdin
    assert all("exit 0" in c for c in decoded), decoded                         # never break Codex's turn
    user_cfg = (tmp_path / "home" / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert f"[hooks.state.'{hp}:stop:0:0']" in user_cfg
    assert _codex_hook_trusted_hash(cmds[0]) in user_cfg                           # pre-trusts hook; no manual /hooks accept


def test_ensure_codex_hook_preserves_existing_codex_config(tmp_path, monkeypatch):
    from mkcrew.agent import ensure_codex_hook
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    dot = tmp_path / ".codex"
    dot.mkdir()
    cp = dot / "config.toml"
    cp.write_text("model = \"gpt-5.5\"\n", encoding="utf-8")

    ensure_codex_hook(tmp_path, "worker2")

    text = cp.read_text(encoding="utf-8")
    assert text == "model = \"gpt-5.5\"\n"
    assert "[hooks.state." in (tmp_path / "home" / ".codex" / "config.toml").read_text(encoding="utf-8")


def test_codex_hook_hash_matches_codex_canonical_hash():
    from mkcrew.agent import _codex_hook_trusted_hash
    command = 'node "${PLUGIN_ROOT}/components/start-work-continuation/dist/cli.js" hook stop'
    assert _codex_hook_trusted_hash(
        command,
        timeout=10,
        status_message="(OmO) Checking Start-Work Continuation",
    ) == "sha256:9370f893a95f79e88fb55d85e4eeef6d70dec4b9e75ef3319ec8f16a34371220"


def test_ensure_codex_hook_idempotent(tmp_path):
    from mkcrew.agent import ensure_codex_hook
    ensure_codex_hook(tmp_path, "worker2")
    ensure_codex_hook(tmp_path, "worker2")
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["Stop"]) == 1   # self-heals, doesn't stack
    cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    decoded = base64.b64decode(cmd.split()[-1]).decode("utf-16le")
    assert "MK_ACTOR='worker2'" in decoded
    assert "PYTHONIOENCODING='utf-8'" in decoded
    assert "mkcrew.finish_hook" in decoded


# --- opencode: fully-internal PULL delivery.  The daemon only writes the inbox + marks the job
#     DELIVERED — no keystrokes, no HTTP push.  opencode's own in-process plugin (written by
#     ensure_opencode_plugin) polls /next, gets the doorbell, and injects it into the live TUI. ---

def test_register_stores_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("w", pane_id="%1", provider="opencode")
    assert d.providers["w"] == "opencode"


def test_deliver_opencode_writes_inbox_no_keystrokes(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9", provider="opencode")
    job = d.jobs.open(frm="main", to="worker", text="do the opencode thing")

    d._deliver(job)

    inbox = list(config.agent_inbox_dir("worker").glob("*.md"))
    assert len(inbox) == 1 and "do the opencode thing" in inbox[0].read_text(encoding="utf-8")
    assert mux.lines == [] and mux.enters == 0          # NO keystrokes — opencode's plugin pulls
    assert d.jobs.get(job.id).status == "DELIVERED"
    # the in-process plugin pulls via /next -> next_for hands out the doorbell (read inbox + mk-done)
    nxt = d.next_for("worker")
    assert nxt is not None and job.id in nxt["reason"] and "mk-done" in nxt["reason"].lower()


def test_ensure_opencode_plugin_writes_pull_plugin(tmp_path):
    from mkcrew.agent import ensure_opencode_plugin
    pp = ensure_opencode_plugin(tmp_path)
    assert pp == tmp_path / ".opencode" / "plugins" / "mkcrew-pull.ts"
    src = pp.read_text(encoding="utf-8")
    assert "/next" in src           # pulls the queued task from the daemon
    assert "MK_ACTOR" in src        # scoped to this worker's role
    assert "submit" in src.lower()  # injects into the live TUI + submits


def test_poller_recovers_unknown_actor_artifact_by_job_id(tmp_path, monkeypatch):
    """A worker that didn't set MK_ACTOR writes its mk-done artifact to unknown/finish/;
    the poller must still pick it up by job_id so the reply isn't lost.  This protects Codex
    and other agents whose tool-shell-call env doesn't inherit MK_ACTOR from the hook process."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker2", pane_id="%4")
    job = d.jobs.open(frm="main", to="worker2", text="t")
    d.jobs.mark_delivered(job.id)

    # worker ran mk-done.exe without MK_ACTOR -> artifact lands in unknown/finish/
    unknown_dir = config.agent_finish_dir("unknown")
    art = unknown_dir / f"done-{job.id}-1.json"
    art.write_text(json.dumps({"job_id": job.id, "actor": "unknown", "reply": "pong - Codex, GPT-5"}),
                   encoding="utf-8")

    d._poll_once()

    assert d.jobs.get(job.id).status == "DONE"
    assert d.jobs.get(job.id).reply == "pong - Codex, GPT-5"
    assert str(art) in d._seen
    assert job.id not in d._events  # no ask() waited for it; events cleaned up


# --- agy (Antigravity): no silent hook/plugin/API exists in its shipped CLI, so the daemon types
#     the DOORBELL POINTER (job id + inbox path + mk-done) into the pane.  The task BODY is never
#     typed — it stays in the inbox, which agy reads (the proven Gemini-family path). ---

def test_deliver_antigravity_types_doorbell_pointer_not_task(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    mux = FakeMux()
    d = Mkd(mux=mux)
    d.register_agent("worker", pane_id="%9", provider="antigravity")
    job = d.jobs.open(frm="main", to="worker", text="SECRET_TASK_BODY do the thing")

    d._deliver(job)

    inbox = list(config.agent_inbox_dir("worker").glob("*.md"))
    assert len(inbox) == 1 and "SECRET_TASK_BODY" in inbox[0].read_text(encoding="utf-8")
    typed = "\n".join(line for _, line in mux.lines)
    assert mux.lines, "agy must get the doorbell typed into its pane (no silent path exists)"
    assert job.id in typed and "mk-done" in typed.lower()   # a POINTER: job id + how to report
    assert "SECRET_TASK_BODY" not in typed                  # ...but NOT the task body (stays in inbox)
    j = d.jobs.get(job.id)
    # doorbell IS the delivery (no pull to await) -> mark injected so the watchdog won't re-type
    # it mid-task or give up on a long-running agy task.
    assert j.status == "DELIVERED" and any(e.get("label") == "injected" for e in j.events)


# --- teammates-FYI envelope: parallel workers get told what else is in flight ---

def test_inbox_includes_teammates_fyi_when_others_in_flight(tmp_path, monkeypatch):
    """Two parallel asks: worker2's inbox names worker1's in-flight job (same-checkout collision
    avoidance) + the mk pend hint; worker1's task text itself is the envelope's first line."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = Mkd(mux=FakeMux())
    d.register_agent("worker1", pane_id="%1")
    d.register_agent("worker2", pane_id="%2")
    j1 = d.jobs.open(frm="main", to="worker1", text="refactor auth module\nmore detail")
    d._deliver(j1)
    j2 = d.jobs.open(frm="main", to="worker2", text="write docs")
    d._deliver(j2)

    inbox2 = (config.agent_inbox_dir("worker2") / f"{j2.id}.md").read_text(encoding="utf-8")
    assert "write docs" in inbox2
    assert f"worker1 <- {j1.id}: refactor auth module" in inbox2   # first line only, teammate named
    assert "mk pend" in inbox2                                      # the live-list hint
    # worker1 was delivered FIRST (nothing else in flight then) -> its inbox has NO FYI
    inbox1 = (config.agent_inbox_dir("worker1") / f"{j1.id}.md").read_text(encoding="utf-8")
    assert "FYI" not in inbox1
