# src/mkcrew/agent.py
import base64, hashlib, json, sys
from pathlib import Path
from . import config

def _hook_command() -> dict:
    # Frozen: `MKCREW.exe finish-hook` (argv dispatch); dev: `python -m mkcrew.finish_hook`.
    from . import frozen
    args = ["finish-hook"] if frozen.is_frozen() else ["-m", "mkcrew.finish_hook"]
    return {"type": "command", "command": sys.executable, "args": args, "timeout": 30}

def _is_mkcrew_finish_hook(group) -> bool:
    """True if a Stop-hook group is a MKCREW finish hook (any arg naming finish_hook / finish-hook), so
    a stale one can be replaced -- e.g. after a module rename or a frozen<->dev switch."""
    return any(str(a).replace("-", "_").endswith("finish_hook")
               for h in group.get("hooks", []) for a in h.get("args", []))

def ensure_project_hook(project_dir) -> Path:
    """Merge the MKCREW Stop hook into <project>/.claude/settings.json (create/merge, never clobber)."""
    dot = Path(project_dir) / ".claude"
    dot.mkdir(parents=True, exist_ok=True)
    sp = dot / "settings.json"
    data = {}
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    # Drop any prior MKCREW finish-hook (heals a renamed module), then add the current one, so an
    # existing target project self-corrects on the next `mk start` instead of stacking a dead hook.
    stop[:] = [g for g in stop if not _is_mkcrew_finish_hook(g)]
    stop.append({"matcher": "", "hooks": [_hook_command()]})
    sp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return sp


def _codex_hook_command(role: str) -> str:
    """Windows command string for codex's Stop hook running our forward-delivery hook.

    Codex hooks inherit the env codex was LAUNCHED with, and launch.cmd sets MK_ACTOR=<role> per pane
    before codex starts — so the hook reads THIS pane's role from the inherited env. Env-first
    (`if (-not $env:MK_ACTOR)`) with the baked <role> only as a fallback is what lets TWO codex agents
    share the one project .codex/hooks.json yet each pull their OWN role (a bare overwrite made both
    use the last-baked role). Codex has no per-hook env field, so an encoded PowerShell wrapper runs it
    (works via cmd.exe or PowerShell); it pipes raw hook stdin to Python; finish_hook tolerates the BOM."""
    from . import frozen
    exe = sys.executable
    tail = "finish-hook" if frozen.is_frozen() else "-m mkcrew.finish_hook"
    inner = (
        "$ProgressPreference='SilentlyContinue'; "
        f"if (-not $env:MK_ACTOR) {{ $env:MK_ACTOR='{role}' }}; "
        "$env:PYTHONIOENCODING='utf-8'; "
        f"[Console]::In.ReadToEnd() | & '{exe}' {tail}; "
        "exit 0"
    )
    encoded = base64.b64encode(inner.encode("utf-16le")).decode("ascii")
    return f"powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def _codex_hook_trusted_hash(command: str, timeout: int = 30, status_message: str | None = None) -> str:
    """Return Codex's trust hash for our normalized Stop hook identity.

    Codex hashes a canonical JSON form of `{event_name, hooks}` after selecting commandWindows on
    Windows and normalizing away commandWindows.  Keeping this local lets MKCREW launch Codex
    unattended without requiring the user to open `/hooks` and manually trust the generated hook.
    """
    hook = {"async": False, "command": command, "timeout": timeout, "type": "command"}
    if status_message is not None:
        hook["statusMessage"] = status_message
    identity = {"event_name": "stop", "hooks": [hook]}

    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value

    raw = json.dumps(canonical(identity), separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _ensure_codex_hook_trusted(config_path: Path, hook_path: Path, command: str) -> None:
    key = f"{hook_path}:stop:0:0"
    section = f"[hooks.state.'{key}']"
    alt_section = f"[hooks.state.{json.dumps(key)}]"
    block = f'{section}\ntrusted_hash = "{_codex_hook_trusted_hash(command)}"\n'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() not in (section, alt_section):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].startswith("["):
            j += 1
        lines[i:j] = block.rstrip("\n").splitlines()
        config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return
    prefix = text.rstrip()
    config_path.write_text((prefix + "\n\n" if prefix else "") + block, encoding="utf-8")


