<#
================================================================================
  MKCREW - one-click bootstrap & preflight for Windows
--------------------------------------------------------------------------------
  Checks every prerequisite, installs ONLY what is missing (asks Y/N first),
  and never forces anything. Re-runnable (idempotent). User-scope by default -
  no administrator rights required for the normal path.

  Run it (either way):
    * Double-click  install.bat        (easiest - handles the execution policy)
    * powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1

  Flags:
    -Yes         assume "yes" to every install prompt (unattended)
    -CheckOnly   preflight only: report status, install NOTHING (a `mk doctor`)
    -DryRun      print what it WOULD do, change nothing
    -NoUv        use the venv + PATH path instead of a uv tool-install

  One thing this script canNOT do for you: log in to an agent CLI. Vendor logins
  (claude / codex / ...) are interactive OAuth - it installs the CLI and tells
  you the one command to run.
================================================================================
#>
param([switch]$Yes, [switch]$CheckOnly, [switch]$DryRun, [switch]$NoUv)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot   # empty when piped via `irm ... | iex` (no script file on disk)
$FromClone = [bool]$Root -and (Test-Path (Join-Path $Root "pyproject.toml"))  # clone (editable) vs remote one-liner
$script:Missing = @()   # REQUIRED items still missing when we finish
$script:Notes   = @()   # manual follow-ups to print at the end

# --- deployment config: where the psmux FORK binary comes from --------------
#     psmux is a fork with MKCREW-specific fixes; upstream `cargo install psmux`
#     is NOT the same binary. Publish your fork's release, then set these once.
$PSMUX_RELEASE_URL = "https://github.com/rayngnpc/psmux-mk/releases/download/v3.3.6-mk/psmux-3.3.6-mk-win-x64.zip"
$PSMUX_FORK_REPO   = "https://github.com/rayngnpc/psmux-mk"   # cargo fallback: cargo install --git
$PSMUX_MIN_VER     = "3.3.6"
$MKCREW_REPO       = "https://github.com/rayngnpc/mkcrew"   # the fork repo (docs / cargo-style refs)
$MKCREW_TARBALL    = "https://github.com/rayngnpc/mkcrew/archive/refs/heads/main.tar.gz"   # git-FREE install source for the no-clone one-liner (a fresh box has no git)
$BinDir            = Join-Path $env:LOCALAPPDATA "Programs\mkcrew\bin"

