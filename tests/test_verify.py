# tests/test_verify.py
"""Tests for P2-3: mk-verify-team invariant checker (TDD — written before verify.py)."""
import json
import shutil
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers: build a fully-valid temp project
# ---------------------------------------------------------------------------

def _make_valid_project(tmp_path: Path) -> Path:
    """Set up a temp project that satisfies ALL hard checks 1-5.

    Uses cmd_init helpers so we exercise the real install path.
    """
    from mkcrew.cli import install_skills, scaffold_self_improvement
    from mkcrew import teamconfig

    project = tmp_path / "myproject"
    project.mkdir()

    # Check 1: write valid team.config
    teamconfig.dump_default(project)

    # Check 2 + 3: install 5 skills (includes safe-agent-delegation with anchors)
    install_skills(project)

    # Check 5: self-improvement scaffold
    scaffold_self_improvement(project)

    return project


def _results_by_name(results: list[dict]) -> dict:
    return {r["name"]: r for r in results}


# ---------------------------------------------------------------------------
# Baseline: fully-valid project — all hard checks pass
# ---------------------------------------------------------------------------

def test_all_hard_checks_pass_for_valid_project(tmp_path, monkeypatch):
    """A fully initialised project must yield ok=True for all 5 hard checks."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    # Monkeypatch shutil.which so check 4 (mk-done on PATH) passes
    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done" if name == "mk-done" else None)

    from mkcrew.verify import verify_team
    results = verify_team(project)
    by_name = _results_by_name(results)

    hard_names = {"config", "skills", "planner-readonly", "mk-done", "self-improvement"}
    for name in hard_names:
        assert name in by_name, f"missing check '{name}' in results"
        assert by_name[name]["ok"], (
            f"check '{name}' unexpectedly failed: {by_name[name].get('detail', '')}"
        )


def test_verify_team_returns_list_of_dicts(tmp_path, monkeypatch):
    """verify_team must return a list of dicts each with name/ok/detail keys."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    from mkcrew.verify import verify_team
    results = verify_team(project)
    assert isinstance(results, list)
    assert len(results) >= 5  # at least 5 hard checks (+ optional daemon check)
    for r in results:
        assert "name" in r
        assert "ok" in r
        assert "detail" in r


# ---------------------------------------------------------------------------
# Check 1: config
# ---------------------------------------------------------------------------

def test_check1_fails_when_config_missing(tmp_path, monkeypatch):
    """Check 'config' must fail when team.config does not exist."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    # Remove the config file
    (project / ".mkcrew" / "team.config").unlink()

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["config"]["ok"], "config check should fail with missing team.config"


def test_check1_fails_when_config_invalid_json(tmp_path, monkeypatch):
    """Check 'config' must fail when team.config contains invalid JSON."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    (project / ".mkcrew" / "team.config").write_text("NOT JSON", encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["config"]["ok"]


