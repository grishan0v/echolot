"""A wrapper over Perfetto Trace Processor plus the .sql detector parser.

A detector is a self-contained .sql file with metadata in its header:

    -- @id: main_thread_block
    -- @title: Where the main thread spent its time
    -- @why: ...
    -- @param: min_slice_ms = 16
    -- @identity: location, detail

@param values are defaults. The project config overrides them. A number is
followed by its unit and a string by nothing, and what the parameter means
comes after a dash, on as many lines as it takes:

    -- @param: min_overrun_ms = 4 ms — how far past its deadline a frame
    --       has to finish to count as late
    -- @param: name_glob = *GC — GLOB over slice names: the collections

@column says what one column of the rows means in this detector, the same
way: `-- @column: max_ms — the worst frame's overrun`. A detector names every
column its query returns. Both are what `echolot explain` prints (#494).

@identity names the columns that tell one row of the result from another —
what the query GROUP BYs, as it reaches the report. It defaults to `location`
and only needs saying when a detector groups by something else as well; see
Detector.identity for what goes wrong when it is left unsaid.

@needs names what the trace has to hold for the detector's silence to mean
anything: `-- @needs: platform_state`, straight after `@why`. A detector that
reads only what every recording `collect` makes holds says nothing. See
needs.py for the sources and for what `analyze` does with them.

@plain_title, @plain_why and @plain_clean are the detector in words for
someone who has never opened a trace: what was checked, how it touches the
app, and what a clean result says. Fixed text, read by plain.py; the header
ends with them, after the last of the keys above.

A line reading `-- @intervals` after the query starts a second one, which
says what stretches of the main thread's time the rows of the first stand
for; see Detector.render_intervals. A line reading `-- @samples` starts a
third, which picks out the callstack samples behind each row; see
Detector.render_samples.

A first query that finds its pieces in several steps writes them down once,
`CREATE OR REPLACE PERFETTO TABLE _det_<id> AS …;`, before the statement
that returns the rows, and the queries after it read that table instead of
repeating the steps; see _own_tables.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import os
import platform
import re
import shutil
import subprocess
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import needs as needs_mod
from . import orphans, sandbox
from .config import NUMBER, ConfigError

_CALIB = re.compile(
    r"^(\w+)\s*=\s*(p|top)(\d+)\s*\(\s*(\w+)\s*\)\s*(?:\*\s*([\d.]+))?$")
_META_KEY = re.compile(r"^\s*--\s*@(\w+)\s*:\s*(.*?)\s*$")
_META_CONT = re.compile(r"^\s*--\s+(\S.*?)\s*$")
_META_BLANK = re.compile(r"^\s*--\s*$")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
# The trailing LIMIT is stripped only when measuring the distribution.
_LIMIT_TAIL = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)
# Where a detector's second and third queries begin: a line of its own each,
# the first of them straight after the first query, so that the first still
# ends in its LIMIT.
_SECTION = re.compile(r"^--\s*@(intervals|samples)\s*$", re.MULTILINE)
# Where the words begin in `@param` and `@column`: a dash with space before
# it, and space or the end of the line after it, the words then going on
# below. A GLOB has no use for an em dash, and a hyphen it may well hold.
_MEANING = re.compile(r"\s+—(?:\s+|$)")
# A number and its unit: `4 ms`, `1.5 ×`, `50 %`.
_WITH_UNIT = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s+(\S+)$")
# A table a detector's first query writes for the queries after it, and any
# name of that shape a query reads. Looked for with the comments left out,
# so that a sentence about a table is not taken for one.
_WRITES = re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?PERFETTO\s+TABLE\s+(\w+)",
                     re.IGNORECASE)
_OWN_NAME = re.compile(r"\b_det_\w+")
_COMMENT = re.compile(r"--[^\n]*")


@dataclass(frozen=True)
class Calibration:
    """How to derive a threshold from a known-healthy run.

    Declared in the detector header right next to the threshold itself:

        -- @param: min_slice_ms = 16
        -- @calibrate: min_slice_ms = top10(self_ms) * 1.5

    Two forms, and the difference between them matters.

    `topN(column)` is the Nth largest value. It reads as "on a healthy run this
    detector should produce no more than N rows", so it sets the report size
    directly and does not depend on how many groups the sample holds. It is
    taken over one run's rows, and `calibrate` takes the median across runs:
    over the rows of k runs pooled, N would count rows of all of them.

    `pNN(column)` is a percentile. Fine when the population is stable, but on
    live traces it jumps around: a cold start has 175 distinct slice names, a
    minute of gameplay 4729. p95 gave a sensible 27 ms in the first case and
    0.8 ms in the second, above which more than two hundred rows remained.
    Hence topN by default.

    Only thresholds that zero OPENS are calibrated: the measuring pass sets
    them to zero to see the full distribution. A ratio like max_covered_pct is
    not calibrated this way — 50% stays 50% on any device, and zeroing it would
    shut the filter completely.
    """
    param: str
    kind: str        # 'p' — percentile, 'top' — Nth largest
    n: int
    column: str
    factor: float

    @property
    def expr(self) -> str:
        return f"{self.kind}{self.n}({self.column})"

    def needs(self) -> int:
        """The minimum number of values this form is meaningful on."""
        return self.n if self.kind == "top" else 1

    def value(self, values: list[float]) -> float:
        ordered = sorted(values, reverse=True)
        if self.kind == "top":
            return ordered[self.n - 1]
        return _percentile(values, self.n)


def _percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p / 100.0
    low = int(k)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


@dataclass
class Header:
    """What a detector's comment header says, as `parse_header` reads it."""
    meta: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    calibrations: list[Calibration] = field(default_factory=list)
    # Per parameter, what its number is counted in: `ms`, `frames`; empty
    # for a string, and for a number whose header does not say.
    units: dict[str, str] = field(default_factory=dict)
    # Per parameter, and per column of the rows, what it means in words.
    meanings: dict[str, str] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)


def parse_meta(text: str):
    """The header's keys, parameters and calibrations: `parse_header`, less
    the words."""
    h = parse_header(text)
    return h.meta, h.params, h.calibrations


def _meaning(value: str) -> tuple[str, str]:
    """`value — meaning`, split at the dash: what is left of it, and the words
    after it, empty when it has none."""
    head, *rest = _MEANING.split(value, maxsplit=1)
    return head.strip(), (rest[0].strip() if rest else "")


