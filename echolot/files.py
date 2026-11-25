"""One reader for each file echolot writes and reads back (#833).

echolot reads its own files again: from an older version, after a hand edit,
or as another tool left them. Each file used to get a shape check of its own,
in the place that crashed on it (#470, #474, #555, #580, #733, #870), and the
`schema` number every writer puts in its file was read by nothing.

Here each file has one reader. It reads the bytes, checks the schema number
the writer and the reader share, and checks the shape against one table: the
fields, the type each holds, and which of them must be there. It returns the
file as the writer wrote it, typed, or a `Refusal`: one sentence saying why
the file does not read, which the command or the page says in turn, with how
to write the file again.

A shape table is made of a few parts:

  - `TEXT`, `WHOLE`, `NUMBER`, `FLAG` and `ANY` for a single value. True and
    false are no number here, though Python takes a bool for an int.
  - `Whole(low)` for a whole number with a least value, `OneOf(...)` for text
    that is one of a few words, `Token(pattern)` for text that is all one
    token of a pattern.
  - `ListOf(shape)` for a list whose every item has that shape.
  - `Obj({"key": need(shape) or may(shape)})` for an object: a field that is
    `need`ed must be there and not null; one that `may` be there may also be
    null or left out. Keys the table does not name are left as they are, so a
    newer writer may add a field without an older reader refusing the file.
  - `may(shape, drop=True)` for a field that reads as absent when it holds the
    wrong type, where the rest of a file is worth reading without it.

A schema goes up when a field changes meaning. The reader of the new schema
reads the old one through an upgrade written once, here (`Spec.upgrades`), or
refuses it with a sentence that names the command that writes it again.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypedDict, TypeVar, cast

T = TypeVar("T")


# ---------------------------------------------------------------------------
# what a file that does not read says


@dataclass(frozen=True)
class Refusal:
    """Why a file does not read, as a sentence a person can act on.

    `absent` is a file that is not there at all: most readers treat that as
    nothing written yet rather than as a fault. `stale` is a file refused
    for its schema number alone: another version of echolot wrote it, and
    no hand edited it.
    """
    why: str
    absent: bool = False
    stale: bool = False

    def __str__(self) -> str:
        return self.why


def kind_of(value: object) -> str:
    """What a JSON value is, in the words a reader of the file would use."""
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "true or false"
    return {dict: "an object", list: "a list", str: "text", int: "a number",
            float: "a number"}.get(type(value), type(value).__name__)


# ---------------------------------------------------------------------------
# the parts of a shape table


class Shape:
    """What one value of a file holds. `what` names it in a sentence."""
    what = "anything"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        """The value as it reads, and None; or the first part of it that does
        not hold this shape, in words."""
        raise NotImplementedError


def _wrong(value: Any, at: str, what: str) -> str:
    return f"`{at}` is {kind_of(value)}, not {what}"


@dataclass(frozen=True)
class Kind(Shape):
    """One value of one or more JSON types."""
    types: tuple[type, ...]
    what: str = "a value"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if isinstance(value, bool) and bool not in self.types:
            return value, _wrong(value, at, self.what)
        if not isinstance(value, self.types):
            return value, _wrong(value, at, self.what)
        return value, None


@dataclass(frozen=True)
class Whole(Shape):
    """A whole number, not true or false, from `low` up, and up to `high`
    where there is one."""
    low: int = 0
    high: int | None = None
    what: str = "a whole number"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if isinstance(value, bool) or not isinstance(value, int):
            return value, _wrong(value, at, self.what)
        if value < self.low:
            return value, f"`{at}` is {value}, below {self.low}"
        if self.high is not None and value > self.high:
            return value, f"`{at}` is {value}, above {self.high}"
        return value, None


@dataclass(frozen=True)
class OneOf(Shape):
    """Text that is one of a few words."""
    words: tuple[str, ...]

    @property
    def what(self) -> str:  # type: ignore[override]
        return " or ".join(f"`{w}`" for w in self.words)

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if not isinstance(value, str):
            return value, _wrong(value, at, "text")
        if value not in self.words:
            return value, f"`{at}` is `{value}`, not {self.what}"
        return value, None


@dataclass(frozen=True)
class Token(Shape):
    """Text that is, whole, one match of `pattern`: an id, never a path."""
    pattern: re.Pattern[str]
    what: str = "a token"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if not isinstance(value, str):
            return value, _wrong(value, at, self.what)
        if not self.pattern.fullmatch(value):
            return value, f"`{at}` is not {self.what}"
        return value, None


class _Any(Shape):
    what = "anything"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        return value, None


@dataclass(frozen=True)
class Finite(Shape):
    """A number that is neither NaN nor infinite: a count or a duration."""
    what: str = "a number"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, _wrong(value, at, self.what)
        try:
            finite = math.isfinite(value)
        except OverflowError:   # a whole number too long to become a float
            finite = False
        if not finite:
            return value, f"`{at}` is {'too long' if isinstance(value, int) else value}, not a finite number"
        return value, None


TEXT = Kind((str,), "text")
WHOLE = Whole()
NUMBER = Kind((int, float), "a number")
FLAG = Kind((bool,), "true or false")
ANY = _Any()


@dataclass(frozen=True)
class ListOf(Shape):
    """A list whose every item has `item`'s shape. With `drop`, an item that
    does not is left out and the rest are read."""
    item: Shape
    drop: bool = False
    what: str = "a list"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if not isinstance(value, list):
            return value, _wrong(value, at, self.what)
        out: list[Any] | None = None
        for i, got in enumerate(value):
            read, wrong = self.item.fault(got, f"{at}[{i}]")
            if wrong and self.drop:
                out = out if out is not None else value[:i]
                continue
            if wrong:
                return value, wrong
            if out is not None:
                out.append(read)
            elif read is not got:
                out = [*value[:i], read]
        return (value if out is None else out), None


@dataclass(frozen=True)
class Field:
    """One field of an object: its shape, whether it must be there, and
    whether the wrong type reads as absent (`drop`) rather than refusing."""
    shape: Shape
    needed: bool = False
    drop: bool = False


def need(shape: Shape) -> Field:
    """A field that must be there, and not null."""
    return Field(shape, needed=True)


def may(shape: Shape, drop: bool = False) -> Field:
    """A field that may be left out or null."""
    return Field(shape, drop=drop)


@dataclass(frozen=True)
class Obj(Shape):
    """An object with the fields named, each holding its shape. Keys not
    named are left as they are."""
    fields: Mapping[str, Field]
    what: str = "an object"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if not isinstance(value, dict):
            return value, _wrong(value, at, self.what)
        out: dict[str, Any] | None = None
        for key, f in self.fields.items():
            got = value.get(key)
            where = f"{at}.{key}" if at else key
            if got is None:
                if f.needed:
                    return value, f"`{where}` is missing"
                continue
            read, wrong = f.shape.fault(got, where)
            if wrong and f.drop:
                out = out if out is not None else dict(value)
                del out[key]
                continue
            if wrong:
                return value, wrong
            if read is not got:
                out = out if out is not None else dict(value)
                out[key] = read
        return (value if out is None else out), None


# ---------------------------------------------------------------------------
# a file: its name, its schema, its shape


@dataclass(frozen=True)
class Spec(Generic[T]):
    """How one file is read.

    `schema` is the number the writer puts in the file and the reader reads:
    the writer imports it from here. `upgrades` maps an older number to what
    turns a file of that schema into one of the next, so a file from before
    a change reads after it. `unnumbered` is the schema a file with no
    number is taken for: one written before its writer put the number in.
    A file whose `kind` is not `kind` is no such file at all.

    What a file that does not read is to do (write it again, set it aside,
    or skip it) is the caller's: the command or the page that reads it
    knows which command writes it anew.
    """
    name: str
    shape: Obj
    schema: int | None = None
    upgrades: Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)
    kind: str | None = None
    unnumbered: int | None = None

    def check(self, data: Any) -> T | Refusal:
        """A value already read as JSON, as this file: typed, or why not."""
        if not isinstance(data, dict):
            return Refusal(f"it holds {kind_of(data)}, not an object")
        if self.kind is None and isinstance(data.get("kind"), str):
            return Refusal(f"it is a {data['kind']}, not a {self.name}")
        if self.kind is not None and data.get("kind") != self.kind:
            got = data.get("kind")
            said = f"`{got}`" if isinstance(got, str) else kind_of(got)
            return Refusal(f"its `kind` is {said}, not `{self.kind}`")
        if self.schema is not None:
            data_or_why = self._upgraded(data)
            if isinstance(data_or_why, Refusal):
                return data_or_why
            data = data_or_why
        read, wrong = self.shape.fault(data, "")
        if wrong:
            return Refusal(wrong)
        return cast(T, read)

    def _upgraded(self, data: dict[str, Any]) -> dict[str, Any] | Refusal:
        n = data.get("schema")
        if n is None and self.unnumbered is not None:
            n = self.unnumbered
        if isinstance(n, bool) or not isinstance(n, int):
            return Refusal("it carries no schema number" if n is None else
                           f"its schema number is {kind_of(n)}, not a whole number")
        assert self.schema is not None
        if n > self.schema:
            return Refusal(f"a newer echolot wrote it, in schema {n}; this one reads schema {self.schema}",
                           stale=True)
        while n < self.schema:
            step = self.upgrades.get(n)
            if step is None:
                return Refusal(f"an older echolot wrote it, in schema {n}; this one reads schema {self.schema}",
                               stale=True)
            data = {**step(data), "schema": n + 1}
            n += 1
        return data

    def read(self, path: Path) -> T | Refusal:
        """The file at `path`, typed, or why it does not read."""
        got = load(path)
        return got if isinstance(got, Refusal) else self.check(got)


def load(path: Path) -> Any:
    """A JSON file as the value it holds, or a `Refusal`: no file, not UTF-8,
    empty, or not whole JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Refusal("there is no such file", absent=True)
    except UnicodeDecodeError:
        return Refusal("it is not UTF-8 text")
    except OSError as e:
        return Refusal(f"it cannot be read ({e.strerror or e})")
    if not text.strip():
        return Refusal("it is empty")
    try:
        return json.loads(text)
    except ValueError as e:
        where = f" at line {e.lineno}" if isinstance(e, json.JSONDecodeError) else ""
        return Refusal(f"it is not whole JSON{where}")