# --- pretty output (tech-savvy, colour-coded, robust) -----------------------
# one Write-Host per line (no -NoNewline chains): chained segments split across lines in
# transcripts/CI logs (proven in the sandbox tests). Whole-line color, journalctl-style.
function Rule { Write-Host ("  " + ("-" * 70)) -ForegroundColor DarkGray }
function Sec($t)  { Write-Host ""; Rule; Write-Host "  :: $t" -ForegroundColor Cyan; Rule }
function Ok($t)   { Write-Host "  [ OK ] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [WARN] $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "  [FAIL] $t" -ForegroundColor Red }
function Info($t) { Write-Host "  [ .. ] $t" -ForegroundColor DarkCyan }
function Have($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Ver($n)  { try { (& $n --version 2>$null | Select-Object -First 1) } catch { "" } }

# real console with a live keyboard? (false when piped / CI / ISE -> typed fallbacks)
function Test-Tui { ($Host.Name -eq 'ConsoleHost') -and -not [Console]::IsInputRedirected }

function Ask($q) {
    if ($DryRun)    { Write-Host "  ?  $q  [Y/n] -> skipped (dry-run)" -ForegroundColor Yellow; return $false }
    if ($CheckOnly) { return $false }
    if ($Yes)       { Write-Host "  ?  $q  [Y/n] -> Y (auto)" -ForegroundColor Yellow; return $true }
    Write-Host "  ?  $q" -ForegroundColor Yellow
    if (-not (Test-Tui)) {                                     # piped/CI: plain typed prompt
        $a = Read-Host "     [Y/n]"
        return ($a -eq "" -or $a -match '^(y|yes)$')
    }
    # arrow-key YES/NO toggle on one short line (question stays on its own line above)
    $sel = $true
    [Console]::CursorVisible = $false
    try {
        while ($true) {
            $line = if ($sel) { "     -> [ YES ]    NO       (arrows toggle - Enter confirms - y/n direct)" }
                    else      { "        YES    [ NO ] <-    (arrows toggle - Enter confirms - y/n direct)" }
            $w = [Math]::Max(60, [Math]::Min([Console]::WindowWidth, 120) - 1)
            Write-Host ("`r" + $line.PadRight($w)) -NoNewline -ForegroundColor $(if ($sel) { 'Green' } else { 'Red' })
            $k = [Console]::ReadKey($true)
            switch ($k.Key) {
                { $_ -in 'LeftArrow','RightArrow','UpArrow','DownArrow','Tab' } { $sel = -not $sel }
                'Enter'  { Write-Host ""; return $sel }
                'Y'      { Write-Host ""; return $true }
                'N'      { Write-Host ""; return $false }
                'Escape' { Write-Host ""; return $false }
            }
        }
    } finally { [Console]::CursorVisible = $true }
}

function Select-Mode {
    # arrow-key menu: up/down move, Enter confirms; 1/2/3/Q select instantly. Typed fallback for pipes/CI.
    $items = @(
        @{ k='1'; name='INSTALL'; desc='scan everything, install what is missing'; note='confirms each';   c='White'    }
        @{ k='2'; name='AUDIT';   desc='read-only preflight report';               note='changes nothing'; c='Green'    }
        @{ k='3'; name='AUTO';    desc='unattended install of ALL missing';        note='zero prompts';    c='Yellow'   }
        @{ k='Q'; name='QUIT';    desc='';                                         note='';                c='DarkGray' }
    )
    if (-not (Test-Tui)) {
        foreach ($it in $items) { Write-Host ("    [{0}]  {1,-8} {2,-42} {3}" -f $it.k, $it.name, $it.desc, $it.note) -ForegroundColor $it.c }
        return (Read-Host "  mode [1/2/3/q]")
    }
    foreach ($it in $items) { Write-Host "" }                  # reserve the rows (handles buffer scroll)
    $top = [Console]::CursorTop - $items.Count
    $idx = 0
    [Console]::CursorVisible = $false
    try {
        while ($true) {
            [Console]::SetCursorPosition(0, $top)
            $w = [Math]::Max(70, [Math]::Min([Console]::WindowWidth, 120) - 1)
            for ($i = 0; $i -lt $items.Count; $i++) {
                $it  = $items[$i]
                $row = ("    [{0}]  {1,-8} {2,-42} {3}" -f $it.k, $it.name, $it.desc, $it.note).PadRight($w)
                if ($i -eq $idx) { Write-Host ("  > " + $row.Substring(4)) -ForegroundColor Black -BackgroundColor DarkCyan }
                else             { Write-Host $row -ForegroundColor $it.c }
            }
            $k = [Console]::ReadKey($true)
            switch ($k.Key) {
                'UpArrow'   { $idx = ($idx + $items.Count - 1) % $items.Count }
                'DownArrow' { $idx = ($idx + 1) % $items.Count }
                'Enter'     { return $items[$idx].k }
            }
            $ch = ([string]$k.KeyChar).ToUpper()
            $hit = @($items | Where-Object { $_.k -eq $ch })
            if ($hit.Count) { return $hit[0].k }
        }
    } finally { [Console]::CursorVisible = $true }
}

function Run($desc, [scriptblock]$block) {
    if ($DryRun) { Info "DRYRUN would: $desc"; return $true }
    Info $desc
    # Native tools (uv/cargo/npm) write progress to STDERR. In PS 5.1, unredirected stderr never reaches
    # transcripts/logs (failures showed NO reason), and under $ErrorActionPreference=Stop a stderr line can
    # abort the block MID-INSTALL. Relax EAP for the block and merge 2>&1 so every line is visible + logged.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try   { & $block 2>&1 | ForEach-Object { Write-Host "    $_" }; return $true }
    catch { Bad "$desc  ->  $($_.Exception.Message)"; return $false }
    finally { $ErrorActionPreference = $prevEAP }
}

function Add-UserPath($dir) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $cur = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($cur -split ';') -notcontains $dir) {
        $new = if ([string]::IsNullOrEmpty($cur)) { $dir } else { "$cur;$dir" }
        [Environment]::SetEnvironmentVariable("Path", $new, "User")
        Ok "added to your user PATH: $dir"
    }
    if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$env:Path;$dir" }  # this session too
}

