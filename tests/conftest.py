# tests/conftest.py
import pytest


@pytest.fixture(autouse=True)
def _strip_cockpit_pane_env(monkeypatch):
    """Tests must never inherit a cockpit pane's pinning env (LIVE INCIDENT, 2026-07-10).

    launch.sh exports MK_RUNTIME_ROOT (and MK_ACTOR) into every agent pane so account wrappers
    that rewrite HOME/XDG can't move where hooks find the daemon. config.runtime_root() honors
    MK_RUNTIME_ROOT FIRST -- by design, ahead of XDG_STATE_HOME. Consequence: a test suite run
    FROM INSIDE a cockpit pane silently bypassed every test's monkeypatch.setenv("XDG_STATE_HOME")
    isolation, and a daemon-serving test wrote its port/pid into the LIVE runtime dir
    (daemon.py serve() -> config.port_file()/pid_file()), hijacking mk clients to a dying test
    server ("mkd not reachable / 404" on the next mk ask). Stripping the pane env here restores
    the isolation every test already asked for."""
    monkeypatch.delenv("MK_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("MK_ACTOR", raising=False)