# ---------------------------------------------------------------------------
# report.json, written by `echolot analyze` (`report.build`, `report.merge`)

# 2 since a detector's threshold became a flag on each row rather than a
# filter (#832): its `rows` are what it showed, and its `below` every other
# row it measured, from 1 ms. A report of schema 1 has the same shape, its
# `below` what a second query with the thresholds open found from 5 ms, at
# most 200 rows, and reads as it is (`REPORT.upgrades`). `compare` across the
# two gives no verdict on a detector, since every detector's `sha` moved.
REPORT_SCHEMA = 2


class Detector(TypedDict, total=False):
    """One detector's result in a report: what it is, and its rows."""
    id: str
    rows: list[dict[str, Any]]


class Report(TypedDict, total=False):
    """report.json, as `analyze` writes it."""
    schema: int
    generated_at: str
    trace: str
    traces: list[Any]
    scenario: str | None
    budget_ms: float | None
    toolchain: dict[str, Any]
    window: dict[str, Any]
    environment: dict[str, Any]
    recording: dict[str, Any] | None
    markers: dict[str, Any]
    summary: dict[str, Any]
    config: dict[str, Any]
    detectors: list[Detector]


# What `report`, `compare`, `status`, the start page and the report page read
# off every report before anything else, and what they used to check each in
# a place of its own (`state._as_written`, `state.misshapen`, #470, #555): a
# summary that is a list, a count that is text or a `generated_at` that is a
# number ended `status --next` and the start page in a traceback, and
# `"detectors": "x"` read a letter as a detector. `"traces": "ab"` read as
# two runs. A report is refused whole for any of them: what still reads of
# it describes no analysis.
REPORT: Spec[Report] = Spec(
    name="report.json", schema=REPORT_SCHEMA, unnumbered=1,
    upgrades={1: lambda report: report},
    shape=Obj({
        "generated_at": may(TEXT),
        "traces": may(Kind((list,), "a list")),
        "summary": may(Obj({"detectors_run": may(WHOLE), "detectors_fired": may(WHOLE)})),
        "config": may(Obj({"sha": may(TEXT), "defaults": may(FLAG)})),
        "detectors": may(ListOf(Obj({"id": need(TEXT), "rows": need(ListOf(Obj({})))}))),
        "markers": may(Obj({"rows": may(ListOf(Obj({}))), "absent": may(Kind((list,), "a list"))})),
    }))