function Banner {
    Write-Host ""
    Write-Host "  ========================================================================" -ForegroundColor DarkCyan
    Write-Host "   M K C R E W  ::  multi-agent CLI cockpit  //  native Windows" -ForegroundColor Cyan
    Write-Host "   bootstrap + preflight  ::  detect -> report -> ask -> install" -ForegroundColor DarkGray
    Write-Host "  ========================================================================" -ForegroundColor DarkCyan
}

# --- installers (each: user-scope, best-effort, honest on failure) ----------
function Install-Uv {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uvbin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path (Join-Path $uvbin "uv.exe")) { Add-UserPath $uvbin }
}
function Install-Rust {
    $ri = Join-Path $env:TEMP "rustup-init.exe"
    Invoke-RestMethod https://win.rustup.rs/x86_64 -OutFile $ri
    & $ri -y --no-modify-path
    Add-UserPath (Join-Path $env:USERPROFILE ".cargo\bin")
}
function Install-PsmuxBinary($url) {
    $zip = Join-Path $env:TEMP "psmux-mkcrew.zip"
    $tmp = Join-Path $env:TEMP "psmux-mkcrew"
    Invoke-RestMethod $url -OutFile $zip
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $exe = Get-ChildItem -Recurse -Path $tmp -Filter "psmux.exe" | Select-Object -First 1
    if (-not $exe) { throw "psmux.exe not found inside the release archive" }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Copy-Item $exe.FullName (Join-Path $BinDir "psmux.exe") -Force
    Add-UserPath $BinDir
}

# ============================================================================
Banner

# --- MENU (skipped when a flag already chose the mode) ----------------------
if (-not $Yes -and -not $CheckOnly -and -not $DryRun) {
    Write-Host ""
    Write-Host "    arrows move - Enter confirms - or press 1/2/3/q     flags: -CheckOnly -Yes -DryRun -NoUv" -ForegroundColor DarkGray
    Write-Host ""
    $c = Select-Mode
    switch -Regex ($c) {
        '^2$'    { $script:CheckOnly = $true }
        '^3$'    { $script:Yes = $true }
        '^[Qq]$' { Write-Host "  bye."; return }
        default  { }   # 1 / Enter / anything else -> interactive install
    }
}

# --- ENVIRONMENT ------------------------------------------------------------
Sec "Environment"
$psv = $PSVersionTable.PSVersion
if ($psv.Major -ge 5) { Ok "PowerShell $psv" } else { Warn "PowerShell $psv (5.1+ recommended)" }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if ($admin) { Info "running as Administrator (fine, but not required)" }
else        { Info "running as a normal user (user-scope install - no admin needed)" }

# uv's installer (and other .ps1 tools) refuse to run unless the EFFECTIVE policy is
# Unrestricted/RemoteSigned/Bypass. Windows ships 'Restricted' -> fix it, user-scope, NO admin/UAC.
$pol = (Get-ExecutionPolicy).ToString()
if ($pol -in @('Unrestricted','RemoteSigned','Bypass')) {
    Ok "execution policy: $pol"
} else {
    Warn "execution policy '$pol' blocks PowerShell installer scripts (uv's included)."
    if (-not $DryRun -and -not $CheckOnly) {
        # transient rescue so THIS run always works: process scope dies with this window, changes nothing
        try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; Info "applied Bypass for THIS run only (process scope - transient, no admin)" } catch {}
    }
    if (Ask "Set policy 'RemoteSigned' for your user account? (persistent, no admin/UAC - what uv recommends)") {
        try { Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force; Ok "execution policy now: RemoteSigned (CurrentUser)" }
        catch { Bad "could not set policy: $($_.Exception.Message)"; $script:Notes += "policy: run  Set-ExecutionPolicy RemoteSigned -Scope CurrentUser" }
    } else {
        $script:Notes += "execution policy kept at '$pol': THIS run works, but future installer scripts may refuse. fix anytime:  Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
    }
}

