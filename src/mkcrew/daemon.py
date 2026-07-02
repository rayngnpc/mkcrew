# src/mkcrew/daemon.py
import json, os, signal, time, threading, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import config
from .jobs import JobStore
from .eventlog import EventLog
from .panic import PanicController
from .psmux import PsmuxBackend
from .safety import budget_exceeded, detect_deadlock

# Watchdog constants
WAKE_RETRY_SECONDS = 30.0   # a delivered job not yet picked up (no 'injected' event) is re-woken after
                            # this long — long enough for the worker's turn-end Stop hook to fire first
MAX_RETRY = 2               # re-wakes before giving up on a job the worker never picks up
WATCHDOG_INTERVAL_SECONDS = 4.0  # how often the watchdog re-checks in-flight jobs

# Post-pickup stall deadline.  BUG FIX: after a worker emits 'injected' (it picked up the task) the
# watchdog used to stop watching it entirely — so a worker that PICKS UP then HANGS (never writes a
# finish artifact) rode the full 1800s ask() timeout, freezing the lead's `mk ask` pane for ~30 min.
# Now we keep watching: a picked-up job that makes NO progress (its pane is unchanged AND no finish
# artifact appears) for this long is given up, unblocking the lead early.  This is the "observer
# heartbeat freezes for ~10 minutes -> cancelled" contract in safe-agent-delegation/SKILL.md.  The
# pane-content is the heartbeat: ANY change re-arms the clock, so a worker doing legitimate long work
# that's still producing output is never cut off.  Configurable; kept well under the 1800s ceiling.
POST_PICKUP_STALL_SECONDS = 600.0

# A bare Enter is a no-op on an idle TUI, so it never triggers the turn the Stop hook needs.
# Type this SHORT nudge instead: it triggers a quick turn end; the precise task (job id + inbox path
# + mk-done) is then injected INTO the next turn by the worker's Stop hook via /next. Do not tell the
# worker to inspect the inbox here, or Codex may start manual fallback work before its Stop hook pulls.
WAKE_NUDGE = "MKCREW wake ping. End this turn now; do not inspect files or run commands."

# Safety constants
MAX_CONSECUTIVE_FAILURES = 3  # consecutive INCOMPLETE give-ups before pause
ZOMBIE_TICKS = 3              # consecutive blank-capture ticks before zombie give-up


