<#
  Build the MKCREW desktop app into a single double-click MKCREW.exe (option 1 of app/browser/CLI).

  The .exe is "busybox" style: no args -> the app window; "MKCREW.exe mkd|core-view|done|<mk subcmd>"
  -> that internal tool (so the cockpit it spawns can re-invoke the same exe). psmux is bundled INSIDE.

  Prereqs (one-time, into the project venv):  .venv\Scripts\python -m pip install pyinstaller pywebview
  Run:     powershell -ExecutionPolicy Bypass -File build_app.ps1
  Output:  dist\MKCREW.exe   (still needs >=1 agent CLI on PATH: claude/codex/opencode/agy)
#>
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pkg  = Join-Path $root "src\mkcrew"

$psmux = (Get-Command psmux -ErrorAction SilentlyContinue).Source
if (-not $psmux) { Write-Warning "psmux not on PATH - building WITHOUT a bundled psmux (users must install it)." }

# Console build (NOT --windowed): the cockpit core/agent panes are this same exe re-invoked and must
# render to their psmux pane console. The app's own console sits behind the window (polish later).
$icon = Join-Path $root "assets\mkcrew.ico"
$piArgs = @(
  "--onefile", "--name", "MKCREW", "--noconfirm", "--clean",
  "--icon", "$icon",
  "--add-data", "$pkg\studio_ui.html;mkcrew",
  "--add-data", "$pkg\skills;mkcrew\skills",
  "--collect-submodules", "mkcrew",
  "--collect-all", "webview"
)
if ($psmux) { $piArgs += @("--add-binary", "$psmux;.") }
$piArgs += (Join-Path $pkg "_run.py")

$pyi = if (Test-Path "$root\.venv\Scripts\pyinstaller.exe") { "$root\.venv\Scripts\pyinstaller.exe" } else { "pyinstaller" }
& $pyi @piArgs

$exe = Join-Path $root "dist\MKCREW.exe"
Write-Host "`nBuilt: $exe"
Write-Host "Smoke it:  $exe --check"
Write-Host "Run it:    $exe"
