# src/mkcrew/sessions.py
"""Per-(project, role) agent session-id store for resume.
Persisted at <project>/.mkcrew/sessions.json (machine-local; gitignored).

The store is provider-agnostic: `ensure` mints one stable uuid per (project, role) no matter which
CLI the role runs, so a codex/opencode/agy/gemini main resumes its prior session on cockpit restart
the way a claude main already does. `is_resumable` then encodes each provider's resume rule."""
import json
import re
import uuid
from pathlib import Path

# "continue-last" / preset-id CLIs that reopen a prior session on restart. claude is handled
# separately (it stat()s a saved transcript); these reopen the most-recent (or id'd) PROJECT
# session, so once a role has launched before in this project they are resumable. An unknown or
# `custom` provider is intentionally absent -> never resumed (always relaunched fresh).
_RESUMABLE_PROVIDERS = {"codex", "opencode", "antigravity", "gemini"}

# TRUE "continue-last" CLIs: codex `resume --last`, opencode/antigravity `--continue` reopen THE most
# recent PROJECT session with NO per-role id to target. So 2+ agents of one of these in the same
# project can't each resume THEIR own history -- they would ALL reopen the single shared "last"
# session (corrupted history + cross-talk between panes). When a team runs 2+ of one of these, that
# provider is launched FRESH instead (see resume_flag). claude/gemini are deliberately ABSENT: they
# key resume off a per-role UUID (claude via its transcript, gemini via `--session-id`/`--resume
# <uuid>`), so two agents of those providers never target the same session and stay resumable.
_CONTINUE_LAST_PROVIDERS = {"codex", "opencode", "antigravity"}


def _dir(project_dir) -> Path:
    return Path(project_dir) / ".mkcrew"


def _path(project_dir) -> Path:
    return _dir(project_dir) / "sessions.json"


def _load(project_dir) -> dict:
    p = _path(project_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def ensure(project_dir, role: str) -> tuple[str, bool]:
    """Return (session_uuid, is_new) for (project, role).
    Generates + persists a new uuid the first time a role is seen."""
    data = _load(project_dir)
    if role in data:
        return data[role], False
    sid = str(uuid.uuid4())
    data[role] = sid
    d = _dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    _path(project_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("sessions.json\n", encoding="utf-8")
    return sid, True


def clear(project_dir) -> None:
    """Remove the store so the next start creates fresh sessions."""
    _path(project_dir).unlink(missing_ok=True)


def is_resumable(project_dir, session_id: str, provider: str = "claude") -> bool:
    """True when (provider, session_id) can be resumed on a cockpit restart.

    The caller still gates on `not is_new` (i.e. this role has launched before in this project);
    this function adds the per-provider rule on top of that:

    - claude: resumable ONLY once claude has actually SAVED the transcript. Claude stores it at
      ~/.claude/projects/<cwd with :\\/ replaced by '-'>/<id>.jsonl; a launched-but-never-used
      session has none, so `claude --resume <id>` would fail 'No conversation found' and the agent
      would exit -- so we only --resume when the transcript exists, else recreate it fresh.
    - codex/opencode/antigravity/gemini: "continue-last" CLIs that reopen the most-recent (or id'd)
      project session, so "launched before" is sufficient -- there is no per-id transcript to stat.
    - anything else (unknown / `custom`): never resumable -> always relaunch fresh."""
    if provider != "claude":
        return provider in _RESUMABLE_PROVIDERS
    enc = re.sub(r"[:\\/]", "-", str(Path(project_dir)))
    return (Path.home() / ".claude" / "projects" / enc / f"{session_id}.jsonl").exists()


def resume_flag(project_dir, session_id: str, provider: str, *, shared_provider: bool) -> bool:
    """Whether to RESUME this agent: `is_resumable` PLUS a same-provider collision guard.

    A "continue-last" provider (codex/opencode/antigravity) has no per-role session id -- its only
    resume is `resume --last` / `--continue`, which always reopen THE most-recent project session.
    When 2+ agents in the team run the SAME such provider (`shared_provider=True`) they would ALL
    reopen that one session -> shared/corrupted history + cross-talk. So each is launched FRESH
    instead (a fresh codex/opencode/agy launch starts its OWN new session; two fresh launches never
    collide), trading cross-restart continuity for correctness -- which co-resuming a single shared
    session cannot give them anyway. A SOLE agent of the provider (`shared_provider=False`) resumes
    normally. claude/gemini are unaffected: they resume by a per-role UUID, so two of them never
    target the same session -- they stay resumable even when the provider is shared.

    Isolation is done here (refuse co-resume) rather than by pointing each agent at a per-role
    CODEX_HOME / data dir: codex auth is ChatGPT OAuth and opencode mixes OAuth with API keys, both
    co-located WITH the sessions in that home dir, so a per-role home would strand auth (duplicating
    rotating OAuth tokens across N dirs is the fragile trap MKCREW already abandoned once)."""
    if shared_provider and provider in _CONTINUE_LAST_PROVIDERS:
        return False
    return is_resumable(project_dir, session_id, provider)