def parse_header(text: str) -> Header:
    """Parses a detector's comment header.

    A key's value may span several lines: a line shaped `--   text` with no
    `@key:` of its own counts as a continuation. A blank `--` line ends the
    continuation, so notes placed after the params do not get glued onto @why.
    The header ends at the first line that is not a comment.

    A `@param` or a `@column` line goes on the same way, into what the
    parameter or the column means.
    """
    h = Header()
    meta, params, calibrations = h.meta, h.params, h.calibrations
    # Where a continuation goes: a key of `meta`, or a parameter's or a
    # column's words.
    current: tuple[str, str] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            current = None
            continue
        if not stripped.startswith("--"):
            break

        m = _META_KEY.match(line)
        if m:
            key, value = m.group(1), m.group(2)
            if key == "param":
                if "=" not in value:
                    raise ValueError(f"@param without '=': {value}")
                name, raw = value.split("=", 1)
                name = name.strip()
                raw, h.meanings[name] = _meaning(raw.strip())
                # A number the unit follows; anything else is the value as it
                # stands, a mask with its spaces, or a number nobody gave a unit.
                n = _WITH_UNIT.match(raw)
                params[name], h.units[name] = ((_coerce(n.group(1)), n.group(2)) if n
                                               else (_coerce(raw), ""))
                current = ("param", name)
            elif key == "column":
                name, words = _meaning(value)
                if not re.fullmatch(r"\w+", name):
                    raise ValueError(f"@column: expected 'name — what it means', got: {value}")
                if name in h.columns:
                    raise ValueError(f"@column: {name} is said twice")
                h.columns[name] = words
                current = ("column", name)
            elif key == "module":
                # A stdlib module the query needs. Kept out of the SQL body
                # because a detector is run as one statement, and an INCLUDE
                # in front of the SELECT would make it two. Declared here, the
                # runner can also load it once per session instead of per row.
                meta["module"] = (meta.get("module", "") + "," + value).strip(",")
                current = None
            elif key == "calibrate":
                m = _CALIB.match(value)
                if not m:
                    raise ValueError(
                        f"@calibrate: expected 'param = topN(column) * K' or "
                        f"'param = pNN(column) * K', got: {value}"
                    )
                calibrations.append(Calibration(
                    param=m.group(1),
                    kind=m.group(2),
                    n=int(m.group(3)),
                    column=m.group(4),
                    factor=float(m.group(5)) if m.group(5) else 1.0,
                ))
                current = None
            else:
                meta[key] = value
                current = ("meta", key)
            continue

        if _META_BLANK.match(line):
            current = None
            continue

        cont = _META_CONT.match(line)
        if cont and current:
            kind, key = current
            into = {"meta": meta, "param": h.meanings, "column": h.columns}[kind]
            into[key] = f"{into[key]} {cont.group(1)}".strip()

    for c in calibrations:
        if c.param not in params:
            raise ValueError(
                f"@calibrate refers to a @param that does not exist: {c.param}")
    return h


# Which columns name a row when nothing says otherwise.
DEFAULT_IDENTITY = ("location",)


