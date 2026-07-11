# src/mkcrew/config.py
import hashlib
import json
import os
from pathlib import Path

def runtime_root() -> Path:
    # MK_RUNTIME_ROOT (exported into each agent pane by `mk start`) pins the runtime dir so an agent CLI
    # whose account wrapper rewrites HOME or the profile dirs can't move where mk-done, the completion
    # hooks, and the opencode plugin look for the daemon -- they must resolve the SAME dir the daemon
    # uses, not one shifted by the wrapper. Unset (normal mk/mkd processes) -> the LOCALAPPDATA default.
    forced = os.environ.get("MK_RUNTIME_ROOT")
    if forced:
        return Path(forced)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "mkcrew"

def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def agent_config_dir(agent: str) -> Path:
    return _ensure(runtime_root() / "runtime" / agent / "claude-config")

def agent_inbox_dir(agent: str) -> Path:
    return _ensure(runtime_root() / "runtime" / agent / "inbox")

def agent_finish_dir(agent: str) -> Path:
    return _ensure(runtime_root() / "runtime" / agent / "finish")

def port_file() -> Path:
    return _ensure(runtime_root() / "runtime") / "mkd.port"

def pid_file() -> Path:
    return _ensure(runtime_root() / "runtime") / "mkd.pid"

def event_db(project=None) -> Path:
    """Per-project event log — each project's tasks stay isolated instead of pooling in one global
    DB.  `project` is the project dir; None falls back to MK_PROJECT (set on the daemon) or the cwd,
    so the daemon, `mk status`, and the live core (all running in / launched with the project dir)
    resolve to the same per-project database."""
    root = project or os.environ.get("MK_PROJECT") or os.getcwd()
    key = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]
    return _ensure(runtime_root() / "runtime" / "projects" / key) / "events.db"

def profiles_dir() -> Path:
    return _ensure(runtime_root() / "profiles")

def sentinel_file() -> Path:
    return runtime_root() / "PANIC"

def cockpit_project_file() -> Path:
    """Path of the project whose cockpit is currently running — written by `mk start`, removed by
    `mk kill`. Lets Studio warn before a Launch replaces a DIFFERENT project's live cockpit."""
    return _ensure(runtime_root() / "runtime") / "cockpit_project.txt"


def load_accounts() -> list:
    """User-defined account wrappers (accounts.json under runtime_root): a JSON list of
    {label, provider, bin, default?}. `bin` is a launcher that scopes an account's credentials (a
    wrapper exporting CLAUDE_CONFIG_DIR / CODEX_HOME / XDG / HOME). Validated (provider + bin present;
    ~ expanded); [] when absent/invalid. Shared by Studio (dropdown options) and the launch path
    (default-account resolution for bare providers)."""
    p = runtime_root() / "accounts.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for a in (data if isinstance(data, list) else []):
        prov, binp = a.get("provider"), (a.get("bin") or "").strip()
        if prov and binp:
            out.append({"label": a.get("label") or "", "provider": prov,
                        "bin": str(Path(binp).expanduser()), "default": bool(a.get("default"))})
    return out


def default_account_bin(provider: str) -> str | None:
    """The account wrapper a BARE built-in provider should run: the account flagged `default: true` for
    `provider`, else the FIRST account listed for it, else None. This is the ROOT fix for account drift:
    a bare `claude`/`codex`/... otherwise reads the shared, ambient ~/.claude (or $CLAUDE_CONFIG_DIR),
    whose signed-in account changes over time; resolving to a dedicated wrapper makes it deterministic."""
    accts = [a for a in load_accounts() if a["provider"] == provider]
    if not accts:
        return None
    return next((a["bin"] for a in accts if a["default"]), accts[0]["bin"])
