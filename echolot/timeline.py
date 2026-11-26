"""Where a report's findings are on the timeline, and the links that open them
in Perfetto UI (#132).

A report says how long and how often; to see a finding, a person opened the
trace in ui.perfetto.dev, looked for the process and the thread, went to the
moment and zoomed in. A moment here is one place to look: the scenario's
window, and the longest occurrence of a detector's first rows, which
`analyze` finds with the detector's `@longest` query and keeps on the row as
`at` (`ts` and `dur` in the trace's own nanoseconds, the thread, and the
repeat it is in as `run`, an index into `traces`).

What a link can and cannot do, from what Perfetto UI reads:

- It cannot carry the trace. `?url=` fetches a trace over HTTPS from a server
  that allows the UI's origin, and a recording on this machine has no such
  address: a link to `http://127.0.0.1` comes back "Could not fetch the
  trace". So a link in `report.md` opens the UI on the moment, and the trace
  named beside it is opened there by hand (Open trace file, or dropped on the
  page): the UI applies the link to the first trace it loads.
- `echolot ui` has the trace, and hands it over itself: its timeline page
  (`pages/timeline_page.py`) holds the UI in a frame and posts the trace's
  bytes to it, which is the UI's own way for a page that knows a trace to
  open it (`postMessage`, after its PING is answered). One click, and the
  trace goes nowhere but the browser.
- The moment is the link's `visStart` and `visEnd`: the UI zooms to the
  occurrence with a tenth of the view either side, after checking it against
  the trace's bounds, so an occurrence the trace holds always fits. One of
  the UI's stable startup commands pins the thread it happened on to the
  top, so the occurrence is in view without looking for its process.
- What the UI offers beyond that does not hold, and is not used (Perfetto
  UI v59). Its `ts` and `dur`, which select a slice, pan the view after the
  zoom has started, measure from where the view was, and the zoom is lost.
  A debug track with the occurrence as its one slice, to mark it, shows on a
  trace the UI fetched itself and is gone from one handed to it.

Nothing in a link depends on where or when it is made: the moment's numbers
are the trace's, and the rest is fixed text. The same set gives the same
`report.md`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .merge import AT

UI = "https://ui.perfetto.dev"

# How many of a detector's rows get a link: its first ones, as a view shows.
PER_DETECTOR = 3

# What a JavaScript regular expression reads as more than itself.
_REGEX_SPECIAL = re.compile(r"[\\^$.*+?()\[\]{}|]")
_ASCII_NAME = re.compile(r"[ -~]+")



@dataclass(frozen=True)
class Moment:
    """One place to look at in one trace of a report."""
    what: str                 # what it is, for a person: "the scenario window"
    detector: str | None      # the detector whose row it is, None for the window
    location: str | None      # the row's location
    run: int                  # which repeat: an index into the report's traces
    trace: str                # that trace, as the report names it
    ts: int                   # where it starts, in the trace's nanoseconds
    dur: int                  # how long it is, in nanoseconds, more than 0
    tid: int | None = None    # the thread it happened on, where it has one
    thread: str | None = None  # that thread's name, as the trace has it


def _whole(value: Any) -> int | None:
    """A number of nanoseconds as a report keeps it, or None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def traces_of(report: dict[str, Any]) -> list[str]:
    """The report's traces in the order `run` counts them: `traces` for a
    set, `trace` alone for one recording."""
    traces = report.get("traces")
    if isinstance(traces, list) and traces and all(isinstance(t, str) for t in traces):
        return list(traces)
    one = report.get("trace")
    return [one] if isinstance(one, str) and one else []


def short_name(trace: str) -> str:
    """A trace as a person names it in a sentence: the file, without
    `.perfetto-trace`."""
    return Path(trace).name.removesuffix(".perfetto-trace")


def _window(report: dict[str, Any], traces: list[str]) -> Moment | None:
    w = report.get("window")
    if not isinstance(w, dict) or not traces:
        return None
    start, end = _whole(w.get("ts_start")), _whole(w.get("ts_end"))
    if start is None or end is None or end <= start:
        return None
    # The merged window is the first repeat's, as its `pid` is.
    name = w.get("main_thread_name")
    return Moment(what="the scenario window", detector=None, location=None, run=0,
                  trace=traces[0], ts=start, dur=end - start, tid=_whole(w.get("pid")),
                  thread=name if isinstance(name, str) and name else None)


