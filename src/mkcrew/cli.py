# src/mkcrew/cli.py
import os, sys, time, subprocess, json, urllib.request, urllib.error, shutil
from collections import Counter
from pathlib import Path
from . import config, agent, teamconfig, prompts, verify, layouts, sessions, templates
from .psmux import PsmuxBackend

SESSION = "mkcrew"


def _require_port() -> int:
    """Return the daemon port as an int, or sys.exit with a friendly message."""
    try:
        text = config.port_file().read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("port file is empty")
        return int(text)
    except FileNotFoundError as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")
    except (ValueError, OSError) as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")


def _post(path, payload):
    port = _require_port()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionRefusedError) as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")


def _get(path):
    port = _require_port()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
            data = json.loads(body) if body else {"error": str(e)}
        except (json.JSONDecodeError, Exception):
            data = {"error": str(e)}
        return e.code, data
    except (urllib.error.URLError, ConnectionRefusedError) as exc:
        sys.exit(f"error: mkd not reachable — run `mk start` first ({exc})")

def _project_dir():
    return Path.cwd()


def _arg_value(argv, flag):
    """Return the value following `flag` in argv (e.g. `--name Testing` -> 'Testing'), or None when
    the flag is absent or trailing."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


# ---------------------------------------------------------------------------
# FIX #3: one cockpit per directory — a per-workspace pid lock + os-level liveness check
# ---------------------------------------------------------------------------

def _cockpit_lock(project) -> Path:
    """Per-workspace liveness marker: <project>/.mkcrew/cockpit.lock holds the live daemon's pid.
    Written by `mk start`, removed on clean shutdown — so `mk add` can refuse a directory whose own
    cockpit is still running (clobbering a live cockpit's config would break its running agents)."""
    return Path(project) / ".mkcrew" / "cockpit.lock"


def _pid_alive(pid) -> bool:
    """os-level liveness check for a pid. Windows-safe: `os.kill(pid, 0)` TERMINATES the process on
    Windows, so probe with OpenProcess via ctypes instead (access-denied still means 'alive')."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED   # exists but not queryable -> alive
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE                   # a finished pid reports its exit code
            return True
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False


def _cockpit_live_at(project) -> bool:
    """True if the running cockpit is the one at <project>.

    Two signals, either suffices:
      (1) the live-cockpit project marker (cockpit_project.txt, written by every `mk start`,
          removed by `mk kill`) names THIS project.  This is the reliable check: the pid lock
          below records the DAEMON's pid, and the daemon can die/crash while the psmux session
          (the actual cockpit the user is typing in) lives on — measured live: a cockpit whose
          mkd had died reported 'not live' and let `mk add` clobber its own directory (the
          duplicate-tab bug).  Callers (cmd_add) have already verified the psmux session exists,
          and cmd_start rewrites the marker on every start, so marker==project + session alive
          means the live cockpit IS this directory.
      (2) the per-dir cockpit.lock pid is still running (kept as a fallback for a clobbered
          marker).  A missing/garbage/dead-pid lock alone counts as NOT live (safe to clobber)."""
    try:
        live = config.cockpit_project_file().read_text(encoding="utf-8").strip()
    except OSError:
        live = ""
    if live:
        try:
            if Path(live).resolve() == Path(project).resolve():
                return True
        except (OSError, ValueError):
            pass
    try:
        pid = int(_cockpit_lock(project).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _pid_alive(pid)


def _write_cockpit_lock(project, pid) -> None:
    """Record the live daemon's pid in <project>/.mkcrew/cockpit.lock (best-effort)."""
    if pid is None:
        return
    try:
        lock = _cockpit_lock(project)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _clear_live_cockpit_lock() -> None:
    """Remove the live cockpit's per-workspace lock on clean shutdown (the project is read from the
    cockpit-project marker `mk start` wrote, so `mk kill` from anywhere clears the right lock)."""
    try:
        live = config.cockpit_project_file().read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        live = ""
    if live:
        try:
            _cockpit_lock(Path(live)).unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Skills installation helpers
# ---------------------------------------------------------------------------

_SKILLS_SRC = Path(__file__).parent / "skills"
_SKILL_NAMES = [
    "task-router",
    "mkcrew-worker",
    "safe-agent-delegation",
    "senior-developer-loop",
    "team-self-improvement",
    "domain-playbooks",
]


def install_skills(project_dir) -> list[Path]:
    """Copy bundled SKILL.md files into <project>/.claude/skills/<name>/SKILL.md.

    Returns the list of destination paths written.
    """
    project_dir = Path(project_dir)
    installed = []
    for name in _SKILL_NAMES:
        src = _SKILLS_SRC / name / "SKILL.md"
        dst_dir = project_dir / ".claude" / "skills" / name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / "SKILL.md"
        shutil.copy2(src, dst)
        installed.append(dst)
    return installed


def scaffold_self_improvement(project_dir) -> list[Path]:
    """Create .mkcrew-self-improvement/ scaffold with seed files.

    Returns the list of paths created (skips files that already exist).
    """
    project_dir = Path(project_dir)
    base = project_dir / ".mkcrew-self-improvement"
    base.mkdir(parents=True, exist_ok=True)
    created = []

    seeds = {
        "lessons.md": "# MKCREW Self-Improvement Lessons\n\n"
                      "<!-- Append accepted lessons here using the Lesson Format in team-self-improvement skill. -->\n",
        "proposals.md": "# MKCREW Self-Improvement Proposals\n\n"
                        "<!-- Draft proposed changes here before critique. Move to lessons.md after acceptance. -->\n",
        "README.md": "# .mkcrew-self-improvement\n\n"
                     "Durable lessons and proposals for the MKCREW team.\n\n"
                     "- `lessons.md` — accepted lessons (append-only)\n"
                     "- `proposals.md` — proposed changes awaiting review\n\n"
                     "Managed by the `team-self-improvement` skill.\n",
    }
    for name, content in seeds.items():
        p = base / name
        if not p.exists():
            p.write_text(content, encoding="utf-8")
            created.append(p)
    return created


def _prompt_str(label, default):
    try:
        val = input(f"{label} [{default}]: ").strip()
    except EOFError:
        val = ""
    return val or default


def _prompt_int(label, default):
    try:
        return int(_prompt_str(label, str(default)))
    except ValueError:
        return default


def _parse_init_flags(argv):
    """Return (agents:int|None, layout:str|None, providers:list|None, reconfigure:bool)."""
    agents = layout = providers = None
    reconfigure = "--reconfigure" in argv
    for i, a in enumerate(argv):
        if a == "--agents" and i + 1 < len(argv):
            try:
                agents = int(argv[i + 1])
            except ValueError:
                sys.exit("error: --agents must be a number")
        elif a == "--layout" and i + 1 < len(argv):
            layout = argv[i + 1]
        elif a == "--providers" and i + 1 < len(argv):
            providers = [s.strip() for s in argv[i + 1].split(",")]
    return agents, layout, providers, reconfigure


def cmd_init(argv):
    project = _project_dir()
    sp = agent.ensure_project_hook(project)
    print("project hook ensured:", sp)
    cfg = teamconfig._config_path(project)
    agents_n, layout, providers, reconfigure = _parse_init_flags(argv)
    has_flags = any(x is not None for x in (agents_n, layout, providers))
    if has_flags or reconfigure or not cfg.exists():
        if agents_n is None:
            agents_n = _prompt_int("How many agents?", 9)
        if layout is None:
            layout = _prompt_str("Layout (hub/tiled)?", "hub")
        if layout not in layouts.LAYOUTS:
            sys.exit(f"error: unknown layout '{layout}' "
                     f"(available: {', '.join(sorted(layouts.LAYOUTS))})")
        if providers is None:
            raw = _prompt_str("Provider per agent, comma-separated (blank = all claude)?", "")
            providers = [s.strip() for s in raw.split(",")] if raw.strip() else None
        team = teamconfig.build_team(agents_n, providers)
        teamconfig.write_team(project, team, layout)
        print(f"wrote team config: {cfg} ({len(team)} agents, layout={layout})")
    else:
        print(f"team config already present: {cfg} "
              f"(use --agents/--layout/--providers or `mk layout` to change)")
    installed = install_skills(project)
    for p in installed:
        print("skill installed:", p)
    created = scaffold_self_improvement(project)
    for p in created:
        print("self-improvement file created:", p)

def _session_exists(mux: PsmuxBackend, session: str) -> bool:
    """Return True if psmux has a session named *session*."""
    result = mux._run("has-session", "-t", session)
    return result.returncode == 0


def _clear_stale_daemon_files() -> None:
    """Remove any leftover daemon port/pid files BEFORE spawning a fresh daemon, so the
    port-file wait in cmd_start blocks until the NEW daemon binds and writes its port.  A
    stale port file used to satisfy that wait instantly, making the first /register POST hit
    a dead port (WinError 10061) and abort `mk start` before the cockpit was built."""
    config.port_file().unlink(missing_ok=True)
    config.pid_file().unlink(missing_ok=True)


def cmd_start(argv):
    project = _project_dir()
    agent.ensure_project_hook(project)
    agent.ensure_opencode_plugin(project)     # opencode teammates pull via their in-process plugin (same /next)
    agent.ensure_project_claude_md(project)   # so every claude agent knows the crew model w/o discovery
    agent.ensure_project_agents_md(project)    # AGENTS.md: same briefing for a codex/agy/opencode main/worker

    # 3c: guard against an already-running mkcrew session
    mux = PsmuxBackend()
    if _session_exists(mux, SESSION):
        sys.exit(f"error: a '{SESSION}' session is already running — run `mk kill` first")

    # 3b: spawn daemon with stdout/stderr redirected to a log file
    _clear_stale_daemon_files()   # drop any stale port/pid so the wait below blocks for the NEW daemon
    log_path = config.runtime_root() / "mkd.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")
    from . import frozen
    proc = subprocess.Popen(
        frozen.daemon_cmd(),                         # `python -m mkcrew.daemon` (dev) / `MKCREW.exe mkd` (frozen)
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "MK_PROJECT": str(project)},   # so the daemon logs to THIS project's event DB
        # DETACHED_PROCESS: the daemon gets NO console at all, so closing the cockpit's launch console
        # can no longer kill it (the old CREATE_NO_WINDOW child still died with its parent console --
        # the 'daemon dies with its console' incident). CREATE_NEW_PROCESS_GROUP shields it from the
        # console's Ctrl-C/Ctrl-Break broadcasts. Linux twin: start_new_session=True (no SIGHUP).
        creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)),
    )

    # 3a: wait for port file; bail out if daemon died or file never appeared
    for _ in range(50):
        if config.port_file().exists():
            break
        time.sleep(0.1)
    if not config.port_file().exists() or proc.poll() is not None:
        sys.exit(f"error: mkd failed to start — see {log_path}")

    team = teamconfig.load_team(project)
    layout_name = teamconfig.load_layout(project)
    mode = teamconfig.load_mode(project)
    if mode != "standard":
        try:
            _post("/mode", {"mode": mode})    # daemon watchdog patience follows the posture from boot
        except (SystemExit, Exception):
            pass                              # best-effort: the lead bootstrap carries the clause anyway
    # FIX #4: workspace name = identity. `mk start --name X` sets+persists it; otherwise use the
    # persisted name (None -> the lead gets the generic 'a MKCREW team' wording).
    name = _arg_value(argv, "--name")
    if name:
        teamconfig.set_name(project, name)
    else:
        name = teamconfig.load_name(project)
    # codex teammates pull via their own Stop hook (same /next); bake MK_ACTOR per codex role — codex
    # doesn't pass the pane env to hooks (one codex/project: they share .codex/hooks.json).
    for _r in [a["role"] for a in team if a.get("provider", "claude") == "codex"]:
        agent.ensure_codex_hook(project, _r)
    if "--fresh" in argv:
        sessions.clear(project)
    fresh_roles = set()
    # Count providers up front so a "continue-last" provider (codex/opencode/agy: `resume --last` /
    # `--continue` with NO per-role id) shared by 2+ agents is NOT co-resumed -- they would all reopen
    # the SAME most-recent session (shared/corrupted history + cross-talk). resume_flag launches such a
    # shared provider FRESH instead; a SOLE agent still resumes. claude/gemini resume by a per-role
    # UUID, so two of them never collide and are unaffected.
    prov_counts = Counter(a.get("provider", "claude") for a in team)
    for a in team:
        # Mint a per-(project, role) id for EVERY provider so a non-claude main resumes its prior
        # session on restart the way claude already does (sessions.ensure is provider-agnostic).
        # Resume gating is provider-aware + collision-aware via sessions.resume_flag:
        #   - claude/gemini PRE-SET a per-role id (transcript / --session-id) -> never collide;
        #   - a SOLE codex/opencode/agy reopens the last project session ("launched before" is enough);
        #   - 2+ of one continue-last provider stay FRESH so they don't share one 'last' session.
        # A first launch (is_new) or an unknown/custom provider stays fresh -> gets the bootstrap.
        sid, is_new = sessions.ensure(project, a["role"])
        prov = a.get("provider", "claude")
        # Resolve the account wrapper NOW (a bare built-in provider -> the default account) so the resume
        # check reads the SAME claude config dir the pane will run under -- else a session created under
        # one account is wrongly --resumed under another and the pane crash-loops ("No conversation found").
        a["bin"] = a.get("bin") or config.default_account_bin(prov)
        a["_session_id"] = sid
        a["_resume"] = (not is_new) and sessions.resume_flag(
            project, sid, prov, shared_provider=prov_counts[prov] > 1, bin=a.get("bin"))
        if not a["_resume"]:
            fresh_roles.add(a["role"])
            if not is_new and prov == "claude":
                # A fresh RE-launch must never reuse the old uuid: claude registers an id at
                # --session-id creation (before any transcript), so recreating with it dies
                # "Session ID already in use" and the pane crash-loops (live incident: a spaced
                # project path made the resume check miss a REAL saved session). The old id has
                # no value on a fresh launch -- rotate to a brand-new one.
                a["_session_id"] = sessions.rotate(project, a["role"])
    mux.kill_server()
    role_provider = {a["role"]: a.get("provider", "claude") for a in team}
    panes = layouts.get(layout_name)(
        mux, team, project,
        lambda role, pid: _post("/register", {"agent": role, "pane_id": pid,
                                              "provider": role_provider.get(role, "claude")}),
        SESSION,
    )
    layouts.apply_chrome(mux, name)                          # workspace-name badge in the top bar (all windows)
    if name and hasattr(mux, "rename_window"):               # + label the primary window tab with the workspace name
        mux.rename_window(f"{SESSION}:0", name)

    # 3d: poll each pane for the folder-trust prompt; send Enter when seen (best-effort)
    _TRUST_KEYWORDS = ("trust", "Do you want to proceed", "Allow", "yes/no")
    _POLL_SECS = 8     # claude shows folder-trust within ~2s; don't black-screen 30s/pane
    _POLL_INTERVAL = 0.5
    for pid in panes.values():
        deadline = time.monotonic() + _POLL_SECS
        sent = False
        while time.monotonic() < deadline:
            try:
                content = mux.capture(pid)
                if any(kw.lower() in content.lower() for kw in _TRUST_KEYWORDS):
                    mux.send_enter(pid)
                    sent = True
                    break
            except Exception:
                pass
            time.sleep(_POLL_INTERVAL)
        if not sent:
            try:
                mux.send_enter(pid)  # fallback: send Enter after timeout
            except Exception:
                pass

    # Give the REPLs a moment to finish booting before injecting prompts
    time.sleep(3)

    # Inject role bootstrap prompts (best-effort). A FRESH lead gets the full prompt; a RESUMED
    # lead gets ONLY an update if the team changed since last run (else nothing -- clean resume).
    changes = teamconfig.team_changes(project, team)
    if "main" in panes:
        # frozen: agents call `mk` via the shim on their PATH (mk.exe doesn't exist next to MKCREW.exe)
        mk_exe = "mk" if frozen.is_frozen() else str(Path(sys.executable).parent / "mk.exe").replace("\\", "/")
        if "main" in fresh_roles:
            # provider-aware: a non-claude main skips the claude-only task-router/senior-dev skills line
            mux.send_line(panes["main"], prompts.lead_prompt(mk_exe, team, mode,
                                                             provider=role_provider.get("main", "claude"),
                                                             name=name))
        elif changes:
            mux.send_line(panes["main"], prompts.team_update_prompt(mk_exe, team, changes))
    if "planner" in panes and "planner" in fresh_roles:
        mux.send_line(panes["planner"], prompts.PLANNER_PROMPT)
    config.cockpit_project_file().write_text(str(project), encoding="utf-8")   # who owns the live cockpit
    _write_cockpit_lock(project, getattr(proc, "pid", None))                   # FIX #3: per-workspace liveness lock
    if "--no-attach" in argv:                  # Studio / scripts drive their own attach
        print(f"started session '{SESSION}'. Attach with:  psmux attach -t {SESSION}")
        return
    print(f"started session '{SESSION}' - attaching now (detach to return to your shell)...")
    cmd_attach(argv)                           # land the user IN the cockpit, no second command

