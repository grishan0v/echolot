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
    that is one of a few words.
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

import json
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
    """A whole number, not true or false, from `low` up."""
    low: int = 0
    what: str = "a whole number"

    def fault(self, value: Any, at: str) -> tuple[Any, str | None]:
        if isinstance(value, bool) or not isinstance(value, int):
            return value, _wrong(value, at, self.what)
        if value < self.low:
            return value, f"`{at}` is {value}, below {self.low}"
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
        if value != value or value in (float("inf"), float("-inf")):
            return value, f"`{at}` is {value}, not a finite number"
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
    }))


def read_hunt(path: Path) -> Hunt | Refusal:
    """An investigation record at `path`, or why it does not read."""
    return HUNT.read(path)


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


RUN: Spec[Run] = Spec(
    name="runs.jsonl",
    shape=Obj({
        **{k: may(TEXT, drop=True) for k in ("ts", "cmd", "cwd", "version", "error")},
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
    "ANY", "COMPARISON", "COMPARISON_SCHEMA", "FLAG", "HUNT", "HUNT_SCHEMA", "NUMBER", "RUN", "TEXT", "WHOLE",
    "ComparedSide", "Comparison", "Field", "Finite", "Hunt", "Kind", "ListOf", "Obj", "OneOf", "Refusal", "Row",
    "Run", "Shape", "Spec", "Whole", "check_comparison", "kind_of", "load", "may", "need", "read_comparison",
    "read_hunt", "read_runs",
]