def _is_mkcrew_codex_hook(group) -> bool:
    for h in group.get("hooks", []):
        cmd = str(h.get("commandWindows", "")) + str(h.get("command", ""))
        normalized = cmd.replace("-", "_")
        if "finish_hook" in normalized:
            return True
        marker = "_EncodedCommand "
        if marker in normalized:
            try:
                encoded = normalized.rsplit(marker, 1)[1].split()[0]
                decoded = base64.b64decode(encoded).decode("utf-16le").replace("-", "_")
            except Exception:
                continue
            if "finish_hook" in decoded:
                return True
    return False


def ensure_codex_hook(project_dir, role: str) -> Path:
    """Register the MKCREW forward-delivery hook as a codex **Stop** hook in
    <project>/.codex/hooks.json — same {"decision":"block","reason":...} contract as the Claude
    Stop hook, so codex pulls its queued task via /next instead of the daemon typing it.  The command
    resolves the role from the per-pane MK_ACTOR launch env (baked <role> only as a fallback).  So 2+
    codex agents share this one hooks.json yet each pull their OWN role.  Project-scoped (never touches
    the user's global ~/.codex), self-healing (drops a stale entry before re-adding)."""
    dot = Path(project_dir).resolve() / ".codex"
    dot.mkdir(parents=True, exist_ok=True)
    # Codex discovers hooks next to active config layers.  A project with only hooks.json can be skipped
    # by Codex 0.142.x, so create the smallest project config layer when none exists.
    cp = dot / "config.toml"
    if not cp.exists():
        cp.write_text("[features]\nhooks = true\n", encoding="utf-8")
    hp = dot / "hooks.json"
    data = {}
    if hp.exists():
        try:
            data = json.loads(hp.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cmd = _codex_hook_command(role)
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    stop[:] = [g for g in stop if not _is_mkcrew_codex_hook(g)]
    stop.append({"hooks": [{"type": "command", "command": cmd, "commandWindows": cmd, "timeout": 30}]})
    hp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Codex discovers project hooks from `.codex/hooks.json`, but stores hook trust state only in the
    # user/session config layers.  Pre-trust just this generated project hook so unattended cockpit
    # launches do not block on the interactive `/hooks` review prompt.
    _ensure_codex_hook_trusted(Path.home() / ".codex" / "config.toml", hp, cmd)
    return hp


_CREW_MD = """<!-- MKCREW:start -->
## MKCREW cockpit

You are running inside a **MKCREW** multi-agent cockpit. The infrastructure is managed for you:
the coordination daemon is up and your teammates are live in their own terminal panes, ready for
work. Do NOT verify processes / ports / panes or hunt for setup commands.

### Roles
- **Lead (`main`)**: delegate role work with `mk ask <role> "<task>"` (use the full mk path from
  your launch message; a bare `mk` is not on PATH). `mk ask` blocks until that teammate replies.
  Never do teammate work yourself.
- **Worker**: your task arrives in your pane naming a job id + an inbox file. Lifecycle: read the
  task -> do exactly that task -> self-audit (did you meet EVERY stated criterion and touch EVERY
  named location?) -> report with `mk-done <job_id> "<result summary>"`. That command is the ONLY
  completion signal -- saying done in chat does not count and the team stays blocked until it
  runs. Stuck after ~3 attempts at one failure: report
  `mk-done <job_id> "BLOCKED: <question> Option A: ... Option B: ..."` -- BLOCKED is a
  first-class move; the lead rules and re-asks you with your session intact.
- **Planner**: implementation plans only, never implementation; report the plan via mk-done.

### Commands
| Command | Use it when |
|---|---|
| `mk ask <role> "..."` | (lead) delegate a task; blocks until the reply arrives |
| `mk-done <job_id> "..."` | (worker/planner) report your result -- the only completion signal |
| `mk pend` | list open jobs -- check what teammates hold before touching shared files |
| `mk status` | one tower snapshot: roster, live states, recent tasks |
| `mk stats` | per-worker history: done/failed, median time, late + thin-evidence counts |
| `mk trace <job_id>` | one job's full event timeline when something looks wrong |
| `mk mode [<name>]` | show or switch the crew's working posture (applies live) |

### Core modes + the tower
The crew runs in a core mode (standard / fast / thorough / plan-first / architect / warroom /
chief / venture). The LEAD's launch briefing carries the active mode's procedure; a WORKER's task
envelope carries any reply rules that apply to it (checklists, evidence packs, critique formats)
-- **the envelope always wins over this document**. The core pane ("control tower") shows each
agent's live state (`working <age>`; `late-work` = still visibly producing past its deadline --
let it finish), recent tasks, and the active mode; when your assumption and the tower disagree,
trust the tower. A typed `[MKCREW] ...` line appearing in a pane is the daemon talking (an
mk-done reminder, a wake ping, a posture update) -- act on it.
<!-- MKCREW:end -->
"""


def _merge_crew_md(p: Path) -> Path:
    """Idempotently merge the MKCREW operating block (_CREW_MD) into file *p*: drop any stale MKCREW
    block first, then preserve the user's other content. Shared by the CLAUDE.md (claude) and
    AGENTS.md (codex/agy/opencode) writers so both files carry the identical operating section."""
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    start, end = "<!-- MKCREW:start -->", "<!-- MKCREW:end -->"
    if start in text and end in text:                       # drop any stale MKCREW block
        text = text.split(start)[0].rstrip() + text.split(end, 1)[1]
    text = text.strip()
    p.write_text((text + "\n\n" if text else "") + _CREW_MD, encoding="utf-8")
    return p


def ensure_project_claude_md(project_dir) -> Path:
    """Merge the MKCREW operating section into <project>/CLAUDE.md (claude auto-reads it) so every
    claude agent wakes up knowing the crew model + commands without discovery. Idempotent (replaces
    any existing MKCREW block); preserves the user's other CLAUDE.md content."""
    return _merge_crew_md(Path(project_dir) / "CLAUDE.md")


def ensure_project_agents_md(project_dir) -> Path:
    """Merge the MKCREW operating section into <project>/AGENTS.md -- the file codex, agy
    (antigravity) and opencode auto-read -- so a NON-claude lead/worker gets the same crew briefing
    claude gets from CLAUDE.md. Same _CREW_MD content + idempotent merge as ensure_project_claude_md."""
    return _merge_crew_md(Path(project_dir) / "AGENTS.md")


def _model_arg(model: str, flag: str = "-m") -> str:
    """Model flag for a non-claude CLI; SKIP a BLANK model or a claude-* model so the CLI falls back
    to its own default model instead of failing to launch. A blank model must drop the flag entirely:
    emitting a dangling `--model`/`-m` with NO value makes the CLI treat the next token (or EOL) wrong
    and print usage then EXIT — the antigravity `agy --dangerously-skip-permissions --model<EOL>`
    blank-pane bug (worker config left model="", which agy carries inside the model name). Names with
    spaces (e.g. antigravity's 'Gemini 3.5 Flash (High)') are QUOTED so the .cmd passes them as one arg."""
    if not str(model).strip() or str(model).startswith("claude-"):
        return ""
    m = f'"{model}"' if " " in str(model) else str(model)
    return f" {flag} {m}"


_CODEX_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}   # codex's valid reasoning levels (incl. xhigh)

