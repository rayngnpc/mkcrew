# tests/test_skills_install.py
"""Tests for skill installation and self-improvement scaffolding (P2-1)."""
import pytest
from pathlib import Path
from mkcrew.cli import (
    install_skills,
    scaffold_self_improvement,
    _SKILL_NAMES,
)

EXPECTED_SKILLS = {
    "task-router",
    "safe-agent-delegation",
    "senior-developer-loop",
    "team-self-improvement",
    "domain-playbooks",
}


# ---------------------------------------------------------------------------
# install_skills
# ---------------------------------------------------------------------------

def test_install_skills_creates_all_five(tmp_path):
    """install_skills writes all 5 SKILL.md files under <project>/.claude/skills/."""
    install_skills(tmp_path)
    for name in EXPECTED_SKILLS:
        p = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        assert p.exists(), f"missing skill: {name}"


def test_install_skills_returns_five_paths(tmp_path):
    """install_skills returns exactly 5 Path objects."""
    installed = install_skills(tmp_path)
    assert len(installed) == 5


def test_install_skills_paths_match_names(tmp_path):
    """Each returned path corresponds to one of the expected skill names."""
    installed = install_skills(tmp_path)
    names_installed = {p.parent.name for p in installed}
    assert names_installed == EXPECTED_SKILLS


def test_install_skills_is_idempotent(tmp_path):
    """Running install_skills twice does not raise and the files are still present."""
    install_skills(tmp_path)
    install_skills(tmp_path)
    for name in EXPECTED_SKILLS:
        p = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        assert p.exists()


# ---------------------------------------------------------------------------
# Frontmatter validity
# ---------------------------------------------------------------------------

def test_each_skill_has_valid_frontmatter(tmp_path):
    """Every installed SKILL.md must start with '---' and contain 'name:' and 'description:'."""
    install_skills(tmp_path)
    for name in EXPECTED_SKILLS:
        p = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{name}: does not start with frontmatter delimiter"
        # Extract between first and second '---'
        parts = text.split("---", 2)
        assert len(parts) >= 3, f"{name}: frontmatter not closed"
        frontmatter = parts[1]
        assert "name:" in frontmatter, f"{name}: missing 'name:' in frontmatter"
        assert "description:" in frontmatter, f"{name}: missing 'description:' in frontmatter"


# ---------------------------------------------------------------------------
# safe-agent-delegation invariants
# ---------------------------------------------------------------------------

def test_safe_agent_delegation_contains_planner_readonly_marker(tmp_path):
    """safe-agent-delegation SKILL.md must contain the planner read-only contract marker."""
    install_skills(tmp_path)
    p = tmp_path / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    # Check for the HTML comment marker that anchors the planner-readonly section
    assert "PLANNER-READONLY-CONTRACT" in text, (
        "safe-agent-delegation: missing PLANNER-READONLY-CONTRACT marker"
    )


def test_safe_agent_delegation_contains_named_tool_bans(tmp_path):
    """safe-agent-delegation must name at least some prohibited planner actions."""
    install_skills(tmp_path)
    p = tmp_path / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    # Must name file-write tools and destructive ops explicitly
    assert "Edit" in text, "safe-agent-delegation: 'Edit' tool ban missing"
    assert "Write" in text, "safe-agent-delegation: 'Write' tool ban missing"
    assert "destructive" in text.lower(), "safe-agent-delegation: missing destructive ops ban"


def test_safe_agent_delegation_contains_reply_discipline(tmp_path):
    """safe-agent-delegation must contain the mk-done reply-discipline section."""
    install_skills(tmp_path)
    p = tmp_path / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    # The reply discipline section anchored by HTML comment
    assert "REPLY-DISCIPLINE" in text, (
        "safe-agent-delegation: missing REPLY-DISCIPLINE marker"
    )
    # Must mention mk-done as the completion mechanism
    assert "mk-done" in text, (
        "safe-agent-delegation: missing mk-done in reply discipline"
    )
    # Must mention mk trace for checking before re-asking
    assert "mk trace" in text, (
        "safe-agent-delegation: missing 'mk trace' check in reply discipline"
    )


def test_safe_agent_delegation_mentions_planner_second_prompt(tmp_path):
    """The planner must not edit in the same session — second prompt gate must be mentioned."""
    install_skills(tmp_path)
    p = tmp_path / ".claude" / "skills" / "safe-agent-delegation" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    assert "SEPARATE" in text or "second prompt" in text.lower(), (
        "safe-agent-delegation: missing second-prompt gate for planner editing"
    )


# ---------------------------------------------------------------------------
# scaffold_self_improvement
# ---------------------------------------------------------------------------

def test_scaffold_creates_lessons_md(tmp_path):
    """scaffold_self_improvement creates .mkcrew-self-improvement/lessons.md."""
    scaffold_self_improvement(tmp_path)
    p = tmp_path / ".mkcrew-self-improvement" / "lessons.md"
    assert p.exists(), "lessons.md was not created"


def test_scaffold_creates_all_seed_files(tmp_path):
    """scaffold_self_improvement creates all three seed files."""
    scaffold_self_improvement(tmp_path)
    base = tmp_path / ".mkcrew-self-improvement"
    for name in ("lessons.md", "proposals.md", "README.md"):
        assert (base / name).exists(), f"{name} was not created"


def test_scaffold_returns_created_paths(tmp_path):
    """scaffold_self_improvement returns paths for files it created."""
    created = scaffold_self_improvement(tmp_path)
    assert len(created) == 3
    names = {p.name for p in created}
    assert names == {"lessons.md", "proposals.md", "README.md"}


def test_scaffold_does_not_overwrite_existing_lessons(tmp_path):
    """If lessons.md already exists, scaffold_self_improvement leaves it untouched."""
    base = tmp_path / ".mkcrew-self-improvement"
    base.mkdir(parents=True, exist_ok=True)
    existing = base / "lessons.md"
    existing.write_text("# MY EXISTING CONTENT\n", encoding="utf-8")

    scaffold_self_improvement(tmp_path)

    assert existing.read_text(encoding="utf-8") == "# MY EXISTING CONTENT\n"


def test_scaffold_is_idempotent(tmp_path):
    """Running scaffold_self_improvement twice does not raise."""
    scaffold_self_improvement(tmp_path)
    scaffold_self_improvement(tmp_path)
    assert (tmp_path / ".mkcrew-self-improvement" / "lessons.md").exists()


# ---------------------------------------------------------------------------
# _SKILL_NAMES constant
# ---------------------------------------------------------------------------

def test_skill_names_constant_has_all_five():
    """The _SKILL_NAMES list must contain exactly the 5 expected skill names."""
    assert set(_SKILL_NAMES) == EXPECTED_SKILLS
    assert len(_SKILL_NAMES) == 5
