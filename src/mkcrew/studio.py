# src/mkcrew/studio.py
"""MKCREW Studio backend: detect CLIs, catalog templates/modes, read/write config,
launch, and a local web server. Reuses teamconfig + layouts (no engine duplication)."""
import json
import shutil
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config, teamconfig, layouts, profiles, templates

KNOWN_PROVIDERS = ["claude", "codex", "opencode", "antigravity"]
_PROVIDER_BINARY = {"antigravity": "agy"}   # provider name -> the actual CLI binary on PATH

# DEFAULT per-provider model SUGGESTIONS (seed the user-editable models.json). The UI field is a
# typeable combo, so these are just starters — the user can enter any value or edit the list (see
# load_catalog). opencode covers both 'zen' (opencode/<id>, curated gateway) and 'go' (opencode-go/<id>,
# subscription) per opencode.ai/docs/{zen,go}. antigravity model names carry the level (no thinking toggle).
_PROVIDER_MODELS = {
    "claude":      ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5"],
    "codex":       ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "opencode":    ["opencode/big-pickle", "opencode/deepseek-v4-flash-free", "opencode/mimo-v2.5-free",
                    "opencode/north-mini-code-free",                       # zen free tier (docs/zen)
                    "opencode-go/minimax-m3", "opencode-go/deepseek-v4-flash", "opencode-go/glm-5.2",
                    "opencode-go/kimi-k2.7-code", "opencode-go/qwen3.7-max"],   # opencode go (docs/go)
    "antigravity": ["Gemini 3.5 Flash (High)", "Gemini 3.5 Flash (Medium)", "Gemini 3.5 Flash (Low)",
                    "Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (Low)", "Claude Sonnet 4.6 (Thinking)",
                    "Claude Opus 4.6 (Thinking)", "GPT-OSS 120B (Medium)"],   # agy's OWN catalog (not the claude provider)
}
# Thinking / reasoning levels per provider (a CLI flag at launch): claude `--effort`, codex
# `-c model_reasoning_effort`, opencode `--variant` (provider-specific reasoning effort). antigravity
# has none — it bakes the level into the model name. A provider absent here -> no thinking control.
_PROVIDER_THINKING = {
    "claude":   ["low", "medium", "high", "max"],
    "codex":    ["minimal", "low", "medium", "high"],
    "opencode": ["minimal", "low", "medium", "high", "max"],
}


def _catalog_path():
    return config.runtime_root() / "models.json"


def load_catalog() -> dict:
    """The {models, thinking} catalog the Studio modal uses. Returns the built-in defaults UNLESS the
    user has saved a models.json (via the 'Edit models' panel) — we do NOT auto-seed the file, so code
    default updates always reach anyone who hasn't customised, and customisers keep their edits until
    they hit 'Reset to defaults'."""
    p = _catalog_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {"models": data.get("models") or _PROVIDER_MODELS,
                    "thinking": data.get("thinking") or _PROVIDER_THINKING}
        except (OSError, json.JSONDecodeError):
            pass
    return {"models": _PROVIDER_MODELS, "thinking": _PROVIDER_THINKING}


def save_catalog(data: dict) -> dict:
    """Persist a user-edited catalog to models.json, OR (with {'reset': true}) delete it so the catalog
    falls back to the current code defaults."""
    p = _catalog_path()
    if data.get("reset"):
        p.unlink(missing_ok=True)
        return {"ok": True, "reset": True}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"models": data.get("models", {}), "thinking": data.get("thinking", {})},
                            indent=2), encoding="utf-8")
    return {"ok": True, "path": str(p)}

# Preferred monitor shape per layout ('wide' | 'tall' | None) — the ONE fit hint that isn't in the
# frozen registry (templates.py owns key/label/group/files_ide/desc + the agent range). The Studio
# gallery scores + sorts cards by how well they fit the chosen agent count + screen shape.
_TEMPLATE_SCREEN = {
    "main-vertical":   "wide",   # lead dominant + stacked column reads best wide
    "even-horizontal": "wide",   # a row of agents wants width
    "lead-left-ide":   "wide",   # LEAD LEFT + the files-IDE strip -> wide
    # pages / dashboard: no shape preference (paged grids fit any monitor)
}

