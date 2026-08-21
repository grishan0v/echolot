"""A wrapper over Perfetto Trace Processor plus the .sql detector parser.

A detector is a self-contained .sql file with metadata in its header:

    -- @id: main_thread_block
    -- @title: Where the main thread spent its time
    -- @why: ...
    -- @param: min_slice_ms = 16
    -- @identity: location, detail

@param values are defaults. The project config overrides them.

@identity names the columns that tell one row of the result from another —
what the query GROUP BYs, as it reaches the report. It defaults to `location`
and only needs saying when a detector groups by something else as well; see
Detector.identity for what goes wrong when it is left unsaid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ConfigError

_CALIB = re.compile(
    r"^(\w+)\s*=\s*(p|top)(\d+)\s*\(\s*(\w+)\s*\)\s*(?:\*\s*([\d.]+))?$")
_META_KEY = re.compile(r"^\s*--\s*@(\w+)\s*:\s*(.*?)\s*$")
_META_CONT = re.compile(r"^\s*--\s+(\S.*?)\s*$")
_META_BLANK = re.compile(r"^\s*--\s*$")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
# The trailing LIMIT is stripped only when measuring the distribution.
_LIMIT_TAIL = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Calibration:
    """How to derive a threshold from a known-healthy run.

    Declared in the detector header right next to the threshold itself:

        -- @param: min_slice_ms = 16
        -- @calibrate: min_slice_ms = top10(self_ms) * 1.5

    Two forms, and the difference between them matters.

    `topN(column)` is the Nth largest value. It reads as "on a healthy run this
    detector should produce no more than N rows", so it sets the report size
    directly and does not depend on how many groups the sample holds.

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


def parse_meta(text: str):
    """Parses a detector's comment header.

    A key's value may span several lines: a line shaped `--   text` with no
    `@key:` of its own counts as a continuation. A blank `--` line ends the
    continuation, so notes placed after the params do not get glued onto @why.
    The header ends at the first line that is not a comment.
    """
    meta: dict[str, str] = {}
    params: dict[str, Any] = {}
    calibrations: list[Calibration] = []
    current: str | None = None

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
                params[name.strip()] = _coerce(raw.strip())
                current = None
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
                current = key
            continue

        if _META_BLANK.match(line):
            current = None
            continue

        cont = _META_CONT.match(line)
        if cont and current:
            meta[current] = f"{meta[current]} {cont.group(1)}".strip()

    for c in calibrations:
        if c.param not in params:
            raise ValueError(
                f"@calibrate refers to a @param that does not exist: {c.param}")
    return meta, params, calibrations


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

    @classmethod
    def from_file(cls, path: Path) -> "Detector":
        text = path.read_text(encoding="utf-8")
        try:
            meta, params, calibrations = parse_meta(text)
        except ValueError as e:
            raise ValueError(f"{path.name}: {e}") from e
        return cls(
            id=meta.get("id", path.stem),
            title=meta.get("title", path.stem),
            why=meta.get("why", ""),
            params=params,
            calibrations=calibrations,
            identity=_identity(meta.get("identity")),
            modules=tuple(m.strip() for m in meta.get("module", "").split(",")
                          if m.strip()),
            sql=text,
            path=path,
        )

    def render_open(self, overrides: dict[str, Any] | None = None) -> str:
        """The query with thresholds opened up — for measuring the distribution.

        Calibrated thresholds are zeroed so HAVING lets everything through, and
        LIMIT is stripped: a statistic over a truncated top twenty would be a
        statistic over the tail.
        """
        resolved = dict(self.params)
        resolved.update(overrides or {})
        for c in self.calibrations:
            resolved[c.param] = 0
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
        not a threshold of one.

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
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                kind = "a number"
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
            m for m in _PLACEHOLDER.findall(self.sql) if m not in resolved
        }
        if missing:
            raise ValueError(
                f"{self.id}: no values for {sorted(missing)}"
            )
        sql = _PLACEHOLDER.sub(
            lambda m: sql_value(resolved[m.group(1)]), self.sql)
        return sql, resolved


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


def toolchain_info(bin_path: str | None = None) -> dict[str, Any]:
    """What exactly parsed the trace.

    This rides into report.json for a reason. The strings 'Running', 'R' and
    'binder transaction async' that the detectors match on are invented by
    trace_processor, not by the kernel and not by the app. So when numbers
    diverge between two runs, the first question is whether anything underneath
    changed; this field answers it immediately rather than after an hour of
    digging.
    """
    info: dict[str, Any] = {"perfetto_package": None,
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
        info["source"] = "--tp-binary"
        info["binary"] = str(bin_path)
        info["trace_processor"] = _binary_version(bin_path)
        return info

    try:
        from perfetto.prebuilts.manifests.trace_processor_shell import (
            TRACE_PROCESSOR_SHELL_MANIFEST as manifest,
        )
        match = re.search(r"/v([\d.]+)/", manifest[0]["url"])
        if match:
            info["trace_processor"] = f"v{match.group(1)}"
    except Exception:
        pass
    return info


def _binary_version(bin_path: str) -> str | None:
    import subprocess
    try:
        out = subprocess.run([bin_path, "--version"], capture_output=True,
                             text=True, timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0] or None
    except Exception:
        return None


def resolve_binary_path(bin_path: str | None = None) -> str | None:
    """Where the pin points. Needed only by doctor, to show the fact."""
    if bin_path:
        return str(bin_path)
    try:
        from perfetto.trace_processor.platform import PlatformDelegate
        return PlatformDelegate().get_shell_path(None)
    except Exception:
        return None


def _coerce(raw: str) -> Any:
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def load_detectors(directory: Path) -> list[Detector]:
    return [
        Detector.from_file(p) for p in sorted(directory.glob("*.sql"))
    ]


def render_sql(text: str, params: dict[str, Any]) -> str:
    missing = {m for m in _PLACEHOLDER.findall(text) if m not in params}
    if missing:
        raise ValueError(f"no values for {sorted(missing)}")
    return _PLACEHOLDER.sub(lambda m: sql_value(params[m.group(1)]), text)


class TraceSession:
    """A thin wrapper over perfetto.trace_processor.TraceProcessor."""

    def __init__(self, trace_path: str | Path, binary: str | None = None):
        try:
            from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig
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

        cfg = TraceProcessorConfig(bin_path=binary) if binary else None
        self._tp = TraceProcessor(trace=str(trace_path), config=cfg) if cfg \
            else TraceProcessor(trace=str(trace_path))

    def exec_script(self, sql: str) -> None:
        """Runs a multi-statement script (DDL), discarding the output."""
        for stmt in _split_statements(sql):
            list(self._tp.query(stmt))

    def query(self, sql: str) -> list[dict[str, Any]]:
        rows = []
        for r in self._tp.query(sql):
            rows.append(dict(r.__dict__))
        return rows

    def close(self) -> None:
        try:
            self._tp.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _split_statements(sql: str) -> list[str]:
    """A script into statements, with comment lines dropped first.

    First, because the split is on `;` and prose has semicolons in it. A
    sentence in a header comment used to cut the script in half, leaving one
    fragment that begins mid-word and fails to parse and another that silently
    never runs — and the error names a line of English, which reads as
    anything but "your comment has a semicolon in it". Twice in one sitting.
    """
    body = "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    )
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]