def _codex_effort(effort) -> str:
    """Codex reasoning level via a `-c` config override. Only the levels codex accepts are passed
    (claude's 'max', or any unset value, is skipped so codex keeps its default instead of erroring)."""
    return f' -c model_reasoning_effort="{effort}"' if effort in _CODEX_EFFORTS else ""


# agy (antigravity) has NO launch-time reasoning-effort flag — it carries the thinking level INSIDE the
# --model value as a "(Level)" variant suffix (e.g. "Gemini 3.5 Flash (High)"), the exact form agy itself
# persists to ~/.gemini/antigravity-cli/settings.json and `agy models` lists.  Map our generic effort
# names to agy's capitalized suffix levels (Gemini Flash: Low/Medium/High; Pro: Low/High).
_AGY_THINKING_LEVELS = {"low": "Low", "medium": "Medium", "high": "High"}

def _agy_model_with_thinking(model: str, effort) -> str:
    """Fold the picked thinking level into agy's model-variant name so the chosen effort ACTUALLY
    reaches the launch (agy exposes no effort flag — thinking rides in --model, e.g.
    "Gemini 3.5 Flash (High)").  Returned UNCHANGED for: a blank/claude-* model (the CLI handles it),
    an effort agy has no variant for ('max'/unknown -> agy keeps the model's own default), or a model
    that ALREADY carries a "(...)" variant suffix (e.g. the fixed "Claude Opus 4.6 (Thinking)" or a
    pre-built "Gemini 3.5 Flash (High)") — so we never double the suffix and never drop the effort."""
    m = str(model).strip()
    level = _AGY_THINKING_LEVELS.get(str(effort).strip().lower()) if effort else None
    if not m or m.startswith("claude-") or level is None:
        return m
    if m.endswith(")") and "(" in m:        # already a "(Level)"/"(Thinking)" variant -> don't double it
        return m
    return f"{m} ({level})"


