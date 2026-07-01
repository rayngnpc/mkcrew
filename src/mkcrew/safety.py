# Derived from awslabs/cli-agent-orchestrator, Apache-2.0
"""Pure, testable safety-trigger logic for the MKCREW daemon.

All functions take plain inputs and return decisions; the daemon applies them.
No asyncio, no imports from daemon — this module must stay dependency-free.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Budget caps
# ---------------------------------------------------------------------------

MAX_TEAM_JOBS: int = 1000
MAX_TEAM_MINUTES: float = 240.0


def budget_exceeded(jobs_completed: int, elapsed_minutes: float) -> str | None:
    """Return a reason string if a budget cap is exceeded, otherwise None.

    Checks job count first, then elapsed time.  Returns None when under both
    caps so callers can use a simple ``if reason := budget_exceeded(...):`` pattern.
    """
    if jobs_completed >= MAX_TEAM_JOBS:
        return (
            f"budget exceeded: {jobs_completed} jobs completed "
            f"(cap: {MAX_TEAM_JOBS})"
        )
    if elapsed_minutes >= MAX_TEAM_MINUTES:
        return (
            f"budget exceeded: {elapsed_minutes:.1f} minutes elapsed "
            f"(cap: {MAX_TEAM_MINUTES})"
        )
    return None


# ---------------------------------------------------------------------------
# Deadlock detection
# ---------------------------------------------------------------------------

def detect_deadlock(inflight_edges: list[tuple[str, str, str]]) -> list[str] | None:
    """Find a cycle in the wait-for graph built from in-flight job edges.

    Parameters
    ----------
    inflight_edges:
        List of ``(job_id, from_agent, to_agent)`` for every in-flight job.
        Each entry means *from_agent* is waiting for *to_agent* to finish
        (i.e. there is a directed edge from_agent → to_agent in the wait-for graph).

    Returns
    -------
    list[str] | None
        The ``job_id``s that form the cycle, or ``None`` if no cycle exists.
        Only the jobs *inside* the cycle are returned (not jobs that merely
        lead into the cycle from outside).
    """
    if not inflight_edges:
        return None

    # Build: from_agent → job_id (the job it's waiting via)
    # and:   from_agent → to_agent (the wait-for edge)
    from_to_job: dict[str, str] = {}   # from_agent → job_id
    graph: dict[str, str] = {}          # from_agent → to_agent

    for job_id, from_agent, to_agent in inflight_edges:
        graph[from_agent] = to_agent
        from_to_job[from_agent] = job_id

    # Cycle detection via DFS: for each node try to find a back-edge.
    # We use Floyd/path-following since the graph is at most one out-edge per node.
    visited: set[str] = set()

    for start in list(graph.keys()):
        if start in visited:
            continue

        path: list[str] = []
        path_set: set[str] = set()
        current: str | None = start

        while current is not None:
            if current in path_set:
                # Found a cycle — isolate just the cycle portion
                cycle_start_idx = path.index(current)
                cycle_nodes = path[cycle_start_idx:]
                # Collect job_ids for edges *within* the cycle
                cycle_jobs: list[str] = []
                for node in cycle_nodes:
                    job_id = from_to_job.get(node)
                    if job_id is not None:
                        cycle_jobs.append(job_id)
                return cycle_jobs if cycle_jobs else None

            if current in visited:
                break  # already explored this path with no cycle found from it

            path.append(current)
            path_set.add(current)
            visited.add(current)
            current = graph.get(current)

    return None