def test_check1_fails_when_agents_list_empty(tmp_path, monkeypatch):
    """Check 'config' must fail when agents list is empty."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    cfg_path = project / ".mkcrew" / "team.config"
    cfg_path.write_text(json.dumps({"entry_window": "main", "agents": []}), encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["config"]["ok"]


def test_check1_fails_when_entry_window_missing(tmp_path, monkeypatch):
    """Check 'config' must fail when entry_window key is absent."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    cfg_path = project / ".mkcrew" / "team.config"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    del data["entry_window"]
    cfg_path.write_text(json.dumps(data), encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["config"]["ok"]


def test_check1_fails_when_agent_missing_required_key(tmp_path, monkeypatch):
    """Check 'config' must fail when an agent is missing 'role'."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    cfg_path = project / ".mkcrew" / "team.config"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Remove 'role' from first agent
    del data["agents"][0]["role"]
    cfg_path.write_text(json.dumps(data), encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["config"]["ok"]


# ---------------------------------------------------------------------------
# Check 2: skills
# ---------------------------------------------------------------------------

def test_check2_fails_when_skill_missing(tmp_path, monkeypatch):
    """Check 'skills' must fail when a skill directory is absent."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    # Remove one skill
    skill_path = project / ".claude" / "skills" / "task-router" / "SKILL.md"
    skill_path.unlink()

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["skills"]["ok"]


def test_check2_fails_when_skill_dir_entirely_absent(tmp_path, monkeypatch):
    """Check 'skills' must fail when a skill dir is entirely missing."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    shutil.rmtree(project / ".claude" / "skills" / "domain-playbooks")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["skills"]["ok"]


def test_check2_passes_with_all_five_skills(tmp_path, monkeypatch):
    """Check 'skills' must pass when all 5 skill SKILL.md files exist."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert results["skills"]["ok"]


# ---------------------------------------------------------------------------
# Check 3: planner-readonly anchors
# ---------------------------------------------------------------------------

def test_check3_fails_when_planner_readonly_anchor_stripped(tmp_path, monkeypatch):
    """Check 'planner-readonly' fails when PLANNER-READONLY-CONTRACT anchor is removed."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    skill_file = project / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    stripped = text.replace("PLANNER-READONLY-CONTRACT", "PLANNER-READONLY-REMOVED")
    skill_file.write_text(stripped, encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["planner-readonly"]["ok"]


def test_check3_fails_when_reply_discipline_anchor_stripped(tmp_path, monkeypatch):
    """Check 'planner-readonly' fails when REPLY-DISCIPLINE anchor is removed."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    skill_file = project / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    # Replace with a string that does NOT contain the original anchor as a substring
    stripped = text.replace("REPLY-DISCIPLINE", "REMOVED-ANCHOR")
    skill_file.write_text(stripped, encoding="utf-8")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["planner-readonly"]["ok"]


def test_check3_passes_when_both_anchors_present(tmp_path, monkeypatch):
    """Check 'planner-readonly' passes when both anchors exist in safe-agent-delegation."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert results["planner-readonly"]["ok"]


# ---------------------------------------------------------------------------
# Check 4: mk-done on PATH
# ---------------------------------------------------------------------------

def test_check4_fails_when_mk_done_not_on_path(tmp_path, monkeypatch):
    """Check 'mk-done' must fail when shutil.which('mk-done') returns None."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: None)

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["mk-done"]["ok"]


def test_check4_passes_when_mk_done_on_path(tmp_path, monkeypatch):
    """Check 'mk-done' passes when shutil.which returns a non-None path."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "C:/fake/mk-done.exe")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert results["mk-done"]["ok"]


# ---------------------------------------------------------------------------
# Check 5: self-improvement
# ---------------------------------------------------------------------------

def test_check5_fails_when_lessons_md_missing(tmp_path, monkeypatch):
    """Check 'self-improvement' fails when .mkcrew-self-improvement/lessons.md is absent."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    (project / ".mkcrew-self-improvement" / "lessons.md").unlink()

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert not results["self-improvement"]["ok"]


def test_check5_passes_when_lessons_md_exists(tmp_path, monkeypatch):
    """Check 'self-improvement' passes when lessons.md exists."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    assert results["self-improvement"]["ok"]


# ---------------------------------------------------------------------------
# Daemon check (check 6): best-effort, never hard-fails
# ---------------------------------------------------------------------------

def test_check6_daemon_skips_when_no_port_file(tmp_path, monkeypatch):
    """Check 'daemon' must not hard-fail when no port file exists."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")

    from mkcrew.verify import verify_team
    results = _results_by_name(verify_team(project))
    # Daemon check present but does NOT count as a hard failure
    if "daemon" in results:
        # ok=True (skip) is fine; ok=False must not cause exit code 1 (tested in main tests)
        # The result must have ok and detail
        assert "ok" in results["daemon"]
        assert "detail" in results["daemon"]


# ---------------------------------------------------------------------------
# Hard check exit-code: main() returns 0 iff all hard checks pass
# ---------------------------------------------------------------------------

def test_main_returns_0_when_all_hard_checks_pass(tmp_path, monkeypatch):
    """main() should exit 0 when all 5 hard checks pass."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")
    monkeypatch.chdir(project)

    from mkcrew.verify import main
    code = main()
    assert code == 0


def test_main_returns_1_when_hard_check_fails(tmp_path, monkeypatch):
    """main() should exit 1 when at least one hard check fails (missing skill)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")
    monkeypatch.chdir(project)

    # Remove a skill to trigger failure
    (project / ".claude" / "skills" / "task-router" / "SKILL.md").unlink()

    from mkcrew.verify import main
    code = main()
    assert code == 1


def test_main_prints_pass_fail_per_check(tmp_path, monkeypatch, capsys):
    """main() must print PASS or FAIL for each check."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    project = _make_valid_project(tmp_path)

    import mkcrew.verify as verify_mod
    monkeypatch.setattr(verify_mod.shutil, "which", lambda name: "/fake/bin/mk-done")
    monkeypatch.chdir(project)

    from mkcrew.verify import main
    main()
    out = capsys.readouterr().out
    assert "PASS" in out or "FAIL" in out


# ---------------------------------------------------------------------------
# COMMANDS table in cli includes 'verify'
# ---------------------------------------------------------------------------

def test_cli_commands_has_verify():
    """cli.COMMANDS must include a 'verify' entry."""
    from mkcrew import cli
    assert "verify" in cli.COMMANDS, "'verify' must be in cli.COMMANDS"
