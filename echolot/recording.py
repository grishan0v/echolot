"""How a set was recorded, carried into its report (#533).

A comparison subtracts two sets of recordings, and the subtraction is only
about the app when the recordings were made the same way. Two things decide
that, and neither was in `report.json`:

  * what the recording asked the device for — its data sources, its atrace
    categories, its ftrace events. A set recorded by a Macrobenchmark with its
    own trace config, against one `collect` recorded with echolot's, gave
    `compare` 44 rows that held and 185 that were gone, and a reader had no way
    to tell which of them moved because the recording changed. The trace
    carries this itself: the tracing service writes the config at its head,
    and trace_processor prints it back as `trace_config_pbtxt`. So it is read
    for every trace, old or new.

  * how the repeats were driven — which runner, and what state the app was in
    when each one began. The same APK on an SM-A515F, recorded on two days
    with the same config: one day a plain `collect`, whose force-stop found
    the app still running from the repeat before it in 14 of 15 repeats; the
    other day a script stopped the app first and ran `collect -n 1` fifteen
    times, so the stop found it gone every time. Every start was cold on both
    days, the clock and the throttling matched, and the window came out
    0.68 s shorter on the second, with 60 rows that held and no warning. A
    start half a second after a kill shares the device with the system
    cleaning up the process it killed. None of this is in the trace, so
    `collect` writes it beside each trace it records (`write`), and `analyze`
    reads it back (`read`).

The block is a list of conditions (`CONDITIONS`), each with a key in
`report.json`, how a set's repeats combine, and how `compare` words a
difference. A new fact about the recording — the device's animation scales,
"Don't keep activities" — is one more entry here and one more field in what
`collect` writes; `merge` and `moved` need no change for it.

A condition no repeat could say is `None`: unknown, never "the same". A
report written before the block existed has no `recording` at all, and
`compare` says that the recordings could not be checked rather than that
they matched.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Beside the trace, under its whole name: `startup_iter000.perfetto-trace`
# gets `startup_iter000.perfetto-trace.recording.json`. A glob for traces
# does not match it, and a trace moved with its sidecar keeps its account.
SIDECAR_SUFFIX = ".recording.json"


@dataclass(frozen=True)
class Condition:
    """One fact about how a recording was made.

    `source` is where it comes from: `trace` for what the recording's own
    config says, `collect` for what `collect` wrote beside it.

    `per_repeat` is a fact that may differ repeat by repeat without anything
    being wrong: the first repeat of a `collect` finds the app as the last
    session left it, every later one as the repeat before it left it. Such a
    set is taken by what most of its repeats did, and its own spread is not
    a warning. Any other condition is one a set should share, and a set whose
    repeats do not is said.

    `differs` says what is different between the two sides' blocks, for a
    condition the generic "`x` before, `y` after" says less about than it
    could; it is handed both blocks, so that it can say how many repeats.
    """
    key: str
    label: str
    source: str
    per_repeat: bool = False
    differs: Callable[[dict[str, Any], dict[str, Any]], str] | None = None


def _code(value: Any) -> str:
    return f"`{value}`"


def _listed(items: list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items)


def _sets(key: str, label: str) -> Callable[[dict[str, Any], dict[str, Any]], str]:
    """What a list-valued condition has on one side only, named."""
    def differs(rb: dict[str, Any], ra: dict[str, Any]) -> str:
        b, a = set(rb.get(key) or ()), set(ra.get(key) or ())
        bits = []
        if b - a:
            bits.append(f"only before {_listed(sorted(b - a))}")
        if a - b:
            bits.append(f"only after {_listed(sorted(a - b))}")
        return f"{label}, " + " and ".join(bits)
    return differs


def _repeats(block: dict[str, Any], key: str) -> str:
    """`14 of 15` repeats for the value a set mostly had, `every` when all did."""
    for entry in (block.get("mixed") or {}).get(key) or ():
        if _frozen(entry["value"]) == _frozen(block.get(key)):
            k, _, n = str(entry["runs"]).partition("/")
            return f"in {k} of {n} repeats"
    return "in every repeat"


def _app_at_stop(rb: dict[str, Any], ra: dict[str, Any]) -> str:
    def side(name: str, block: dict[str, Any]) -> str:
        if block.get("app_at_stop") == "running":
            return (f"{name}, the force-stop found the app still running "
                    f"{_repeats(block, 'app_at_stop')} and killed it")
        return (f"{name}, the force-stop found the app already stopped "
                f"{_repeats(block, 'app_at_stop')}")
    return f"{side('before', rb)}; {side('after', ra)}"


CONDITIONS: tuple[Condition, ...] = (
    Condition("runner", "runner", "collect"),
    Condition("reset_policy", "reset policy", "collect"),
    Condition("app_at_stop", "the app at the stop", "collect", per_repeat=True,
              differs=_app_at_stop),
    Condition("data_sources", "data sources", "trace",
              differs=_sets("data_sources", "data sources")),
    Condition("atrace_categories", "atrace categories", "trace",
              differs=_sets("atrace_categories", "atrace categories")),
    Condition("ftrace_events", "ftrace events", "trace",
              differs=_sets("ftrace_events", "ftrace events")),
)

KEYS = tuple(c.key for c in CONDITIONS)
_FROM_COLLECT = tuple(c.key for c in CONDITIONS if c.source == "collect")
# What else `collect` keeps beside a trace, for the report rather than for
# `compare`'s conditions, each read on its own (`kept`): `cooldown`, the
# `runner.cooldown` the repeat waited for and the reading before it, or null
# when it was off (#522); `markers_in_build`, the temporary markers `collect`
# found in the build on the phone before it recorded (#536).
_ALSO = ("cooldown", "markers_in_build")

# What a detector's `@needs` source is fed by in the recording's config, so
# that a difference there names the detectors that read it. Composable names
# need the app to write them as well, but a recording without `track_event`
# gets none whatever the app does.
FEEDS: dict[tuple[str, str], str] = {
    ("ftrace_events", "sched/sched_blocked_reason"): "platform_state",
    ("data_sources", "android.surfaceflinger.frametimeline"): "frame_timeline",
    ("data_sources", "track_event"): "composable_names",
    ("atrace_categories", "am"): "atrace:am",
    ("atrace_categories", "dalvik"): "atrace:dalvik",
    ("atrace_categories", "binder_driver"): "atrace:binder_driver",
}


# --- what collect keeps beside a trace ----------------------------------------

def sidecar(trace: str | Path) -> Path:
    """Where `collect` keeps its account of a trace: beside it, under its name."""
    path = Path(trace)
    return path.with_name(path.name + SIDECAR_SUFFIX)


def write(trace: str | Path, facts: dict[str, Any]) -> None:
    """What `collect` knows of how it recorded `trace`, beside it.

    Never fails the collect: a trace without its account is a trace whose
    recording `compare` cannot check, and that is said there. A recorded
    trace lost to a full disk here would be a repeat thrown away for a
    sentence.
    """
    from . import __version__

    body = {"echolot": __version__,
            **{k: facts.get(k) for k in _FROM_COLLECT},
            **{k: facts[k] for k in _ALSO if k in facts}}
    try:
        sidecar(trace).write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
    except OSError:
        pass


def read(trace: str | Path) -> dict[str, Any]:
    """What `collect` kept beside `trace`, its keys only; empty when there is
    nothing to read: a trace recorded by hand, by another tool, or by a
    `collect` from before it kept this."""
    try:
        body = json.loads(sidecar(trace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(body, dict):
        return {}
    return {k: body[k] for k in _FROM_COLLECT
            if isinstance(body.get(k), str)}


_ABSENT = object()


def kept(trace: str | Path, key: str, default: Any = _ABSENT) -> Any:
    """One of the other facts `collect` kept beside `trace` (`_ALSO`), as it
    wrote it; `default` (None unless given) when the account does not hold the
    key, which tells "kept as null" from "never kept"."""
    try:
        body = json.loads(sidecar(trace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = None
    if isinstance(body, dict) and key in body:
        return body[key]
    return None if default is _ABSENT else default


# --- what the trace says it asked for -----------------------------------------

_DATA_SOURCE = re.compile(r'^\s*name:\s*"([^"]+)"', re.MULTILINE)
_CATEGORY = re.compile(r'atrace_categories:\s*"([^"]+)"')
_EVENT = re.compile(r'ftrace_events:\s*"([^"]+)"')


def from_config(text: str | None) -> dict[str, Any]:
    """The data sources, atrace categories and ftrace events a recording asked
    for, each a sorted list; `None` for all three when the trace carries no
    config, since a recording that does not say asked for nothing anyone
    can tell."""
    if not text:
        return {"data_sources": None, "atrace_categories": None, "ftrace_events": None}
    return {
        "data_sources": sorted(set(_DATA_SOURCE.findall(text))),
        "atrace_categories": sorted(set(_CATEGORY.findall(text))),
        "ftrace_events": sorted(set(_EVENT.findall(text))),
    }


def of_trace(tp: Any, trace: str | Path) -> dict[str, Any]:
    """The block for one trace: its config, and what `collect` kept beside it."""
    try:
        rows = tp.query("SELECT str_value AS text FROM metadata "
                        "WHERE name = 'trace_config_pbtxt'")
        text = (rows[0]["text"] if rows else None) or None
    except Exception:  # a trace_processor without the key
        text = None
    out: dict[str, Any] = {key: None for key in KEYS}
    out.update(from_config(text))
    out.update(read(trace))
    return out


# --- a set of repeats ----------------------------------------------------------

def _frozen(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def merge(blocks: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """One set's block from its repeats'.

    Each key takes the value most repeats had. Where they did not all agree,
    `mixed` lists every value with its share, so that "launch in 3 of 5" is
    not rounded away into "launch". A repeat from a report written before
    the block existed counts as not saying. `None` when none of them said
    anything at all.
    """
    if not any(isinstance(b, dict) for b in blocks):
        return None
    n = len(blocks)
    out: dict[str, Any] = {}
    mixed: dict[str, list[dict[str, Any]]] = {}
    for key in KEYS:
        values = [(b or {}).get(key) if isinstance(b, dict) else None for b in blocks]
        counts = Counter(_frozen(v) for v in values)
        # Ties go to the value met first: the earliest repeat's.
        order = list(dict.fromkeys(_frozen(v) for v in values))
        top = max(order, key=lambda v: counts[v])
        out[key] = list(top) if isinstance(top, tuple) else top
        if len(counts) > 1:
            mixed[key] = [{"value": list(v) if isinstance(v, tuple) else v,
                           "runs": f"{counts[v]}/{n}"} for v in order]
    if mixed:
        out["mixed"] = mixed
    return out


# --- two sets -------------------------------------------------------------------

def _affected(key: str, before: Any, after: Any,
              needs: dict[str, tuple[str, ...]]) -> list[str]:
    """The detectors that read something one side recorded and the other did not."""
    if not isinstance(before, list) or not isinstance(after, list):
        return []
    sources = {FEEDS[(key, item)] for item in set(before) ^ set(after)
               if (key, item) in FEEDS}
    return sorted(d for d, wants in needs.items() if sources & set(wants))


def shipped_needs() -> dict[str, tuple[str, ...]]:
    """Each shipped detector's `@needs`, by id."""
    from .tp import load_detectors

    return {d.id: d.needs for d in load_detectors(Path(__file__).parent / "sql" / "detectors")}