# Only modes that actually change behavior. 'standard' is the silent default (no prompt clause);
# 'fast' drops the gates. Genius/Loop were removed -- they only re-labelled what the lead already
# does from the task (task-router escalates risky work; "keep developing" triggers the dev loop).
MODES = [
    {"key": "standard",   "name": "Standard",   "desc": "Delegate -> do -> review. The balanced default."},
    {"key": "fast",       "name": "Fast",       "desc": "No gates -- ship directly. Skips the plan/review/verify ceremony."},
    {"key": "thorough",   "name": "Thorough",   "desc": "Correctness over speed: review gate on every result, claims verified by running them. Patient watchdog + 90-min ask ceiling for deep work."},
    {"key": "plan-first", "name": "Plan First", "desc": "The lead presents its full task breakdown and waits for your OK before the first delegation."},
    {"key": "architect",  "name": "Architect",  "desc": "Flagship-as-judge: the lead never touches code -- it writes task contracts, workers implement and cross-verify, it arbitrates on evidence packs and spot-audits. Near-solo quality at a fraction of the lead's tokens; 90-min ask ceiling."},
]
# A RUNNING cockpit can switch anytime:  mk mode <key>  (persists + updates daemon and lead live).


def detect_clis() -> dict:
    """Map each known provider -> whether its CLI binary is on PATH (some differ: antigravity -> agy)."""
    return {name: shutil.which(_PROVIDER_BINARY.get(name, name)) is not None for name in KNOWN_PROVIDERS}


def list_templates() -> list:
    """The layouts the Studio gallery offers, straight from the frozen registry (templates.py) so the
    web + app can't drift from the wizard. Each dict carries the registry fields the UI reads
    (key/label/group/files_ide/desc, in display order) plus `name` (alias of label the gallery/modal
    render), the fit-sort agent range (`min`/`max`, derived from min_agents/max_agents) and a
    screen-shape hint (`screen`: 'wide' | 'tall' | None)."""
    out = []
    for t in templates.all_templates():
        d = t.to_dict()                         # key, label, group, files_ide, desc, add_capable, min/max_agents
        d["name"] = t.label                     # gallery/modal render `name`; keep `label` too
        d["min"], d["max"] = t.min_agents, t.max_agents
        d["screen"] = _TEMPLATE_SCREEN.get(t.key)
        out.append(d)
    return out


def list_modes() -> list:
    return list(MODES)


def _workspace_label(project_dir) -> str:
    """The cockpit's human label (FIX #4: name-as-identity): the persisted workspace name, or the
    folder name as a sensible fallback — so the UI shows e.g. 'Testing' / 'Prod', never a bare 'main'."""
    return teamconfig.load_name(project_dir) or Path(project_dir).name


def read_config(project_dir) -> dict:
    """Current config for the project WITHOUT writing anything. Defaults if absent. `name` surfaces
    the workspace identity (persisted name, else the folder name) for the UI's cockpit label."""
    cfg = teamconfig._config_path(project_dir)
    name = _workspace_label(project_dir)
    if cfg.exists():
        return {"agents": teamconfig.load_team(project_dir),
                "layout": teamconfig.load_layout(project_dir), "name": name}
    return {"agents": teamconfig.default_team(), "layout": "main-vertical", "name": name}


def save_config(project_dir, count: int, layout: str, providers=None, mode: str = "standard",
                models=None, efforts=None, name=None) -> dict:
    """Build + persist the team.config for this project (per-agent provider/model/thinking). A non-blank
    `name` is persisted as the workspace identity (FIX #4); the returned `name` is the resolved label."""
    agents = teamconfig.build_team(count, providers, models, efforts)
    teamconfig.write_team(project_dir, agents, layout, mode)
    if name:
        teamconfig.set_name(project_dir, name)
    return {"ok": True, "agents": agents, "layout": layout, "mode": mode,
            "name": _workspace_label(project_dir)}


def _mk_exe() -> str:
    """The `mk` the cockpit .cmd invokes. Frozen: the single MKCREW.exe IS `mk` (argv dispatch in
    _run.py), so there's no separate mk.exe next to it — re-invoke the exe itself."""
    from . import frozen
    return sys.executable if frozen.is_frozen() else str(Path(sys.executable).parent / "mk.exe")