def read_report(path: Path) -> Report | Refusal:
    """report.json at `path`, or why it does not read."""
    return REPORT.read(path)


def check_report(data: Any) -> Report | Refusal:
    """A report already in hand, or why it does not read as one."""
    return REPORT.check(data)


def runs(report: Mapping[str, Any]) -> int:
    """How many recordings a report is of: what it says, else the traces it
    lists, else one. A count that is no whole number above zero, or traces
    that are no list, read as not said (#994)."""
    n = report.get("runs")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return n
    traces = report.get("traces")
    return (len(traces) if isinstance(traces, list) else 0) or 1


def text_sha256(text: str) -> str:
    """The SHA-256 of the text as UTF-8, in hex: what tells a file's text from
    the text it held before. `codex` keeps such hashes of the rule files
    earlier releases wrote, so the hash stays SHA-256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# comparison.json, written by `echolot compare` (`compare.build`)

# 2 since `overlap` gave way to `shift` and `holds`: a reader written for the
# first shape finds the verdict it read gone, not moved. The plain reading
# came later (#483), so no comparison a page can draw has schema 1.
COMPARISON_SCHEMA = 2

# One side of a row: the row's figure under its metric's name, and its spread.
_FACE = Obj({
    "values": may(ListOf(NUMBER)), "min": may(NUMBER), "max": may(NUMBER),
    "count": may(NUMBER), "runs": may(TEXT), "covered_ms": may(NUMBER), "detail": may(TEXT),
    "total_ms": may(NUMBER), "self_ms": may(NUMBER),
})
_SHIFT = Obj({
    "ms": may(NUMBER), "low_ms": may(NUMBER), "high_ms": may(NUMBER), "floor_ms": may(NUMBER),
    "resolves_ms": may(NUMBER), "runs_needed": may(WHOLE),
})
_ROW = Obj({
    "location": need(TEXT), "detector": may(TEXT), "metric": may(TEXT),
    "change": may(TEXT), "matched_by": may(TEXT),
    "before": may(_FACE), "after": may(_FACE),
    "delta_ms": may(NUMBER), "ratio": may(NUMBER), "shift": may(_SHIFT), "holds": may(FLAG),
    "detail": may(TEXT), "shares": may(Obj({"before": may(TEXT), "after": may(TEXT)})),
})
_LEVELED = {"movement": may(TEXT), "attention": may(TEXT)}
_SAID = {"title": may(TEXT), "text": may(TEXT)}
_PLAIN_SIDE = Obj({**_LEVELED, "ms": may(NUMBER), "found": may(TEXT), "reason": may(TEXT),
                   "findings": may(WHOLE)})
_WINDOW_SIDE = Obj({"ms": may(NUMBER), "low_ms": may(NUMBER), "high_ms": may(NUMBER), "runs": may(WHOLE)})
_PLAIN = Obj({
    "verdict": need(Obj({**_LEVELED, "headline": may(TEXT), "text": may(TEXT), "by": may(TEXT),
                         "before_ms": may(NUMBER), "after_ms": may(NUMBER)})),
    "notes": may(ListOf(Obj({**_LEVELED, **_SAID, "id": may(TEXT)}))),
    "checks": need(ListOf(Obj({**_LEVELED, **_SAID, "id": may(TEXT), "why": may(TEXT),
                               "before": may(_PLAIN_SIDE), "after": may(_PLAIN_SIDE)}))),
    "window": need(Obj({"before": may(_WINDOW_SIDE), "after": may(_WINDOW_SIDE),
                        "budget_ms": may(NUMBER), "noun": may(TEXT)})),
    "scenario": may(TEXT), "process": may(TEXT),
})
_COMPARED_SIDE = Obj({"path": may(TEXT), "runs": may(WHOLE), "generated_at": may(TEXT),
                      "config_sha": may(TEXT), "config_path": may(TEXT), "defaults": may(FLAG)})


class ComparedSide(TypedDict, total=False):
    path: str
    runs: int
    generated_at: str
    config_sha: str
    config_path: str
    defaults: bool


class Row(TypedDict, total=False):
    """One row of a comparison: one place a detector found, on both sides."""
    location: str
    detector: str
    metric: str
    change: str
    matched_by: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    delta_ms: float | None
    ratio: float | None
    shift: dict[str, Any] | None
    holds: bool | None
    detail: str
    shares: dict[str, str]


class Comparison(TypedDict, total=False):
    """comparison.json, as `compare.build` writes it and `moves.annotate`
    adds its plain reading to (`plain`, with `--plain` or `--html`)."""
    schema: int
    kind: str
    generated_at: str
    comparable: bool
    warnings: list[dict[str, Any]]
    before: ComparedSide
    after: ComparedSide
    window: dict[str, Any]
    startup: dict[str, Any] | None
    noise_floor: dict[str, Any]
    confidence: float
    holds_against: str
    summary: dict[str, Any]
    rows: list[Row]
    markers: list[Row]
    names: dict[str, Any] | None
    plain: dict[str, Any]


COMPARISON: Spec[Comparison] = Spec(
    name="comparison.json", schema=COMPARISON_SCHEMA, kind="comparison",
    shape=Obj({
        "generated_at": may(TEXT), "comparable": may(FLAG),
        "warnings": may(ListOf(Obj({"id": may(TEXT), "text": may(TEXT)}))),
        "before": need(_COMPARED_SIDE), "after": need(_COMPARED_SIDE),
        "window": may(Obj({})), "startup": may(Obj({})),
        "noise_floor": may(Obj({"abs_ms": may(NUMBER), "ratio": may(NUMBER)})),
        "confidence": may(NUMBER), "holds_against": may(TEXT),
        "summary": may(Obj({})),
        "rows": need(ListOf(_ROW)), "markers": may(ListOf(_ROW)),
        "names": may(Obj({})),
        "plain": may(_PLAIN),
    }))


def read_comparison(path: Path) -> Comparison | Refusal:
    """comparison.json at `path`, or why it does not read."""
    return COMPARISON.read(path)


def check_comparison(data: Any) -> Comparison | Refusal:
    """A comparison already in hand (built, or read some other way), or why
    it does not read as one."""
    return COMPARISON.check(data)


# ---------------------------------------------------------------------------
# hunt.json, the investigation record (`hunt.open_new`, `touch`,
# `record_report`, `record_traces` and `conclude` write nothing else)

# 1 from the first record that carried a number. A record with none is of the
# same shape, written before it was kept.
HUNT_SCHEMA = 1


class Hunt(TypedDict, total=False):
    """One investigation: a person's question and the work done on it."""
    schema: int
    n: int
    question: str
    since: str | None
    scenario: str | None
    config_sha: str | None
    confirmed: dict[str, Any]
    opened_at: str
    touched_at: str
    closed_at: str
    status: str
    collects: int
    analyzes: int
    reports: int
    traces: list[str]
    filed: list[dict[str, Any]]
    conclusion: str | None
    verdict: dict[str, Any]
    verdict_check: dict[str, Any]


