# src/mkcrew/finish_hook.py
import sys, json, time, traceback
from pathlib import Path
from . import config
from .transcript import last_assistant_reply
def _resolve_transcript(transcript_path: str, session_id: str) -> str:
    """Return the path to the best transcript to read.

    Claude Code 2.1.173 writes the hook's transcript_path as
    ~/.claude/projects/<enc>/<uuid>.jsonl which contains only an ai-title line.
    The real conversation is at ~/.claude/transcripts/ses_<sessionId>*.jsonl.
    Try that first; fall back to the original path.
    """
    if session_id:
        transcripts_dir = Path.home() / ".claude" / "transcripts"
        matches = sorted(transcripts_dir.glob(f"ses_{session_id}*.jsonl"))
        if matches:
            return str(matches[-1])
    return transcript_path


def decide_block(data: dict, actor: str, fetch_next):
    """Decide whether to inject a queued task into the agent's context at turn-end.

    Returns ``{"decision": "block", "reason": ...}`` to inject (Claude continues the
    turn with the task in context — zero visible keystrokes), or ``None`` to let the
    turn end normally.  ``fetch_next(role)`` returns the daemon's /next payload
    (``{"job_id", "reason"}``) or ``None``.
    """
    if data.get("stop_hook_active"):
        return None  # already in a hook-driven continuation — don't block again (else infinite loop)
    try:
        nxt = fetch_next(actor)
    except Exception:
        return None  # a delivery hiccup must NEVER break the agent's turn
    if nxt and nxt.get("reason"):
        return {"decision": "block", "reason": nxt["reason"]}
    return None


def _fetch_next(actor: str):
    """GET the daemon's /next?role=<actor>; return its JSON payload or None on failure."""
    import urllib.request, urllib.parse
    port = config.port_file().read_text(encoding="utf-8").strip()
    url = f"http://127.0.0.1:{port}/next?role={urllib.parse.quote(actor)}"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read() or b"{}")


def run(stdin) -> int:
    actor = __import__("os").environ.get("MK_ACTOR", "unknown")
    raw = stdin.read()
    if raw.startswith("ï»¿"):
        raw = raw[3:]
    raw = raw.lstrip("\ufeff")
    try:
        data = json.loads(raw) if raw.strip() else {"stop_hook_active": False}
    except Exception:
        return 0  # never break the agent's turn

    # Forward delivery: at turn-end, pull any task queued for this actor and inject it
    # into the model's context via a block decision — NO visible keystrokes in the pane.
    # decide_block swallows fetch errors; the outer guard ensures we never break the turn.
    try:
        decision = decide_block(data, actor, _fetch_next)
        if decision is not None:
            print(json.dumps(decision))
            return 0  # blocking continues the turn with the task in context; skip reply-capture
    except Exception:
        pass

    # Resolve which transcript file actually has the conversation.
    # Prefer data["session_id"] (direct); also try reading the sessionId out
    # of the ai-title line in the project file as a fallback.
    session_id = data.get("session_id", "")
    raw_path = data.get("transcript_path", "")

    if not session_id and raw_path:
        try:
            p = Path(raw_path)
            if p.exists():
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        sid = obj.get("sessionId", "")
                        if sid:
                            session_id = sid
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    resolved_path = _resolve_transcript(raw_path, session_id)
    try:
        reply = last_assistant_reply(resolved_path)
        if not reply and resolved_path != raw_path:   # resolved gave nothing — try the original
            reply = last_assistant_reply(raw_path)
    except Exception:
        reply = ""   # codex/agy transcript formats differ — never let capture break the turn

    artifact = {
        "actor": actor,
        "reply": reply,
        "transcript_path": resolved_path,
        "ts": time.time(),
    }
    dest = config.agent_finish_dir(actor) / f"{int(time.time()*1000)}.json"
    try:
        dest.write_text(json.dumps(artifact), encoding="utf-8")
    except Exception:
        traceback.print_exc()  # visible in the pane; never raises

    return 0


def main() -> int:
    return run(sys.stdin)

if __name__ == "__main__":
    raise SystemExit(main())