def moved(before: dict[str, Any], after: dict[str, Any],
          needs: dict[str, tuple[str, ...]] | None = None) -> list[dict[str, str]]:
    """`compare`'s sentences about how the two sides were recorded.

    `recording` when a condition both sides know differs, naming what
    differs and the detectors that read it; `recording-mixed` for a side
    whose own repeats were not recorded alike; `recording-unknown` once, for
    whatever one side could not say while the other could. A condition
    neither side could say gets nothing: there is nothing to set against
    anything.
    """
    rb, ra = before.get("recording"), after.get("recording")
    if not isinstance(rb, dict) and not isinstance(ra, dict):
        return []
    if not isinstance(rb, dict) or not isinstance(ra, dict):
        side = "before" if not isinstance(rb, dict) else "after"
        return [{
            "id": "recording-unknown",
            "text": f"The {side} report was written before reports said how "
                    f"their traces were recorded, so whether the two sides "
                    f"were recorded the same way — the runner, the trace "
                    f"config — cannot be checked. Analyze that side's traces "
                    f"again to have it checked.",
        }]
    if needs is None:
        needs = shipped_needs()
    present = {d["id"] for rep in (before, after) for d in rep.get("detectors") or []}
    needs = {d: wants for d, wants in needs.items() if d in present}

    out: list[dict[str, str]] = []
    differ: list[str] = []
    affected: set[str] = set()
    for c in CONDITIONS:
        vb, va = rb.get(c.key), ra.get(c.key)
        if vb is None or va is None or _frozen(vb) == _frozen(va):
            continue
        differ.append(c.differs(rb, ra) if c.differs is not None
                      else f"{c.label} `{vb}` before and `{va}` after")
        affected.update(_affected(c.key, vb, va, needs))
    if differ:
        text = ("The two sides were not recorded the same way: "
                + "; ".join(differ) + ". A row can move for that reason "
                "alone, on code nobody touched")
        if affected:
            text += (f", and {_listed(sorted(affected))} read what one side "
                     f"recorded and the other did not")
        text += ". Record both sides the same way to compare the app."
        out.append({"id": "recording", "text": text})

    for side, block in (("before", rb), ("after", ra)):
        split = [_split(c, (block.get("mixed") or {})[c.key]) for c in CONDITIONS
                 if not c.per_repeat and c.key in (block.get("mixed") or {})]
        if split:
            out.append({
                "id": "recording-mixed",
                "text": f"The {side} side's repeats were not recorded alike: "
                        + "; ".join(split) + ". Its medians mix more than one "
                        "way of recording; record that side again in one.",
            })

    # What one side cannot say and the other can. A condition neither side
    # says is nothing to set against anything, and gets no sentence; nor does
    # what follows from the runner, such as the app at a stop a runner with
    # no stop never made.
    for side, mine, other in (("before", rb, ra), ("after", ra, rb)):
        blind = []
        if mine.get("runner") is None and other.get("runner") is not None:
            blind.append("how its repeats were driven, which `collect` keeps "
                         "beside each trace it records")
        if mine.get("data_sources") is None and other.get("data_sources") is not None:
            blind.append("what its recordings asked the device for, which a "
                         "trace carries in its own config")
        if blind:
            out.append({
                "id": "recording-unknown",
                "text": f"The {side} side does not say "
                        + ", nor ".join(blind)
                        + ". Whether the two sides were recorded alike cannot "
                          "be checked there: its traces were recorded before "
                          "that was kept, or by something other than "
                          "`collect`.",
            })
    return out


def _split(c: Condition, entries: list[dict[str, Any]]) -> str:
    """One condition a side's repeats did not share, with how they split."""
    if any(isinstance(e["value"], list) for e in entries):
        return f"{c.label} differ between them ({len(entries)} different sets)"
    return f"{c.label} " + ", ".join(
        f"{'unknown' if e['value'] is None else _code(e['value'])} in "
        f"{e['runs'].replace('/', ' of ')}" for e in entries)
