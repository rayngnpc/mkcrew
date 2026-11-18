"""Single source of truth for the workspace templates users pick.

Every consumer reads from here so the offered set can't drift across surfaces:
  addworkspace.py (wizard / Ctrl-b A), studio.py + studio_ui.html (web/app),
  cli.py (mk add / mk start), layouts.py (builders).

`key` doubles as the layout key persisted in team.config AND the builder key in
layouts.LAYOUTS. Keep this module a leaf (stdlib only) so the frozen wizard
popup, Studio, cli and layouts can all import it without cycles.
"""

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    desc: str
    add_capable: bool = True   # offered by `mk add` (the wizard).
    min_agents: int = 1
    max_agents: int = 4      # per-template fit ceiling: single-window layouts stay readable to ~4 agents;
                             # paged layouts (pages) raise this. Pickers allow up to 6 -- the fit-sort
                             # steers oversize teams toward the templates whose range actually holds them.

    def to_dict(self):
        return asdict(self)


# Order = display order in the pickers.
TEMPLATES = [
    Template("main-vertical", "LEAD LEFT",
             "Lead top-left, workers stacked on the right, tower bottom-left."),
    Template("even-horizontal", "Side by Side",
             "Agents in side-by-side columns over a tower strip."),
    # pages scales: the builder grids up to 6 agents per window (_PER_PAGE) + a core strip per tab,
    # so it's the template that actually FITS a 5-6 agent team; <=6 agents stays a single page.
    Template("pages", "Pages",
             "Agents across paged grid windows, each with a tower strip.",
             max_agents=6),
]

_BY_KEY = {t.key: t for t in TEMPLATES}

RETIRED = {"lead-left-ide": "main-vertical"}


def resolve(key):
    """Return the successor key for a retired layout key, or `key` itself otherwise."""
    return RETIRED.get(key, key)


def get(key):
    return _BY_KEY.get(key)

def all_templates():
    return list(TEMPLATES)

def keys():
    return [t.key for t in TEMPLATES]

def wizard_templates():
    """Templates the `mk add` wizard can build."""
    return [t for t in TEMPLATES if t.add_capable]


if __name__ == "__main__":
    # ponytail: guards the data itself (unique keys, retired mapping, wizard set)
    assert len(keys()) == len(set(keys())), "duplicate template keys"
    assert resolve("lead-left-ide") == "main-vertical"
    assert resolve("main-vertical") == "main-vertical"
    assert all(t.add_capable for t in wizard_templates())
    print("templates.py OK:", ", ".join(t.key for t in TEMPLATES))
