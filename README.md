# MKCREW

**Native-Windows, event-sourced, multi-agent CLI orchestration.** Run a *team* of real coding-agent CLIs
(**Claude Code, codex, opencode, antigravity**) in terminal panes — a **lead** delegates to
**worker / reviewer / planner** agents, and an append-only **event log** drives a live "control tower."

> 100% interactive CLIs — no headless/`-p`, no SDK. Windows-native, built on **psmux** (a tmux clone).

---

## Prerequisites

MKCREW orchestrates tools it does **not** install. Before you start, have:

1. **Windows** + **Python 3.12+** — *or* just **[uv](https://docs.astral.sh/uv/)** (it can provide Python for you).
2. **psmux** on your PATH — the terminal multiplexer MKCREW drives. The installer fetches the **MKCREW
   fork** for you; by hand: `cargo install --git https://github.com/rayngnpc/psmux-mk`. Verify: `psmux -V` → `tmux 3.3.6`.
3. **At least one agent CLI** on PATH: `claude`, `codex`, `opencode`, or `agy` (antigravity).

The installer **checks 2 & 3** and tells you what's missing — it won't silently leave you broken.

---

## Install

### ⚡ One command — no clone, installs everything

```powershell
powershell -c "irm https://raw.githubusercontent.com/rayngnpc/mkcrew/main/install.ps1 | iex"
```

Like a Debian bootstrap: that single line downloads and runs `install.ps1`, which checks and installs
**only what's missing** — uv, Python 3.12, MKCREW, the **psmux fork binary**, Node. Then log into one
agent CLI (`claude`, `codex`, …) and run `mk studio`.

### 🛠 Clone + install (to develop it — editable)

```powershell
git clone https://github.com/rayngnpc/mkcrew
cd mkcrew
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1    # or double-click install.bat
```

`install.ps1` uses **uv** if present (isolated tool-venv + global shims, `--editable` so code edits are
live), otherwise builds a local `.venv` and adds it to your user PATH. Re-runnable.

> Either way: open a **new terminal** afterward and `mk` (plus `mkd`, `mk-core-view`, …) works everywhere —
> no venv activation.

---

## Quickstart

```powershell
mk studio        # browser GUI: pick a folder, team size, per-pane providers, a layout — then Launch the cockpit
```

…or headless:

```powershell
cd path\to\your\project
mk start         # build the cockpit (spawns the daemon + psmux panes for the team)
mk attach        # watch the team; the lead delegates with `mk ask <role> "<task>"`
mk kill          # tear it down (sessions persist; relaunch resumes)
```

## Upgrade / uninstall

```powershell
uv tool upgrade mkcrew       # installed via uv
uv tool uninstall mkcrew
```

(Installed via the PATH fallback instead? Uninstall = remove the `.venv\Scripts` entry from your user PATH.)

---

## Docs & credits

- **Built on** [psmux](https://crates.io/crates/psmux) (Windows tmux clone) — a separate tool, driven via its
  CLI, not vendored.