# A field that holds what echolot never writes there reads as absent, and the
# rest of the record is read (#580): a record is a person's question and the
# work behind it, worth showing what still reads of it. `"reports": "abc"`,
# `"n": "x"`, `"traces": 7` or a number in `opened_at` stopped `hunt --show`,
# `hunt --list` and every page of `echolot ui` that reads the record, and
# `"question": 5` printed a question nobody asked. A count is a whole one, not
# true or false, never below zero, and `n` is one or more, since it names a
# directory; a trace set is a path.
HUNT: Spec[Hunt] = Spec(
    name="hunt.json", schema=HUNT_SCHEMA, unnumbered=1,
    shape=Obj({
        "n": may(Whole(1), drop=True),
        **{k: may(WHOLE, drop=True) for k in ("collects", "analyzes", "reports")},
        **{k: may(TEXT, drop=True) for k in ("question", "since", "scenario", "config_sha", "status",
                                             "conclusion", "opened_at", "touched_at", "closed_at")},
        **{k: may(Obj({}), drop=True) for k in ("confirmed", "verdict", "verdict_check")},
        "traces": may(ListOf(TEXT, drop=True), drop=True),
        # Whether each report filed is a round or a look (#908): a mark that
        # does not read is left out, and `compare` tells that report from
        # the report itself, as it does one filed before the marks.
        "filed": may(ListOf(Obj({"report": need(Whole(1)), "kind": need(OneOf(("round", "look"))),
                                 "why": may(TEXT, drop=True),
                                 "recordings": may(ListOf(TEXT, drop=True), drop=True)}),
                            drop=True), drop=True),
    }))


