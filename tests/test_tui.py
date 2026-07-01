# Licensed under the Apache 2.0 License.
# Derived from awslabs/cli-agent-orchestrator, Apache-2.0.
"""Tests for the TUI module (P3-3) — light, no live Textual App.run().

TDD: these tests were written BEFORE the implementation and should fail until
the production code is added.
"""
import json, threading, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ---------------------------------------------------------------------------
# Minimal in-process HTTP stub for fetch_status / fetch_jobs tests
# ---------------------------------------------------------------------------

def _make_stub_handler(status_data: dict, jobs_data: dict):
    """Return an HTTP handler that serves /status and /jobs from dicts."""
    class StubHandler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/status":
                body = json.dumps(status_data).encode()
            elif self.path == "/jobs":
                body = json.dumps(jobs_data).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return StubHandler


def _start_stub(status_data, jobs_data):
    """Start a stub HTTP server; return (httpd, port)."""
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), _make_stub_handler(status_data, jobs_data)
    )
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


# ---------------------------------------------------------------------------
# 1. GET /status endpoint — daemon returns the expected keys
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_expected_keys(tmp_path, monkeypatch):
    """/status returns panicked, paused, pause_reason, agents list, jobs count."""
    import urllib.request
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.daemon import Mkd, _make_handler
    from http.server import ThreadingHTTPServer

    d = Mkd()
    d.register_agent("main", pane_id="%1")
    d.register_agent("worker", pane_id="%2")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(d))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    finally:
        httpd.shutdown()
        d.stop()

    assert "panicked" in data, "/status must have 'panicked' key"
    assert "paused" in data, "/status must have 'paused' key"
    assert "pause_reason" in data, "/status must have 'pause_reason' key"
    assert "agents" in data, "/status must have 'agents' key"
    assert "jobs" in data, "/status must have 'jobs' key"
    assert isinstance(data["agents"], list)
    assert isinstance(data["jobs"], int)
    assert data["panicked"] is False
    assert data["paused"] is False
    assert sorted(data["agents"]) == ["main", "worker"]


def test_status_endpoint_reflects_panic(tmp_path, monkeypatch):
    """/status.panicked is True after panic_now()."""
    import urllib.request
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.daemon import Mkd, _make_handler
    from http.server import ThreadingHTTPServer

    d = Mkd()
    d.panic_now("test")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(d))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    finally:
        httpd.shutdown()
        d.stop()

    assert data["panicked"] is True


def test_status_endpoint_reflects_paused(tmp_path, monkeypatch):
    """/status.paused is True and pause_reason matches after pause()."""
    import urllib.request
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.daemon import Mkd, _make_handler
    from http.server import ThreadingHTTPServer

    d = Mkd()
    d.pause("budget exceeded")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(d))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    finally:
        httpd.shutdown()
        d.stop()

    assert data["paused"] is True
    assert data["pause_reason"] == "budget exceeded"


def test_status_endpoint_jobs_count(tmp_path, monkeypatch):
    """/status.jobs reflects the number of jobs in the store."""
    import urllib.request
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew.daemon import Mkd, _make_handler
    from http.server import ThreadingHTTPServer

    class FakeMux:
        def send_line(self, *a): pass
        def capture(self, *a): return ""

    d = Mkd(mux=FakeMux())
    # Register two separate agents to allow two concurrent in-flight jobs
    d.register_agent("worker", pane_id="%1")
    d.register_agent("reviewer", pane_id="%2")
    # Open one job per agent (JobStore enforces one in-flight per agent)
    d.jobs.open(frm="main", to="worker", text="job1")
    d.jobs.open(frm="main", to="reviewer", text="job2")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(d))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    finally:
        httpd.shutdown()
        d.stop()

    assert data["jobs"] == 2


# ---------------------------------------------------------------------------
# 2. fetch_status / fetch_jobs — data layer (no App.run())
# ---------------------------------------------------------------------------

def test_fetch_status_parses_response():
    """fetch_status(port) returns a dict with expected keys from a live stub."""
    from mkcrew.tui import fetch_status

    stub_data = {
        "panicked": False,
        "paused": True,
        "pause_reason": "test reason",
        "agents": ["main", "worker"],
        "jobs": 3,
    }
    httpd, port = _start_stub(stub_data, {"jobs": []})
    try:
        result = fetch_status(port)
    finally:
        httpd.shutdown()

    assert result["panicked"] is False
    assert result["paused"] is True
    assert result["pause_reason"] == "test reason"
    assert result["agents"] == ["main", "worker"]
    assert result["jobs"] == 3


def test_fetch_jobs_parses_response():
    """fetch_jobs(port) returns the list of job dicts from a live stub."""
    from mkcrew.tui import fetch_jobs

    jobs_payload = {"jobs": [
        {"id": "job1", "from": "main", "to": "worker", "status": "DELIVERED", "retry_count": 0},
        {"id": "job2", "from": "worker", "to": "main", "status": "DONE", "retry_count": 1},
    ]}
    httpd, port = _start_stub({}, jobs_payload)
    try:
        result = fetch_jobs(port)
    finally:
        httpd.shutdown()

    assert len(result) == 2
    assert result[0]["id"] == "job1"
    assert result[1]["id"] == "job2"
    assert result[1]["retry_count"] == 1


def test_fetch_status_returns_down_sentinel_on_connection_error():
    """fetch_status returns a sentinel 'down' dict when the daemon is unreachable."""
    from mkcrew.tui import fetch_status

    # Port 1 is not in use on Windows; connection should be refused immediately.
    result = fetch_status(1)

    # Must not raise; must return a dict signalling daemon down
    assert isinstance(result, dict)
    assert result.get("down") is True


def test_fetch_jobs_returns_empty_list_on_connection_error():
    """fetch_jobs returns an empty list when the daemon is unreachable."""
    from mkcrew.tui import fetch_jobs

    result = fetch_jobs(1)

    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# 3. Module import + App instantiation (no App.run())
# ---------------------------------------------------------------------------

def test_tui_module_imports():
    """mkcrew.tui can be imported without error."""
    import mkcrew.tui  # noqa: F401


def test_mk_app_can_be_instantiated_without_running():
    """MkApp(port=9999) constructs without calling .run() or a live server."""
    from mkcrew.tui import MkApp
    app = MkApp(port=9999)
    assert app is not None


def test_tui_main_exits_when_no_port_file(tmp_path, monkeypatch, capsys):
    """tui.main() prints a helpful message and exits if the port file is absent."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from mkcrew import tui
    import sys

    exited = False
    code = None
    try:
        tui.main()
    except SystemExit as e:
        exited = True
        code = e.code

    assert exited, "main() must call sys.exit when port file is absent"
    out = capsys.readouterr().out
    assert "mkd not running" in out or "mk start" in out
