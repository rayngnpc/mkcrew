# src/mkcrew/daemon.py
import json, os, signal, time, threading, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import config, projections
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

# An ORPHANED-RESULT notice (finish artifact whose job_id this daemon has never heard of) is only
# typed to the lead when the artifact is at most this old (wall-clock). Finish dirs are global
# per-role, the event log is per-project -- so on a NEW project every historical leftover on the
# machine is "unknown"; only a FRESH unknown can be a genuinely lost live result.
ORPHAN_FRESH_SECONDS = 900.0


class Mkd:
    def __init__(self, mux=None, poll_interval=0.2, eventlog=None, mode="standard"):
        self.mux = mux or PsmuxBackend()
        self._eventlog = eventlog if eventlog is not None else EventLog(":memory:")
        self.jobs = JobStore(eventlog=self._eventlog)
        self._rehydrate()   # restart-proofing: reload any job still in flight when the last daemon died
        self.panes: dict[str, str] = {}
        self.providers: dict[str, str] = {}   # role -> provider (set at /register); delivery is provider-aware
        self.poll_interval = poll_interval
        # Core-mode posture (standard/fast/thorough/plan-first/architect). thorough+architect widen watchdog patience
        # (deep work legitimately takes long, quiet turns); everything else keeps the defaults.
        self.mode = mode
        self._events: dict[str, threading.Event] = {}
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._now = time.monotonic          # injectable clock for tests
        # Watchdog state: job_id -> {"hash": ..., "progress_ts": ..., "retries": ...}
        # Deliberately NOT rehydrated across restarts (unlike jobs -- see _rehydrate()): a
        # rehydrated job is reinserted straight into INCOMPLETE, so there is no live delivery
        # left to watch and no asker left to re-wake. Watchdog state always starts clean.
        self._wd: dict[str, dict] = {}
        self._last_wd: float = 0.0
        # Pane-activity feed for the control tower: role -> {"hash": <last capture's hash>,
        # "changed_ts": <self._now() when that hash last changed>}.  Tracked for EVERY registered
        # agent, not just in-flight ones -- a worker that keeps streaming output past its job's
        # watchdog give-up (INCOMPLETE) must still read as "alive" over /status.  (The incident this
        # exists for: a worker worked 49 min, the ask() ceiling gave up at 30, and the tower showed
        # INCOMPLETE/idle while the pane was visibly live.)
        self._activity: dict[str, dict] = {}
        # Panic controller — layer 1 / 2 / 3 kill switch
        self.panic = PanicController()
        # Pause state (distinct from panic — soft stop, resumable)
        self._paused: bool = False
        self._pause_reason: str = ""
        # Safety tracking
        self._jobs_completed: int = 0
        self._team_start: float | None = None   # set on first _deliver()
        self._consecutive_failures: int = 0

    def _rehydrate(self) -> None:
        """Startup replay of the persistent event log (restart-proofing). A job whose latest
        folded state is still PENDING/DELIVERED never reached job.done in a PREVIOUS daemon
        process -- that process (and the blocking mk ask() waiting on it) is gone, so
        INCOMPLETE is the truthful status now. Reinsert it under its ORIGINAL job_id so the
        worker's eventual mk-done is handled by the EXISTING late_reply()/_late_result() path
        (a LATE RESULT line to the lead) instead of an artifact nobody is tracking silently
        dropping the result. Terminal jobs are skipped -- projections.jobs() already folds a
        job.done event into DONE/INCOMPLETE/PANICKED, all terminal. An empty or fresh
        (:memory:) log folds to {}, so this is a no-op by construction; _wd/_events are never
        touched here (rehydrated jobs are already terminal-ish -- no asker is waiting).
        Terminal ids are remembered too (NOT reloaded as jobs): finish artifacts are files a
        prior session may not have deleted yet, so without this set every restart would
        ORPHANED-RESULT-spam the lead once per leftover artifact of a long-done job."""
        self._replayed_terminal: set[str] = set()
        for view in projections.jobs(self._eventlog.replay()).values():
            if view.status in ("PENDING", "DELIVERED"):
                self.jobs.rehydrate_incomplete(view.id, view.frm, view.to, view.text)
            else:
                self._replayed_terminal.add(view.id)

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
        # Architect mode: the lead judges evidence packs instead of re-reading diffs, so the
        # envelope tells the worker the reply shape. Checklist-echo + recorded-assumption +
        # positive scoping are the measured weak-model compliance levers (see architect research:
        # checklists beat free-form self-review; negative rule lists backfire; silent assumptions
        # are the top small-model failure). Planner replies are plans, not diffs -- skip.
        if self.mode == "architect" and job.to != "planner":
            body += ("\n\n---\nARCHITECT-MODE reply contract: follow the blueprint exactly -- "
                     "implement precisely what it names, the simplest version that meets the "
                     "criteria, touching only the files it lists. Where the blueprint is silent, "
                     "take the simplest option and RECORD that assumption in your reply.\n"
                     "Your mk-done reply is an EVIDENCE PACK:\n"
                     "1) the acceptance criteria restated as a CHECKLIST, each item ticked with "
                     "its proof (the exact command you ran + output tail)\n"
                     "2) changed files (file:line list)\n"
                     "3) recorded assumptions + risks.\n"
                     "Blocked, or the task doesn't match reality? Say so IN your reply -- never "
                     "ask main mid-task.")
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

    def _ask_ceiling(self, timeout: float) -> float:
        """thorough/architect expect DEEP worker turns: triple the blocking-ask ceiling (1800s -> 90 min).
        Every other mode is unchanged. (Live case: codex legitimately worked 40+ min and the 30-min
        ask timed it out TWICE — the work survived on disk but the reply was lost.)"""
        return timeout * (3 if self.mode in ("thorough", "architect") else 1)

    def _late_result(self, job_id, agent, reply, ts=None) -> None:
        """A finish artifact for a job that is not the current in-flight job: the WORK is real
        (it's on disk) -- surface it instead of dropping it.
          - Known job, still INCOMPLETE (timed out, or rehydrated after a restart -- see
            _rehydrate()): late_reply() records the real reply; tell the lead LATE RESULT.
          - job_id matches NO job at all (replay couldn't know it -- a corrupt/missing event
            db, or a cross-version daemon): ORPHANED RESULT, same don't-re-delegate framing --
            but ONLY when the artifact is FRESH (`ts` within ORPHAN_FRESH_SECONDS of wall-clock
            now). Finish dirs are GLOBAL per-role while the event log is per-PROJECT, so a NEW
            project's daemon knows none of the machine's historical job ids: without the
            freshness gate its first poll walked WEEKS of leftover artifacts and typed one
            orphan line per file into the lead (live incident). A genuinely-lost live result is
            always minutes old; anything stale (or missing ts) drains silently.
        A job that EXISTS but isn't eligible (DONE/PANICKED, or already late-replied once) is
        untouched -- late_reply()'s own guards keep that silent exactly like today, so
        heartbeats/stale artifacts never re-notify."""
        if not job_id:
            return
        if self.jobs.late_reply(job_id, reply):
            pane = self.panes.get("main")
            if pane is not None:
                try:
                    self.mux.send_line(
                        pane,
                        f"[MKCREW] LATE RESULT from {agent} ({job_id}): the worker FINISHED after your ask "
                        f"timed out. Reply: {str(reply)[:200]} -- review/integrate its work; do NOT re-delegate that task.")
                except Exception:
                    pass
            return
        try:
            self.jobs.get(job_id)
            return  # exists but not INCOMPLETE (or already late) -- today's silent no-op
        except KeyError:
            pass  # truly unknown to this daemon -- fall through to the orphan notice
        if job_id in self._replayed_terminal:
            return  # leftover artifact of a job that COMPLETED in a previous session (terminal in
                    # the replayed log) -- not a lost result, just an undeleted file; stay silent.
        # Wall-clock comparison on purpose: artifact ts is time.time() from mk-done (done_cli),
        # while self._now is monotonic and NOT comparable across processes.
        if not ts or (time.time() - ts) > ORPHAN_FRESH_SECONDS:
            return  # stale or unstamped: historical backlog / another project's leftover -- drain
                    # silently (the caller deletes it); never type old history into a live lead.
        pane = self.panes.get("main")
        if pane is not None:
            try:
                self.mux.send_line(
                    pane,
                    f"[MKCREW] ORPHANED RESULT from {agent} ({job_id}): a worker finished a task this "
                    f"daemon no longer tracks (likely a restart). Reply: {str(reply)[:200]} -- "
                    f"review/integrate its work; do NOT re-delegate.")
            except Exception:
                pass

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
        did = ev.wait(self._ask_ceiling(timeout))
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
            for art in sorted(config.agent_finish_dir(agent).glob("*.json")):
                if str(art) in self._seen:
                    continue
                self._seen.add(str(art))
                try:
                    data = json.loads(art.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if inflight is None or data.get("job_id") != inflight.id:
                    # heartbeat / other-job artifact — OR a LATE finish for a job the ask already
                    # timed out (it left the in-flight set, so it can never match here). Surface it;
                    # late_reply() no-ops for anything that isn't a timed-out job.
                    self._late_result(data.get("job_id"), agent, data.get("reply", ""),
                                      ts=data.get("ts"))
                    self._consume_artifact(art)
                    continue
                self.jobs.complete(inflight.id, reply=data.get("reply", ""))
                if inflight.id in self._events:
                    self._events[inflight.id].set()
                self._wd.pop(inflight.id, None)
                self._on_job_completed(inflight.id, status="DONE")
                self._evidence_gate(inflight, data.get("reply", ""))
                inflight_by_id.pop(inflight.id, None)
                self._consume_artifact(art)
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
                self._consume_artifact(art)

        # Watchdog tick — fire on interval
        if self._now() - self._last_wd >= WATCHDOG_INTERVAL_SECONDS:
            self._watchdog_tick()
            self._last_wd = self._now()

    def _evidence_gate(self, job, reply: str) -> None:
        """Architect-mode completion tripwire -- HARNESS-enforced, not prompt-enforced: prompted
        procedure alone measures ~70-90% compliance while an external check runs near 100%, and
        agents demonstrably self-certify. The envelope's reply contract asks for a checklist with
        command proof; a reply that clearly lacks that shape (too short, or no numbered/ticked
        item at all) is stamped into the ledger ('thin_evidence' -- folded by `mk stats`) and the
        lead is told to treat it as UNVERIFIED. Deliberately a tripwire, not a wall: the job still
        completes (a blocking gate could deadlock the ask), the lead just stops accepting a bare
        'done' as proof. Planner replies are plans, not evidence -- exempt."""
        if self.mode != "architect" or job.to == "planner":
            return
        r = (reply or "").strip()
        if len(r) >= 200 and ("1)" in r or "1." in r or "[x]" in r.lower()):
            return
        try:
            self.jobs.record_event(job.id, "thin_evidence")
        except Exception:
            pass
        pane = self.panes.get("main")
        if pane is not None:
            try:
                self.mux.send_line(
                    pane,
                    f"[MKCREW] EVIDENCE GATE: {job.to}'s reply for {job.id} lacks the evidence-pack "
                    f"shape (checklist + command proof). Treat it as UNVERIFIED -- re-ask for the "
                    f"proof or send your verifier before accepting.")
            except Exception:
                pass

    @staticmethod
    def _consume_artifact(art) -> None:
        """Delete a PROCESSED finish artifact (completed / late / orphan-notified / stale-dup).
        The event log is the durable ledger; finish files are a hand-off queue, and a drained
        entry has no reader left -- leaving them (the old behavior) meant the in-memory _seen
        dedup died with the process and every restart re-walked the leftovers. Parse-FAILED
        artifacts are never passed here: a partially-written file must survive for a later
        tick. Deletion failures are ignored -- _seen still dedups within this session."""
        try:
            art.unlink()
        except OSError:
            pass

    def _safe_capture(self, pane_id: str):
        """One mux.capture() call for `pane_id`, or None on any failure (dead pane / mux error) --
        so a bad pane can never raise out of _poll_once and take down the poll thread.  Shared by
        the zombie/progress checks and the pane-activity feed below; never call mux.capture() twice
        for the same pane in one tick -- it would desync ScriptedCaptureMux (tests/test_daemon.py),
        which advances its cursor per call."""
        try:
            return (self.mux.capture(pane_id) or "").strip()
        except Exception:
            return None

    def _record_activity(self, role: str, sig: str) -> None:
        """Update the tower's pane-activity feed for `role`: a CHANGED capture re-arms changed_ts
        (the /status 'seconds since last change' clock); an unchanged capture leaves it alone so
        idle time keeps accruing.  Only called with a successful capture -- a capture failure is a
        true no-op (self._activity untouched), matching the 'treat as no-change' contract."""
        h = hash(sig)
        prev = self._activity.get(role)
        if prev is None or prev["hash"] != h:
            self._activity[role] = {"hash": h, "changed_ts": self._now()}

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
        captured: set[str] = set()   # agents already captured this tick -- step 3 below must not repeat them
        for agent, pane_id in list(self.panes.items()):
            inflight = self.jobs.inflight_for(agent)
            if not inflight:
                continue
            wd = self._wd.get(inflight.id)
            if wd is None:
                continue

            # ONE capture per tick, reused for the zombie check, the post-pickup progress signal
            # (a changing pane == the worker is actively working), AND the tower's pane-activity
            # feed.  Capturing more than once here would desync ScriptedCaptureMux, which advances
            # its cursor per call.  A capture failure (None) is treated as blank/no-progress here,
            # exactly as before -- but it does NOT touch the activity feed (see _record_activity).
            raw = self._safe_capture(pane_id)
            sig = raw if raw is not None else ""
            captured.add(agent)
            if raw is not None:
                self._record_activity(agent, raw)

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
                # thorough/architect = patient watchdog: deep tasks stay quiet 3x longer before giveup
                stall = POST_PICKUP_STALL_SECONDS * (3 if self.mode in ("thorough", "architect") else 1)
                if self._now() - wd.get("progress_ts", self._now()) > stall:
                    self._giveup(inflight, "[stall_giveup] worker picked up the task but stopped making progress")
                continue

            # Not yet picked up: the content-free wake may not have landed (an idle TUI ignores a
            # bare Enter) — re-wake on an interval, then give up if it's never picked up.
            if self._now() - wd["delivered_ts"] > WAKE_RETRY_SECONDS:
                if wd["wakes"] < (MAX_RETRY * 2 if self.mode in ("thorough", "architect") else MAX_RETRY):
                    self._wake(agent)
                    self.jobs.record_event(inflight.id, "rewake")
                    wd["wakes"] += 1
                    wd["delivered_ts"] = self._now()
                else:
                    self._giveup(inflight, "[delivery_giveup] worker never picked up the task")

        # --- 3. Pane-activity feed for registered agents NOT captured in step 2 (idle, or their
        # in-flight job just ended) -- e.g. a worker whose job went INCOMPLETE (its _wd entry is
        # gone) but is STILL visibly working; the tower needs that signal precisely once the job
        # store stops tracking it.  Same one-capture-per-pane-per-tick rule as step 2.
        for agent, pane_id in list(self.panes.items()):
            if agent in captured:
                continue
            raw = self._safe_capture(pane_id)
            if raw is not None:
                self._record_activity(agent, raw)

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


def _job_created_ts(job):
    """The job's creation epoch time, read off its own event list (job.events[0] is always the
    'created' event appended by JobStore.open()) -- avoids touching jobs.py just to expose it over
    HTTP.  Returns None if a 'created' event is somehow missing (defensive; should not happen)."""
    for e in job.events:
        if e.get("label") == "created":
            return e.get("ts")
    return None


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
                        "created_ts": _job_created_ts(j),
                    }
                    for j in jobs
                ]
                self._json(200, {"jobs": result})
            elif self.path == "/status":
                now = mkd._now()
                self._json(200, {
                    "panicked": mkd.panic.is_panicked,
                    "paused": mkd._paused,
                    "pause_reason": mkd._pause_reason,
                    "agents": sorted(mkd.panes.keys()),
                    "jobs": len(mkd.jobs.list_jobs()),
                    "mode": mkd.mode,
                    # role -> seconds since its pane last changed (1dp), or None if never captured.
                    # Additive/optional: older daemons simply lack this key -- the tower (coreview.py)
                    # falls back to rendering exactly as before when it's absent.
                    "activity": {
                        role: (round(now - mkd._activity[role]["changed_ts"], 1)
                               if role in mkd._activity else None)
                        for role in mkd.panes
                    },
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
                    "created_ts": _job_created_ts(j),
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
            if self.path == "/mode":
                m = body.get("mode")
                if not m or not isinstance(m, str):
                    self._json(400, {"error": "missing field"})
                    return
                mkd.mode = m                                   # live watchdog patience switch
                pane = mkd.panes.get("main")                   # tell the RUNNING lead its new posture
                if pane is not None:
                    from . import prompts
                    try:
                        mkd.mux.send_line(pane, prompts.mode_update_prompt(m))
                    except Exception:
                        pass                                   # posture persisted; notify is best-effort
                self._json(200, {"ok": True, "mode": m})
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