_OPENCODE_PLUGIN = r'''// .opencode/plugins/mkcrew-pull.ts  — written by MKCREW `mk start` (idempotent, overwrites).
// Invisible INTERNAL delivery: runs inside opencode, PULLS this worker's queued task from the
// MKCREW daemon (GET /next?role=<MK_ACTOR>), and injects it into THIS live TUI via the prompt
// channel — the same path the IDE editors use and the only one that renders (session.prompt /
// prompt_async run server-side but never show in the TUI: opencode #8564).  No external process
// types into the pane.  Inert outside a cockpit (no MK_ACTOR => no-op).
import { readFileSync, appendFileSync } from "node:fs"

export const MkcrewPull = async ({ client }) => {
  const role = process.env.MK_ACTOR
  // MK_RUNTIME_ROOT (pinned by `mk start`) is the daemon's runtime root, so an account wrapper
  // that rewrites profile dirs can't hide the daemon; fall back to the LOCALAPPDATA formula.
  const local = process.env.LOCALAPPDATA
  const root = process.env.MK_RUNTIME_ROOT || (local && `${local}/mkcrew`)
  if (!role || !root) return {}                 // not in a cockpit — do nothing
  const portFile = `${root}/runtime/mkd.port`
  const logFile  = `${root}/runtime/mk_opencode_plugin.log`
  // ponytail: debug breadcrumb while proving internal delivery — drop once confirmed live.
  const dbg = (m) => { try { appendFileSync(logFile, `${Date.now()} ${role} ${m}\n`) } catch {} }

  let idle = true        // a freshly-launched TUI is idle; in a cockpit only WE drive this pane
  let busy = false       // re-entrancy guard for one in-flight pull

  async function pull() {
    if (!idle || busy) return
    busy = true
    try {
      const port = readFileSync(portFile, "utf-8").trim()
      const res = await fetch(`http://127.0.0.1:${port}/next?role=${encodeURIComponent(role)}`)
      if (!res.ok) { dbg(`/next HTTP ${res.status}`); return }
      const job = await res.json().catch(() => ({}))
      if (!job || !job.reason) return            // {} => no queued work => stop (loop terminator)
      dbg(`pulled ${job.job_id} — injecting`)
      idle = false                               // a turn is starting; pause pulls until next idle
      await client.tui.appendPrompt({ body: { text: job.reason } })
      await client.tui.submitPrompt()
    } catch (e) { dbg(`error ${e}`) }
    finally { busy = false }
  }

  const timer = setInterval(pull, 2500)          // poll covers cold start (no turn yet -> no idle)
  if (typeof timer.unref === "function") timer.unref()
  dbg("loaded")

  return {
    event: async ({ event }) => {
      if (event?.type === "session.idle") { idle = true; await pull() }
    },
  }
}
'''


