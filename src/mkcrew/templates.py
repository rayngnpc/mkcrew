"""Single source of truth for the workspace templates users pick.

Two groups the pickers show:
  - "normal":       core pane (control tower) + agent panes. NO file explorer/editor.
  - "experimental": adds the files-IDE pane (core | explorer | editor).

Every consumer reads from here so the grouping can't drift across surfaces:
  addworkspace.py (wizard / Ctrl-b A), studio.py + studio_ui.html (web/app),
  cli.py (mk add / mk start), layouts.py (builders).

`key` doubles as the layout key persisted in team.config AND the builder key in
layouts.LAYOUTS. Keep this module a leaf (stdlib only) so the frozen wizard
popup, Studio, cli and layouts can all import it without cycles.
"""

from dataclasses import dataclass, asdict

NORMAL = "normal"
EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    group: str          # NORMAL | EXPERIMENTAL
    files_ide: bool     # True -> files-IDE pane; False -> core pane + agents
    desc: str
    add_capable: bool = True   # offered by `mk add` (the wizard). pages/dashboard are mk-start/Studio only.
    min_agents: int = 1
    max_agents: int = 8

    def to_dict(self):
        return asdict(self)


# Order = display order in the pickers.
TEMPLATES = [
    Template("main-vertical", "LEAD LEFT", NORMAL, False,
             "Lead top-left, workers stacked on the right, core status bottom-left."),
    Template("even-horizontal", "Side by Side", NORMAL, False,
             "Agents in side-by-side columns with a core status strip."),
    Template("lead-left-ide", "LEAD LEFT + Files IDE", EXPERIMENTAL, True,
             "LEAD LEFT plus the files IDE pane (core | explorer | editor). Experimental."),
    # Studio / mk-start only (mk add can't build paged grids): core-based, no files-IDE.
    Template("pages", "Pages", NORMAL, False,
             "Agents across paged grid windows, each with a core strip.", add_capable=False),
]

_BY_KEY = {t.key: t for t in TEMPLATES}


def get(key):
    return _BY_KEY.get(key)

def all_templates():
    return list(TEMPLATES)

def by_group(group):
    return [t for t in TEMPLATES if t.group == group]

def keys():
    return [t.key for t in TEMPLATES]

def wizard_templates():
    """Templates the `mk add` wizard can build."""
    return [t for t in TEMPLATES if t.add_capable]

def includes_files_ide(key):
    t = _BY_KEY.get(key)
    return bool(t and t.files_ide)


if __name__ == "__main__":
    # ponytail: guards the data itself (unique keys, valid groups, the split holds)
    assert len(keys()) == len(set(keys())), "duplicate template keys"
    assert all(t.group in (NORMAL, EXPERIMENTAL) for t in TEMPLATES), "bad group"
    assert includes_files_ide("lead-left-ide") is True
    assert includes_files_ide("main-vertical") is False
    assert [t.key for t in by_group(EXPERIMENTAL)] == ["lead-left-ide"], "exactly one experimental"
    assert all(t.add_capable for t in wizard_templates())
    print("templates.py OK:", ", ".join(f"{t.key}[{t.group}{'+ide' if t.files_ide else ''}]" for t in TEMPLATES))