def _row(det_id: str, row: dict[str, Any], traces: list[str]) -> Moment | None:
    at = row.get(AT)
    if not isinstance(at, dict):
        return None
    ts, dur, run = _whole(at.get("ts")), _whole(at.get("dur")), _whole(at.get("run"))
    run = 0 if run is None else run
    if ts is None or dur is None or dur <= 0 or not 0 <= run < len(traces):
        return None
    location = row.get("location")
    thread = at.get("thread")
    named = str(location).replace("`", "'")
    return Moment(what=f"the longest of `{named}`", detector=det_id,
                  location=None if location is None else str(location), run=run,
                  trace=traces[run], ts=ts, dur=dur, tid=_whole(at.get("tid")),
                  thread=thread if isinstance(thread, str) and thread else None)


def moments(report: dict[str, Any], per_detector: int = PER_DETECTOR) -> list[Moment]:
    """Every place the report can open, in a fixed order: the window first,
    then each detector's first rows that say where their longest occurrence
    is. Its index is how `echolot ui` names a moment in an address, so it is
    the same for the same report."""
    traces = traces_of(report)
    found: list[Moment] = []
    window = _window(report, traces)
    if window is not None:
        found.append(window)
    for det in report.get("detectors") or []:
        if not isinstance(det, dict) or not isinstance(det.get("id"), str):
            continue
        kept = 0
        for row in det.get("rows") or []:
            if kept >= per_detector:
                break
            m = _row(det["id"], row, traces) if isinstance(row, dict) else None
            if m is not None:
                found.append(m)
                kept += 1
    return found


def of_detector(found: list[Moment], det_id: str) -> list[tuple[int, Moment]]:
    """The moments of one detector, with their index among all of them."""
    return [(i, m) for i, m in enumerate(found) if m.detector == det_id]


def window_of(found: list[Moment]) -> tuple[int, Moment] | None:
    return next(((i, m) for i, m in enumerate(found) if m.detector is None), None)


def _regex(text: str) -> str:
    return _REGEX_SPECIAL.sub(lambda m: "\\" + m.group(0), text)


def commands(m: Moment) -> list[dict[str, Any]]:
    """The UI's startup commands for the moment: the thread it happened on
    pinned to the top, so that the occurrence is in view without looking for
    its process. One of the commands Perfetto keeps stable, and on its list
    of those a link may run. Perfetto UI titles a thread's track by its name
    and its tid; a name that is not plain ASCII is left out rather than
    guessed at, and the moment is then the zoom alone."""
    if m.thread is None or m.tid is None or not _ASCII_NAME.fullmatch(m.thread):
        return []
    return [{"id": "dev.perfetto.PinTracksByRegex", "args": [f"^{_regex(m.thread)} {m.tid}$"]}]


def fragment(m: Moment) -> str:
    """The part of the UI's address after `#!/?`: the moment to zoom to, and
    the commands for it (`commands`)."""
    found = f"visStart={m.ts}&visEnd={m.ts + m.dur}"
    cmds = commands(m)
    if cmds:
        found += "&startupCommands=" + quote(json.dumps(cmds, separators=(",", ":")), safe="")
    return found


def url(m: Moment) -> str:
    """The link that opens Perfetto UI on the moment, its thread pinned. From
    report.md the trace is opened there by hand: the UI applies the link to
    the first trace it loads."""
    return f"{UI}/#!/?{fragment(m)}"


def label(m: Moment) -> str:
    """The link's words in a report: what it opens and in which trace."""
    took = f"{m.dur / 1e6:.1f} ms"
    return f"{m.what}, {took}, in `{short_name(m.trace)}`"


# Said once wherever links are shown on their own, without `echolot ui`.
HOW = ("Each link opens Perfetto UI on the moment: open the trace it names there "
       "(Open trace file, or drop it on the page) and the view goes to it. On the "
       "report's page in `echolot ui` one click opens it with its trace.")