def ensure_opencode_plugin(project_dir) -> Path:
    """Write MKCREW's opencode plugin to <project>/.opencode/plugins/mkcrew-pull.ts.  opencode has no
    silent Stop-hook equivalent (session.prompt/prompt_async never render in the live TUI — opencode
    #8564), so the INTERNAL analog of the Claude/codex Stop hook is this in-process plugin: on idle it
    PULLS the worker's task via GET /next?role=<MK_ACTOR> and injects it through the /tui prompt
    channel (the only one that renders).  No daemon keystrokes.  Idempotent (overwrites)."""
    dot = Path(project_dir) / ".opencode" / "plugins"
    dot.mkdir(parents=True, exist_ok=True)
    pp = dot / "mkcrew-pull.ts"
    pp.write_text(_OPENCODE_PLUGIN, encoding="utf-8")
    return pp


def _agent_command_line(provider: str, model: str, mode: str,
                         effort: str | None, role: str, project_dir,
                         session_id: str | None = None, resume: bool = False,
                         command: str | None = None, bin: str | None = None) -> str:
    """Return the provider-specific command line (no trailing newline).

    All providers launch as PERSISTENT INTERACTIVE sessions.  Per-job tasks are
    delivered later by the daemon via psmux send-keys (pointing to the per-job
    inbox and running mk-done <job_id>).  No inbox path or task is baked in here.

    `bin` overrides the CLI executable for a BUILT-IN provider while KEEPING the
    provider (so its hooks + delivery routing are unchanged) -- e.g. provider
    'claude' with bin='C:/Users/u/.local/bin/claudew.cmd' runs a work-account
    wrapper that sets the right CLAUDE_CONFIG_DIR, yet still uses the claude
    Stop hook.
    """
    if provider == "custom":
        return command or ""
    if provider == "claude":
        effort_flag = f" --effort {effort}" if effort is not None else ""
        session_flag = ""
        if session_id:
            session_flag = (f" --resume {session_id}" if resume
                            else f" --session-id {session_id}")
        return f"{bin or 'claude'} --permission-mode {mode} --model {model}{effort_flag}{session_flag}"
    if provider == "gemini":
        # --skip-trust: bypass the trusted-folder gate (like claude's folder-trust) so the
        #   agent runs unattended; -y/--yolo: auto-approve all tools; -m: model.
        # Gemini defaults to interactive without -p; tasks arrive via send-keys.
        # Verified live: gemini reads the inbox, does the task, and runs mk-done.
        # RESUME (like claude -- gemini can PRE-SET a session id): `gemini --session-id <uuid>`
        #   starts a NEW session under our MKCREW per-role uuid ("Start a new session with a manually
        #   provided UUID", per `gemini --help`), and on a restart `gemini --resume <uuid>` reopens
        #   THAT session. Both stay interactive (never -p). Reuses the same id claude does, so resume
        #   is deterministic per role rather than "whatever ran last".
        # confirm: resume-BY-UUID (`--resume <uuid>`) is the documented behavior, but this gemini's
        #   --help only spells out `--resume latest`/index -- if a UUID is rejected on a live run,
        #   switch the resume arm to `--resume latest` (one gemini main per project => latest==this role).
        session_flag = ""
        if session_id:
            session_flag = (f" --resume {session_id}" if resume
                            else f" --session-id {session_id}")
        return f"{bin or 'gemini'} --skip-trust -y{session_flag}{_model_arg(model)}"
    if provider == "opencode":
        # opencode (no subcommand) starts the interactive TUI.  Delivery is INTERNAL: the
        # mkcrew-pull plugin (ensure_opencode_plugin) runs in-process and PULLS /next, so there is
        # no external HTTP push and thus no server port to pin.  -m: model.  (opencode v1.17.9 has
        # NO --variant/reasoning-effort flag — passing one makes opencode reject the args and exit.)
        # RESUME (continue-last; opencode has no settable id at launch): `--continue`/`-c` reopens the
        #   LAST session in the SAME interactive TUI (verified in `opencode --help`: "-c, --continue
        #   continue the last session"; pairs with -m). Fresh launches omit it. Never `opencode run`.
        continue_flag = " --continue" if resume else ""
        return f"{bin or 'opencode'}{continue_flag}{_model_arg(model)}"
    if provider == "antigravity":
        # `agy` (Antigravity CLI) is interactive by default (--print/-p is the headless mode we
        # must avoid).  --dangerously-skip-permissions is its auto-approve (claude's
        # bypassPermissions analog).  --model: model.  Tasks arrive later via send-keys.
        # RESUME (continue-last; agy has no settable conversation id at launch): `--continue`/`-c`
        #   continues the MOST RECENT conversation (verified in `agy --help`: "--continue  Continue
        #   the most recent conversation"). Fresh launches omit it; stays interactive (never -p).
        # THINKING: agy has NO launch-time reasoning-effort flag (verified `agy --help` — its only knobs
        #   are --model, --continue/--conversation, --dangerously-skip-permissions, -i/--prompt-interactive,
        #   -p/--print, --sandbox).  It carries the thinking level INSIDE the --model value as a
        #   "(High)"/"(Medium)"/"(Low)" suffix — the exact form agy persists to
        #   ~/.gemini/antigravity-cli/settings.json (e.g. "Gemini 3.5 Flash (High)") and `agy models`
        #   lists.  So the wizard's separately-picked effort is FOLDED onto the base model name here
        #   (_agy_model_with_thinking) into ONE --model value that actually reaches the launch — no
        #   silently-dropped control, and no separate effort/-c flag (agy has none).
        continue_flag = " --continue" if resume else ""
        agy_model = _agy_model_with_thinking(model, effort)
        return f"{bin or 'agy'} --dangerously-skip-permissions{continue_flag}{_model_arg(agy_model, '--model')}"
    if provider == "codex":
        # codex with NO subcommand launches its DEFAULT interactive TUI (the headless mode is
        # the 'exec' subcommand, which we must never use).  --dangerously-bypass-approvals-and-
        # sandbox is codex's analog of claude's bypassPermissions / gemini's -y: run unattended
        # with no approval prompts.  -m: model; -c model_reasoning_effort: the thinking level.
        # --dangerously-bypass-hook-trust is kept as a belt-and-suspenders automation flag; ensure_codex_hook
        # also writes Codex's project-local trusted hash so no `/hooks` prompt is required.
        # RESUME (continue-last; codex has no settable session id): the `resume` SUBCOMMAND reopens a
        #   prior session in the interactive TUI -- `codex resume --last` skips the picker and reopens
        #   the most recent session for THIS cwd (the headless analog is `codex exec resume`, which we
        #   must never use). codex's global flags (-m / -c / the bypass flags) go BEFORE the subcommand,
        #   so we append ` resume --last` to the END of the existing global-flag command.
        # confirm: `codex resume --last` composing with the --dangerously-bypass-* flags is
        #   version-dependent and codex could not be launched here to verify (running it triggers an
        #   auto-update); if a live `mk start` restart shows codex rejecting a flag on the resume line,
        #   drop that flag from the resume arm after the test.
        resume_sub = " resume --last" if resume else ""
        return (f"{bin or 'codex'} --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"
                f"{_model_arg(model)}{_codex_effort(effort)}{resume_sub}")
    raise ValueError(f"unknown provider: {provider!r}")