@dataclass
class Detector:
    id: str
    title: str
    why: str
    params: dict[str, Any] = field(default_factory=dict)
    calibrations: list[Calibration] = field(default_factory=list)
    identity: tuple[str, ...] = DEFAULT_IDENTITY
    # Perfetto stdlib modules this query needs included first.
    modules: tuple[str, ...] = ()
    sql: str = ""
    path: Path | None = None
    # The query after `-- @intervals`, empty for a detector without one.
    intervals_sql: str = ""
    # The query after `-- @samples`, the same.
    samples_sql: str = ""
    # The tables its first query writes for the queries after it,
    # `_det_<id>…`, in the order it writes them; see _own_tables.
    tables: tuple[str, ...] = ()
    # What the trace has to hold for a silent run to mean anything: names
    # from needs.SOURCES, `()` for a detector that reads only what every
    # recording `collect` makes holds.
    needs: tuple[str, ...] = ()
    # The three fixed texts of the plain reading (plain.py), from
    # `@plain_title`, `@plain_why` and `@plain_clean`. Empty for a detector
    # that has not written them, which a test refuses for every shipped one.
    plain_title: str = ""
    plain_why: str = ""
    plain_clean: str = ""
    # Thresholds opened for the merge of repeats though no `@calibrate` line
    # names them (`@bar: param, …`). A bar that is a fixed quantity, one
    # frame, is not calibrated, and a row under it in one repeat is still
    # there (#505); without this it read as absent again.
    bars: tuple[str, ...] = ()
    # `@fires`: the one predicate over the query's output columns that says a
    # row is worth showing (#832), `{{param}}`s and all. A detector that has
    # one measures every row and drops none: its query has no threshold in
    # it, and `render` wraps it as `SELECT *, (<fires>) AS over_bar FROM
    # (…)`. The threshold then decides which rows are shown, never which
    # rows exist, so the merge of repeats, `compare`, `calibrate` and the
    # main thread's account read every measurement, whatever the bar. Empty
    # for a detector still on the old path, whose query filters its rows.
    fires: str = ""
    # `@shape`: the numeric parameters that decide what a row is, rather
    # than whether it is shown: `main_thread_outlier`'s `factor`. Moving one
    # changes the rows themselves. Every other numeric parameter of a
    # detector with `@fires` appears in the predicate.
    shape: tuple[str, ...] = ()
    # The header in words (#494): what each parameter is counted in and
    # means, and what each column of the rows means, in the query's order.
    # Read by `explain`; a test refuses a shipped detector that leaves one
    # unsaid, and `doctor` one whose columns are not the query's.
    units: dict[str, str] = field(default_factory=dict)
    meanings: dict[str, str] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)

    @property
    def sha(self) -> str:
        """What this detector reads the trace with, as twelve hex digits (#507).

        Its queries, the columns that name its rows and the stdlib modules it
        includes: what decides its rows. Comment lines and the run of the
        whitespace do not count, so a sentence reworded in `@why` leaves it
        as it was, and its thresholds do not either, since the report carries
        those itself and `compare` names any that moved. Two reports whose
        detector has the same `sha` read the trace the same way; `compare`
        gives no verdict on the rows of one whose `sha` differs, since a move
        there may be the tool's.
        """
        import hashlib

        def code(text: str) -> str:
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
            return " ".join(" ".join(lines).split())

        body = "\n".join([code(self.sql), code(self.intervals_sql), code(self.samples_sql),
                          ",".join(self.identity), ",".join(self.modules)]
                         + ([code(self.fires)] if self.fires else []))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def from_file(cls, path: Path) -> "Detector":
        text = path.read_text(encoding="utf-8")
        try:
            header = parse_header(text)
            meta, params, calibrations = header.meta, header.params, header.calibrations
            needs = needs_mod.parse(meta.get("needs"))
        except ValueError as e:
            raise ValueError(f"{path.name}: {e}") from e
        first, *rest = _SECTION.split(text)
        sections: dict[str, str] = {}
        for name, body in zip(rest[::2], rest[1::2], strict=True):
            if name in sections:
                # The second would quietly win, and the first would be a
                # query nobody runs that still reads as the one in force.
                raise ValueError(f"{path.name}: two `-- @{name}` sections")
            sections[name] = body
        det_id = meta.get("id", path.stem)
        tables = _own_tables(det_id, first, sections, path.name)
        return cls(
            id=det_id,
            title=meta.get("title", path.stem),
            why=meta.get("why", ""),
            params=params,
            calibrations=calibrations,
            identity=_identity(meta.get("identity")),
            modules=tuple(m.strip() for m in meta.get("module", "").split(",")
                          if m.strip()),
            sql=first,
            path=path,
            intervals_sql=sections.get("intervals", ""),
            samples_sql=sections.get("samples", ""),
            tables=tables,
            needs=needs,
            bars=tuple(b.strip() for b in meta.get("bar", "").split(",") if b.strip()),
            fires=meta.get("fires", ""),
            shape=tuple(p.strip() for p in meta.get("shape", "").split(",") if p.strip()),
            plain_title=meta.get("plain_title", ""),
            plain_why=meta.get("plain_why", ""),
            plain_clean=meta.get("plain_clean", ""),
            units=header.units,
            meanings=header.meanings,
            columns=header.columns,
        )

    def render_open(self, overrides: dict[str, Any] | None = None) -> str:
        """The query with thresholds opened up — for measuring the distribution.

        Calibrated thresholds, and the `@bar`s, are zeroed so HAVING lets everything through, and
        LIMIT is stripped: a statistic over a truncated top twenty would be a
        statistic over the tail.
        """
        if self.fires:
            # Every row is in what `render` returns already.
            return self.render(overrides)[0]
        resolved = dict(self.params)
        resolved.update(overrides or {})
        for param in [c.param for c in self.calibrations] + list(self.bars):
            resolved[param] = 0
        sql, _ = self.render(resolved)
        return _LIMIT_TAIL.sub("", sql)

    def check(self, overrides: dict[str, Any] | None = None,
              source: str = "") -> None:
        """A value of the wrong kind for the parameter it is replacing.

        `@param` declares the default and, by what the default IS, the kind:
        a number for a threshold, a string for a mask. An override arrives
        from `echolot.yml` or from `--set` through `yaml.safe_load`, which is
        perfectly happy to hand back a string where a number belongs. Nothing
        looked.

        It matters because thresholds are substituted into the query body
        unquoted — `>= {{min_slice_ms}} * 1000000` — so whatever arrives lands
        in the arithmetic as itself. Two ways that goes wrong, and the quiet
        one is the reason this exists:

            min_slice_ms: 16ms        →  a SQL error, reported against the
                                         detector's name, which is a long way
                                         from "a threshold has to be a number"
            min_slice_ms: 16 OR 1=1   →  `HAVING x >= 16 OR 1=1 * 1000000`,
                                         valid SQL with another meaning. Every
                                         row passes, the detector reports the
                                         whole trace, and nothing says a word.

        int and float are one kind: `calibrate` derives 41.2 for a default of
        16, and that is the feature working as designed. A bool is not a
        number here whatever Python thinks — `min_slice_ms: yes` is a mistake,
        not a threshold of one. Nor is `.nan` or `.inf`, which YAML reads as
        floats: either went into the query as the bare word `nan` or `inf`,
        and the detector failed with "no such column" (#852). The kind is
        the config's own `NUMBER`, the one its declared keys use.

        Raises ValueError. `plan_detectors` turns it into a ConfigError before
        a trace is opened, because that is what it is.
        """
        where = f" ({source})" if source else ""
        for key, value in (overrides or {}).items():
            if key not in self.params:
                # A key that is not a parameter substitutes nothing, so the
                # threshold silently stays at its default: `min_slice` for
                # `min_slice_ms` reads as calibration that did not take, and
                # the report says `params_source: config` either way. Refused
                # with the list, which is what `--set` has always done for the
                # same typo typed on the command line.
                raise ValueError(
                    f"{self.id}{where} has no parameter '{key}'. "
                    f"It has: {', '.join(sorted(self.params))}"
                )
            default = self.params[key]
            if isinstance(default, (int, float)):
                ok = NUMBER.accepts(value)
                kind = NUMBER.said
            else:
                ok = isinstance(value, str)
                kind = "a string"
            if not ok:
                raise ValueError(
                    f"{self.id}.{key}{where} must be {kind}, like the "
                    f"detector's own {default!r} — got {value!r}"
                )

    def render(self, overrides: dict[str, Any] | None = None) -> tuple[str, dict]:
        self.check(overrides)
        resolved = dict(self.params)
        resolved.update(overrides or {})
        missing = {
            m for m in _PLACEHOLDER.findall(self.sql + self.fires) if m not in resolved
        }
        if missing:
            raise ValueError(
                f"{self.id}: no values for {sorted(missing)}"
            )
        sql = _PLACEHOLDER.sub(
            lambda m: sql_value(resolved[m.group(1)]), self.sql)
        if self.fires:
            sql = self._wrapped(sql, resolved)
        return sql, resolved

    def _wrapped(self, sql: str, resolved: dict[str, Any]) -> str:
        """The query with its predicate as a column, `over_bar` (#832).

        The last statement is the one that returns the rows; the ones before
        it write the detector's own tables, and stay as they are. Its `LIMIT`
        goes: a row the twentieth place cut off is a measurement like any.
        """
        *head, last = _split_statements(sql)
        last = _LIMIT_TAIL.sub("", last).rstrip()
        fires = _PLACEHOLDER.sub(lambda m: sql_value(resolved[m.group(1)]), self.fires)
        rows = f"SELECT *, ({fires}) AS over_bar FROM (\n{last}\n)"
        # The header stays in front, as it is in front of a query on the old
        # path: what a failing query is, by its `@id`, for whoever reads it.
        header = "".join(ln + "\n" for ln in itertools.takewhile(
            lambda ln: ln.strip().startswith("--"), sql.splitlines()))
        return header + ";\n".join([*head, rows]) + ";"

    def render_intervals(self, resolved: dict[str, Any]) -> str | None:
        """The second query, with the values the first one ran with.

        It answers one question about the rows the first query returned:
        which stretches of the main thread's time they stand for. `analyze`
        runs it straight after the first, with those rows in a table named
        `_rows` — `location` and `detail`, the latter NULL where a row has
        none, and each other column of `@identity`, `thread` or `state` — so
        the query only has to find its way back from a row to the time behind
        it. Which rows cleared a threshold is already decided,
        and repeating the HAVING here would be one more place for the two to
        drift apart. Where the first query found its pieces in steps, it
        wrote them into a table of its own, and this reads that table rather
        than taking the steps again; see _own_tables.

        It returns `ts` and `dur`, in nanoseconds, one line per stretch.
        Stretches may overlap each other and run past the window: the caller
        clips them and counts each moment once, which is the whole point.
        Only the main thread's time, because that is the thread the window's
        length is made of; rows about other threads return nothing.

        None when the detector has no second query. `resolved` is what
        `render` returned, so both queries see the same thresholds.
        """
        return self._render_section(self.intervals_sql, "@intervals", resolved)

    def render_samples(self, resolved: dict[str, Any]) -> str | None:
        """The third query, with the values the first one ran with.

        It picks out the callstack samples behind each row: `location`, and
        `callsite_id`, NULL for a sample that came without a stack. One line
        per sample, from `_samples_by_slice` or `_samples_win`, with the rows
        in `_rows` as for the second query. What the samples name — what ran
        on each stack, and the nearest frame of the project's own code — is
        read in Python, the same way for every detector; see stacks.py.

        `analyze` runs it only when the recording sampled, so that a trace
        without samples gets the report it always got. None when the
        detector has no third query.
        """
        return self._render_section(self.samples_sql, "@samples", resolved)

    def _render_section(self, sql: str, name: str,
                        resolved: dict[str, Any]) -> str | None:
        if not sql.strip():
            return None
        missing = {m for m in _PLACEHOLDER.findall(sql) if m not in resolved}
        if missing:
            raise ValueError(f"{self.id} {name}: no values for {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: sql_value(resolved[m.group(1)]), sql)


def _own_tables(det_id: str, first: str, sections: dict[str, str],
                where: str) -> tuple[str, ...]:
    """The tables a detector's first query writes for the queries after it.

    `@intervals` says what time a detector's rows stand for, and the pieces
    that time is made of are the pieces the rows were counted from. A first
    query that finds them in several steps — `app_init`'s stretch after
    `makeApplication` and the sections in it, `anr_risk`'s stretches with no
    way back to the queue — used to have its second query repeat the steps,
    two copies of one logic with nothing but the tests to say when they
    drifted apart, and the work done twice on every trace (#490). Now it
    writes them down once, before the statement that returns its rows:

        CREATE OR REPLACE PERFETTO TABLE _det_<id> AS …;

    and both read the table. Three rules keep that safe, and each is refused
    as the file is read rather than found out on a trace:

    - The name is `_det_<id>`, or `_det_<id>_` and more. The window's tables
      (`_slice_win`, `_window`, …) are names a detector must not write over,
      and two detectors must not write one table: `load_detectors` refuses
      a name two of them write, `anr`'s `_det_anr_risk` beside `anr_risk`.
    - `OR REPLACE`. The first query runs again in the same session: with its
      thresholds open for the merge of repeats, and once more per trace under
      `calibrate`. A plain CREATE fails the second time.
    - Every `_det_` name its queries read is one its own first query writes.
      A table holds what the last run of its detector left, which another
      detector's query cannot know.

    The second run, the one with the thresholds open, leaves its own pieces
    in the table, so `analyze` reads `@intervals` and `@samples` before it;
    see `analyze_trace`.
    """
    written: list[str] = []
    for m in _WRITES.finditer(_COMMENT.sub("", first)):
        name = m.group(2)
        if name != f"_det_{det_id}" and not name.startswith(f"_det_{det_id}_"):
            raise ValueError(
                f"{where}: its first query writes `{name}`. A table of a "
                f"detector's own is named after it: `_det_{det_id}`, or "
                f"`_det_{det_id}_` and more")
        if not m.group(1):
            raise ValueError(
                f"{where}: `{name}` is written with CREATE PERFETTO TABLE. "
                f"The first query runs again in the same session, with its "
                f"thresholds open, and a plain CREATE fails there: write "
                f"CREATE OR REPLACE PERFETTO TABLE")
        written.append(name)
    for text in (first, *sections.values()):
        for name in _OWN_NAME.findall(_COMMENT.sub("", text)):
            if name not in written:
                raise ValueError(
                    f"{where}: it reads `{name}`, which its first query does "
                    f"not write. A detector reads only the tables of its own")
    return tuple(written)


def _identity(declared: str | None) -> tuple[str, ...]:
    """What `-- @identity:` says, or `location`.

    A row of a detector's result is identified by what the query grouped by.
    `location` alone is the common case and stays the default; a detector that
    also groups by the thread or the thread's state has two rows carrying one
    location in a single run, and merging repeats on the name alone folds two
    different phenomena into one median.
    """
    cols = [c.strip() for c in (declared or "").split(",") if c.strip()]
    if not cols:
        return DEFAULT_IDENTITY
    if "location" not in cols:
        raise ValueError(f"@identity must include location, got: {declared}")
    return tuple(cols)


def sql_value(value: Any) -> str:
    """A placeholder is substituted into the query text verbatim.

    Single quotes are doubled: a slice name with an apostrophe would otherwise
    break the string literal. For numbers this is a no-op.
    """
    return str(value).replace("'", "''")


def toolchain_info(bin_path: str | None = None,
                   source: str | None = None) -> dict[str, Any]:
    """What exactly parsed the trace.

    This rides into report.json for a reason. The strings 'Running', 'R' and
    'binder transaction async' that the detectors match on are invented by
    trace_processor, not by the kernel and not by the app. So when numbers
    diverge between two runs, the first question is whether anything underneath
    changed; this field answers it immediately rather than after an hour of
    digging.

    `source` is who asked for `bin_path`, and it has to be told rather than
    assumed. The field used to read `--tp-binary` for every custom binary,
    including the ones that came from `toolchain.tp_binary` in a `local.yml`
    — a file that is normally gitignored and therefore invisible to whoever
    reads the report next. They would search their history for a flag nobody
    typed while the answer sat in a file beside the config.
    """
    from . import __version__

    # `echolot`: which echolot wrote the report (#507). Two versions that
    # differ in a detector read one trace differently, and `compare` used to
    # take that for a change in the app — 11 rows that held between two
    # reports of the very same fifteen traces.
    info: dict[str, Any] = {"echolot": __version__,
                            "perfetto_package": None,
                            "trace_processor": None,
                            "source": "pinned",
                            "binary": None}
    try:
        from importlib.metadata import version
        info["perfetto_package"] = version("perfetto")
    except Exception:
        pass

    if bin_path:
        # A custom binary bypasses the pin: asking for the package version here
        # would be meaningless.
        info["source"] = source or "--tp-binary"
        info["binary"] = named_binary(bin_path)
        info["trace_processor"] = _binary_version(info["binary"])
        return info

    try:
        manifest, _ = _perfetto_prebuilts()
        info["trace_processor"] = _version_of(manifest[0]["url"])
    except Exception:
        pass
    return info


def _binary_version(bin_path: str) -> str | None:
    try:
        out = subprocess.run([bin_path, "--version"], capture_output=True,
                             text=True, timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0] or None
    except Exception:
        return None


def _version_of(url: str) -> str | None:
    """`v59.1` out of the manifest's URL, which is the only place it is said."""
    match = re.search(r"/v([\d.]+)/", url)
    return f"v{match.group(1)}" if match else None


def _perfetto_prebuilts():
    """The perfetto package's manifest for trace_processor, and its downloader.

    The two pieces of that package's internals echolot uses, behind this one
    function so that a perfetto release which moves either breaks one place.
    Everything else goes through `TraceProcessor`, the package's public API.
    Imported here rather than at the top: `import echolot.main` must not pull
    perfetto in for commands that never open a trace.
    """
    from perfetto.prebuilts.manifests.trace_processor_shell import (
        TRACE_PROCESSOR_SHELL_MANIFEST,
    )
    from perfetto.prebuilts.perfetto_prebuilts import download_or_get_cached
    return TRACE_PROCESSOR_SHELL_MANIFEST, download_or_get_cached


class ToolchainError(ConfigError):
    """The trace_processor a command needs is not here and could not be got.

    A ConfigError on purpose. Every command that opens a trace already turns
    one into `error: …` and exit 2, and a first run offline used to get a
    traceback instead: a CalledProcessError from deep inside perfetto that
    quoted a curl command line and said nothing a person could act on.
    `doctor` catches this one by name, because there it is not a check that
    failed but the ground every check stands on that never arrived.
    """


@dataclass(frozen=True)
class PinnedBuild:
    """The trace_processor the perfetto package pins, for this OS and CPU."""
    version: str | None   # "v59.1"
    arch: str             # the manifest's name for the OS and CPU: "linux-amd64"
    file_name: str        # what perfetto calls the file: "trace_processor_shell"
    url: str
    size: int             # bytes, as the manifest gives them
    sha256: str
    path: Path            # where perfetto keeps it once it is here

    @property
    def size_text(self) -> str:
        return f"{self.size / 1e6:.1f} MB"


def pinned_build() -> PinnedBuild | None:
    """The pinned trace_processor for this machine, and where it lives.

    Never downloads. Worked out the way perfetto works it out, so that the
    destination said before a download is the one the download writes and
    "is it here yet" is answered without starting one: the first manifest
    entry whose `platform` is `sys.platform` and whose `machine` list holds
    `platform.machine()`, kept in ~/.local/share/perfetto/prebuilts under
    its file name with the first sixteen hex digits of its SHA-256 appended
    — the hash in the name is how perfetto trusts the file without hashing
    it again. tests/test_first_run.py holds this path to the one perfetto
    itself computes.

    None when the pin has no build for this machine, or only a placeholder
    with no URL yet.
    """
    manifest, _ = _perfetto_prebuilts()
    plat, machine = sys.platform.lower(), platform.machine().lower()
    for entry in manifest:
        if entry.get("platform") != plat or machine not in entry.get("machine", []):
            continue
        if not entry.get("url"):
            return None
        root, ext = os.path.splitext(entry["file_name"])
        home = Path(os.path.expanduser("~"))
        return PinnedBuild(
            version=_version_of(entry["url"]),
            arch=entry.get("arch") or f"{plat}-{machine}",
            file_name=entry["file_name"],
            url=entry["url"],
            size=int(entry.get("file_size") or 0),
            sha256=entry["sha256"],
            path=(home / ".local" / "share" / "perfetto" / "prebuilts"
                  / f"{root}-{entry['sha256'][:16]}{ext}"),
        )
    return None


def named_binary(bin_path: str | Path) -> str:
    """A binary somebody named, as the absolute path that will be run.

    Checked with `is_file`, which looks in the current directory, and then
    started by `Popen`, which looks a bare name up in PATH: a
    `trace_processor_shell` beside the user passed the check and failed to
    start, or another one on PATH ran instead. A file at the path as named
    is that file; a bare name that is no file here is looked up in PATH, the
    way a shell would; anything else stays where it would have been.
    """
    path = Path(bin_path).expanduser()
    if not path.exists() and path.name == str(bin_path) and (found := shutil.which(str(bin_path))):
        return found
    return str(path.resolve())


def resolve_binary_path(bin_path: str | None = None) -> str:
    """The trace_processor a session runs: the one named, else the pin.

    The pin is downloaded here the first time, and nowhere else. It used to
    happen inside perfetto, on the way to starting the binary, and it
    showed: `Downloading <url>` printed to stdout in front of the first
    `names --json`; a CalledProcessError traceback out of every command on a
    first run offline; and `doctor` paying for the failure twice — once for
    the path it shows, once more for the self-check. Now it is one step: a
    notice on stderr that says what, how big and where; one attempt per
    process, with whatever asks again after a failure handed the same
    ToolchainError without curl being run again; and an error that names the
    cause and the ways round it.

    What is downloaded and how it is checked stay perfetto's: the URL and
    SHA-256 from the manifest the package pins, fetched by its own
    downloader. The pin is the point.

    A named binary comes back as an absolute path — see `named_binary`.
    Whether it is there is the session's question, and doctor shows the path
    either way.
    """
    if bin_path:
        return named_binary(bin_path)
    try:
        pin = pinned_build()
    except ImportError as e:
        raise ToolchainError("the perfetto package is not installed — run: "
                             "pip install perfetto") from e
    if pin is None:
        raise ToolchainError(
            f"the pinned trace_processor has no build for this machine "
            f"({sys.platform} {platform.machine()}). Point --tp-binary, or "
            f"toolchain.tp_binary in local.yml, at a trace_processor_shell "
            f"built for it; the report then says the pin was bypassed.")
    return _fetch(pin)


# Why each destination could not be downloaded, for the rest of this process.
# A command can ask for the binary more than once — doctor for the path it
# shows and again for the self-check, analyze once per trace — and whatever
# asks after a failure gets that failure, not a second wait on the same curl
# that fails the same way.
_FAILED: dict[Path, str] = {}

# What curl's exit status means, for the ones a download usually ends in.
# The number alone is what the CalledProcessError said, and it is all the run
# log would keep: curl's own sentence goes to the terminal and nowhere else.
_CURL_EXITS = {
    5: "the proxy's name did not resolve",
    6: "the server's name did not resolve",
    7: "there was no connection to the server",
    22: "the server answered with an HTTP error",
    28: "it timed out",
    35: "the TLS handshake failed",
    56: "the connection broke off",
    60: "the server's certificate was not trusted, which a proxy that "
        "inspects TLS causes",
}


def _fetch(pin: PinnedBuild) -> str:
    """The pinned binary's path, downloading it first when it is not here."""
    if pin.path.is_file():
        return str(pin.path)
    if pin.path in _FAILED:
        raise ToolchainError(_FAILED[pin.path])
    # stdout first: whatever the command already printed there belongs above
    # this, and behind a pipe it would otherwise wait for the exit.
    sys.stdout.flush()
    print(_notice(pin), file=sys.stderr, flush=True)
    _, download = _perfetto_prebuilts()
    # One download at a time per build, across processes: the files that
    # appear while this one holds the lock are this attempt's own, and a
    # failure no longer deletes another echolot's download in progress. The
    # second to get the lock finds the binary there.
    with _download_lock(pin):
        if pin.path.is_file():
            return str(pin.path)
        return _download(pin, download)


@contextlib.contextmanager
def _download_lock(pin: PinnedBuild):
    """An exclusive lock for this build; none off POSIX.

    In the system's temporary directory rather than in perfetto's cache,
    which holds nothing of ours.
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    import tempfile
    with open(Path(tempfile.gettempdir()) / f"echolot-{pin.file_name}.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


def _download(pin: PinnedBuild, download) -> str:
    before = _partials(pin)
    try:
        # perfetto announces the download itself, with a `print` — to
        # stdout, which is the result channel: the first `names --json` came
        # out with `Downloading <url>` in front of the JSON. The notice above
        # says that and more, where notes go.
        with contextlib.redirect_stdout(io.StringIO()):
            return download(file_name=pin.file_name, url=pin.url,
                            sha256=pin.sha256)
    except BaseException as e:
        # The file this attempt created goes with it. perfetto leaves it
        # under its temporary name when curl fails or the hash does not
        # match, and names the next attempt's file afresh, so every failure
        # left one more in the cache: part of the binary, or a whole file
        # that is not the pin. Only this build's temporary name, and only a
        # file that was not there before the attempt: the binary and other
        # builds' files never have that name, and an earlier run's leftover
        # was there before. Nothing is said about it either way; the error
        # below is what happened.
        #
        # Ctrl-C too: it skipped an `except Exception`, and the file stayed in
        # the cache for good. An interruption is not a failure to remember,
        # though — the next attempt in this process may well succeed.
        for leftover in _partials(pin) - before:
            with contextlib.suppress(OSError):
                leftover.unlink()
        if not isinstance(e, Exception):
            raise
        _FAILED[pin.path] = _cannot_fetch(pin, _cause(e))
        raise ToolchainError(_FAILED[pin.path]) from e


def _partials(pin: PinnedBuild) -> set[Path]:
    """This build's downloads in progress, or abandoned, in perfetto's cache.

    perfetto downloads into `<binary>.<number>.tmp` beside where the binary
    goes — the number random, so two downloads at once do not share a file —
    and gives it the pinned name only once its SHA-256 matched. Nothing else
    has that shape: not the binary, not another build's file.
    """
    shape = re.compile(re.escape(pin.path.name) + r"\.[0-9]+\.tmp")
    try:
        return {p for p in pin.path.parent.iterdir() if shape.fullmatch(p.name)}
    except OSError:
        return set()


def _notice(pin: PinnedBuild) -> str:
    """What is about to be downloaded, how big, from where and into where.

    Said once, before curl starts and its progress bar with it, on stderr
    with the other notes: stdout is what the command answers with.
    """
    return (f"[i] trace_processor {pin.version} is not on this machine yet — "
            f"downloading it once, {pin.size_text}, the build the perfetto "
            f"package pins:\n"
            f"      from {pin.url}\n"
            f"      into {pin.path}")


def _cause(e: Exception) -> str:
    """Why the download failed, in words: the half of the error that varies."""
    if isinstance(e, subprocess.CalledProcessError):
        gloss = _CURL_EXITS.get(e.returncode)
        return (f"curl exited with status {e.returncode}"
                + (f" — {gloss}" if gloss else ""))
    if isinstance(e, FileNotFoundError) and shutil.which("curl") is None:
        return "curl is not installed, or not on PATH"
    if str(e).startswith("Checksum mismatch"):
        # perfetto's own words for it. The file it hashed was under a
        # temporary name, and `_fetch` removes it: the pinned name is only
        # ever given to a file whose hash matched.
        return ("what arrived is not the file the pin names — its SHA-256 "
                "differs — so it was not put in place. A proxy or a captive "
                "portal answering in the server's stead does this")
    return str(e) or type(e).__name__


def _cannot_fetch(pin: PinnedBuild, cause: str) -> str:
    """The error for a download that failed: the cause, then the ways round it.

    All three, whatever the cause: the cause says which one is likely, and
    the copy works where the other two cannot. Whole without the notice
    printed before it, because the run log keeps this and not that.

    A fourth, first, when the command ran in Codex's sandbox: there the
    download has neither the network nor a place to write, since perfetto
    keeps the binary outside the workspace, and the likely cause is that.
    """
    in_codex = ""
    if sandbox.host() == sandbox.CODEX:
        in_codex = (f"  - running echolot outside Codex's sandbox, where this "
                    f"command ran: approve that when Codex asks, or let it out "
                    f"for good with the rule `{sandbox.rule_command()}` writes, "
                    f"run outside the sandbox;\n")
    return (
        f"trace_processor {pin.version} could not be downloaded: {cause}.\n"
        f"  Every trace is read with it, so nothing that opens one can run "
        f"until it is here. Any one of these puts it in place:\n"
        f"{in_codex}"
        f"  - a network that reaches {urlsplit(pin.url).netloc}. Behind a "
        f"proxy, export HTTPS_PROXY — curl honours it — and run this again;\n"
        f"  - curl, installed and on PATH: the download runs through it;\n"
        f"  - offline, a copy of ~/.local/share/perfetto/prebuilts/"
        f"{pin.path.name} from a machine with the same OS and CPU "
        f"({pin.arch}) that already has it, put at {pin.path}. Keep the "
        f"name: it carries the hash of the contents, and a file under any "
        f"other name is not looked at.")


def _coerce(raw: str) -> Any:
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def load_detectors(directory: Path) -> list[Detector]:
    found = [Detector.from_file(p) for p in sorted(directory.glob("*.sql"))]
    # A name made of another detector's: `anr` may write `_det_anr_risk` by
    # its own rule, and would write over `anr_risk`'s pieces.
    owner: dict[str, str] = {}
    for d in found:
        for table in d.tables:
            if table in owner:
                raise ValueError(
                    f"{d.path.name if d.path else d.id}: `{table}` is written by "
                    f"`{owner[table]}` too. Two detectors cannot share a table")
            owner[table] = d.id
    return found


def render_sql(text: str, params: dict[str, Any]) -> str:
    missing = {m for m in _PLACEHOLDER.findall(text) if m not in params}
    if missing:
        raise ValueError(f"no values for {sorted(missing)}")
    return _PLACEHOLDER.sub(lambda m: sql_value(params[m.group(1)]), text)


class TraceSession:
    """A thin wrapper over perfetto.trace_processor.TraceProcessor, connected
    to a trace_processor_shell started here (tpshell.py).

    `extra` is packets for trace_processor to read after the trace, as if the
    trace had carried them — the build's R8 mapping (mapping.py). They are
    streamed behind the file rather than written into a copy of it: a trace
    runs to hundreds of megabytes, and the file stays as it was recorded.
    """

    def __init__(self, trace_path: str | Path, binary: str | None = None,
                 extra: bytes | None = None, *, cut_ok: bool = False):
        self._tp = None
        self._signals = False
        # Where the file ends inside its last packet, said; None for a file
        # that is whole or that this cannot tell about. See `cut_short`.
        self.cut: str | None = None
        try:
            from perfetto.trace_processor import (
                TraceProcessor,
                TraceProcessorException,
            )
        except ImportError as e:
            raise RuntimeError(
                "the perfetto package is not installed — run: pip install perfetto"
            ) from e

        # Named on the command line, and the commonest thing to get wrong
        # about it is the path — a glob that matched nothing expands to
        # itself, and `analyze .echolot/traces/*.perfetto-trace` on an empty
        # directory hands that string straight to here. It came out as a bare
        # FileNotFoundError traceback out of the CLI.
        #
        # Checked here rather than in each command: this is the one place a
        # trace is opened, so nothing can go round it.
        path = Path(trace_path)
        if not path.is_file():
            raise ConfigError(
                f"no such trace: {path}"
                + ("  (a glob that matches nothing is passed through as "
                   "written — is the directory empty?)" if "*" in str(path)
                   else ""))
        # Read before trace_processor starts. perfetto started it first and
        # stopped it only on its own parse error: a trace this process may
        # not read raised past that, out of the CLI as a traceback, and left
        # the shell running with init as its parent.
        try:
            with path.open("rb") as f:
                f.read(1)
        except OSError as e:
            raise ConfigError(f"cannot read trace {path}: {e.strerror or e}") from e
        # By its absolute path. perfetto reads a string that does not start
        # with `/` or `.` and has a colon in it as `resolver:args`, so
        # `run12:00.perfetto-trace` failed with `KeyError: 'run12'`.
        path = path.resolve()
        # A recording that lost its tail: a pull that was interrupted, a full
        # disk, a download that stopped early. trace_processor drops the
        # unfinished packet without a word, and what the tail held went with
        # it — in a launch recording, the process list, the last frames and
        # the memory counters. The report on it looked sound, or `analyze`
        # refused it with advice to change a project.process that was right
        # (#568). Walked before trace_processor starts: it costs a read of
        # each packet's length, a hundredth of a second for most traces.
        self.cut = cut_short(path)
        if self.cut and not cut_ok:
            raise ConfigError(
                f"{path}: {self.cut}. What a recording keeps at its end — the "
                f"process list, the last frames, the memory counters — is "
                f"gone with it, and a report on the rest reads as whole. Pull "
                f"or copy it again, or leave it out and run again.")

        # The binary is settled before the shell starts: left to perfetto,
        # the pin would be downloaded deep inside its start, with none of
        # what resolve_binary_path says.
        binary = resolve_binary_path(binary)
        if not Path(binary).is_file():
            # A path named by `--tp-binary` or `toolchain.tp_binary` that is
            # not there. perfetto refused it with a bare Exception, which came
            # out of `names` and `analyze` as a traceback.
            raise ConfigError(
                f"no trace_processor at {binary} — the path given for it "
                f"(--tp-binary, or toolchain.tp_binary in the config) is not "
                f"a file")
        # From here to close(), SIGTERM and SIGHUP unwind through close()
        # rather than end the process with trace_processor still running;
        # and the shell is stopped if the start fails, an interrupt
        # included. See orphans.py (#570).
        #
        # The shell is started here and asked every 10 ms whether it
        # listens; perfetto's client is given its address. perfetto, left to
        # start it, waited a second for a shell that listens in 30 ms
        # (tpshell.py, #487).
        self._signals = orphans.hold_signals()
        try:
            self._tp = orphans.open_processor(
                binary, lambda address: TraceProcessor(
                    trace=_then(path, extra), remote=address))
        except TraceProcessorException as e:
            self.close()
            # A file that is there and is not a trace: a capture cut short, a
            # log saved under the wrong name. It came out of the CLI as a
            # PerfettoException traceback, and one such file among the
            # repeats took a whole multi-trace `analyze` with it without
            # saying which. Only the parse is the file's fault — a
            # trace_processor that would not start is not, and stays what it
            # is.
            said = str(e)
            if "parsing trace" not in said:
                raise
            why = said.split("Error message:", 1)[-1].strip().rstrip(".")
            raise ConfigError(
                f"{path}: trace_processor cannot read this file — {why}. A "
                f"capture that was cut short, or a file that is not a trace; "
                f"leave it out and run again.") from e
        except OSError as e:
            self.close()
            # Before trace_processor starts, a socket is bound to find a
            # free port for it (tpshell._free_port). An agent's sandbox with
            # no network refuses that, and it came out of `analyze` as a
            # PermissionError traceback and out of `doctor` as "Operation not
            # permitted" — both read as a broken install (#190).
            if not sandbox.refused(e):
                raise
            raise sandbox.trace_processor_refused(e) from e
        except BaseException:
            self.close()
            raise
        stopped = self._stopped_short(path)
        if stopped:
            self.close()
            raise ConfigError(stopped)

    def _stopped_short(self, path: Path) -> str | None:
        """What to say when the load ended before the tables a trace ends with.

        A gzip stream with no end: trace_processor finds it out at the end of
        the file and says so only on its own command line. Through perfetto's
        client, which drops what the end-of-file step answers, the session
        opens with no `process`, no `slice` — and the first query echolot
        sent, the process list, ended `probe`, `names` and `analyze` in a
        traceback reading "no such table: process" (#578). Every recording
        has the table, empty or not; a load without it did not finish.
        """
        try:
            self.query("SELECT 1 FROM process LIMIT 1")
            return None
        except Exception as e:
            if "no such table" not in str(e):
                raise
        if _is_gzip(path):
            return (f"{path}: the gzip stream ends early — the file was not "
                    f"pulled or written whole. Pull or copy it again, or leave "
                    f"it out and run again.")
        return (f"{path}: trace_processor stopped before the end of this file "
                f"and built none of a trace's tables from it. A capture that "
                f"was cut short, or a file that is not a trace; leave it out "
                f"and run again.")

    def exec_script(self, sql: str) -> None:
        """Runs a multi-statement script (DDL), discarding the output."""
        for stmt in _split_statements(sql):
            orphans.check_joined()
            list(self._tp.query(stmt))

    def query(self, sql: str) -> list[dict[str, Any]]:
        # A worker's session closed from the main thread (#488): the run
        # was stopped, and this ends the worker rather than every detector
        # failing in turn. See orphans.Group.
        orphans.check_joined()
        rows = []
        for r in self._tp.query(sql):
            rows.append(dict(r.__dict__))
        return rows

    def close(self) -> None:
        tp, self._tp = self._tp, None
        if tp is not None:
            orphans.close(tp)
        if self._signals:
            self._signals = False
            orphans.let_go()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def cut_short(path: Path) -> str | None:
    """Where a recording ends inside its last packet, in words; None when it
    does not, or when this cannot tell.

    A trace file is a run of length-prefixed packets — field 1 of the `Trace`
    message, each a 0x0A tag, a varint length and that many bytes — so a file
    cut at any byte but a packet's last disagrees with the lengths it
    declares. Only that shape is walked: a gzip, a JSON trace or a systrace
    starts otherwise and is left to trace_processor, and so is a file whose
    very first packet is unfinished, which trace_processor refuses by itself.
    """
    try:
        size = path.stat().st_size
        f = path.open("rb")
    except OSError:
        return None
    pos = whole = 0
    with f:
        while pos < size:
            f.seek(pos)
            head = f.read(11)
            if head[0] != 0x0A:
                return None
            length, used = _varint(head, 1)
            if used is None and len(head) == 11:
                return None                    # no length is that long: not a trace
            if used is None or pos + used + length > size:
                if not whole:
                    return None
                short = ("" if used is None
                         else f", {pos + used + length - size:,} bytes short")
                return (f"the recording is cut short: it ends at byte {size:,}, "
                        f"inside its last packet{short} — it was not pulled or "
                        f"written whole")
            pos += used + length
            whole += 1
    return None


def _varint(buf: bytes, at: int) -> tuple[int, int | None]:
    """The varint at `at` and the bytes up to its end; None for the second
    when `buf` ends before it does."""
    value = shift = 0
    for i in range(at, len(buf)):
        value |= (buf[i] & 0x7F) << shift
        if buf[i] < 0x80:
            return value, i + 1
        shift += 7
    return value, None


def _is_gzip(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _then(path: Path, extra: bytes | None):
    """The trace's bytes, then `extra`.

    A trace is a run of packets, so packets added at its end are read as part
    of it — which is how Perfetto's own tools attach a deobfuscation map.
    """
    with path.open("rb") as trace:
        while chunk := trace.read(32 << 20):
            yield chunk
    if extra is not None:
        yield extra


def _split_statements(sql: str) -> list[str]:
    """A script into statements, with comment lines dropped first.

    First, because the split is on `;` and prose has semicolons in it. A
    sentence in a header comment used to cut the script in half, leaving one
    fragment that begins mid-word and fails to parse and another that silently
    never runs — and the error names a line of English, which reads as
    anything but "your comment has a semicolon in it". Twice in one sitting.

    And a `;` inside a string literal ends nothing. An anchor or a name mask
    is a quoted value from the config, and ART names a class initialisation
    after its descriptor, `Lcom/example/app/Store;`: split there, the
    literal was cut in half and `analyze` stopped on a syntax error. A piece
    is added to the statement until SQLite calls it complete.
    """
    body = "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    )
    out: list[str] = []
    buf = ""
    for piece in body.split(";"):
        buf += piece + ";"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()[:-1].strip()
            if stmt:
                out.append(stmt)
            buf = ""
    rest = buf.strip()[:-1].strip()
    if rest:
        out.append(rest)
    return out