def cmd_attach(argv):
    _set_cockpit_font()
    subprocess.run(PsmuxBackend().attach_command(SESSION))

def _set_cockpit_font():
    """Best-effort: size THIS console's font to the cockpit's densest window so agent panes stay
    readable — more agents / smaller screen -> smaller font (more cells per pane). No-op on failure;
    the launch .cmd's fixed shrink remains as the fallback."""
    try:
        from . import console
        forced = os.environ.get("MK_FONT", "")          # MK_FONT=<px> forces a size (overrides adaptive)
        if forced.isdigit():
            console.set_console_font(int(forced)); return
        project = _project_dir()
        panes = layouts.panes_per_window(teamconfig.load_layout(project),
                                         len(teamconfig.load_team(project)))
        console.set_console_font(console.adaptive_font_height(panes))
    except Exception:
        pass

def _pid_is_mkd(pid: str) -> bool:
    """True only if PID is alive AND its command line is our daemon.

    Guards against force-killing a STALE pid file whose number the OS has
    reused for an unrelated process (e.g. explorer.exe). We must NEVER
    taskkill an unverified pid.
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False
    cl = r.stdout or ""
    return "mkcrew.daemon" in cl or cl.rstrip().lower().endswith("mkcrew.exe\" mkd") or cl.rstrip().endswith(" mkd")


def _kill_daemon() -> None:
    """Terminate the mkd process via its pid file and remove runtime files.

    Only kills the pid if it is VERIFIED to be our daemon. A stale pid whose
    number the OS reused for another process must never be force-killed.
    """
    pid_path = config.pid_file()
    port_path = config.port_file()
    try:
        pid = pid_path.read_text(encoding="utf-8").strip()
        if pid and _pid_is_mkd(pid):
            subprocess.run(["taskkill", "/PID", pid, "/F"],
                           capture_output=True, check=False)
        elif pid and _pid_alive(pid):
            # alive, but its cmdline isn't our daemon -> the OS reused the pid; never kill a stranger.
            print(f"warning: pid {pid} in mkd.pid is alive but is NOT our daemon "
                  f"(reused pid) -- refusing to kill it")
        elif pid:
            # dead pid: the daemon already stopped and left a stale pid file (cleared below). Benign.
            print(f"note: cleared a stale mkd.pid (pid {pid} was already stopped)")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"warning: could not kill mkd pid: {exc}")
    for f in (pid_path, port_path):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


def cmd_kill(argv):
    PsmuxBackend().kill_server()
    _kill_daemon()
    _clear_live_cockpit_lock()                          # FIX #3: drop the per-workspace lock (before the marker)
    config.cockpit_project_file().unlink(missing_ok=True)
    print("killed psmux session and mkd daemon.")

def cmd_panic(argv):
    # Layer 1: try POST /panic to the daemon (short timeout; best-effort)
    try:
        port_text = config.port_file().read_text(encoding="utf-8").strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port_text}/panic",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # mkd unreachable — continue anyway

    # Layer 2: kill psmux panes
    PsmuxBackend().kill_server()

    # Layer 3: terminate mkd via pid file
    _kill_daemon()
    _clear_live_cockpit_lock()                          # FIX #3: drop the per-workspace lock

    print("PANIC: team halted.")

def cmd_pend(argv):
    """Print a table of all jobs known to the running daemon."""
    _, data = _get("/jobs")
    jobs = data.get("jobs", [])
    if not jobs:
        print("no jobs")
        return
    fmt = "{:<10}  {:<12}  {:<12}  {:<12}  {:>7}"
    print(fmt.format("ID", "FROM", "TO", "STATUS", "RETRIES"))
    print("-" * 58)
    for j in jobs:
        print(fmt.format(j["id"], j["from"], j["to"], j["status"], j["retry_count"]))


def cmd_stats(argv):
    """`mk stats`: per-worker delivery metrics folded from the durable event log -- jobs, done vs
    failed, median duration, late results, and thin-evidence flags (architect's evidence gate).
    The crew's measurement loop: you cannot tune a mode you cannot see. Offline reader (this
    project's events.db); needs no running daemon."""
    from .eventlog import EventLog
    log = EventLog(config.event_db())
    try:
        events = list(log.replay())
    finally:
        log.close()
    jobs = {}
    for e in events:
        if e.type == "job.created":
            jobs[e.job_id] = {"to": e.data.get("to", "?"), "t0": e.ts, "t1": None,
                              "status": "OPEN", "thin": False, "late": False}
            continue
        j = jobs.get(e.job_id)
        if j is None:
            continue
        if e.type == "job.done":
            j["t1"], j["status"] = e.ts, e.data.get("status", "DONE")
        elif e.type == "job.late_done":
            j["late"] = True
        elif e.type == "job.event" and e.data.get("label") == "thin_evidence":
            j["thin"] = True
    if not jobs:
        print("no jobs in this project's ledger yet")
        return
    per = {}
    for j in jobs.values():
        w = per.setdefault(j["to"], {"n": 0, "done": 0, "inc": 0, "thin": 0, "late": 0, "durs": []})
        w["n"] += 1
        if j["status"] == "DONE":
            w["done"] += 1
            if j["t1"] is not None:
                w["durs"].append(j["t1"] - j["t0"])
        elif j["status"] in ("INCOMPLETE", "PANICKED"):
            w["inc"] += 1
        w["thin"] += j["thin"]
        w["late"] += j["late"]
    import statistics
    fmt = "{:<10}  {:>5}  {:>5}  {:>6}  {:>8}  {:>5}  {:>5}"
    print(fmt.format("WORKER", "JOBS", "DONE", "FAILED", "MED-TIME", "LATE", "THIN"))
    print("-" * 58)
    for w in sorted(per):
        s = per[w]
        med = f"{statistics.median(s['durs']) / 60:.1f}m" if s["durs"] else "-"
        print(fmt.format(w, s["n"], s["done"], s["inc"], med, s["late"], s["thin"]))
    open_n = sum(1 for j in jobs.values() if j["status"] == "OPEN")
    print(f"\n{len(jobs)} job(s) total, {open_n} open.  THIN = completed without the "
          "evidence-pack shape (architect gate); LATE = finished after the ask timed out.")


def cmd_trace(argv):
    """Show full detail and event log for a single job."""
    if not argv:
        print("usage: mk trace <job_id>")
        return
    job_id = argv[0]
    status, data = _get(f"/jobs/{job_id}")
    if status == 404 or "error" in data:
        print(f"error: {data.get('error', 'unknown error')} ({job_id})")
        return
    import datetime
    print(f"id:       {data['id']}")
    print(f"from:     {data['from']}")
    print(f"to:       {data['to']}")
    print(f"status:   {data['status']}")
    print(f"retries:  {data['retry_count']}")
    if data.get("reply"):
        print(f"reply:    {data['reply']}")
    events = data.get("events", [])
    if events:
        print("events:")
        for ev in events:
            ts = datetime.datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S.%f")[:-3]
            print(f"  {ts}  {ev['label']}")


def cmd_repair(argv):
    """Force resubmission of an in-flight job.  Usage: mk repair resubmit <job_id>"""
    if len(argv) < 2 or argv[0] != "resubmit":
        print("usage: mk repair resubmit <job_id>")
        return
    job_id = argv[1]
    result = _post("/repair", {"job_id": job_id})
    if result.get("ok"):
        print(f"ok: job {job_id} resubmitted")
    else:
        print(f"failed: {result.get('error', 'unknown error')}")


def cmd_verify(argv):
    """Run clone invariant checks (wraps verify.main)."""
    raise SystemExit(verify.main())


def cmd_resume(argv):
    """Clear the paused state so /ask accepts new jobs again."""
    result = _post("/resume", {})
    if result.get("ok"):
        print("resumed: daemon is accepting new jobs")
    else:
        print(f"error: {result}")


def cmd_tui(argv):
    """Launch the observability TUI (requires a running mkd)."""
    from . import tui
    tui.main()


_SINGLE_WINDOW_LAYOUTS = {"tiled", "main-vertical", "main-horizontal",
                          "even-horizontal", "even-vertical"}  # all single-window; hub is structural


def _best_preset(panes: int, cols: int, rows: int) -> str:
    """Best single-window preset for `panes` panes in a cols x rows CELL grid. Terminal cells are
    ~2x taller than wide, so pixel aspect ~= cols/(2*rows). Thresholds are calibration knobs."""
    if panes <= 1:
        return "tiled"
    ratio = cols / (rows * 2) if rows else 1.0          # >1 wider than tall, <1 taller (in pixels)
    if panes == 2:
        return "even-horizontal" if ratio >= 1 else "even-vertical"   # split the longer axis
    if panes <= 5:
        if ratio >= 1.8:   return "even-horizontal"     # clearly wide -> one row
        if ratio <= 0.55:  return "even-vertical"       # clearly tall -> one column
        return "tiled"
    return "tiled"                                       # many panes -> grid (fits the window)


def _layout_auto(project) -> None:
    """Measure the running cockpit window + pane count and apply the best-fitting layout LIVE."""
    from .psmux import PsmuxBackend
    mux = PsmuxBackend()
    if not _session_exists(mux, "mkcrew"):
        print("auto needs a running cockpit (it measures your screen) — `mk start` + attach first")
        return
    out = mux._run("display-message", "-t", "mkcrew", "-p", "#{window_width} #{window_height}").stdout.strip()
    try:
        cols, rows = (int(x) for x in out.split()[:2])
    except (ValueError, IndexError):
        print(f"auto: couldn't read the window size ({out!r}) — try `mk layout tiled`")
        return
    panes = len(teamconfig.load_team(project)) + 1       # agents + the core pane
    preset = _best_preset(panes, cols, rows)
    teamconfig.set_layout(project, preset)
    try:
        mux.select_layout("mkcrew", preset)
    except Exception:
        pass
    print(f"auto -> '{preset}' ({panes} panes on {cols}x{rows} cells) — applied live")


def cmd_layout(argv):
    """`mk layout` lists current+available; `mk layout <name>` sets it. The single-window layouts
    (tiled/main-vertical/main-horizontal) apply LIVE to a running cockpit via select-layout; any
    switch involving hub is structural, so it applies on the next `mk start`."""
    available = ", ".join(sorted(layouts.LAYOUTS)) + ", auto"
    if not argv:
        print(f"layout: {teamconfig.load_layout(_project_dir())}")
        print(f"available: {available}  (auto = pick the best fit for your screen + team)")
        return
    name = argv[0]
    if name == "auto":
        _layout_auto(_project_dir())
        return
    if name not in layouts.LAYOUTS:
        print(f"error: unknown layout '{name}' (available: {available})")
        return
    current = teamconfig.load_layout(_project_dir())
    teamconfig.set_layout(_project_dir(), name)
    # Live-flip only works BETWEEN single-window layouts on a running session (hub is structural).
    if name in _SINGLE_WINDOW_LAYOUTS and current in _SINGLE_WINDOW_LAYOUTS:
        from .psmux import PsmuxBackend
        mux = PsmuxBackend()
        if _session_exists(mux, "mkcrew"):
            try:
                mux.select_layout("mkcrew", name)
                print(f"layout -> '{name}' applied live")
                return
            except Exception:
                pass  # fall through to the persisted-for-next-start message
    print(f"layout set to '{name}' — run `mk relayout {name}` to apply it to a running cockpit "
          f"(rebuilds, sessions resume), or it takes effect on next `mk start`")


def cmd_mode(argv):
    """`mk mode [<mode>]`: show or switch the crew's working posture (standard / fast / thorough /
    plan-first / architect). Persists to team.config; a RUNNING cockpit switches LIVE — the daemon's watchdog
    patience follows immediately and the lead gets a one-line posture update in its pane."""
    from . import prompts
    valid = ["standard"] + sorted(prompts._MODE_CLAUSE)
    project = _project_dir()
    if not argv:
        print(f"core mode: {teamconfig.load_mode(project)}   (available: {', '.join(valid)})")
        return
    m = argv[0]
    if m not in valid:
        sys.exit(f"error: unknown mode '{m}' — pick one of: {', '.join(valid)}")
    teamconfig.set_mode(project, m)
    note = "applies on next `mk start`"
    if config.port_file().exists():
        try:
            _post("/mode", {"mode": m})
            note = "live: daemon updated, lead notified"
        except (SystemExit, Exception):
            pass                                  # daemon gone/stale port: persisted for next start
    print(f"core mode -> {m}   ({note})")


def cmd_relayout(argv):
    """Switch to ANY layout (incl. structural hub/pages/dashboard) by rebuilding the cockpit. psmux
    can't relocate live panes, so a structural change needs a rebuild — but your CLI sessions RESUME
    per-directory, so every conversation comes back. Run from the LAUNCH terminal, NOT inside a pane
    (rebuilding the session you're attached to would kill this command mid-flight)."""
    if not argv:
        print(f"usage: mk relayout <layout>  ({', '.join(sorted(layouts.LAYOUTS))})")
        return
    name = argv[0]
    if name not in layouts.LAYOUTS:
        print(f"error: unknown layout '{name}'")
        return
    if os.environ.get("TMUX"):
        print(f"you're inside the cockpit -- press Ctrl-b then d to detach, then run "
              f"`mk relayout {name}` from the launch terminal")
        return
    teamconfig.set_layout(_project_dir(), name)
    print(f"rebuilding cockpit as '{name}' (your CLI sessions will resume)...")
    cmd_kill([])
    cmd_start([])
    cmd_attach([])


def cmd_studio(argv):
    """Launch MKCREW Studio (local web configurator + launcher)."""
    from . import studio
    studio.serve(project_dir=_project_dir())


# Plain psmux presets `mk add` still accepts alongside the registry's wizard templates (main-vertical /
# even-horizontal / lead-left-ide). These carry no files-IDE, so they build a core pane like the normals.
_WS_EXTRA_LAYOUTS = ("tiled", "even-vertical", "main-horizontal")


def _valid_ws_templates() -> set:
    """Accepted `mk add --template` keys: the registry's wizard templates + the plain presets above."""
    return {t.key for t in templates.wizard_templates()} | set(_WS_EXTRA_LAYOUTS)

def cmd_add(argv):
    """`mk add <dir> [--name N] [--agents 1-6] [--provider P] [--model M] [--template LAYOUT]`: add a workspace as
    a new window (tab) in the running cockpit.  Spawns `--agents` agents (each running `--provider`'s
    CLI), a core pane, and a files pane, arranged by the chosen built-in psmux layout.

    ponytail: the agents are INDEPENDENT (each a usable provider CLI in <dir>) — no `mk ask` crew
    routing between them yet (that's the daemon-prefix engine).  Roles are workspace-prefixed
    (`<name>.main` …) and NOT registered with the daemon, so they can't collide with the main team."""
    from . import frozen, teamconfig
    def _flag(f):
        return argv[argv.index(f) + 1] if f in argv and argv.index(f) + 1 < len(argv) else None
    pos = [a for a in argv if not a.startswith("-")]
    if not pos:
        sys.exit("usage: mk add <dir> [--name N] [--agents 1-6] [--provider P] [--model M] [--template LAYOUT]")
    project = Path(pos[0]).expanduser().resolve()
    if not project.is_dir():
        sys.exit(f"not a directory: {project}")
    mux = PsmuxBackend()
    if not _session_exists(mux, SESSION):
        sys.exit("no cockpit running — run `mk start` first")
    # FIX #3: one cockpit per directory. A dir whose OWN cockpit is LIVE is refused even with --force —
    # clobbering a running cockpit's config breaks its live agents. A stale/dead-pid lock is ignored.
    if _cockpit_live_at(project):
        sys.exit(f"A MKCREW cockpit is already running at {project}; "
                 f"close it first, or use 'mk open {project}' to attach.")
    name = _flag("--name") or project.name
    # Duplicate-tab guard (idempotency): psmux resolves window targets BY NAME to the FIRST match, so
    # a second window with this name would route every later split / select-layout / title into the
    # FIRST one — mangling it (the final fixed-cell layout then DROPS the excess panes: the measured
    # "agent panes but no core pane" tab) while the new tab is left as one bare pane. One name = one
    # window: refuse up front (the wizard surfaces this message as a toast).
    if hasattr(mux, "window_names") and name in mux.window_names(SESSION):
        sys.exit(f"a workspace tab named '{name}' already exists in the cockpit — switch to it with "
                 f"Ctrl-b n, close it first (Ctrl-b x), or re-add with a different --name")
    provider = _flag("--provider") or "claude"
    model = _flag("--model") or ""
    effort = _flag("--effort") or "high"
    try:
        count = max(1, min(int(_flag("--agents") or "1"), 6))   # pages grids <=6/window; single-window layouts best <=4
    except ValueError:                                          # non-numeric / '3.5' -> friendly exit, no traceback
        sys.exit("error: --agents must be a number")
    layout = _flag("--template") or "main-vertical"
    if layout not in _valid_ws_templates():
        layout = "main-vertical"
    win = f"{SESSION}:{name}"
    # Per-agent comma lists (from the wizard) override the single --provider/--model/--effort (back-compat).
    providers_csv = _flag("--providers")
    models_csv = _flag("--models")
    efforts_csv = _flag("--efforts")

    def _pad(items, fill):
        return (items + [fill] * count)[:count]                # pad/truncate to exactly `count` slots
    provs = _pad(providers_csv.split(","), provider) if providers_csv else [provider] * count
    if models_csv is not None:
        mods = _pad(models_csv.split(","), "")                 # keep "" slots = "use roster default"
    else:
        mods = [model] * count if model else None
    # --efforts e1,..,eN is per-agent (like --models); a single --effort replicates across all agents.
    effs = _pad(efforts_csv.split(","), effort) if efforts_csv else [effort] * count
    team = teamconfig.build_team(count, providers=provs, models=mods, efforts=effs)
    # Persist the team so the workspace is resumable (`mk open`) and listable (`mk workspaces`). Guard an
    # existing `.mkcrew` setup unless --force: the wizard's overwrite-Yes path passes --force, while its
    # overwrite-No path uses `mk open` instead and never reaches here. Write BEFORE prefixing roles so the
    # saved config carries plain roles (main/worker1/…) that `mk open`/`mk start` can resume cleanly.
    cfg = teamconfig._config_path(project)
    if cfg.exists() and "--force" not in argv:
        sys.exit(f"workspace already configured at {cfg} — pass --force to overwrite, "
                 f"or `mk open {project}` to resume it")
    teamconfig.write_team(project, team, layout)
    teamconfig.set_name(project, name)                                     # FIX #4: persist the workspace identity
    for a in team:
        a["role"] = f"{name}.{a['role']}"                                  # unique per workspace
    # Build the panes: the lead (a new window/tab), the workers, and ONE "extra" pane -- REBALANCING to
    # 'tiled' after EVERY split so the next split always has room. Without this, psmux halves the active
    # pane on each split; a few splits in, the pane is too small to split, that split RAISES, and cmd_add
    # aborts mid-build -> agents/files go missing and no final layout is applied (this is the #1/#10/#12
    # breakage, and why stacking 3-4 agents lost panes). The cockpit's own builders rebalance after each
    # split for exactly this reason; cmd_add matches.
    #
    # The extra pane honors the registry's normal-vs-experimental split (templates.includes_files_ide):
    #   - lead-left-ide (EXPERIMENTAL) -> a files-IDE pane (core | explorer | editor).
    #   - every NORMAL/plain template  -> a live CORE status pane instead (no files-IDE).
    # select-layout fills cells by pane CREATION order, so the extra pane is created at the position its
    # layout string expects: main-vertical's core sits SECOND (its cells are lead, core, workers); every
    # other template's extra pane is created LAST (files/core is the trailing cell).
    files_ide = templates.includes_files_ide(layout)
    a0 = team[0]
    pid = mux.new_window(SESSION, name, layouts._launch(a0, str(project)), cwd=str(project))  # first agent (lead)
    mux.set_pane_title(pid, f"{a0['role']} - {a0.get('provider', 'claude')}")
    core_pid = files_pid = None
    if layout == "main-vertical":                                          # core LEAD-LEFT: core pane is cell 2
        core_pid = mux.split_window(win, frozen.core_view_cmd(str(project), "h"))  # wide/short strip -> 'h' side-by-side tables
        mux.select_layout(win, "tiled")
        mux.set_pane_title(core_pid, "core - control tower")
    worker_pids = []
    for a in team[1:]:                                                     # the rest of the agents
        p = mux.split_window(win, layouts._launch(a, str(project)))
        mux.select_layout(win, "tiled")                                    # rebalance so the next split has room
        mux.set_pane_title(p, f"{a['role']} - {a.get('provider', 'claude')}")
        worker_pids.append(p)
    if files_ide:                                                          # EXPERIMENTAL: files-IDE pane, LAST cell
        files_pid = mux.split_window(win, frozen.files_view_cmd(project))
        mux.select_layout(win, "tiled")
        mux.set_pane_title(files_pid, "files - core | explorer | editor")
    elif layout != "main-vertical":                                        # NORMAL/plain: core pane, LAST cell
        orient = "h" if layout in ("even-horizontal", "pages") else "v"    # wide/short strips render side-by-side
        core_pid = mux.split_window(win, frozen.core_view_cmd(str(project), orient))
        mux.select_layout(win, "tiled")
        mux.set_pane_title(core_pid, "core - control tower")
    w, h = mux.window_size(win)
    if layout == "lead-left-ide":                                          # EXPERIMENTAL: files-IDE LEAD-LEFT
        mux.select_layout(win, layouts._main_vertical_with_files(
            w, h, pid[1:], [p[1:] for p in worker_pids], files_pid[1:]))
    elif layout == "main-vertical":                                        # NORMAL LEAD-LEFT: lead + core + workers
        mux.select_layout(win, layouts._main_vertical_layout(
            w, h, pid[1:], core_pid[1:], [p[1:] for p in worker_pids]))
    elif layout == "even-horizontal":                                      # NORMAL SIDE-BY-SIDE: agent row + core strip
        mux.select_layout(win, layouts._sidebyside_core_layout(
            w, h, [pid[1:]] + [p[1:] for p in worker_pids], core_pid[1:]))
    elif layout == "pages":                                                # PAGES page: agent GRID + core strip
        mux.select_layout(win, layouts._grid_strip_layout(                 # (<=4 agents -> a single page/tab)
            w, h, [pid[1:]] + [p[1:] for p in worker_pids], core_pid[1:]))
    elif layout == "tiled":                                                # GRID: even grid of agents + core
        ids = [pid[1:]] + [p[1:] for p in worker_pids] + [core_pid[1:]]    # numeric ids, in creation order
        mux.select_layout(win, layouts._tiled_layout(w, h, ids))
    else:                                                                  # legacy presets (even-vertical, main-horizontal)
        mux.select_layout(win, layout)
    # Report the ACTUAL per-agent providers (a mixed team must not read "4 claude agent(s)" off the
    # singular --provider default); breakdown preserves team order, e.g. "1 claude, 1 codex".
    prov_counts = Counter(a.get("provider", "claude") for a in team)
    breakdown = ", ".join(f"{n} {prov}" for prov, n in prov_counts.items())
    print(f"added workspace '{name}': {count} agent(s) ({breakdown}), {layout} layout — Ctrl-b n to switch")
    return 0


def cmd_open(argv):
    """`mk open <folder>`: RESUME a workspace from its existing `.mkcrew` config (team.config + saved
    layout) WITHOUT re-running setup — i.e. `mk start` pointed at <folder>. The wizard's "open existing"
    and overwrite-No paths call this so an already-configured directory comes straight back up (CLI
    sessions resume per-directory). Reuses cmd_start's machinery by pointing the cwd at <folder>."""
    pos = [a for a in argv if not a.startswith("-")]
    if not pos:
        sys.exit("usage: mk open <folder>")
    folder = Path(pos[0]).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"not a directory: {folder}")
    if not teamconfig._config_path(folder).exists():
        sys.exit(f"no MKCREW setup at {folder} — no .mkcrew/team.config "
                 f"(run the add-workspace wizard or `mk add` first)")
    rest, dropped = [], False                       # drop the folder positional; keep flags (--no-attach/--fresh)
    for a in argv:
        if not dropped and not a.startswith("-"):
            dropped = True
            continue
        rest.append(a)
    orig = os.getcwd()
    os.chdir(folder)                                # cmd_start reads _project_dir() (= cwd) throughout
    try:
        cmd_start(rest)
    finally:
        os.chdir(orig)


