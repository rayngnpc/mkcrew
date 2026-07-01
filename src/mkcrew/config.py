# src/mkcrew/config.py
import hashlib
import os
from pathlib import Path

def runtime_root() -> Path:
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