def write_launch_cmd(role: str, model: str, project_dir,
                     mode: str = "bypassPermissions", effort: str | None = None,
                     provider: str = "claude", session_id: str | None = None,
                     resume: bool = False, command: str | None = None,
                     bin: str | None = None) -> Path:
    scripts = Path(sys.executable).parent
    from . import frozen
    shim = frozen.shim_bin()                              # frozen: dir with mk.cmd/mk-done.cmd shims
    path_dirs = f"{scripts};{shim}" if shim else f"{scripts}"
    p = config.agent_config_dir(role) / "launch.cmd"
    if not bin and provider != "custom":
        # ROOT fix for account drift: a BARE built-in provider (no explicit bin) resolves to the user's
        # DEFAULT account wrapper from accounts.json, so it can't fall through to the shared, ambient
        # ~/.claude whose signed-in account changes. None (no account defined) -> stays bare, unchanged.
        bin = config.default_account_bin(provider)
    agent_cmd = _agent_command_line(provider, model, mode, effort, role, project_dir,
                                    session_id=session_id, resume=resume, command=command, bin=bin)
    if bin and provider != "custom" and bin.lower().endswith((".cmd", ".bat")):
        # A batch script invoking another batch without `call` never RETURNS -- the relaunch loop
        # below would silently die with the wrapper. (Custom commands carry their own `call`.)
        agent_cmd = "call " + agent_cmd
    # codex self-updates via the LazyCodex/omo `session-start-checking-auto-update` hook, then exits
    # "please restart" — which would drop THIS agent pane to a bare shell mid-cockpit. Disable that
    # auto-update for this launch only (env-scoped, honoured by omo's auto-update.mjs); the user's
    # global codex keeps auto-updating normally outside MKCREW. ponytail: env flag, no global edits.
    codex_env = ('set "LAZYCODEX_AUTO_UPDATE_DISABLED=1"\r\n'
                 'set "OMO_CODEX_AUTO_UPDATE_DISABLED=1"\r\n') if provider == "codex" else ""
    if provider == "opencode":
        codex_env = 'set "OPENCODE_SKIP_UPDATE=1"\r\n'   # launch-time self-update can strand the pane
    # Relaunch loop: codex's NATIVE updater (and any crash/quit) can still drop the pane to a bare
    # shell, and users don't remember the bypass/resume flags. The pane itself remembers: any key
    # re-runs the SAME command with the same env. ponytail: same baked argv on relaunch - a fresh-start
    # command starts a fresh session (use the CLI's own /resume to recover context if needed).
    p.write_text(
        "@echo off\r\n"
        f'cd /d "{Path(project_dir)}"\r\n'
        f'set "PATH={path_dirs};%PATH%"\r\n'
        f'set "MK_ACTOR={role}"\r\n'
        f'set "MK_RUNTIME_ROOT={config.runtime_root()}"\r\n'
        f"{codex_env}"
        ":mkcrew_relaunch\r\n"
        f"{agent_cmd}\r\n"
        "echo(\r\n"
        f"echo [MKCREW] {role} CLI exited (quit / crash / self-update).\r\n"
        "echo [MKCREW] Press any key to relaunch it with the same setup - or Ctrl-b x to close this pane.\r\n"
        "pause >nul\r\n"
        "goto mkcrew_relaunch\r\n",
        encoding="utf-8")
    return p

def launch_command(role: str, model: str, project_dir,
                   mode: str = "bypassPermissions", effort: str | None = None,
                   provider: str = "claude", session_id: str | None = None,
                   resume: bool = False, command: str | None = None,
                   bin: str | None = None) -> list[str]:
    # /k (not /c): if the agent CLI exits (crash / bad model / not logged in), keep the pane
    # open showing the error, instead of silently closing it so the cockpit looks empty.
    return ["cmd", "/k", str(write_launch_cmd(role, model, project_dir,
                                              mode=mode, effort=effort,
                                              provider=provider,
                                              session_id=session_id, resume=resume,
                                              command=command, bin=bin))]