def is_session_running() -> bool:
    """True if a 'mkcrew' psmux session already exists."""
    from .psmux import PsmuxBackend
    return PsmuxBackend()._run("has-session", "-t", "mkcrew").returncode == 0


# Shrinks ONLY this console's font (Win32 SetCurrentConsoleFontEx) so the cockpit gets far more
# rows/cols -> readable panes at 4+ agents. Does NOT touch the OS display or any other terminal.
_CONSOLE_FONT_PS1 = r'''try {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class CF {
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetCurrentConsoleFontEx(IntPtr h, bool b, ref CONSOLE_FONT_INFOEX f);
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct CONSOLE_FONT_INFOEX {
    public uint cbSize; public uint nFont; public short FontWidth; public short FontHeight;
    public int FontFamily; public int FontWeight;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string FaceName; }
}
"@
$f = New-Object CF+CONSOLE_FONT_INFOEX
$f.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($f)
$f.FontHeight = 12        # px -- smaller = more content per pane (tune to taste; Windows default ~16)
$f.FaceName = "Consolas"
$f.FontWeight = 400
[CF]::SetCurrentConsoleFontEx([CF]::GetStdHandle(-11), $false, [ref]$f) | Out-Null
} catch {}
'''


def _write_cockpit_cmd(project_dir, mk: str, running: bool) -> Path:
    """Write the .cmd the cockpit console runs: shrink the console font, cd into the project, start
    the team (unless a session is already up), then attach.  Driving a SCRIPT FILE (vs an inline
    `cmd /k "..."`) avoids the Windows quote-mangling that turned the quoted mk path into a literal."""
    runtime = config.runtime_root() / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    font = runtime / "set_console_font.ps1"
    font.write_text(_CONSOLE_FONT_PS1, encoding="utf-8")
    bat = runtime / "launch_cockpit.cmd"
    msg = ("Re-applying your config -- rebuilding the cockpit (agents resume)..." if running
           else "Starting MKCREW cockpit -- building the team, this can take ~20s...")
    lines = ["@echo off", f'cd /d "{Path(project_dir)}"',
             f'powershell -NoProfile -ExecutionPolicy Bypass -File "{font}"',   # smaller font -> bigger grid
             f"echo  {msg}", "echo."]
    if running:
        lines.append(f'"{mk}" kill')     # tear down the live session so the just-saved config applies
    lines.append(f'"{mk}" start --no-attach')   # build only — Studio attaches explicitly next
    lines.append(f'"{mk}" attach')
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return bat


def _running_project():
    """The project path whose cockpit is currently live (or None) — from the marker `mk start` wrote."""
    try:
        return config.cockpit_project_file().read_text(encoding="utf-8").strip() or None
    except (OSError, FileNotFoundError):
        return None


def launch(project_dir, force=False) -> dict:
    """Open ONE visible terminal that starts the team (if needed) then attaches to the cockpit,
    so the user lands IN their workspace. If a session is already running, just attach.
    Drives a generated .cmd script to avoid Windows quote-mangling of the mk path.

    GUARD: MKCREW is single-instance (one psmux session), so launching a 2nd project would tear the
    first down. If a DIFFERENT project's cockpit is live, return {conflict: True} WITHOUT touching it
    (the UI confirms first); `force=True` proceeds with the replace."""
    mk = _mk_exe()
    running = is_session_running()
    if running and not force:
        other = _running_project()
        if other and Path(other).resolve() != Path(project_dir).resolve():
            return {"ok": False, "conflict": True, "running_project": other}
    bat = _write_cockpit_cmd(project_dir, mk, running)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 3   # SW_MAXIMIZE — fill the display so cockpit panes are as large as the screen allows
    # Force the CLASSIC console host (conhost) so the cockpit's adaptive font (SetCurrentConsoleFontEx)
    # actually applies — Windows Terminal, the Win11 default, silently IGNORES that API, which is why
    # panes looked uniformly cramped. conhost.exe ships in System32; fall back to bare cmd if it's gone.
    inner = ["cmd", "/k", str(bat)]
    launcher = ["conhost.exe", *inner] if shutil.which("conhost.exe") else inner
    subprocess.Popen(launcher,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                     startupinfo=si)
    return {"ok": True, "was_running": running}


