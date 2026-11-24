"""The structured verdict a hunt can close with.

`echolot hunt --done "<text>"` keeps one string. What an agent returns at the
end of a hunt has a shape every time — causes ranked by what they cost, each
with a place in the code, a mechanism, the measurements that show it, a change
to make and how sure the agent is — and as one string it can be read but not
checked, and two hunts cannot be set side by side.

`echolot hunt --done --verdict <file>` takes the same answer as JSON, and
`echolot hunt --verdict <file>` without `--done` runs the same check and
records nothing. This module is everything that is known about that JSON
without the project: its shape, the check that every measurement it cites is
a row of a report the investigation filed, and how it reads back as text. It
touches no files, so the same verdict against the same reports is accepted or
refused the same way every time, which is what the check is for.

The content is the agent's. Nothing here rewrites, ranks or fills in a field:
the verdict is stored as it came, and what echolot adds — what it found when
it looked — goes beside it, in `hunt.json`'s `verdict_check`. Only the rows a
verdict cites are checked against the reports; its lists of what was ruled
out, what else was measured and what to change beyond any one cause are held
to their shape and nothing more, because there is no row they point at. So
are a cause's `cost_ms`, what it costs as measured, and its `cost_of`, what
that figure measures: the agent took the number from its rows, and echolot
does not recompute it. The estimate of what a fix should win back stays in
`expected_ms`, so a reader can tell a measurement from a guess.

A citation is a `report` number, a `location`, and optionally a `detector`.
The number is the one a report is filed under in the investigation
(`.echolot/hunts/<n>/reports/003.json` is report 3; `analyze` prints the name
when it files one). The location is a row's `location` as the report holds it,
compared as written: a row is identified by that string, and a guess at which
row a near miss meant would be the opposite of a check. Without a detector the
row may be any detector's or one of the markers' (`AGENTTMP_…`), which is
where the temporary markers' own numbers live.

What the causes were measured against is said in `against`, the one field
that decides how a verdict reads. With it, the verdict sets the build it
measured against a reference — a set of an earlier build, analysed under the
same investigation and filed as a report of its own — and its causes are what
changed. Without it, nothing was recorded to compare with, and the causes are
where the build measured spends its time: the costliest places, which a hunt
with no reference finds whether or not anything changed (#538). A sentence in
`summary` cannot be held to that; the field can. So `against` names a report
this investigation filed, every cause cites a row outside it, and the
reference measured the same scenario as the rows it is set against.
"""

from __future__ import annotations

import json
import math
from typing import Any

CONFIDENCE = ("high", "medium", "low")

# What the agent says about the temporary markers it put in. echolot counts
# them itself either way (`mark.Markers.leftovers`) and records both.
CLEANUP = ("removed", "none_added", "left")

TOP_FIELDS = ("summary", "against", "inside", "causes", "suggestions", "ruled_out",
              "also_measured", "cleanup")
AGAINST_FIELDS = ("report", "what")
CAUSE_FIELDS = ("title", "where", "mechanism", "evidence", "cost_ms", "cost_of",
                "fix", "expected_ms", "confidence", "confidence_why")
CITATION_FIELDS = ("report", "detector", "location")
SUGGESTION_FIELDS = ("fix", "expected_ms")

# The reports of an investigation by number. None is a file that is there and
# does not read as a report.
Reports = dict[int, dict[str, Any] | None]

# How many problems are printed; the rest are counted.
SHOWN = 20


def parse(text: str) -> tuple[Any, str | None]:
    """The JSON in `text`, or why it is not."""
    if not text.strip():
        return None, "the verdict is empty"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, f"the verdict is not JSON: {e}"


# --- the shape ---------------------------------------------------------------

