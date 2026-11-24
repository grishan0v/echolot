"""`echolot explain`: one detector in words, with the values it runs on here (#494).

An agent that wanted to know why `frame_jank` printed `Max 1572 ms` beside
`longest 89.1 ms` had the whole report guide to read, or the SQL. What it
needed was four things about one detector: what the trace has to hold for it,
what each threshold means and is counted in, the value `analyze` would run it
with in this project and where that value came from, and what each column of
its rows means there.

All four are read, not written here. The words come from the detector's own
header — `@why`, `@needs`, `@param`, `@calibrate`, `@column`, see tp.py —
and the values from the config through `plan_detectors`, the function
`analyze` plans its run with. So a threshold `explain` prints is the one the
next `analyze` uses, and a config `analyze` would refuse is refused here too.

Without a detector it is the list: one line each, the title, and what the
config changed. `--json` is the same in fields, for a script.
"""

from __future__ import annotations

import difflib
import shutil
import sys
import textwrap
from typing import Any

from . import needs as needs_mod
from .tp import Detector


def find(detectors: list[Detector], name: str) -> Detector | None:
    """The detector called `name`, or None."""
    return next((d for d in detectors if d.id == name), None)


def unknown(name: str, detectors: list[Detector]) -> str:
    """What a name that is no detector gets: the nearest ones, then the list.

    The nearest by spelling and by part: `frame_jnk` is `frame_jank`, and
    `jank` or `main_thread` are as likely, the start of a name or a piece of
    it, as a typo.
    """
    ids = [d.id for d in detectors]
    near = [i for i in ids if name and name.lower() in i]
    near += [i for i in difflib.get_close_matches(name, ids, n=3, cutoff=0.6)
             if i not in near]
    said = f"error: no detector '{name}'."
    if near:
        said += f" Did you mean {', '.join(near)}?"
    return f"{said}\nThe detectors: {', '.join(ids)}"


def entries(detectors: list[Detector], plan: list[tuple] | None,
            own: dict[str, dict] | None = None) -> list[dict[str, Any]]:
    """Each detector as `explain --json` gives it.

    `plan` is what `plan_detectors` returned for this config: the detectors
    `analyze` runs, each with its overrides. A detector missing from it is
    one the config turned off. None for no config: every detector, on the
    values it ships with. `own` is the selected scenario's own thresholds
    (`Config.scenario_overrides`), which make a value's source `scenario`
    rather than `config`, as `params_source` has it in a report.
    """
    planned = {d.id: overrides for d, overrides, _ in plan} if plan is not None else None
    own = own or {}
    out = []
    for d in detectors:
        overrides = (planned or {}).get(d.id) or {}
        mine = own.get(d.id) or {}
        calibrated = {c.param: f"{c.expr} * {c.factor:g}" for c in d.calibrations}
        params = []
        for name, default in d.params.items():
            source = ("scenario" if name in mine else "config" if name in overrides
                      else "default")
            params.append({
                "name": name,
                "value": overrides.get(name, default),
                "unit": d.units.get(name, ""),
                "source": source,
                "default": default,
                "meaning": d.meanings.get(name, ""),
                # What `calibrate` derives it from, for a threshold it derives.
                "calibrate": calibrated.get(name),
            })
        out.append({
            "id": d.id,
            "title": d.title,
            "why": d.why,
            "off": planned is not None and d.id not in planned,
            "needs": [{"id": n, "about": needs_mod.SOURCES[n].about} for n in d.needs],
            "params": params,
            "identity": list(d.identity),
            "columns": [{"name": c, "meaning": m} for c, m in d.columns.items()],
        })
    return out


def document(entries_: list[dict[str, Any]], config: str | None,
             scenario: str | None) -> dict[str, Any]:
    """What `--json` prints: where the values came from, and the detectors."""
    return {"config": config, "scenario": scenario, "detectors": entries_}


def _value(p: dict[str, Any]) -> str:
    """A value as the header writes it: a number with its unit, a string as
    itself, quoted, since a mask can begin or end with a space."""
    v = p["value"]
    if isinstance(v, str):
        return f"`{v}`"
    number = f"{v:g}" if isinstance(v, float) else str(v)
    return f"{number} {p['unit']}".strip()


def _changed(e: dict[str, Any]) -> str:
    """What the config did to one detector, for its line in the list."""
    if e["off"]:
        return "off in this config"
    moved = [f"{p['name']} = {_value(p)}" for p in e["params"] if p["source"] != "default"]
    return ("tuned: " + ", ".join(moved)) if moved else ""


def _context(config: str | None, scenario: str | None) -> str:
    if config is None:
        return "at their defaults (no echolot.yml here)"
    return f"as {config} runs them" + (f" for scenario {scenario}" if scenario else "")


def listing(entries_: list[dict[str, Any]], config: str | None,
            scenario: str | None) -> str:
    """One line a detector, and how to ask about one."""
    width = max(len(e["id"]) for e in entries_) + 2
    lines = [f"{len(entries_)} detectors, {_context(config, scenario)}.", ""]
    for e in entries_:
        changed = _changed(e)
        lines.append(f"{e['id']:<{width}}{e['title']}" + (f" · {changed}" if changed else ""))
    lines += ["", "echolot explain <detector> — one of them: what it reads, its "
                  "thresholds with their units and the values in force, and what "
                  "the columns of its rows mean."]
    return "\n".join(lines)


def one(e: dict[str, Any], config: str | None, scenario: str | None,
        width: int | None = None) -> str:
    """One detector, whole. `width` wraps the prose to a terminal; None leaves
    each paragraph on a line of its own, for a pipe and a grep."""

    def para(text: str, indent: str = "") -> list[str]:
        if not width:
            return [indent + text]
        return textwrap.wrap(text, width=width, initial_indent=indent,
                             subsequent_indent=indent) or [indent]

    lines = [f"{e['id']} — {e['title']}", ""]
    lines += para(e["why"][:1].upper() + e["why"][1:])
    if e["off"]:
        lines += [""] + para(f"Turned off in this config (`{e['id']}: false`): "
                             f"`analyze` does not run it.")
    for n in e["needs"]:
        lines += [""] + para(f"Needs {n['id']}: {n['about']}. A trace without it "
                             f"leaves this detector silent, and the report says it "
                             f"could not look.")
    lines.append("")
    if e["params"]:
        lines.append(f"Thresholds, {_context(config, scenario)}:")
        for p in e["params"]:
            facts = ["default" if p["source"] == "default" else
                     f"from {'the scenario' if p['source'] == 'scenario' else 'the config'}, "
                     f"default {_value({**p, 'value': p['default']})}"]
            if p["calibrate"]:
                facts.append(f"calibrate: {p['calibrate']}")
            lines.append(f"  {p['name']} = {_value(p)} · {' · '.join(facts)}")
            lines += para(p["meaning"], "      ")
    else:
        lines.append("No thresholds.")
    lines += ["", f"Columns, a row per {' and '.join(e['identity'])}:"]
    pad = max((len(c["name"]) for c in e["columns"]), default=0) + 2
    for c in e["columns"]:
        if width:
            lines += textwrap.wrap(c["meaning"], width=width,
                                   initial_indent=f"  {c['name']:<{pad}}",
                                   subsequent_indent=" " * (pad + 2))
        else:
            lines.append(f"  {c['name']:<{pad}}{c['meaning']}")
    return "\n".join(lines)


def terminal_width() -> int | None:
    """The width to wrap to on a terminal, at most 100; None for a pipe."""
    if not sys.stdout.isatty():
        return None
    return min(shutil.get_terminal_size((100, 24)).columns, 100) - 1
