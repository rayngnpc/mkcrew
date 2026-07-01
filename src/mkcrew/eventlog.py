# src/mkcrew/eventlog.py
"""Append-only SQLite event log — the single source of truth for MKCREW."""
import json, sqlite3, threading, time
from dataclasses import dataclass


@dataclass
class Event:
    seq: int
    ts: float
    type: str
    job_id: str
    actor: str
    data: dict


class EventLog:
    def __init__(self, path):
        self._lock = threading.Lock()
        # check_same_thread=False: the daemon's poll thread + HTTP threads share one log.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL, type TEXT, job_id TEXT, actor TEXT, data TEXT)"
        )
        self._conn.commit()

    def append(self, type, job_id="", actor="", data=None, ts=None):
        ts = time.time() if ts is None else ts
        payload = json.dumps(data or {})
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, type, job_id, actor, data) VALUES (?,?,?,?,?)",
                (ts, type, job_id, actor, payload),
            )
            self._conn.commit()
            return cur.lastrowid

    def since(self, seq):
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, type, job_id, actor, data FROM events"
                " WHERE seq > ? ORDER BY seq",
                (seq,),
            ).fetchall()
        return [Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5])) for r in rows]

    def replay(self):
        return self.since(0)

    def close(self):
        with self._lock:
            self._conn.close()
