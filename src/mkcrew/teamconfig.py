# src/mkcrew/teamconfig.py
"""Config-driven team definition and loader."""
import json
import os
from pathlib import Path


def default_team() -> list[dict]:
    """Return the default 8-agent team definition (uniform workers + planner; no special reviewer)."""
    return [
        {"role": "main",     "model": "claude-opus-4-8",   "effort": "max",  "window": "main",    "mode": "bypassPermissions"},
        {"role": "worker1",  "model": "claude-opus-4-8",   "effort": "high", "window": "worker1", "mode": "bypassPermissions"},
        {"role": "worker2",  "model": "claude-sonnet-5", "effort": "high", "window": "worker2", "mode": "bypassPermissions"},
        {"role": "worker3",  "model": "claude-opus-4-8",   "effort": "high", "window": "worker3", "mode": "bypassPermissions"},
        {"role": "worker4",  "model": "claude-sonnet-5", "effort": "high", "window": "worker4", "mode": "bypassPermissions"},
        {"role": "worker5",  "model": "claude-opus-4-8",   "effort": "high", "window": "worker5", "mode": "bypassPermissions"},
        {"role": "worker6",  "model": "claude-sonnet-5", "effort": "high", "window": "worker6", "mode": "bypassPermissions"},
        {"role": "planner",  "model": "claude-opus-4-8",   "effort": "high", "window": "plan",    "mode": "bypassPermissions"},  # read-only enforced by prompt in Phase 2 (plan mode breaks the inbox read + mk-done)
    ]


def _config_path(project_dir) -> Path:
    return Path(project_dir) / ".mkcrew" / "team.config"


def load_team(project_dir) -> list[dict]:
    """Load team from <project>/.mkcrew/team.config.

    If the file is absent, write the default config and return the default team.
    Raises SystemExit if the file is present but corrupt or missing the 'agents' key.
    Each agent's 'provider' field defaults to 'claude' if absent (backward compat).
    """
    cfg = _config_path(project_dir)
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            agents = data["agents"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise SystemExit(
                f"error: .mkcrew/team.config is invalid ({exc}); run `mk init` to restore defaults."
            )
        for agent in agents:
            agent.setdefault("provider", "claude")
        return agents
    # Write default
    agents = default_team()
    write_team(project_dir, agents, "hub")
    return agents


_ROLE_PRIORITY = ["main", "worker1", "worker2", "worker3", "planner",
                  "worker4", "worker5", "worker6"]
_BUILTIN_PROVIDERS = {"claude", "codex", "gemini", "opencode", "antigravity"}


def write_team(project_dir, agents, layout: str = "hub", mode: str = "standard") -> Path:
    """The single team.config writer: {entry_window, layout, mode, agents}."""
    cfg = _config_path(project_dir)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"entry_window": "main", "layout": layout, "mode": mode, "agents": agents}, indent=2),
        encoding="utf-8",
    )
    return cfg


def build_team(count: int, providers=None, models=None, efforts=None, mode=None) -> list[dict]:
    """Compose `count` agents from the priority roster; apply per-agent provider / model / thinking.

    Each `providers` entry is a built-in name (claude/gemini/opencode) -> sets provider, or any other
    non-blank string -> provider='custom', command=<entry> (verbatim launch). `models[i]` / `efforts[i]`
    override that agent's model / reasoning level when non-blank (blank = keep the roster default).

    `mode` makes the seating mode-aware: chief, warroom and venture are built AROUND the planner seat
    (the blueprint/draft chair), but the priority roster only reaches 'planner' at count 5 -- so for
    those modes a count that sliced it out gets its LAST seat replaced by the planner (count >= 3,
    keeping at least one worker; a 2-agent main+worker team is left alone). Provider/model/effort
    overlays stay index-mapped, so the UI row that became the planner keeps its picked CLI."""
    roster = {a["role"]: a for a in default_team()}
    count = max(1, min(int(count), len(_ROLE_PRIORITY)))
    roles = list(_ROLE_PRIORITY[:count])
    if mode in ("chief", "warroom", "venture") and count >= 3 and "planner" not in roles:
        roles[-1] = "planner"
    agents = [dict(roster[r]) for r in roles]
    for i, spec in enumerate(providers or []):
        if i >= len(agents):
            break
        spec = (spec or "").strip()
        if not spec:
            continue
        # "<builtin>@<binary>": keep the PROVIDER (so its completion hook + daemon delivery routing are
        # intact) but run a specific CLI executable -- typically an account wrapper. Example:
        # `claude@/home/u/bin/claudew` -> provider 'claude' with bin=/home/u/bin/claudew, which loads
        # that account's credentials while still using the claude Stop hook.
        base, sep, binpath = spec.partition("@")
        if sep and base in _BUILTIN_PROVIDERS and binpath.strip():
            agents[i]["provider"] = base
            agents[i]["bin"] = os.path.expanduser(binpath.strip())
        elif spec in _BUILTIN_PROVIDERS:
            agents[i]["provider"] = spec
        else:
            agents[i]["provider"] = "custom"
            agents[i]["command"] = spec
    explicit_model = set()
    for i, m in enumerate(models or []):
        if i < len(agents) and (m or "").strip():
            agents[i]["model"] = m.strip()
            explicit_model.add(i)
    # A non-claude agent with NO explicit model must NOT inherit the claude roster default (every
    # roster model is claude-*): persisting e.g. "claude-sonnet-5" on an opencode agent is
    # misleading. Blank it so team.config reflects reality and the provider CLI picks its own default.
    for i, a in enumerate(agents):
        if (i not in explicit_model and a.get("provider", "claude") != "claude"
                and str(a.get("model", "")).startswith("claude-")):
            a["model"] = ""
    for i, e in enumerate(efforts or []):
        if i < len(agents) and (e or "").strip():
            agents[i]["effort"] = e.strip()
    return agents