def read_hunt(path: Path) -> Hunt | Refusal:
    """An investigation record at `path`, or why it does not read."""
    return HUNT.read(path)


# ---------------------------------------------------------------------------
# collect.json, the collect in flight (`runner.Progress`)

# 1 from the first file that carried a number; one with none is of the same
# shape, written before it was kept.
COLLECT_SCHEMA = 1
# The largest process id `os.kill` takes: it hands the number on as a C int.
PID_MAX = 2**31 - 1


class Collect(TypedDict, total=False):
    """Where the last `collect` stands, as it wrote it while it ran."""
    schema: int
    started: float
    finished: float | None
    pid: int
    done: int
    exit: int
    hunt: int
    scenario: str
    cooling: str
    error: str
    iterations: Any


# Every echolot that writes this file, 0.8.0 on, writes these: the times from
# `time.time()`, the process id from `os.getpid()`. Text in `started`, or in
# `finished` with `exit` set, ended `echolot` and the start page of `echolot
# ui` in a traceback, and so did `Infinity`, which Python's json reads and no
# clock writes, and a whole number too long to become a float, which
# `when.ago` subtracts from one. A pid past what `os.kill` takes ended
# `status --next` as well, and 0 or -1 read as a collect still running, since
# `os.kill` then asks the process's own group or every process (#474). Such a
# file says nothing about a collect: the next `collect` writes it anew.
# `iterations` is not held to a type: `collect` writes the count it was handed
# before it checks it, so `runner.iterations: five` leaves `"five"` in a run
# that failed at once, and the failure's line says why.
COLLECT: Spec[Collect] = Spec(
    name="collect.json", schema=COLLECT_SCHEMA, unnumbered=1,
    shape=Obj({
        "started": need(Finite()), "finished": may(Finite()),
        "pid": may(Whole(1, PID_MAX)),
        **{k: may(Kind((int,), "a whole number")) for k in ("done", "exit", "hunt")},
        **{k: may(TEXT) for k in ("scenario", "cooling", "error")},
    }))


