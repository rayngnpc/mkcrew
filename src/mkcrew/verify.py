# src/mkcrew/verify.py
"""mk-verify-team: checks clone invariants for a MKCREW project directory."""
import json
import shutil
import urllib.request
import urllib.error
from pathlib import Path

from . import config


_SKILL_NAMES = [
    "task-router",
    "safe-agent-delegation",
    "senior-developer-loop",
    "team-self-improvement",
    "domain-playbooks",
]

_AGENT_REQUIRED_KEYS = {"role", "model", "window", "mode"}


def _check_config(project_dir: Path) -> dict:
    """Check 1: .mkcrew/team.config exists, is valid JSON, has entry_window and non-empty agents."""
    cfg_path = project_dir / ".mkcrew" / "team.config"
    if not cfg_path.exists():
        return {
            "name": "config",
            "ok": False,
            "detail": f"Missing {cfg_path}. Run `mk init` to create it.",
        }
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "name": "config",
            "ok": False,
            "detail": f"team.config is not valid JSON: {exc}. Run `mk init` to recreate.",
        }
    if "entry_window" not in data:
        return {
            "name": "config",
            "ok": False,
            "detail": "team.config missing 'entry_window' key.",
        }
    agents = data.get("agents", [])
    if not agents:
        return {
            "name": "config",
            "ok": False,
            "detail": "team.config 'agents' list is empty. Run `mk init` to restore defaults.",
        }
    for i, agent in enumerate(agents):
        missing = _AGENT_REQUIRED_KEYS - set(agent.keys())
        if missing:
            return {
                "name": "config",
                "ok": False,
                "detail": f"Agent[{i}] missing keys: {sorted(missing)}.",
            }
    return {
        "name": "config",
        "ok": True,
        "detail": f"Valid config with {len(agents)} agents, entry_window={data['entry_window']!r}.",
    }


def _check_skills(project_dir: Path) -> dict:
    """Check 2: all 5 skill SKILL.md files are present."""
    missing = []
    for name in _SKILL_NAMES:
        p = project_dir / ".claude" / "skills" / name / "SKILL.md"
        if not p.exists():
            missing.append(name)
    if missing:
        return {
            "name": "skills",
            "ok": False,
            "detail": f"Missing skills: {missing}. Run `mk init` to reinstall.",
        }
    return {
        "name": "skills",
        "ok": True,
        "detail": f"All {len(_SKILL_NAMES)} skills present.",
    }


def _check_planner_readonly(project_dir: Path) -> dict:
    """Check 3: safe-agent-delegation contains both required anchors."""
    skill_file = project_dir / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    if not skill_file.exists():
        return {
            "name": "planner-readonly",
            "ok": False,
            "detail": "safe-agent-delegation/SKILL.md missing; run `mk init`.",
        }
    text = skill_file.read_text(encoding="utf-8")
    missing_anchors = []
    if "PLANNER-READONLY-CONTRACT" not in text:
        missing_anchors.append("PLANNER-READONLY-CONTRACT")
    if "REPLY-DISCIPLINE" not in text:
        missing_anchors.append("REPLY-DISCIPLINE")
    if missing_anchors:
        return {
            "name": "planner-readonly",
            "ok": False,
            "detail": (
                f"safe-agent-delegation/SKILL.md is missing anchors: {missing_anchors}. "
                "Run `mk init` to reinstall the skill."
            ),
        }
    return {
        "name": "planner-readonly",
        "ok": True,
        "detail": "Both PLANNER-READONLY-CONTRACT and REPLY-DISCIPLINE anchors present.",
    }


def _check_mk_done() -> dict:
    """Check 4: mk-done is resolvable on PATH."""
    found = shutil.which("mk-done")
    if found is None:
        return {
            "name": "mk-done",
            "ok": False,
            "detail": (
                "mk-done not found on PATH. "
                "Run: .venv\\Scripts\\pip install -e . to reinstall the shim."
            ),
        }
    return {
        "name": "mk-done",
        "ok": True,
        "detail": f"mk-done found at {found}.",
    }


def _check_self_improvement(project_dir: Path) -> dict:
    """Check 5: .mkcrew-self-improvement/lessons.md exists."""
    lessons = project_dir / ".mkcrew-self-improvement" / "lessons.md"
    if not lessons.exists():
        return {
            "name": "self-improvement",
            "ok": False,
            "detail": (
                f"Missing {lessons}. Run `mk init` to scaffold .mkcrew-self-improvement/."
            ),
        }
    return {
        "name": "self-improvement",
        "ok": True,
        "detail": f"{lessons} exists.",
    }


def _check_daemon_health() -> dict:
    """Check 6 (best-effort): if port file exists, GET /health must return ok."""
    port_path = config.port_file()
    if not port_path.exists():
        return {
            "name": "daemon",
            "ok": True,
            "detail": "No port file found; daemon not running (skipped).",
        }
    try:
        port = port_path.read_text(encoding="utf-8").strip()
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
            if resp.status == 200 and body.get("ok"):
                return {
                    "name": "daemon",
                    "ok": True,
                    "detail": f"Daemon healthy at :{port}.",
                }
            return {
                "name": "daemon",
                "ok": True,  # best-effort: warn only
                "detail": f"Daemon at :{port} returned unexpected response: {body} (warning only).",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "daemon",
            "ok": True,  # best-effort: never hard-fail
            "detail": f"Daemon health check failed (warning only): {exc}.",
        }


# Hard checks (1-5): failure causes exit code 1
_HARD_CHECKS = {"config", "skills", "planner-readonly", "mk-done", "self-improvement"}


def verify_team(project_dir) -> list[dict]:
    """Run all invariant checks and return a list of result dicts.

    Each dict has: name (str), ok (bool), detail (str).
    The daemon check (name='daemon') is best-effort and never a hard failure.
    """
    project_dir = Path(project_dir)
    results = [
        _check_config(project_dir),
        _check_skills(project_dir),
        _check_planner_readonly(project_dir),
        _check_mk_done(),
        _check_self_improvement(project_dir),
        _check_daemon_health(),
    ]
    return results


def main() -> int:
    """Entrypoint for `mk-verify-team` and `mk verify`."""
    project_dir = Path.cwd()
    results = verify_team(project_dir)
    any_hard_failure = False
    for r in results:
        name = r["name"]
        ok = r["ok"]
        is_hard = name in _HARD_CHECKS
        label = "PASS" if ok else "FAIL"
        suffix = "" if is_hard else " (best-effort)"
        print(f"  {label}  {name}{suffix}: {r['detail']}")
        if not ok and is_hard:
            any_hard_failure = True
    if any_hard_failure:
        print("\nResult: FAIL — one or more hard checks failed.")
        return 1
    print("\nResult: PASS — all hard checks OK.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