if (Have "conhost.exe") { Ok "conhost.exe present (needed for the cockpit's adaptive font)" }
else { Warn "conhost.exe not found - the cockpit still runs, but font sizing may not apply" }

# --- CORE RUNTIME: uv -> Python -> MKCREW -----------------------------------
Sec "Core runtime (uv, Python, MKCREW)"

if (Have "uv") {
    Ok "uv present  ($(Ver uv))"
} elseif ($NoUv) {
    Info "uv skipped (-NoUv). Will use the venv + PATH path (needs a system Python 3.12+)."
} else {
    Warn "uv (the Python/tool manager - it also fetches Python for you) is not installed."
    if (Ask "Install uv? (user-scope, no admin)") { Run "install uv" { Install-Uv } }
}

if ((Have "uv") -and -not $NoUv) {
    # uv tool-install builds an isolated, GLOBAL install + auto-provisions Python 3.12. Source: a local
    # clone (editable) or GitHub's TARBALL (no-clone one-liner). Tarball -- NOT git+ -- so a blank machine
    # with no `git` still installs (uv needs system git for git+ sources; a fresh box has none).
    # NO splatting here: `$x = if(...){ @(1item) }` unwraps to a STRING, and splatting a string passes it
    # CHAR-BY-CHAR to the native exe (uv saw 'h','t','t','p',...) -- the sandbox-test failure. Explicit branches.
    $mkDesc = if ($FromClone) { "uv tool install --editable . --force" } else { "uv tool install <mkcrew tarball> --force" }
    $already = [bool](Have "mk")
    if ($already) { Ok "MKCREW already installed  (mk on PATH: $((Get-Command mk).Source))" }
    $go = if ($already) { Ask "Reinstall/upgrade MKCREW?" } else { Ask "Install MKCREW now ($mkDesc ; pulls Python 3.12 + textual)?" }
    if ($go) {
        $null = Run $mkDesc {
            if ($FromClone) { & uv tool install --editable "$Root" --force }
            else            { & uv tool install "$MKCREW_TARBALL" --force }
            & uv tool update-shell
        }
        # VERIFY the `mk` shim actually exists -- uv can print success yet fail (e.g. a git+ source with no
        # git), and a native non-zero exit does NOT throw in PS 5.1. Trust the shim on disk, not the exit code.
        if (-not $DryRun) {
            $tb = (& uv tool dir --bin 2>$null | Out-String).Trim()
            if ($tb -and (Test-Path (Join-Path $tb "mk.exe"))) {
                Add-UserPath $tb                              # ensure uv's tool-bin (mk's home) is on PATH
                Ok "MKCREW installed (uv). Uninstall later: uv tool uninstall mkcrew"
            } else {
                Bad "uv reported done but 'mk' was not produced -- the install did not complete."
                $script:Missing += "mkcrew"
            }
        }
    } elseif (-not $already) { $script:Missing += "mkcrew" }
} else {
    # Fallback: venv + user PATH (needs a system Python).
    $venv = Join-Path $Root ".venv"; $scripts = Join-Path $venv "Scripts"; $py = Join-Path $scripts "python.exe"
    if (Test-Path $py) { Ok "project venv present" }
    elseif (Have "py") {
        if (Ask "Create the project venv + editable install (needs network)?") {
            Run "py -3 -m venv + pip install -e ." { & py -3 -m venv $venv; & $py -m pip install -e "$Root" }
        }
    } else {
        Bad "No uv and no system Python. Install uv (recommended) or Python 3.12+, then re-run."
        $script:Missing += "python/uv"
    }
    if (Test-Path $scripts) { Add-UserPath $scripts }
}