def _workspace_roots() -> list[Path]:
    """Roots to scan for configured workspaces: the cwd and its parent (each scanned one level deep).
    Small + bounded — 'projects live next to each other in one dev folder' is the common case; the live
    cockpit's project (if any) is folded in by the caller so an out-of-tree workspace still lists."""
    cwd = Path.cwd()
    out, seen = [], set()
    for r in (cwd, cwd.parent):
        rp = r.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def _scan_workspaces(roots) -> list[dict]:
    """Find configured MKCREW setups (a `.mkcrew/team.config`) at each root and its immediate children.
    Returns [{name, path}], deduped by resolved path, depth-1 + capped so the scan stays cheap."""
    found, seen = [], set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        candidates = [root]
        try:
            candidates += sorted(c for c in root.iterdir() if c.is_dir())
        except OSError:
            pass
        for d in candidates:
            if not (d / ".mkcrew" / "team.config").exists():
                continue
            rp = d.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            found.append({"name": d.name, "path": str(rp)})
            if len(found) >= 200:                   # bound the result set (cheap + the wizard lists a handful)
                return found
    return found


def cmd_workspaces(argv):
    """`mk workspaces`: print already-configured MKCREW setups as JSONL — one {"name","path"} object per
    line — so the wizard's "open existing" list can parse them (it also accepts name<TAB>path / bare
    paths). Source: scan sensible roots (cwd + parent, depth 1) for `.mkcrew/team.config`, plus the live
    cockpit's project. Bounded + best-effort."""
    roots = _workspace_roots()
    rows = _scan_workspaces(roots)
    seen = {r["path"] for r in rows}
    try:                                            # fold in the live cockpit's project (may be out-of-tree)
        live = config.cockpit_project_file().read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        live = ""
    if live:
        lp = Path(live)
        if (lp / ".mkcrew" / "team.config").exists() and str(lp.resolve()) not in seen:
            rows.append({"name": lp.name, "path": str(lp.resolve())})
    for ws in rows:
        print(json.dumps(ws))
    return 0