def kill() -> dict:
    """Stop the running cockpit: `mk kill` tears down the psmux session + daemon. MKCREW is
    single-instance, so this stops whichever project's cockpit is currently live."""
    try:
        subprocess.run([_mk_exe(), "kill"],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       timeout=30, capture_output=True)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


import os as _os

_PROJECT = {"path": str(Path.cwd())}


def get_project() -> str:
    return _PROJECT["path"]


def set_project(path: str) -> dict:
    p = Path(path)
    if not p.is_dir():
        return {"ok": False, "error": "not a directory"}
    _PROJECT["path"] = str(p)
    return {"ok": True, "path": str(p)}


# PowerShell for pick_folder(): COM-create the MODERN Common Item Dialog — IFileOpenDialog with the
# FOS_PICKFOLDERS option — i.e. the resizable "Select Folder" dialog that HAS an address bar + a path
# box, so the user can TYPE or PASTE a directory (the old FolderBrowserDialog was a tree with neither).
# Dependency-free: Add-Type compiles a tiny C# COM shim (no pywin32/comtypes). The unused IFileDialog
# vtable slots are declared only to keep the method ORDER right for the ones we call (Show, SetOptions,
# GetOptions, GetResult). Cancel = Show() returns non-zero -> print nothing. If the modern dialog can't
# be built (Add-Type/COM throws), fall back to the old FolderBrowserDialog so the picker never hard-fails.
_PICK_FOLDER_PS = r'''
$cs = @'
using System;
using System.Runtime.InteropServices;
public static class MkFolderPicker {
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItem {
    void BindToHandler(IntPtr pbc, [MarshalAs(UnmanagedType.LPStruct)] Guid bhid, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string ppsz);
    void GetAttributes(uint mask, out uint attribs);
    void Compare(IShellItem psi, uint hint, out int order);
  }
  [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IFileOpenDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint c, IntPtr types);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint i);
    void Advise(IntPtr e, out uint c);
    void Unadvise(uint c);
    void SetOptions(uint fos);
    void GetOptions(out uint fos);
    void SetDefaultFolder(IShellItem i);
    void SetFolder(IShellItem i);
    void GetFolder(out IShellItem i);
    void GetCurrentSelection(out IShellItem i);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string n);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string n);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    void GetResult(out IShellItem i);
    void AddPlace(IShellItem i, int a);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string e);
    void Close(int hr);
    void SetClientGuid([MarshalAs(UnmanagedType.LPStruct)] Guid g);
    void ClearClientData();
    void SetFilter(IntPtr f);
    void GetResults(out IntPtr items);
    void GetSelectedItems(out IntPtr items);
  }
  [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialog { }
  public static string Pick(string title) {
    var d = (IFileOpenDialog)(new FileOpenDialog());
    uint opts; d.GetOptions(out opts);
    d.SetOptions(opts | 0x20u);            // FOS_PICKFOLDERS -> pick a directory, not a file
    if (title != null) d.SetTitle(title);
    if (d.Show(IntPtr.Zero) != 0) return null;   // non-zero HRESULT == cancelled
    IShellItem it; d.GetResult(out it);
    string p; it.GetDisplayName(0x80058000u, out p);   // SIGDN_FILESYSPATH -> real filesystem path
    return p;
  }
}
'@
try {
  Add-Type -TypeDefinition $cs -ErrorAction Stop
  $p = [MkFolderPicker]::Pick('Select the MKCREW project folder')
  if ($p) { Write-Output $p }
} catch {
  Add-Type -AssemblyName System.Windows.Forms
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = 'Select the MKCREW project folder'; $d.ShowNewFolderButton = $true
  if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }
}
'''


