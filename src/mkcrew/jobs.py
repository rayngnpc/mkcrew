# src/mkcrew/jobs.py
import threading, time, uuid
from dataclasses import dataclass, field

@dataclass
class Job:
    id: str
    frm: str
    to: str
    text: str
    status: str = "PENDING"   # PENDING -> DELIVERED -> DONE / INCOMPLETE
    reply: str = ""
    delivered_at: float = 0.0  # epoch seconds; set when job is delivered
    events: list = field(default_factory=list)  # [{"ts": float, "label": str}, ...]

class JobStore:
    def __init__(self, eventlog=None):
        self._jobs: dict[str, Job] = {}
        self._inflight: dict[str, str] = {}   # agent -> job_id
        self._lock = threading.Lock()
        self._log = eventlog

    def _emit(self, type, job_id, actor, data=None):
        # Called INSIDE the JobStore lock so the event log's append-order always
        # matches the in-memory mutation-order (required for correct replay).
        # EventLog has its own lock; ordering is always JobStore-lock -> EventLog-lock
        # (never the reverse), so no deadlock cycle exists.
        if self._log is not None:
            self._log.append(type, job_id=job_id, actor=actor, data=data or {})

    def open(self, frm: str, to: str, text: str) -> Job:
        with self._lock:
            if to in self._inflight:
                raise ValueError(f"{to} already has an in-flight job")
            jid = f"task-{uuid.uuid4().hex[:8]}"  # random: never collides across restarts/projects
            job = Job(id=jid, frm=frm, to=to, text=text)
            job.events.append({"ts": time.time(), "label": "created"})
            self._jobs[jid] = job
            self._inflight[to] = jid
            self._emit("job.created", jid, frm, {"frm": frm, "to": to, "text": text})
        return job

    def active_others(self, exclude_id: str) -> list:
        """Non-terminal jobs OTHER than `exclude_id` — the teammates-FYI source for inbox envelopes
        (all agents edit the same checkout; naming what's in flight lets them avoid collisions)."""
        with self._lock:
            return [j for j in self._jobs.values()
                    if j.id != exclude_id and j.status in {"PENDING", "DELIVERED"}]

    def mark_delivered(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "DELIVERED"
            job.delivered_at = time.time()
            job.events.append({"ts": time.time(), "label": "delivered"})
            self._emit("job.delivered", job_id, job.to, {})

    def rehydrate_incomplete(self, job_id: str, frm: str, to: str, text: str) -> Job:
        """Reinsert a job whose last known event-log state was still in flight when a
        PREVIOUS daemon process died (called only from Mkd's startup replay -- jobs.py
        itself never reads the log). Bypasses open(): the id is FIXED to the original job_id
        (a worker's late mk-done must still find it) and it is deliberately kept OUT of
        _inflight -- this daemon isn't delivering it, so it must never block a fresh open()
        for the same `to`, and it must never look "active" to inflight_for()/active_others().
        Status goes straight to INCOMPLETE: the asker (the blocking ask() that would have
        woken on completion) died with the old process, so there is truthfully no one left
        waiting -- late_reply() then picks up the eventual mk-done exactly like any other
        post-timeout finish."""
        with self._lock:
            job = Job(id=job_id, frm=frm, to=to, text=text, status="INCOMPLETE",
                      reply="[restart] daemon restarted while this task was in flight")
            job.events.append({"ts": time.time(), "label": "rehydrated"})
            self._jobs[job_id] = job
        return job

    def late_reply(self, job_id: str, reply: str) -> bool:
        """A worker finished AFTER its job was timed out (INCOMPLETE): record the real outcome in
        the ledger instead of dropping it. Returns True only when applied (job exists, INCOMPLETE,
        and no late reply recorded yet) — every other status is a no-op, so heartbeats/stale
        artifacts can never rewrite history."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "INCOMPLETE" or job.reply.startswith("[late]"):
                return False
            job.reply = f"[late] {reply}"
            job.events.append({"ts": time.time(), "label": "late_done"})
            self._emit("job.late_done", job_id, job.to, {"reply": reply})
        return True

    def complete(self, job_id: str, reply: str, status: str = "DONE") -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status in {"DONE", "INCOMPLETE", "PANICKED"}:
                self._inflight.pop(job.to, None)  # idempotent cleanup
            else:
                job.status = status
                job.reply = reply
                job.events.append({"ts": time.time(), "label": f"completed:{status}"})
                self._inflight.pop(job.to, None)
                self._emit("job.done", job_id, job.to, {"status": status, "reply": reply})

    def record_event(self, job_id: str, label: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.events.append({"ts": time.time(), "label": label})
            self._emit("job.event", job_id, job.to, {"label": label})

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def inflight_for(self, agent: str):
        with self._lock:
            jid = self._inflight.get(agent)
            return self._jobs[jid] if jid else None

    def list_jobs(self) -> list:
        with self._lock:
            return list(self._jobs.values())