def cmd_doctor(argv):
    """`mk doctor` — preflight: check every prerequisite and report pass/fail + the fix. Installs
    nothing (install.ps1 does that). Exits non-zero if a REQUIRED prerequisite is missing."""
    from . import studio
    ok = True
    def row(state, name, detail, fix=""):
        tag = {"ok": "[ OK ]", "warn": "[WARN]", "bad": "[FAIL]"}[state]
        print(f"  {tag} {name:<12} {detail}")
        if fix and state != "ok":
            print(f"         -> {fix}")
    def first_line(cmd, *a):
        try:
            out = subprocess.run([cmd, *a], capture_output=True, text=True, timeout=10).stdout.strip()
            return out.splitlines()[0] if out else ""
        except Exception:
            return ""
    print("\n  MKCREW doctor  -  prerequisite preflight\n")
    if shutil.which("uv"):
        row("ok", "uv", first_line("uv", "--version"))
    else:
        row("warn", "uv", "not found (recommended; it also fetches Python)", "irm https://astral.sh/uv/install.ps1 | iex")
    row("ok", "python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    mk = shutil.which("mk")
    if mk:
        row("ok", "mkcrew", f"mk on PATH ({mk})")
    else:
        row("warn", "mkcrew", "mk not on PATH", "uv tool install --editable . ; then reopen the terminal")
    if shutil.which("psmux"):
        row("ok", "psmux", first_line("psmux", "-V"))
    else:
        ok = False
        row("bad", "psmux", "NOT on PATH - the cockpit cannot run", "install the MKCREW psmux fork (see install.ps1)")
    if shutil.which("node"):
        row("ok", "node", first_line("node", "--version"))
    else:
        row("warn", "node", "not found (opencode / some CLIs need it)", "install Node LTS")
    present = [name for name, found in studio.detect_clis().items() if found]
    if present:
        row("ok", "agent CLIs", ", ".join(present))
    else:
        ok = False
        row("bad", "agent CLIs", "none found - the team has nothing to run",
            "install >=1 (e.g. npm i -g @anthropic-ai/claude-code) and log in")
    if shutil.which("conhost.exe"):
        row("ok", "conhost", "present")
    else:
        row("warn", "conhost", "not found (cockpit font sizing may not apply)")
    print()
    if ok:
        print("  all required prerequisites present.  run:  mk studio\n")
        return 0
    print("  MISSING required prerequisites (see [FAIL] above).  run install.bat to fix.\n")
    raise SystemExit(1)


COMMANDS = {"init": cmd_init, "start": cmd_start, "attach": cmd_attach,
            "kill": cmd_kill, "panic": cmd_panic, "add": cmd_add,
            "open": cmd_open, "workspaces": cmd_workspaces, "doctor": cmd_doctor,
            "pend": cmd_pend, "trace": cmd_trace, "stats": cmd_stats, "repair": cmd_repair,
            "verify": cmd_verify, "resume": cmd_resume, "tui": cmd_tui,
            "layout": cmd_layout, "relayout": cmd_relayout, "studio": cmd_studio,
            "mode": cmd_mode}

def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "ask":
        from .ask_cli import main as _ask_main
        return _ask_main(argv[1:])
    if argv and argv[0] == "status":
        from .coreview import status_main
        return status_main()
    if not argv or argv[0] not in COMMANDS:
        print("usage: mk {init|start|open|workspaces|doctor|attach|kill|panic|add|pend|trace|stats|repair|verify|resume|tui|layout|relayout|studio|ask|status}")
        return 2
    COMMANDS[argv[0]](argv[1:])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