def pick_folder() -> dict:
    """Open the MODERN Windows folder picker (the resizable "Select Folder" Common Item Dialog — it has
    an address bar and a path box, so you can TYPE or PASTE a directory) and set the project to the
    chosen folder. Runs a PowerShell -STA subprocess that COM-creates IFileOpenDialog with FOS_PICKFOLDERS
    (dependency-free — no pywin32/comtypes); falls back to the old FolderBrowserDialog if the modern
    dialog can't be created. Returns set_project(...) on a pick; {ok: False} on cancel/failure (the UI
    falls back to the in-browser folder list)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", _PICK_FOLDER_PS],
                             capture_output=True, text=True, timeout=300)
        path = (out.stdout or "").strip()
    except Exception:
        return {"ok": False, "error": "native picker unavailable"}
    return set_project(path) if path else {"ok": False, "error": "no folder selected"}


def list_dirs(path: str = None) -> dict:
    """List subdirectories of `path` (for the folder picker). Dirs only, sorted."""
    base = Path(path) if path else Path.cwd()
    try:
        dirs = sorted([e.name for e in _os.scandir(base) if e.is_dir() and not e.name.startswith(".")])
    except (OSError, PermissionError):
        dirs = []
    return {"path": str(base), "parent": str(base.parent),
            "dirs": [{"name": n, "path": str(base / n)} for n in dirs]}


def _ui_html() -> str:
    return (Path(__file__).parent / "studio_ui.html").read_text(encoding="utf-8")


def _make_handler(project_dir):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")   # never serve a stale UI/config
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def _html(self, body: str):
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")   # always serve the current UI
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._html(_ui_html())
            elif path == "/api/clis":
                cat = load_catalog()
                self._json(200, {"clis": detect_clis(), "models": cat["models"],
                                 "thinking": cat["thinking"]})
            elif path == "/api/templates":
                self._json(200, {"templates": list_templates()})
            elif path == "/api/modes":
                self._json(200, {"modes": list_modes()})
            elif path == "/api/config":
                self._json(200, read_config(get_project()))
            elif path == "/api/profiles":
                self._json(200, {"profiles": profiles.list_profiles()})
            elif path == "/api/project":
                self._json(200, {"path": get_project()})
            elif path == "/api/dirs":
                from urllib.parse import parse_qs
                qs = parse_qs(urlparse(self.path).query)
                self._json(200, list_dirs(qs.get("path", [None])[0]))
            else:
                self._json(404, {"error": "not found"})
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "bad request"}); return
            path = urlparse(self.path).path
            if path == "/api/config":
                self._json(200, save_config(get_project(), int(body.get("count", 4)),
                                            body.get("layout", "main-vertical"), body.get("providers"),
                                            body.get("mode", "standard"),
                                            body.get("models"), body.get("efforts"), body.get("name")))
            elif path == "/api/profiles":
                profiles.save(body.get("name", "profile"),
                              {"count": int(body.get("count", 4)), "layout": body.get("layout", "main-vertical"),
                               "providers": body.get("providers", []),
                               "models": body.get("models", []), "efforts": body.get("efforts", []),
                               "mode": body.get("mode", "standard"), "wsName": body.get("wsName", "")})
                self._json(200, {"ok": True})
            elif path == "/api/launch":
                self._json(200, launch(get_project(), bool(body.get("force"))))
            elif path == "/api/kill":
                self._json(200, kill())                  # stop the cockpit (mk kill)
            elif path == "/api/models":
                self._json(200, save_catalog(body))     # user-edited model/thinking catalog
            elif path == "/api/pick-folder":
                self._json(200, pick_folder())          # native OS folder dialog
            elif path == "/api/project":
                self._json(200, set_project(body.get("path", "")))
            else:
                self._json(404, {"error": "not found"})
    return H


def make_server(project_dir=None, port: int = 0) -> ThreadingHTTPServer:
    project_dir = project_dir or Path.cwd()
    set_project(str(project_dir))
    return ThreadingHTTPServer(("127.0.0.1", port), _make_handler(project_dir))


def serve(project_dir=None, port: int = 8765, open_browser: bool = True) -> None:
    httpd = make_server(project_dir, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"MKCREW Studio: {url}  (Ctrl-C to stop)")
    if open_browser:
        try: webbrowser.open(url)
        except Exception: pass
    httpd.serve_forever()