def read_collect(path: Path) -> Collect | Refusal:
    """collect.json at `path`, or why it does not read."""
    return COLLECT.read(path)


# ---------------------------------------------------------------------------
# a job's state, `.echolot/log/<name>.json` (`job.run`): check, setup, dig

JOB_SCHEMA = 1


class Job(TypedDict, total=False):
    """A job the pages started: its steps, and where each stands."""
    schema: int
    job: str
    pid: int
    started: float
    finished: float | None
    what: dict[str, Any]
    why: str
    end: str
    steps: list[dict[str, Any]]


# The same rule as for collect.json: whoever reads a job does sums with the
# times and asks the system about the process id, where text ends in a
# traceback and 0 or -1 asks about this process's own group and reads as a
# job that runs for ever. Each step is a name and a state, with times, an
# exit code and text beside them.
JOB: Spec[Job] = Spec(
    name="job state", schema=JOB_SCHEMA, unnumbered=1,
    shape=Obj({
        "started": need(Finite()), "finished": may(Finite()),
        "pid": may(Whole(1, PID_MAX)),
        "what": may(Obj({})), "why": may(TEXT),
        "steps": need(ListOf(Obj({
            "id": need(TEXT), "status": need(TEXT), "started": may(Finite()), "finished": may(Finite()),
            "exit": may(Kind((int,), "a whole number")), "log": may(TEXT), "shown": may(TEXT),
        }))),
    }))