def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _ms(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _unknown(obj: dict[str, Any], allowed: tuple[str, ...], where: str,
             out: list[str]) -> None:
    extra = sorted(k for k in obj if k not in allowed)
    if extra:
        out.append(f"{where}: no such field {', '.join(extra)} — the fields are "
                   f"{', '.join(allowed)}")


def _optional_text(obj: dict[str, Any], key: str, where: str, out: list[str]) -> None:
    if key in obj and not _text(obj[key]):
        out.append(f"{where}.{key}: has to be a non-empty string")


def _ms_field(obj: dict[str, Any], key: str, where: str, out: list[str]) -> None:
    """An optional field in milliseconds: a number, or `[low, high]`.

    One rule for every such field — a cause's `cost_ms` and `expected_ms`, a
    suggestion's `expected_ms` — so the same figure reads the same wherever it
    stands.
    """
    if key not in obj:
        return
    value = obj[key]
    if _ms(value):
        return
    if (isinstance(value, list) and len(value) == 2 and all(_ms(v) for v in value)
            and value[0] <= value[1]):
        return
    out.append(f"{where}.{key}: a number of milliseconds, or [low, high] with "
               f"low not above high, neither negative")


def _strings(verdict: dict[str, Any], key: str, what: str, out: list[str]) -> None:
    """An optional list of non-empty strings. Only its shape is held."""
    if key not in verdict:
        return
    items = verdict[key]
    if not isinstance(items, list):
        out.append(f"{key}: has to be a list of non-empty strings — {what}")
        return
    for i, item in enumerate(items):
        if not _text(item):
            out.append(f"{key}[{i}]: has to be a non-empty string")


def _suggestions(verdict: dict[str, Any], out: list[str]) -> None:
    """Changes that belong to no one cause: a `fix`, and what it should save.

    The shape of a cause's own `fix` and `expected_ms`. No row is cited, so
    there is nothing to look up.
    """
    if "suggestions" not in verdict:
        return
    items = verdict["suggestions"]
    if not isinstance(items, list):
        out.append('suggestions: has to be a list of objects like {"fix": "…", '
                   '"expected_ms": 300} — changes that belong to no one cause')
        return
    for i, item in enumerate(items):
        where = f"suggestions[{i}]"
        if not isinstance(item, dict):
            out.append(f"{where}: has to be an object with a fix")
            continue
        _unknown(item, SUGGESTION_FIELDS, where, out)
        if not _text(item.get("fix")):
            out.append(f"{where}.fix: required, a non-empty string")
        _ms_field(item, "expected_ms", where, out)


# --- the citations -----------------------------------------------------------

class _Index:
    """What each report holds, read once per check: detector → its rows' locations."""

    def __init__(self, reports: Reports) -> None:
        self.reports = reports
        self._cache: dict[int, tuple[dict[str, list[str]], list[str]]] = {}

    def rows(self, n: int) -> tuple[dict[str, list[str]], list[str]]:
        if n not in self._cache:
            self._cache[n] = _rows_of(self.reports[n] or {})
        return self._cache[n]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _locations(rows: Any) -> list[str]:
    return [r["location"] for r in _list(rows)
            if isinstance(r, dict) and isinstance(r.get("location"), str)]


def _rows_of(report: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """A report's rows by detector, and the markers' rows."""
    by: dict[str, list[str]] = {}
    for d in _list(report.get("detectors")):
        if isinstance(d, dict) and isinstance(d.get("id"), str):
            by[d["id"]] = _locations(d.get("rows"))
    markers = report.get("markers")
    return by, _locations(markers.get("rows")) if isinstance(markers, dict) else []


def _numbers(reports: Reports) -> str:
    return ", ".join(str(n) for n in sorted(reports)) or "none"


def _no_report(reports: Reports, report: int) -> str | None:
    """Why `report` is not a report of this investigation to read, or None."""
    if report not in reports:
        if not reports:
            return (f"report {report} does not exist: this investigation has no "
                    f"archived reports (`echolot analyze` files one for each run "
                    f"it makes while the investigation is open)")
        return (f"report {report} is not one of this investigation's reports "
                f"(it has {_numbers(reports)})")
    if reports[report] is None:
        return f"report {report} is filed but does not read as a report"
    return None


def unresolved(index: _Index, report: int, location: str,
               detector: str | None) -> str | None:
    """Why no row answers this citation, or None when one does."""
    missing = _no_report(index.reports, report)
    if missing:
        return missing
    by, markers = index.rows(report)
    cut = " — it ends in …: the text view of `echolot report` cuts locations; " \
          "`--wide` or `--json` give them whole" if location.endswith("…") else ""
    if detector is not None:
        if detector not in by:
            return (f"report {report} has no detector {detector} "
                    f"(it has {', '.join(sorted(by)) or 'none'})")
        if location in by[detector]:
            return None
        elsewhere = [d for d in sorted(by) if d != detector and location in by[d]]
        if elsewhere:
            hint = f" — that location is a row of {', '.join(elsewhere)}"
        elif by[detector]:
            shown = by[detector][:5]
            more = len(by[detector]) - len(shown)
            hint = " — its rows are " + ", ".join(f'"{s}"' for s in shown) \
                + (f" and {more} more" if more else "")
        else:
            hint = " — it is silent in that report"
        return f'report {report}: {detector} has no row at "{location}"{hint}{cut}'
    if location in markers or any(location in rows for rows in by.values()):
        return None
    return (f'report {report} has no row at "{location}", in any detector or '
            f"among its markers{cut}")


def _citation(entry: Any, where: str, index: _Index, out: list[str]) -> None:
    if not isinstance(entry, dict):
        out.append(f"{where}: has to be an object with report and location")
        return
    _unknown(entry, CITATION_FIELDS, where, out)
    report, location, detector = (entry.get("report"), entry.get("location"),
                                  entry.get("detector"))
    sound = True
    if not _int(report) or report < 1:
        out.append(f"{where}.report: the number of one of this investigation's "
                   f"reports, 1 or more")
        sound = False
    if not _text(location):
        out.append(f"{where}.location: a row's location, as a non-empty string")
        sound = False
    if detector is not None and not _text(detector):
        out.append(f"{where}.detector: a detector's id, or leave it out")
        sound = False
    if sound:
        why = unresolved(index, report, location, detector)
        if why:
            out.append(f"{where}: {why}")


def rows_at(reports: Reports, entry: Any) -> tuple[list[tuple[str | None, dict[str, Any]]],
                                                   str | None]:
    """The rows a citation points at, or why there are none.

    For a page that shows the evidence. It asks what the check asks of a
    citation, through `unresolved`, and answers with the rows themselves, so
    a row is shown exactly when the check would accept it. Each row comes
    with the id of the detector it is a row of, None for one of the markers'.

    With a detector named, that detector's row. Without one the citation may
    be any detector's or a marker's: the markers' row stands for it when
    there is one, because that is where a marker's own numbers live, and
    otherwise every detector's row at that location, in the report's order.

    A verdict was checked when it was handed in; this is asked again later,
    of whatever reports are still on disk, and nothing is stored.
    """
    if not isinstance(entry, dict):
        return [], "it does not read as a citation"
    report, location, detector = (entry.get("report"), entry.get("location"),
                                  entry.get("detector"))
    if (not _int(report) or report < 1 or not _text(location)
            or (detector is not None and not _text(detector))):
        return [], "it does not read as a citation: a report's number and a row's location"
    why = unresolved(_Index(reports), report, location, detector)
    if why:
        return [], why
    data = reports[report] or {}

    def at(rows: Any) -> list[dict[str, Any]]:
        return [r for r in _list(rows) if isinstance(r, dict) and r.get("location") == location]

    by = [(d["id"], at(d.get("rows"))) for d in _list(data.get("detectors"))
          if isinstance(d, dict) and isinstance(d.get("id"), str)]
    if detector is not None:
        return [(detector, r) for did, rows in by if did == detector for r in rows], None
    markers = data.get("markers")
    own = at(markers.get("rows")) if isinstance(markers, dict) else []
    if own:
        return [(None, r) for r in own], None
    return [(did, r) for did, rows in by for r in rows], None


# --- what the build is set against --------------------------------------------

def against(verdict: Any) -> int | None:
    """The report a verdict sets the build it measured against, or None."""
    entry = verdict.get("against") if isinstance(verdict, dict) else None
    report = entry.get("report") if isinstance(entry, dict) else None
    return report if _int(report) else None


def reads_as(verdict: Any, check: Any) -> str | None:
    """How a recorded verdict reads: `regression` when it names its reference,
    `hotspots` when it was checked with none, and None for one recorded before
    echolot asked, whose silence says nothing either way."""
    if isinstance(verdict, dict) and isinstance(verdict.get("against"), dict):
        return "regression"
    if isinstance(check, dict) and "against" in check:
        return "hotspots"
    return None


def _cited_reports(entries: Any) -> set[int]:
    return {e["report"] for e in _list(entries) if isinstance(e, dict) and _int(e.get("report"))}


def _against(verdict: dict[str, Any], index: _Index, out: list[str]) -> int | None:
    """`against`, held to its shape and to the reports; the reference's number
    when it is one to read.

    A reference is a report this investigation filed, of a set recorded on
    another build. What is held here is what the reports can say: that it is
    there and reads, and that it measured the scenario the rows set against
    it measured. Which build it was is the agent's `what`, in words, because
    no report records a build.
    """
    if "against" not in verdict:
        return None
    entry = verdict["against"]
    if not isinstance(entry, dict):
        out.append('against: has to be an object like {"report": 1, "what": "the release '
                   'before the change"} — the report of the reference, filed in this '
                   'investigation; leave it out when nothing was recorded to set this '
                   'build against')
        return None
    _unknown(entry, AGAINST_FIELDS, "against", out)
    if not _text(entry.get("what")):
        out.append("against.what: required, a non-empty string — which build the "
                   "reference is, and from when (\"the release before the change, 4.1\")")
    report = entry.get("report")
    if not _int(report) or report < 1:
        out.append("against.report: the number of the report this investigation filed "
                   "for the reference, 1 or more")
        return None
    missing = _no_report(index.reports, report)
    if missing:
        out.append(f"against: {missing}")
        return None
    return report


def _set_against(verdict: dict[str, Any], reference: int, index: _Index,
                 out: list[str]) -> None:
    """What a verdict with a reference has to show: a change in the build it
    measured.

    Every cause cites at least one row outside the reference — a row only of
    the reference is the earlier build's, and says nothing of what changed —
    and `inside`, the piece found, is the build measured's. A reference of
    another scenario is another journey, which `compare` refuses as well.
    """
    inside = verdict.get("inside")
    if isinstance(inside, dict) and inside.get("report") == reference:
        out.append(f"inside: report {reference} is the reference — the piece found is the "
                   f"build measured's, in a report other than the reference")
    causes = [c for c in _list(verdict.get("causes")) if isinstance(c, dict)]
    for i, cause in enumerate(_list(verdict.get("causes"))):
        if not isinstance(cause, dict):
            continue
        cited = _cited_reports(cause.get("evidence"))
        if cited and cited <= {reference}:
            out.append(f"causes[{i}].evidence: every row it cites is in report {reference}, "
                       f"the reference — a change shows in a row of the build measured, in "
                       f"a report other than the reference")
    others = _cited_reports([inside])
    for cause in causes:
        others |= _cited_reports(cause.get("evidence"))
    theirs = (index.reports[reference] or {}).get("scenario")
    for n in sorted(others - {reference}):
        ours = (index.reports.get(n) or {}).get("scenario")
        if isinstance(theirs, str) and isinstance(ours, str) and theirs != ours:
            out.append(f"against: report {reference} measured `{theirs}` and report {n} "
                       f"measured `{ours}` — a reference is the same scenario on another build")


# --- the check ---------------------------------------------------------------

def validate(verdict: Any, reports: Reports) -> list[str]:
    """Everything wrong with a verdict, one line each; empty when it is sound.

    Sound is two things: the shape, and every citation resolving to a row of
    one of `reports`. All of it is reported at once, so one refusal is enough
    to put it right. Nothing but the verdict and the reports decides.
    """
    if not isinstance(verdict, dict):
        return ["the verdict has to be a JSON object"]
    out: list[str] = []
    index = _Index(reports)
    _unknown(verdict, TOP_FIELDS, "the verdict", out)

    if not _text(verdict.get("summary")):
        out.append("summary: required, a non-empty string — the answer in a sentence")
    reference = _against(verdict, index, out)
    if "inside" in verdict:
        _citation(verdict["inside"], "inside", index, out)
    if "cleanup" in verdict and verdict["cleanup"] not in CLEANUP:
        out.append(f"cleanup: one of {', '.join(CLEANUP)}")

    causes = verdict.get("causes")
    if not isinstance(causes, list):
        out.append("causes: required, a list — empty when nothing was found")
        causes = []
    for i, cause in enumerate(causes):
        where = f"causes[{i}]"
        if not isinstance(cause, dict):
            out.append(f"{where}: has to be an object")
            continue
        _unknown(cause, CAUSE_FIELDS, where, out)
        if not _text(cause.get("title")):
            out.append(f"{where}.title: required, a non-empty string")
        places = cause.get("where")
        if not isinstance(places, list) or not places or not all(_text(p) for p in places):
            out.append(f"{where}.where: required, a list of places in the code, "
                       f"each a non-empty string ('store/StoreFiller.kt:70-88')")
        evidence = cause.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            out.append(f"{where}.evidence: required, a list of rows of this "
                       f"investigation's reports: "
                       f'{{"report": 2, "location": "…"}}')
        else:
            for j, entry in enumerate(evidence):
                _citation(entry, f"{where}.evidence[{j}]", index, out)
        for key in ("mechanism", "fix", "confidence_why"):
            _optional_text(cause, key, where, out)
        # What the cause costs, as measured, and what the fix is expected to
        # win back: two figures with two names, so they are never mixed. The
        # check holds their shape; the agent took them from its rows, and
        # echolot does not recompute them.
        _ms_field(cause, "cost_ms", where, out)
        _ms_field(cause, "expected_ms", where, out)
        _optional_text(cause, "cost_of", where, out)
        if "cost_of" in cause and "cost_ms" not in cause:
            out.append(f"{where}.cost_of: goes with cost_ms — it says what that "
                       f"number measures")
        if "confidence" in cause and cause["confidence"] not in CONFIDENCE:
            out.append(f"{where}.confidence: one of {', '.join(CONFIDENCE)}")

    if reference is not None:
        _set_against(verdict, reference, index, out)
    _suggestions(verdict, out)
    _strings(verdict, "ruled_out", "what was checked and is not a cause", out)
    _strings(verdict, "also_measured", "what else was measured, one line each", out)
    return out


def cited(verdict: dict[str, Any]) -> int:
    """How many rows a sound verdict points at, `inside` among them."""
    n = 1 if isinstance(verdict.get("inside"), dict) else 0
    for cause in _list(verdict.get("causes")):
        if isinstance(cause, dict):
            n += len(_list(cause.get("evidence")))
    return n


def cleanup_agrees(claimed: Any, left: dict[str, Any]) -> bool | None:
    """Does what the agent said about its markers match what echolot counted?

    None when it said nothing. Counted the way `leftovers` counts: a marker
    by its prefix, a line in the shape `mark --remove` takes out whether or
    not it has one, and a tagged line `--remove` leaves.
    """
    if claimed is None:
        return None
    remaining = bool(left.get("markers") or left.get("lines") or left.get("kept"))
    return remaining if claimed == "left" else not remaining


# The places of the lines `mark --remove` leaves that one sentence names; the
# rest are counted. `mark --remove` prints every one.
PLACES = 5


def places(kept: Any, shown: int = PLACES) -> str:
    """The first places in `leftovers`' `kept`, and how many more there are."""
    where = [str(p) for p in _list(kept)]
    more = len(where) - shown
    return ", ".join(where[:shown]) + (f" and {more} more" if more > 0 else "")


def record(verdict: dict[str, Any], reports: Reports, left: dict[str, Any],
           when: str) -> dict[str, Any]:
    """What echolot found when the verdict was handed in, to sit beside it.

    The verdict itself is stored as it came. This is the other half of the
    record: which reports the investigation held, how many rows the verdict
    cites in them, the report it was set against (None: no reference, so
    its causes read as where the build spends its time), and the markers
    echolot counted in the sources next to what the agent claimed.
    """
    claimed = verdict.get("cleanup")
    return {"checked_at": when,
            "reports": sorted(reports),
            "cited": cited(verdict),
            "against": against(verdict),
            "cleanup": {"claimed": claimed, "found": left,
                        "agrees": cleanup_agrees(claimed, left)}}


# --- reading it back ---------------------------------------------------------

def _indented(text: Any, pad: str) -> str:
    """A string that may run to several lines, the next ones under the first."""
    return ("\n" + pad).join(str(text).strip().splitlines()) or ""


def _num(value: float) -> str:
    return f"{value:g}"


def _ms_text(value: Any, about: str = "") -> str | None:
    """A figure in milliseconds as text; `about` stands before a single number."""
    if _ms(value):
        return f"{about}{_num(value)} ms"
    if isinstance(value, list) and len(value) == 2 and all(_ms(v) for v in value):
        return f"{_num(value[0])}–{_num(value[1])} ms"
    return None


def _expected_text(value: Any) -> str | None:
    """What a fix should win back is the agent's estimate, and reads as one."""
    return _ms_text(value, "about ")


def _cost_text(cause: dict[str, Any], under: str) -> str | None:
    """What a cause costs, as measured, and what that number measures.

    No "about": it is a measurement. A `cost_of` with no readable `cost_ms`
    says nothing, so it is left out with it. `under` indents a `cost_of` that
    runs to several lines.
    """
    said = _ms_text(cause.get("cost_ms"))
    if said is None:
        return None
    of = cause.get("cost_of")
    return said + (f" — {_indented(of, under)}" if isinstance(of, str) and of.strip() else "")


def _cite_text(entry: Any) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    bits = [f"report {entry.get('report')}"]
    if entry.get("detector"):
        bits.append(str(entry["detector"]))
    bits.append(str(entry.get("location")))
    return " · ".join(bits)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    return value if _int(value) else 0


def _cleanup_line(claimed: Any, check: Any) -> str | None:
    found = _dict(_dict(_dict(check).get("cleanup")).get("found"))
    said = claimed if isinstance(claimed, str) else "not stated"
    if not found:
        return f"cleanup: {said}" if isinstance(claimed, str) else None
    markers, lines = _count(found.get("markers")), _count(found.get("lines"))
    kept = _list(found.get("kept"))
    files = len(_list(found.get("files")))
    bits = []
    if markers:
        bits.append(f"{markers} {found.get('prefix') or ''} marker(s) in {files} file(s) "
                    "still in the sources")
    elif lines:
        bits.append(f"{lines} line(s) tagged `// echolot:mark` still in the sources")
    if kept:
        bits.append(f"{len(kept)} line(s) tagged `// echolot:mark` that `echolot mark --remove` "
                    f"leaves, to take out by hand: {places(kept)}")
    seen = ("echolot found " + ", and ".join(bits)) if bits else \
        "echolot found none left in the sources"
    return f"cleanup: {said} — {seen}"


# What a verdict with no reference is, said where its causes are read. Its
# confidence is the agent's about what a place costs; whether that place is
# what changed was not measured, and echolot says so rather than the agent.
NO_REFERENCE = ("nothing — no reference was recorded, so the causes are where the "
                "build measured spends its time; none is shown to be what changed")
NOT_MEASURED = "not measured — nothing was recorded to set it against"


def _against_text(verdict: dict[str, Any], kind: str | None) -> str | None:
    entry = verdict.get("against")
    if kind == "regression" and isinstance(entry, dict):
        what = entry.get("what")
        return (f"against: report {entry.get('report')}"
                + (f" — {_indented(what, '    ')}" if isinstance(what, str) and what.strip() else ""))
    return f"against: {NO_REFERENCE}" if kind == "hotspots" else None


def lines(verdict: Any, check: Any = None) -> list[str]:
    """A verdict as text, for `hunt --show`. Reads whatever is stored.

    A record can be edited by hand, so a field of the wrong kind is skipped
    here rather than failing the whole listing.
    """
    v = verdict if isinstance(verdict, dict) else {}
    kind = reads_as(v, check)
    out = []
    if v.get("summary"):
        out.append(f"  verdict: {_indented(v['summary'], '    ')}")
    said = _against_text(v, kind)
    if said:
        out.append(f"  {said}")
    if isinstance(v.get("inside"), dict):
        out.append(f"  inside: {_cite_text(v['inside'])}")
    causes = [c for c in _list(v.get("causes")) if isinstance(c, dict)]
    if causes:
        out.append(f"  causes ({len(causes)}, in the order the agent gave them):")
    elif "causes" in v:
        out.append("  causes: none — nothing was found")
    pad = "       "
    for i, c in enumerate(causes, 1):
        out.append(f"    {i}. {_indented(c.get('title') or '(no title)', pad)}")
        rows: list[tuple[str, list[str]]] = [
            ("where", [str(p) for p in _list(c.get("where"))]),
            ("mechanism", [_indented(c["mechanism"], pad + " " * 12)]
             if c.get("mechanism") else []),
            ("evidence", [_cite_text(e) for e in _list(c.get("evidence"))]),
            ("cost", [t] if (t := _cost_text(c, pad + " " * 12)) else []),
            ("fix", [_indented(c["fix"], pad + " " * 12)] if c.get("fix") else []),
            ("expected", [t] if (t := _expected_text(c.get("expected_ms"))) else []),
            ("confidence", [f"{c['confidence']}"
                            + (" that it costs this" if kind == "hotspots" else "")
                            + (f" — {c['confidence_why']}" if c.get("confidence_why") else "")]
             if c.get("confidence") else []),
            ("changed", [NOT_MEASURED] if kind == "hotspots" else []),
        ]
        for name, values in rows:
            for k, value in enumerate(values):
                out.append(f"{pad}{(name + ':') if k == 0 else '':<12}{value}")
    suggestions = [s for s in _list(v.get("suggestions")) if isinstance(s, dict)]
    if suggestions:
        out.append(f"  suggestions ({len(suggestions)}, not tied to one cause):")
        for i, s in enumerate(suggestions, 1):
            out.append(f"    {i}. {_indented(s.get('fix') or '(no fix)', pad)}")
            if (said := _expected_text(s.get("expected_ms"))):
                out.append(f"{pad}{'expected:':<12}{said}")
    for key, name in (("ruled_out", "ruled out"), ("also_measured", "also measured")):
        items = [s for s in _list(v.get(key)) if isinstance(s, str) and s.strip()]
        if items:
            out.append(f"  {name} ({len(items)}):")
            out.extend(f"    - {_indented(s, '      ')}" for s in items)
    line = _cleanup_line(v.get("cleanup"), check)
    if line:
        out.append(f"  {line}")
    return out