# --- COCKPIT ENGINE: psmux (the FORK) ---------------------------------------
Sec "Cockpit engine (psmux - the MKCREW fork)"
if (Have "psmux") {
    Ok "psmux present  ($(Ver psmux))   (expected fork >= $PSMUX_MIN_VER)"
} else {
    Warn "psmux is NOT on PATH. The cockpit cannot run without it, and it must be the MKCREW FORK (not upstream)."
    $done = $false
    if ($PSMUX_RELEASE_URL -and (Ask "Download the psmux fork binary and add it to PATH? (no Rust needed)")) {
        $done = Run "download psmux fork -> $BinDir" { Install-PsmuxBinary $PSMUX_RELEASE_URL }
    }
    if (-not $done -and $PSMUX_FORK_REPO) {
        if (-not (Have "cargo") -and (Ask "Install Rust (rustup) so psmux can be built from source?")) {
            Run "install rustup" { Install-Rust } | Out-Null
        }
        if ((Have "cargo") -and (Ask "Build psmux from the fork with cargo?")) {
            $done = Run "cargo install --git $PSMUX_FORK_REPO --force" { & cargo install --git $PSMUX_FORK_REPO --force }
        }
    }
    if (-not $done) {
        Bad "psmux still missing."
        $script:Notes += "psmux: set `$PSMUX_RELEASE_URL (a release .zip of the fork) OR `$PSMUX_FORK_REPO at the top of install.ps1, then re-run. Verify with:  psmux -V"
        $script:Missing += "psmux"
    }
}

# --- NODE.JS (opencode / codex plugins / npm-installed CLIs) -----------------
Sec "Node.js (used by opencode, codex plugins, and npm-installed CLIs)"
if (Have "node") {
    Ok "node present  ($(Ver node))"
} else {
    Warn "Node.js not found - opencode and some CLIs need it."
    if (Have "winget") {
        if (Ask "Install Node LTS via winget? (winget may prompt for admin)") {
            Run "winget install OpenJS.NodeJS.LTS" { & winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements }
        } else { $script:Notes += "Node.js: install later from https://nodejs.org/ (LTS)." }
    } else {
        $script:Notes += "Node.js: winget not present - install LTS from https://nodejs.org/ then re-run."
    }
}

# --- AGENT CLIs (need >= 1; each needs its own login) -----------------------
Sec "Agent CLIs (need at least one; login is interactive)"
$agents = @("claude","codex","opencode","agy") | Where-Object { Have $_ }
if ($agents) {
    Ok ("found: " + ($agents -join ", "))
} else {
    Warn "No agent CLI found (claude / codex / opencode / agy) - the team has nothing to run."
    if (Have "node") {
        if (Ask "Install the Claude Code CLI now (npm i -g @anthropic-ai/claude-code)?") {
            if (Run "npm i -g @anthropic-ai/claude-code" { & npm install -g "@anthropic-ai/claude-code" }) {
                $script:Notes += "Claude installed - run `claude` ONCE to log in (interactive; no script can do this for you)."
            }
        } else { $script:Missing += "agent-cli" }
    } else {
        $script:Notes += "Install an agent CLI after Node, e.g.  npm i -g @anthropic-ai/claude-code  (then run `claude` to log in)."
        $script:Missing += "agent-cli"
    }
    $script:Notes += "Other CLIs: codex, opencode, agy - install per their docs; each is auto-detected on next run."
}

# --- SUMMARY ----------------------------------------------------------------
Sec "Summary"
$req = $script:Missing | Sort-Object -Unique
if ($req.Count -eq 0) {
    Ok "All required prerequisites are in place."
    if ($CheckOnly) { Write-Host "  (check-only - nothing was installed)" -ForegroundColor DarkGray }
    else { Write-Host ""; Write-Host "  next ->  open a NEW terminal, then run:  mk studio" -ForegroundColor Green }
} else {
    Bad ("Still missing (required): " + ($req -join ", "))
    Write-Host "  Re-run after resolving, or use the notes below." -ForegroundColor DarkGray
}
if ($script:Notes.Count -gt 0) {
    Write-Host ""
    Write-Host "  Follow-ups:" -ForegroundColor Cyan
    foreach ($n in ($script:Notes | Select-Object -Unique)) { Write-Host "   - $n" -ForegroundColor Gray }
}
Write-Host ""
Rule
Write-Host "  re-check anytime ->  .\install.bat  (mode 2: AUDIT)   or   mk doctor" -ForegroundColor Cyan
Rule