def read_job(path: Path) -> Job | Refusal:
    """A job's state at `path`, or why it does not read."""
    return JOB.read(path)


# ---------------------------------------------------------------------------
# runs.jsonl, the run log (`recorder.record`): one object per line

# The log is appended to by every version that runs in the project, and each
# line names the version that wrote it (`version`), so its lines carry no
# schema number. A line that is no object is skipped, and a field that holds
# what `record` never writes there is dropped with the line kept (#555): a
# doctor's `facts: [1]` ended `status` in a traceback. A count that is no
# finite number is no count. In `facts`, the ones `status`, `next` and the
# start page read off a self-check are held to what doctor writes: `failed` a
# list, `checks` a count, `sandbox` a name.
#
# `session` is the id of the agent session whose command the line is, and
# `agent` the host that named it (#543). The id is an opaque token, the same
# one `recorder.session` writes: `reflect --from-log` names a report file
# after it, so a hand-edited `../../x` here reads as a line with no session.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class Run(TypedDict, total=False):
    """One line of the run log: one invocation of echolot."""
    ts: str
    cmd: str
    argv: list[Any]
    cwd: str
    exit: int
    ms: float
    version: str
    config: dict[str, Any]
    facts: dict[str, Any]
    error: str
    session: str
    agent: str


RUN: Spec[Run] = Spec(
    name="runs.jsonl",
    shape=Obj({
        **{k: may(TEXT, drop=True) for k in ("ts", "cmd", "cwd", "version", "error", "agent")},
        "session": may(Token(SESSION_ID, "a session id"), drop=True),
        "argv": may(Kind((list,), "a list"), drop=True),
        "exit": may(Kind((int,), "a whole number"), drop=True),
        "ms": may(Finite(), drop=True),
        "config": may(Obj({}), drop=True),
        "facts": may(Obj({"failed": may(Kind((list,), "a list"), drop=True),
                          "checks": may(Kind((int,), "a whole number"), drop=True),
                          "sandbox": may(TEXT, drop=True)}), drop=True),
    }))


def read_runs(path: Path) -> list[Run]:
    """Every run the log at `path` holds, oldest first; none for no file.

    A line that does not read as a run is skipped: one that is no text, no
    JSON, or JSON and no object (`[1,2]`, `null`, `42`), which every reader
    took for a run and ended `echolot`, `status` and every page of `echolot
    ui` in a traceback on its first `.get` (#555). Such a line comes from a
    hand edit, a merge of two clones' logs or another tool appending here,
    and the lines around it still read.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    out: list[Run] = []
    for line in raw.splitlines():
        try:
            run = json.loads(line.decode("utf-8"))
        except ValueError:   # no UTF-8 text, or no JSON; a blank line too
            continue
        got = RUN.check(run)
        if not isinstance(got, Refusal):
            out.append(got)
    return out


__all__ = [
    "ANY", "JOB", "JOB_SCHEMA", "Job", "read_job", "COLLECT", "COLLECT_SCHEMA", "Collect", "PID_MAX", "read_collect", "REPORT", "REPORT_SCHEMA", "Detector", "Report", "check_report", "read_report", "COMPARISON", "COMPARISON_SCHEMA", "FLAG", "HUNT", "HUNT_SCHEMA", "NUMBER", "RUN", "SESSION_ID", "TEXT", "WHOLE",
    "ComparedSide", "Comparison", "Field", "Finite", "Hunt", "Kind", "ListOf", "Obj", "OneOf", "Refusal", "Row",
    "Run", "Shape", "Spec", "Token", "Whole", "check_comparison", "kind_of", "load", "may", "need", "read_comparison",
    "read_hunt", "read_runs",
]
