# Licensed under the Apache 2.0 License.
# Derived from awslabs/cli-agent-orchestrator, Apache-2.0.
"""MKCREW observability TUI — Textual app for the threaded MKCREW daemon.

Polls ``GET /status`` and ``GET /jobs`` every ~2 s and renders a status line
plus a DataTable of jobs.  Connection errors are swallowed so the TUI never
dies when mkd is temporarily unreachable.

Bindings:
    q — quit
    p — POST /panic to the daemon

Module-level helpers ``fetch_status(port)`` and ``fetch_jobs(port)`` are plain
urllib functions, fully testable without running the App.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from . import config

# ---------------------------------------------------------------------------
# Data-layer helpers (testable without running the App)
# ---------------------------------------------------------------------------

_DOWN_SENTINEL: dict[str, Any] = {"down": True}


def fetch_status(port: int) -> dict[str, Any]:
    """GET /status from the daemon.  Returns a 'down' sentinel on any error."""
    try:
        url = f"http://127.0.0.1:{port}/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return dict(_DOWN_SENTINEL)  # copy so callers can mutate safely


def fetch_jobs(port: int) -> list[dict[str, Any]]:
    """GET /jobs from the daemon.  Returns an empty list on any error."""
    try:
        url = f"http://127.0.0.1:{port}/jobs"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get("jobs", [])
    except Exception:
        return []


def _post_panic(port: int) -> None:
    """POST /panic to the daemon. Errors are silently swallowed."""
    try:
        url = f"http://127.0.0.1:{port}/panic"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Textual App
# ---------------------------------------------------------------------------

class MkApp(App):
    """Minimal observability TUI for the MKCREW daemon."""

    TITLE = "MKCREW"
    SUB_TITLE = "agent dashboard"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "panic", "Panic"),
    ]

    def __init__(self, port: int) -> None:
        super().__init__()
        self.port = port
        self._status_line: Static = Static("daemon: connecting…", id="status-line")
        self._jobs_table: DataTable = DataTable()

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._status_line
        self._jobs_table.add_columns("ID", "from→to", "status", "retries")
        yield self._jobs_table
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(2.0, self._refresh)

    def _refresh(self) -> None:
        """Poll /status and /jobs; swallow all errors so the TUI never dies."""
        status = fetch_status(self.port)
        if status.get("down"):
            self._status_line.update("daemon: down  (press q to quit)")
        else:
            panicked = status.get("panicked", False)
            paused = status.get("paused", False)
            reason = status.get("pause_reason", "")
            agents = status.get("agents", [])
            jobs_count = status.get("jobs", 0)
            parts = [
                f"agents: {', '.join(agents) or '—'}",
                f"jobs: {jobs_count}",
            ]
            if panicked:
                parts.insert(0, "[bold red]PANICKED[/bold red]")
            elif paused:
                parts.insert(0, f"[yellow]PAUSED[/yellow] ({reason})")
            else:
                parts.insert(0, "[green]OK[/green]")
            self._status_line.update("  ".join(parts))

        jobs = fetch_jobs(self.port)
        self._jobs_table.clear()
        for j in jobs:
            frm_to = f"{j.get('from', '?')}→{j.get('to', '?')}"
            self._jobs_table.add_row(
                j.get("id", ""),
                frm_to,
                j.get("status", ""),
                str(j.get("retry_count", 0)),
            )

    def action_panic(self) -> None:
        """`p` keypress → POST /panic."""
        _post_panic(self.port)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Read the port file; launch MkApp or exit with a helpful message."""
    port_path = config.port_file()
    if not port_path.exists():
        print("mkd not running (run `mk start` first)")
        sys.exit(1)
    try:
        port = int(port_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        print("mkd not running (run `mk start` first)")
        sys.exit(1)
    MkApp(port=port).run()


if __name__ == "__main__":
    main()
