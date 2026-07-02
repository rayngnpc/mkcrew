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
