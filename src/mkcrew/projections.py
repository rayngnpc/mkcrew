# src/mkcrew/projections.py
"""Pure folds over an event list into views. No I/O — trivially testable."""
from dataclasses import dataclass

_TERMINAL = ("DONE", "INCOMPLETE", "PANICKED")


@dataclass
class JobView:
    id: str
    frm: str = ""
    to: str = ""
    text: str = ""
    status: str = "PENDING"
    reply: str = ""
    delivered_at: float = 0.0
    created_ts: float = 0.0   # epoch seconds of the job.created event -- job age for the tower


def jobs(events):
    out = {}
    for e in events:
        if e.type == "job.created":
            d = e.data
            out[e.job_id] = JobView(id=e.job_id, frm=d.get("frm", ""),
                                    to=d.get("to", ""), text=d.get("text", ""),
                                    created_ts=e.ts)
        elif e.job_id in out:
            j = out[e.job_id]
            if e.type == "job.delivered":
                if j.status not in _TERMINAL:
                    j.status = "DELIVERED"
                    j.delivered_at = e.ts
            elif e.type == "job.done":
                if j.status not in _TERMINAL:
                    j.status = e.data.get("status", "DONE")
                    j.reply = e.data.get("reply", "")
    return out


def agents(events):
    state = {}
    for j in jobs(events).values():
        running = j.status in ("PENDING", "DELIVERED")
        state[j.to] = {"state": "running" if running else "idle",
                       "job": j.id if running else None,
                       "task": j.text if running else ""}
    return state


def activity(events, limit=20):
    return [{"ts": e.ts, "type": e.type, "job_id": e.job_id, "actor": e.actor}
            for e in events[-limit:]]
