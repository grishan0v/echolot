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

A line reading `-- @intervals` after the query starts a second one, which
says what stretches of the main thread's time the rows of the first stand
for; see Detector.render_intervals. A line reading `-- @samples` starts a
third, which picks out the callstack samples behind each row; see
Detector.render_samples.
"""

from __future__ import annotations

import contextlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import sandbox
from .config import ConfigError

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
    # The query after `-- @intervals`, empty for a detector without one.
    intervals_sql: str = ""
    # The query after `-- @samples`, the same.
    samples_sql: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "Detector":
        text = path.read_text(encoding="utf-8")
        try:
            meta, params, calibrations = parse_meta(text)
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
        return cls(
            id=meta.get("id", path.stem),
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

    def render_intervals(self, resolved: dict[str, Any]) -> str | None:
        """The second query, with the values the first one ran with.

        It answers one question about the rows the first query returned:
        which stretches of the main thread's time they stand for. `analyze`
        runs it straight after the first, with those rows in a table named
        `_rows` — `location` and `detail`, the latter NULL where a row has
        none — so the query only has to find its way back from a row to the
        time behind it. Which rows cleared a threshold is already decided,
        and repeating the HAVING here would be one more place for the two to
        drift apart.

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
        info["source"] = source or "--tp-binary"
        info["binary"] = str(bin_path)
        info["trace_processor"] = _binary_version(bin_path)
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
    """`v56.1` out of the manifest's URL, which is the only place it is said."""
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
    version: str | None   # "v56.1"
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

    A named binary comes back as named. Whether it is there is the session's
    question, and doctor shows the path either way.
    """
    if bin_path:
        return str(bin_path)
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
    before = _partials(pin)
    try:
        # perfetto announces the download itself, with a `print` — to
        # stdout, which is the result channel: the first `names --json` came
        # out with `Downloading <url>` in front of the JSON. The notice above
        # says that and more, where notes go.
        with contextlib.redirect_stdout(io.StringIO()):
            return download(file_name=pin.file_name, url=pin.url,
                            sha256=pin.sha256)
    except Exception as e:
        # The file this attempt created goes with it. perfetto leaves it
        # under its temporary name when curl fails or the hash does not
        # match, and names the next attempt's file afresh, so every failure
        # left one more in the cache: part of the binary, or a whole file
        # that is not the pin. Only this build's temporary name, and only a
        # file that was not there before the attempt: the binary and other
        # builds' files never have that name, and an earlier run's leftover
        # was there before. Nothing is said about it either way; the error
        # below is what happened.
        for leftover in _partials(pin) - before:
            with contextlib.suppress(OSError):
                leftover.unlink()
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
    return [
        Detector.from_file(p) for p in sorted(directory.glob("*.sql"))
    ]


def render_sql(text: str, params: dict[str, Any]) -> str:
    missing = {m for m in _PLACEHOLDER.findall(text) if m not in params}
    if missing:
        raise ValueError(f"no values for {sorted(missing)}")
    return _PLACEHOLDER.sub(lambda m: sql_value(params[m.group(1)]), text)


class TraceSession:
    """A thin wrapper over perfetto.trace_processor.TraceProcessor.

    `extra` is packets for trace_processor to read after the trace, as if the
    trace had carried them — the build's R8 mapping (mapping.py). They are
    streamed behind the file rather than written into a copy of it: a trace
    runs to hundreds of megabytes, and the file stays as it was recorded.
    """

    def __init__(self, trace_path: str | Path, binary: str | None = None,
                 extra: bytes | None = None):
        try:
            from perfetto.trace_processor import (
                TraceProcessor,
                TraceProcessorConfig,
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

        # The binary is settled before perfetto is asked to start it, and
        # handed over by path: left to perfetto, the pin would be downloaded
        # deep inside the start, with none of what resolve_binary_path says.
        binary = resolve_binary_path(binary)
        if not Path(binary).is_file():
            # A path named by `--tp-binary` or `toolchain.tp_binary` that is
            # not there. perfetto refuses it with a bare Exception, which came
            # out of `names` and `analyze` as a traceback.
            raise ConfigError(
                f"no trace_processor at {binary} — the path given for it "
                f"(--tp-binary, or toolchain.tp_binary in the config) is not "
                f"a file")
        try:
            self._tp = TraceProcessor(
                trace=str(trace_path) if extra is None else _then(path, extra),
                config=TraceProcessorConfig(bin_path=binary))
        except TraceProcessorException as e:
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
            # Before trace_processor starts, perfetto binds a socket to find
            # a free port for it. An agent's sandbox with no network refuses
            # that, and it came out of `analyze` as a PermissionError
            # traceback and out of `doctor` as "Operation not permitted" —
            # both read as a broken install (#190).
            if not sandbox.refused(e):
                raise
            raise sandbox.trace_processor_refused(e) from e

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


def _then(path: Path, extra: bytes):
    """The trace's bytes, then `extra`.

    A trace is a run of packets, so packets added at its end are read as part
    of it — which is how Perfetto's own tools attach a deobfuscation map.
    """
    with path.open("rb") as trace:
        while chunk := trace.read(32 << 20):
            yield chunk
    yield extra


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