class Mkd:
    def __init__(self, mux=None, poll_interval=0.2, eventlog=None):
        self.mux = mux or PsmuxBackend()
        self._eventlog = eventlog if eventlog is not None else EventLog(":memory:")
        self.jobs = JobStore(eventlog=self._eventlog)
        self.panes: dict[str, str] = {}
        self.providers: dict[str, str] = {}   # role -> provider (set at /register); delivery is provider-aware
        self.poll_interval = poll_interval
        self._events: dict[str, threading.Event] = {}
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._now = time.monotonic          # injectable clock for tests
        # Watchdog state: job_id -> {"hash": ..., "progress_ts": ..., "retries": ...}
        # In-flight jobs are NOT persisted across restarts — daemon always starts clean
        # (drained by construction; SQLite persistence is deferred to a future phase).
        self._wd: dict[str, dict] = {}
        self._last_wd: float = 0.0
        # Panic controller — layer 1 / 2 / 3 kill switch
        self.panic = PanicController()
        # Pause state (distinct from panic — soft stop, resumable)
        self._paused: bool = False
        self._pause_reason: str = ""
        # Safety tracking
        self._jobs_completed: int = 0
        self._team_start: float | None = None   # set on first _deliver()
        self._consecutive_failures: int = 0

    def register_agent(self, agent: str, pane_id: str, provider: str = "claude") -> None:
        self.panes[agent] = pane_id
        self.providers[agent] = provider

    # ------------------------------------------------------------------
    # Pause / resume (soft stop — distinct from panic)
    # ------------------------------------------------------------------

    def pause(self, reason: str) -> None:
        """Set the paused state.  New /ask calls return 409; existing jobs continue."""
        self._paused = True
        self._pause_reason = reason

    def resume(self) -> None:
        """Clear the paused state."""
        self._paused = False
        self._pause_reason = ""

    # ------------------------------------------------------------------
    # Budget check
    # ------------------------------------------------------------------

    def _check_budget(self) -> None:
        """Check budget caps and pause if exceeded (soft stop, not panic)."""
        if self._paused:
            return  # already paused
        elapsed_minutes = 0.0
        if self._team_start is not None:
            elapsed_minutes = (self._now() - self._team_start) / 60.0
        reason = budget_exceeded(
            jobs_completed=self._jobs_completed,
            elapsed_minutes=elapsed_minutes,
        )
        if reason:
            self.pause(reason)

    # ------------------------------------------------------------------
    # Completion hook — called after every job completion
    # ------------------------------------------------------------------

    def _on_job_completed(self, job_id: str, status: str) -> None:
        """Update safety counters and check triggers after a job is completed."""
        self._jobs_completed += 1
        if status == "INCOMPLETE":
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES and not self._paused:
                self.pause(f"{MAX_CONSECUTIVE_FAILURES} consecutive failures")
        else:
            self._consecutive_failures = 0
        self._check_budget()

    def _write_inbox(self, job) -> None:
        """Write the per-job task body to the worker's inbox (always invisible). A short teammates-FYI
        rides along when other jobs are in flight: every agent edits the SAME checkout, so naming the
        other open tasks lets parallel workers self-avoid file collisions (`mk pend` = the live list)."""
        body = job.text
        others = self.jobs.active_others(job.id)
        if others:
            fyi = "\n".join(f"- {j.to} <- {j.id}: {(j.text or '').splitlines()[0][:90]}"
                            for j in others[:3])
            body += ("\n\n---\nFYI: teammates are working in this SAME checkout right now:\n"
                     f"{fyi}\n"
                     "Do not edit files their tasks clearly own; run `mk pend` if unsure.")
        (config.agent_inbox_dir(job.to) / f"{job.id}.md").write_text(body, encoding="utf-8")

    def _wake(self, role: str) -> None:
        """Nudge an idle worker into taking a turn so its turn-end Stop hook pulls the queued task
        via /next.  Types a SHORT trigger (WAKE_NUDGE), NOT the task — the task body stays in the
        inbox and is injected by the hook.  No-op for opencode: its in-process mkcrew-pull plugin
        polls /next itself, so the daemon never types into an opencode pane."""
        if self.providers.get(role) == "opencode":
            return
        pane = self.panes.get(role)
        if pane is not None:
            self.mux.send_line(pane, WAKE_NUDGE)

    def _doorbell_text(self, job) -> str:
        """The routing instruction for a delegated job: read its inbox file, do it, then
        run mk-done. Handed to a worker by next_for() so its Stop hook can inject it into
        context (invisible) instead of the daemon typing it into the pane."""
        from pathlib import Path
        inbox = config.agent_inbox_dir(job.to) / f"{job.id}.md"
        done_exe = str(Path(sys.executable).parent / "mk-done.exe").replace("\\", "/")
        return (f'You have a delegated task, task id {job.id}. Read the file "{inbox}" and do everything it asks. '
                f"When completely finished you MUST run this exact command as your final action to report completion: "
                f'"{done_exe}" {job.id} "<a concise one-line summary of what you did>"')

    def next_for(self, role: str):
        """Return {'job_id', 'reason'} for the role's queued job (so its Stop hook can
        inject it), else None.  Marks the job 'injected' so it's handed out only once."""
        job = self.jobs.inflight_for(role)
        if job is None or job.status != "DELIVERED":
            return None  # nothing queued, or inbox not written yet (still PENDING)
        if any(e.get("label") == "injected" for e in job.events):
            return None  # already handed to the hook once — don't re-inject every turn-end
        self.jobs.record_event(job.id, "injected")
        return {"job_id": job.id, "reason": self._doorbell_text(job)}

    def _deliver(self, job) -> None:
        """Write the inbox + mark DELIVERED, init the watchdog, then route by provider:
          - claude: a short wake nudge -> its Stop hook pulls /next and injects (silent).
          - codex: type the DOORBELL pointer directly; faster than a visible wake + hook cycle.
          - opencode: nothing typed -> its in-process plugin polls /next and injects (silent).
          - antigravity (agy): type the DOORBELL pointer (job id + inbox path + mk-done).  agy's
            shipped CLI exposes no silent hook/plugin/API, but it reads its inbox and runs mk-done
            (the proven Gemini-family path).  The task BODY is never typed — it stays in the inbox."""
        if self._team_start is None:
            self._team_start = self._now()
        self._wd[job.id] = {"delivered_ts": self._now(), "wakes": 0, "zombie_ticks": 0,
                            "progress_ts": self._now(), "progress_sig": None, "injected_seen": False}
        self._write_inbox(job)
        self.jobs.mark_delivered(job.id)
        if self.providers.get(job.to) in {"antigravity", "codex"}:
            pane = self.panes.get(job.to)
            if pane is not None:
                self.mux.send_line(pane, self._doorbell_text(job))
                # ponytail: the visible doorbell IS delivery; no wake+hook round-trip to await.
                # Mark 'injected' so the watchdog won't re-type it mid-task or give up.
                self.jobs.record_event(job.id, "injected")
            return
        self._wake(job.to)

    def _giveup(self, job, reply: str) -> None:
        """Mark an in-flight job INCOMPLETE, unblock any waiting ask(), and clean up its
        watchdog state.  One bad job must never kill the poll thread."""
        try:
            self.jobs.complete(job.id, reply=reply, status="INCOMPLETE")
            if job.id in self._events:
                self._events[job.id].set()
        except Exception:
            pass
        self._wd.pop(job.id, None)
        self._on_job_completed(job.id, status="INCOMPLETE")

    def ask(self, frm: str, to: str, text: str, timeout: float = 1800) -> str:
        job = self.jobs.open(frm=frm, to=to, text=text)
        ev = threading.Event()
        self._events[job.id] = ev
        self._deliver(job)
        # Close the window where panic_now() fires between open() and ev.wait():
        # (a) If the daemon is already panicked at this point, mark this job now.
        # (b) If the job was completed by panic_now() during _deliver() or by the
        #     poller, check its status and skip the wait.
        if self.panic.is_panicked:
            try:
                self.jobs.complete(
                    job.id,
                    reply=f"[PANIC] team halted",
                    status="PANICKED",
                )
            except Exception:
                pass  # race: already terminal
            ev.set()
        elif self.jobs.get(job.id).status in ("DONE", "INCOMPLETE", "PANICKED"):
            ev.set()
        did = ev.wait(timeout)
        if not did:
            # Timeout: mark incomplete so the caller always gets a meaningful reply.
            try:
                self.jobs.complete(job.id, reply="[timeout] no response", status="INCOMPLETE")
            except Exception:
                pass  # already terminal — ignore
        self._events.pop(job.id, None)
        return self.jobs.get(job.id).reply

    def panic_now(self, reason: str = "panic") -> None:
        """Trigger the panic controller and unblock every pending job.

        Marks all PENDING/DELIVERED jobs as PANICKED and sets their events so
        any blocked ask() calls return immediately.  Idempotent — safe to call
        multiple times.
        """
        self.panic.trigger()
        # Collect all job IDs that are still in progress (not yet completed)
        all_in_progress = set()
        for job in self.jobs.list_jobs():
            if job.status in ("PENDING", "DELIVERED"):
                all_in_progress.add(job.id)

        for job_id in all_in_progress:
            try:
                self.jobs.complete(
                    job_id,
                    reply=f"[PANIC] team halted: {reason}",
                    status="PANICKED",
                )
            except Exception:
                pass  # race: already completed — ignore

        # Set all registered events so blocked ask() calls wake up
        for job_id, ev in list(self._events.items()):
            ev.set()

    def _check_sentinel(self, sentinel_path=None) -> None:
        """Check whether the sentinel file exists; if so, trigger panic and delete it.

        Factored out so tests can call it directly without needing a background thread.
        """
        if sentinel_path is None:
            sentinel_path = config.sentinel_file()
        if sentinel_path.exists():
            self.panic_now("sentinel")
            try:
                sentinel_path.unlink()
            except FileNotFoundError:
                pass  # race — already gone

    def _poll_once(self) -> None:
        # If panicked, stop processing immediately — no new deliveries or completions
        if self.panic.is_panicked:
            return

        # Pre-build the set of currently-inflight job ids so the unknown-finish fallback
        # below can match completion artifacts by job_id (workers that didn't set MK_ACTOR
        # write to unknown/finish/ — the job_id in the artifact is the authoritative match key).
        inflight_by_id = {
            self.jobs.inflight_for(agent).id: agent
            for agent in list(self.panes.keys())
            if self.jobs.inflight_for(agent) is not None
        }

        for agent, _pid in list(self.panes.items()):
            inflight = self.jobs.inflight_for(agent)
            if not inflight:
                continue
            for art in sorted(config.agent_finish_dir(agent).glob("*.json")):
                if str(art) in self._seen:
                    continue
                self._seen.add(str(art))
                try:
                    data = json.loads(art.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if data.get("job_id") != inflight.id:
                    continue  # heartbeat / other-job artifact: not a completion for this job
                self.jobs.complete(inflight.id, reply=data.get("reply", ""))
                if inflight.id in self._events:
                    self._events[inflight.id].set()
                self._wd.pop(inflight.id, None)
                self._on_job_completed(inflight.id, status="DONE")
                inflight_by_id.pop(inflight.id, None)
                break

        # Fallback: completion artifacts whose actor is "unknown" (worker did not set MK_ACTOR
        # before invoking mk-done — common when a Codex/agy/opencode shell_command tool spawns
        # a fresh process). Match by job_id against the in-flight set; the artifact's reply
        # is authoritative.
        if inflight_by_id:
            unknown_dir = config.agent_finish_dir("unknown")
            for art in sorted(unknown_dir.glob("done-*.json")):
                if str(art) in self._seen:
                    continue
                try:
                    data = json.loads(art.read_text(encoding="utf-8"))
                except Exception:
                    continue
                job_id = data.get("job_id")
                if job_id not in inflight_by_id:
                    continue
                self._seen.add(str(art))
                self.jobs.complete(job_id, reply=data.get("reply", ""))
                if job_id in self._events:
                    self._events[job_id].set()
                self._wd.pop(job_id, None)
                self._on_job_completed(job_id, status="DONE")
                inflight_by_id.pop(job_id, None)

        # Watchdog tick — fire on interval
        if self._now() - self._last_wd >= WATCHDOG_INTERVAL_SECONDS:
            self._watchdog_tick()
            self._last_wd = self._now()

    def _watchdog_tick(self) -> None:
        """Check each in-flight job for progress; redeliver or give up if stale.

        Also runs deadlock detection across all in-flight jobs and zombie
        detection per job (blank pane for ZOMBIE_TICKS consecutive ticks).
        """
        # --- 1. Deadlock detection ---
        inflight_edges = []
        for agent in list(self.panes):
            job = self.jobs.inflight_for(agent)
            if job is not None:
                inflight_edges.append((job.id, job.frm, job.to))

        if len(inflight_edges) >= 2:
            cycle_ids = detect_deadlock(inflight_edges)
            if cycle_ids:
                # Break the OLDEST job in the cycle — measured by delivered_at timestamp
                # so we preserve more recently-started work.
                def _delivered_at(jid: str) -> float:
                    try:
                        return self.jobs.get(jid).delivered_at
                    except KeyError:
                        return float("inf")

                oldest_id = min(cycle_ids, key=_delivered_at)
                try:
                    self.jobs.complete(
                        oldest_id,
                        reply="[deadlock] broken to free the team",
                        status="INCOMPLETE",
                    )
                    if oldest_id in self._events:
                        self._events[oldest_id].set()
                    self._wd.pop(oldest_id, None)
                    self._on_job_completed(oldest_id, status="INCOMPLETE")
                except Exception:
                    pass  # race: already completed

        # --- 2. Per-job pickup / progress / zombie checks (keyed off the 'injected' job event) ---
        for agent, pane_id in list(self.panes.items()):
            inflight = self.jobs.inflight_for(agent)
            if not inflight:
                continue
            wd = self._wd.get(inflight.id)
            if wd is None:
                continue

            # ONE capture per tick, reused for both the zombie check and the post-pickup progress
            # signal (a changing pane == the worker is actively working).  Capturing more than once
            # here would desync ScriptedCaptureMux, which advances its cursor per call.
            sig = (self.mux.capture(pane_id) or "").strip()

            # Zombie: a totally blank pane for ZOMBIE_TICKS consecutive ticks = the CLI died.
            # Codex's TUI can briefly capture blank while still alive; don't fail it before the
            # Stop-hook pickup retry window has a chance to run. Real Codex non-pickups still end via
            # the delivery-giveup path below.
            if self.providers.get(agent) != "codex" and not sig:
                wd["zombie_ticks"] = wd.get("zombie_ticks", 0) + 1
                if wd["zombie_ticks"] >= ZOMBIE_TICKS:
                    self._giveup(inflight, "[zombie] agent pane unresponsive")
                    continue
            else:
                wd["zombie_ticks"] = 0

            injected = any(e.get("label") == "injected" for e in inflight.events)

            # Post-pickup progress clock ("observer heartbeat").  Seed it FRESH the first tick we see
            # the pickup, so pre-pickup idle never counts against the worker; thereafter ANY pane
            # change is progress and re-arms the clock.
            if injected and not wd.get("injected_seen"):
                wd["injected_seen"] = True
                wd["progress_sig"] = sig
                wd["progress_ts"] = self._now()
            elif sig != wd.get("progress_sig"):
                wd["progress_sig"] = sig
                wd["progress_ts"] = self._now()

            # Pickup: once the worker's Stop hook has pulled the task (the 'injected' event) it's
            # working.  BUG FIX: DON'T stop watching it — a worker that picks up then HANGS (never
            # writes a finish artifact) must not ride the full 1800s ask() timeout (~30-min lead
            # freeze).  Give up if its heartbeat has been frozen for POST_PICKUP_STALL_SECONDS so the
            # lead's ask() unblocks early.  A still-progressing worker keeps re-arming progress_ts
            # above and is never cut off.
            if injected:
                if self._now() - wd.get("progress_ts", self._now()) > POST_PICKUP_STALL_SECONDS:
                    self._giveup(inflight, "[stall_giveup] worker picked up the task but stopped making progress")
                continue

            # Not yet picked up: the content-free wake may not have landed (an idle TUI ignores a
            # bare Enter) — re-wake on an interval, then give up if it's never picked up.
            if self._now() - wd["delivered_ts"] > WAKE_RETRY_SECONDS:
                if wd["wakes"] < MAX_RETRY:
                    self._wake(agent)
                    self.jobs.record_event(inflight.id, "rewake")
                    wd["wakes"] += 1
                    wd["delivered_ts"] = self._now()
                else:
                    self._giveup(inflight, "[delivery_giveup] worker never picked up the task")

    def start_poller(self) -> None:
        def loop():
            while not self._stop.is_set():
                self._poll_once()
                time.sleep(self.poll_interval)
        threading.Thread(target=loop, daemon=True).start()

    def start_sentinel(self) -> None:
        """Start a background daemon thread that polls the sentinel file every 0.5s.

        Uses self.panic.wait(0.5) as the sleep so the thread exits promptly when
        the panic controller is triggered (either by sentinel or other means).
        """
        def loop():
            sentinel = config.sentinel_file()
            while not self._stop.is_set():
                self._check_sentinel(sentinel)
                # Wait up to 0.5s; wakes immediately if already panicked
                self.panic.wait(timeout=0.5)
                if self.panic.is_panicked:
                    return  # panic already set — nothing more to watch
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def _make_handler(mkd: Mkd):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"ok": True})
            elif self.path == "/jobs":
                jobs = mkd.jobs.list_jobs()
                result = [
                    {
                        "id": j.id,
                        "from": j.frm,
                        "to": j.to,
                        "status": j.status,
                        "retry_count": mkd._wd.get(j.id, {}).get("retries", 0),
                    }
                    for j in jobs
                ]
                self._json(200, {"jobs": result})
            elif self.path == "/status":
                self._json(200, {
                    "panicked": mkd.panic.is_panicked,
                    "paused": mkd._paused,
                    "pause_reason": mkd._pause_reason,
                    "agents": sorted(mkd.panes.keys()),
                    "jobs": len(mkd.jobs.list_jobs()),
                })
            elif self.path.startswith("/jobs/"):
                job_id = self.path[len("/jobs/"):]
                try:
                    j = mkd.jobs.get(job_id)
                except KeyError:
                    self._json(404, {"error": "unknown job"})
                    return
                self._json(200, {
                    "id": j.id,
                    "from": j.frm,
                    "to": j.to,
                    "status": j.status,
                    "reply": j.reply,
                    "retry_count": mkd._wd.get(j.id, {}).get("retries", 0),
                    "events": j.events,
                })
            elif self.path.startswith("/next"):
                # A worker's Stop hook pulls its queued job here, then injects it into
                # its own context — so the daemon never types a doorbell into the pane.
                from urllib.parse import urlparse, parse_qs
                role = (parse_qs(urlparse(self.path).query).get("role") or [""])[0]
                self._json(200, mkd.next_for(role) or {})
            else:
                self._json(404, {"error": "not found"})
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "bad request"})
                return
            if self.path == "/panic":
                mkd.panic_now("cli")
                self._json(200, {"ok": True})
                return
            if self.path == "/register":
                agent = body.get("agent")
                pane_id = body.get("pane_id")
                if not agent or not pane_id:
                    self._json(400, {"error": "missing field"})
                    return
                mkd.register_agent(agent, pane_id, body.get("provider", "claude"))
                self._json(200, {"ok": True})
                return
            if self.path == "/repair":
                job_id = body.get("job_id")
                if not job_id:
                    self._json(400, {"error": "missing field"})
                    return
                try:
                    job = mkd.jobs.get(job_id)
                except KeyError:
                    self._json(404, {"error": "unknown job"})
                    return
                # Only valid if this job is the current in-flight job for its agent
                current = mkd.jobs.inflight_for(job.to)
                if current is None or current.id != job_id:
                    self._json(200, {"ok": False, "error": "not in-flight"})
                    return
                mkd.jobs.record_event(job_id, "repair-rewake")
                mkd._wake(job.to)
                if job_id in mkd._wd:
                    mkd._wd[job_id]["delivered_ts"] = mkd._now()
                    mkd._wd[job_id]["wakes"] = 0
                    # Re-arm the post-pickup stall clock so a repaired job gets a fresh window
                    # instead of being given up on the very next watchdog tick.
                    mkd._wd[job_id]["progress_ts"] = mkd._now()
                    mkd._wd[job_id]["injected_seen"] = False
                self._json(200, {"ok": True})
                return
            if self.path == "/resume":
                mkd.resume()
                self._json(200, {"ok": True})
                return
            if self.path == "/ask":
                # Reject new asks immediately if panicked
                if mkd.panic.is_panicked:
                    self._json(409, {"error": "panicked"})
                    return
                # Reject new asks if paused (soft stop)
                if mkd._paused:
                    self._json(409, {"error": f"paused: {mkd._pause_reason}"})
                    return
                frm = body.get("from")
                to = body.get("to")
                text = body.get("text")
                if not frm or not to or not text:
                    self._json(400, {"error": "missing field"})
                    return
                if to not in mkd.panes:
                    self._json(404, {"error": f"unknown role: {to}"})
                    return
                try:
                    reply = mkd.ask(frm, to, text)
                except ValueError as exc:
                    self._json(409, {"error": str(exc)})
                    return
                self._json(200, {"reply": reply})
            else:
                self._json(404, {"error": "not found"})
        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H


def serve(mkd: Mkd) -> None:
    mkd.start_poller()
    mkd.start_sentinel()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mkd))
    config.port_file().write_text(str(httpd.server_address[1]), encoding="utf-8")
    config.pid_file().write_text(str(os.getpid()), encoding="utf-8")

    # Signal handlers: SIGINT (Ctrl-C) is the catchable path on Windows.
    # Windows taskkill /F is not catchable (forceful kill) — SIGINT is.
    # We trigger panic first (unblocking all asks), then shut down the HTTP server
    # from a separate thread because httpd.shutdown() cannot be called from the
    # serving thread itself.
    # signal.signal() only works from the main thread; skip silently when called
    # from a background thread (e.g. during tests).
    def _signal_handler(signum, frame):
        mkd.panic_now("signal")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        try:
            signal.signal(signal.SIGTERM, _signal_handler)
        except (OSError, ValueError):
            pass  # SIGTERM not available on this platform (Windows main thread restriction)
    except (ValueError, OSError):
        pass  # not in main thread — signal installation skipped

    httpd.serve_forever()


def main() -> int:
    serve(Mkd(eventlog=EventLog(config.event_db())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
