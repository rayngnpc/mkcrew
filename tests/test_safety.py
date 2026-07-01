# Derived from awslabs/cli-agent-orchestrator, Apache-2.0
"""Tests for src/mkcrew/safety.py — pure-function tests.

Written BEFORE the implementation (TDD). These tests exercise:
  - budget_exceeded() pure function
  - detect_deadlock() pure function
"""
from mkcrew.safety import budget_exceeded, detect_deadlock


# ---------------------------------------------------------------------------
# budget_exceeded
# ---------------------------------------------------------------------------

def test_budget_not_exceeded_when_under_limits():
    assert budget_exceeded(jobs_completed=0, elapsed_minutes=0.0) is None


def test_budget_not_exceeded_just_under_job_cap():
    from mkcrew.safety import MAX_TEAM_JOBS
    assert budget_exceeded(jobs_completed=MAX_TEAM_JOBS - 1, elapsed_minutes=0.0) is None


def test_budget_exceeded_at_job_cap():
    from mkcrew.safety import MAX_TEAM_JOBS
    result = budget_exceeded(jobs_completed=MAX_TEAM_JOBS, elapsed_minutes=0.0)
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_budget_exceeded_above_job_cap():
    from mkcrew.safety import MAX_TEAM_JOBS
    result = budget_exceeded(jobs_completed=MAX_TEAM_JOBS + 100, elapsed_minutes=0.0)
    assert result is not None


def test_budget_not_exceeded_just_under_time_cap():
    from mkcrew.safety import MAX_TEAM_MINUTES
    assert budget_exceeded(jobs_completed=0, elapsed_minutes=MAX_TEAM_MINUTES - 0.1) is None


def test_budget_exceeded_at_time_cap():
    from mkcrew.safety import MAX_TEAM_MINUTES
    result = budget_exceeded(jobs_completed=0, elapsed_minutes=MAX_TEAM_MINUTES)
    assert result is not None
    assert isinstance(result, str)


def test_budget_exceeded_above_time_cap():
    from mkcrew.safety import MAX_TEAM_MINUTES
    result = budget_exceeded(jobs_completed=0, elapsed_minutes=MAX_TEAM_MINUTES + 60.0)
    assert result is not None


def test_budget_exceeded_reason_mentions_cap():
    """The reason string should hint at which cap was hit."""
    from mkcrew.safety import MAX_TEAM_JOBS, MAX_TEAM_MINUTES
    job_reason = budget_exceeded(jobs_completed=MAX_TEAM_JOBS, elapsed_minutes=0.0)
    assert job_reason is not None

    time_reason = budget_exceeded(jobs_completed=0, elapsed_minutes=MAX_TEAM_MINUTES)
    assert time_reason is not None
    # They must differ so callers can distinguish them
    assert job_reason != time_reason


# ---------------------------------------------------------------------------
# detect_deadlock
# ---------------------------------------------------------------------------

def test_detect_deadlock_empty_returns_none():
    assert detect_deadlock([]) is None


def test_detect_deadlock_single_edge_no_cycle():
    # A→B with no B→? — no cycle
    assert detect_deadlock([("job1", "A", "B")]) is None


def test_detect_deadlock_acyclic_chain():
    # A→B, B→C — no cycle
    result = detect_deadlock([("job1", "A", "B"), ("job2", "B", "C")])
    assert result is None


def test_detect_deadlock_two_node_cycle():
    # A→B, B→A — cycle; both job_ids returned
    result = detect_deadlock([("job1", "A", "B"), ("job2", "B", "A")])
    assert result is not None
    assert set(result) == {"job1", "job2"}


def test_detect_deadlock_three_node_cycle():
    # A→B, B→C, C→A — 3-cycle; all three job_ids returned
    edges = [("job1", "A", "B"), ("job2", "B", "C"), ("job3", "C", "A")]
    result = detect_deadlock(edges)
    assert result is not None
    assert set(result) == {"job1", "job2", "job3"}


def test_detect_deadlock_partial_cycle():
    # D→A, A→B, B→A — D is not in the cycle; only A and B's jobs returned
    edges = [
        ("job_d", "D", "A"),
        ("job1", "A", "B"),
        ("job2", "B", "A"),
    ]
    result = detect_deadlock(edges)
    assert result is not None
    # D→A is not part of the cycle; only A↔B cycle members
    assert set(result) == {"job1", "job2"}


def test_detect_deadlock_no_cycle_star():
    # Hub A sends to B, C, D — no cycle
    edges = [("job1", "A", "B"), ("job2", "A", "C"), ("job3", "A", "D")]
    assert detect_deadlock(edges) is None


def test_detect_deadlock_returns_list():
    result = detect_deadlock([("job1", "A", "B"), ("job2", "B", "A")])
    assert isinstance(result, list)
