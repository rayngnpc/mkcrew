# Derived from awslabs/cli-agent-orchestrator, Apache-2.0
"""Per-instance panic state controller (threading variant).

Tiny, dependency-free.  Owns one threading.Event; callers use trigger(),
is_panicked, clear(), and wait(timeout) to coordinate panic across threads.
"""
from __future__ import annotations

import threading


class PanicController:
    """Thread-safe panic flag backed by a threading.Event."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        """Set the panic flag.  Blocked wait() calls return immediately."""
        self._event.set()

    def clear(self) -> None:
        """Reset the panic flag (used by a resume flow)."""
        self._event.clear()

    @property
    def is_panicked(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until triggered or timeout expires.

        Returns True if the event was set, False if the timeout elapsed.
        Mirrors threading.Event.wait() semantics exactly.
        """
        return self._event.wait(timeout=timeout)