def _team_signature(team) -> dict:
    """role -> the CLI it runs (provider, or the custom command) -- for change detection."""
    return {a["role"]: (a.get("command") or a.get("provider", "claude")) for a in team}


def team_changes(project_dir, team) -> list:
    """Diff the current team against the last launched one; return human-readable change lines
    (empty if unchanged), then persist the current team as the new snapshot. Lets a RESUMED lead
    be told ONLY what changed (CLI swaps, agents joined/left) -- no change means no message.
    Scales to any team size (it's a per-role dict diff)."""
    new = _team_signature(team)
    snap = _config_path(project_dir).parent / "last_team.json"
    old = {}
    if snap.exists():
        try:
            old = json.loads(snap.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    changes = []
    for role, cli in new.items():
        if role not in old:
            changes.append(f"{role} ({cli}) joined")
        elif old[role] != cli:
            changes.append(f"{role} is now {cli} (was {old[role]})")
    for role, cli in old.items():
        if role not in new:
            changes.append(f"{role} ({cli}) left")
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps(new, indent=2), encoding="utf-8")
    return changes


def dump_default(project_dir) -> Path:
    """Write the default 8-agent hub config and return the path."""
    return write_team(project_dir, default_team(), "hub")


def load_layout(project_dir) -> str:
    """Return the configured cockpit layout name; default 'hub' when absent/corrupt."""
    cfg = _config_path(project_dir)
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("layout", "hub")
        except json.JSONDecodeError:
            return "hub"
    return "hub"


def load_mode(project_dir) -> str:
    """Return the configured core mode; 'standard' (default posture) when absent/corrupt.
    'fast' tells the lead to drop the planner/review/verify gates (see prompts._MODE_CLAUSE)."""
    cfg = _config_path(project_dir)
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("mode", "standard")
        except json.JSONDecodeError:
            return "standard"
    return "standard"


def _workspace_path(project_dir) -> Path:
    """Per-workspace identity file: <project>/.mkcrew/workspace.json. Kept SEPARATE from team.config so
    a name survives every team.config rewrite (write_team / set_layout / save_config) untouched."""
    return Path(project_dir) / ".mkcrew" / "workspace.json"


def load_name(project_dir) -> str | None:
    """Return the workspace's persisted human name (FIX #4: name-as-identity), or None when unset."""
    p = _workspace_path(project_dir)
    if p.exists():
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get("name") or "").strip() or None
        except (json.JSONDecodeError, OSError):
            return None
    return None


def set_name(project_dir, name) -> Path:
    """Persist the workspace's human name into .mkcrew/workspace.json (a blank/None name clears it)."""
    p = _workspace_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    name = (name or "").strip()
    if name:
        data["name"] = name
    else:
        data.pop("name", None)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def set_layout(project_dir, layout: str) -> Path:
    """Persist `layout` into team.config, preserving agents/entry_window. Creates a
    default config first if none exists. Returns the config path."""
    cfg = _config_path(project_dir)
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
    else:
        data = {"entry_window": "main", "agents": default_team()}
    data["layout"] = layout
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg


def set_mode(project_dir, mode: str) -> Path:
    """Persist the core-mode posture into team.config, preserving everything else (mirrors
    set_layout). Creates a default config first if none exists. Returns the config path."""
    cfg = _config_path(project_dir)
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
    else:
        data = {"entry_window": "main", "agents": default_team()}
    data["mode"] = mode
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg
