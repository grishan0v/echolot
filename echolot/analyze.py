"""The analysis engine: a trace and a config in, a Marker Report out.

Everything `analyze` does between opening the trace and handing the rows to
`report.build`: which process is the app, the window the scenario ran in, the
start-up breakdown, the device and how the build was compiled, what the
sampler recorded, the main thread's budget, frames, crowding, the project's
own markers and every slice name, and each detector's rows with what ran
behind them.

It prints nothing to stdout and parses no arguments. `main.py` keeps the
command line: it reads the config, calls `analyze_trace`, and writes and
prints what comes back. Kept apart so that the line between the two stays
visible, and so that `selftest`, `ui` and the rest can ask for a report
without loading the CLI (#530).
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path
from typing import Any

from . import compare as compare_mod
from . import launch as launch_mod
from . import mapping as mapping_mod
from . import needs as needs_mod
from . import place as place_mod
from . import recording as recording_mod
from . import names as names_mod
from . import report as report_mod
from . import stacks as stacks_mod
from .config import LAUNCH, NO_ANCHOR, PACKAGE, WHOLE, Config, ConfigError
from .tp import TraceSession, load_detectors, render_sql, sql_value, toolchain_info


SQL_DIR = Path(__file__).parent / "sql"
DETECTOR_DIR = SQL_DIR / "detectors"
_OTHERS_SHOWN = 5   # processes named besides the chosen one when a mask is wide


# How trace_processor labels the tracks that `Trace.beginAsyncSection` writes
# to, and what this tool prints in the thread column for a section that has
# no thread. The label rather than a blank: an empty cell reads as a thread
# whose name was lost, and this is a section that never had one. See
# `_aslice` in context.sql for why those sections are read at all.
ASYNC_TRACK = "atrace_async_slice"
ASYNC_THREAD = "(async)"

# A detector parameter that holds the temporary markers' prefix: it decides
# which names a detector treats as the agent's markers.
MARKER_PREFIX_PARAMS = ("marker_prefix",)


def marker_prefix_params(d, cfg: Config) -> dict[str, str]:
    """The project's `Config.temp_prefix` for each of `d`'s parameters that
    holds the markers' prefix, where it is not that parameter's default.

    `repeated_work.marker_prefix` was read by no Python code, so a project
    with its own prefix never got a near miss, and its report said
    `AGENTTMP_` beside a `markers` section saying `PERFHUNT_` (#874). The
    config's `detectors:` section and `--set` still win: they are laid over
    this.
    """
    prefix = cfg.temp_prefix
    return {k: prefix for k in MARKER_PREFIX_PARAMS
            if k in d.params and d.params[k] != prefix}


def plan_detectors(cfg: Config, *, cli_overrides: dict[str, dict] | None = None,
                   use_defaults: bool = False, detectors: list | None = None) -> list[tuple]:
    """Which detectors run, with which overrides, from where.

    Pure — no trace, no trace_processor — so the self-check can pin the
    rules without spinning up a session per case. Returns
    [(detector, overrides, source)] with source `default`, or what gave the
    numbers joined by `+`, in this order: `config` (the top-level
    `detectors:`), `scenario` (the scenario's own `detectors:` under
    `scenarios:`), `cli` (`--set`).

    Also where a threshold of the wrong kind is refused. That has to happen
    here rather than at render time: `analyze_trace` renders inside a
    `try/except` that turns any failure into one detector's error line, and a
    value the config cannot mean is a config error — the same answer
    `parse_set` already gives a `--set` typo, before a trace is opened.
    """
    # `detectors`: the shipped ones, read once by a caller that plans every
    # scenario of a config (`state.refused_by_analyze`).
    detectors = list(detectors) if detectors is not None else load_detectors(DETECTOR_DIR)
    if not use_defaults:
        cfg.unknown_detectors({d.id for d in detectors})
    cli_overrides = cli_overrides or {}
    cfg_overrides = {} if use_defaults else cfg.detector_overrides
    own = {} if use_defaults else cfg.scenario_overrides
    # Only what the config turned off, and only what it turned off on purpose.
    # `--set` on a detector asks for it by name, which outranks a `false`.
    off = (set() if use_defaults else cfg.disabled_detectors) - set(cli_overrides)
    detectors = [d for d in detectors if d.id not in off]
    if not detectors:
        raise ConfigError("every detector is turned off in this config")
    plan = []
    for d in detectors:
        from_cfg = dict(cfg_overrides.get(d.id) or {})
        from_cli = dict(cli_overrides.get(d.id) or {})
        # The project's prefix, under what the config and `--set` say: not a
        # threshold, so `--defaults` keeps it and the source does not name it.
        overrides = {**marker_prefix_params(d, cfg), **from_cfg, **from_cli}
        # A threshold the scenario sets is the scenario's, whatever the top
        # says about it; the rest of the config's numbers are the config's.
        mine = own.get(d.id) or {}
        from_scenario = {k: v for k, v in from_cfg.items() if k in mine}
        from_top = {k: v for k, v in from_cfg.items() if k not in mine}
        source = "+".join(
            s for s, on in (("config", bool(from_top)), ("scenario", bool(from_scenario)),
                            ("cli", bool(from_cli))) if on
        ) or "default"
        # Named per side, so the message points at the file or at the flag
        # rather than at "somewhere in the thresholds".
        for values, whose in ((from_top, "from the config"),
                              (from_scenario, f"from scenarios.{cfg.scenario_name}.detectors"),
                              (from_cli, "from --set")):
            try:
                d.check(values, whose)
            except ValueError as e:
                raise ConfigError(str(e)) from e
        plan.append((d, overrides, source))
    return plan


def analyze_trace(trace, cfg: Config, tp_binary: str | None = None, *,
                  cli_overrides: dict[str, dict] | None = None,
                  use_defaults: bool = False,
                  tp_source: str | None = None,
                  below_bar: bool = False) -> dict:
    """The core of a run: trace + config → Marker Report.

    Separate from cmd_analyze because it has two callers: the command, which
    reads the config from a file and writes the report to disk, and the
    self-check, which keeps the config in memory and compares the result with
    expectations. Raises ConfigError.

    Thresholds come from four places, in this order: the detector's own
    defaults, the config's `detectors:` section, the scenario's own
    `detectors:` when the config names several, then `--set` from the command
    line. `--defaults` drops the config's two — every detector, built-in
    numbers, the config untouched. Each detector in the report says which it
    got (`params_source`), so a reader can tell calibrated numbers from the
    shipped ones, and a scenario's from the config's, without opening it.

    `below_bar` also runs each detector with its thresholds open, and keeps
    what that returned beside its rows for the merge of a set (#505): a row
    one repeat reports may have been there below the bar in another, and
    only this tells it from a row that was not there at all. The merge takes
    it out again, see `report.merge_rows`, and keeps what lies under the bar
    in no repeat's report as the detector's `below` (#715).
    """
    plan = plan_detectors(cfg, cli_overrides=cli_overrides, use_defaults=use_defaults)
    # Refused here, like a threshold of the wrong kind: before a trace is opened.
    budget_ms = cfg.scenario_budget_ms
    results = []
    package = str(cfg.value(PACKAGE) or "")
    # The build's R8 mapping, read before the trace is opened: a path that is
    # not there is the config's mistake, and is said before any work is done.
    mapping = cfg.mapping
    extra = mapping_mod.packet_for(mapping, package) if mapping else None

    with TraceSession(trace, tp_binary, extra=extra) as tp:
        procs = _resolve_process(tp, cfg.process, str(trace))
        # The masks as this run has them: `--set` moves a boundary the same
        # way the config does, and `_claimed_name` is drawn from where they
        # stand rather than from where the file left them.
        bounds = _setup_context(tp, cfg, procs[0]["upid"],
                                {d.id: ov for d, ov, _ in plan if ov}, trace=str(trace))
        window = _window_info(tp, cfg, procs, bounds if "defined_by" in bounds else None)
        environment = _environment_info(
            tp, package or str(procs[0].get("name") or "").split(":")[0],
            mapped=extra is not None)
        # Whether the device was cooled before this repeat is in the record
        # `collect` kept beside the set, not in the trace (#522).
        from . import runner
        cooled = runner.cooldown_record(Path(trace))
        if cooled is not None:
            environment["cooldown"] = cooled
        markers = _markers_info(tp, cfg)
        # Every slice name of the window, for `compare` to set against the
        # other set's (#610). The merge keeps what it needs of it.
        try:
            names = _slice_names_info(tp, markers)
        except Exception as e:  # the report stands without it, as without a detector
            names = None
            print(f"[!] slice names: {e}", file=sys.stderr)
        # How the trace was recorded: what it asked the device for, and what
        # `collect` kept beside it (#533).
        recording = recording_mod.of_trace(tp, trace)
        # What each detector's silence can be taken to mean: whether the trace
        # holds what it reads (#441). Asked once, of the sources the planned
        # detectors name.
        held = needs_mod.survey(tp, environment,
                                {n for d, _, _ in plan for n in d.needs})
        # What ran behind a row is read only where a sampler ran: a trace
        # without samples gets the report it always got. The checkout, for
        # whose code is whose, is the config's directory, and there is none
        # for a config that lives in memory.
        sampled = bool((environment.get("sampling") or {}).get("started"))
        root = Path(cfg.path).resolve().parent if cfg.path else None

        _include_modules(tp, [d for d, _, _ in plan])
        # The names each folded name stands for, for the rows' `variants`.
        families = _families_of(tp)

        # The main thread's time behind every row, for `_in_rows`, and the
        # detectors whose rows could not be placed on it.
        spans: list[tuple[int, int]] = []
        uncounted: list[str] = []
        for d, overrides, source in plan:
            try:
                sql, params = d.render(overrides)
                rows = tp.query(sql)
                err = None
            except Exception as e:  # SQL is version-fragile — never fail the run
                # The values it was asked to run with, as `render` resolves
                # them. The shipped ones in their place made a calibrated
                # detector that failed read as one whose thresholds moved,
                # to `compare` and to anyone reading the report.
                rows, params, err = [], {**d.params, **(overrides or {})}, str(e)
                print(f"[!] {d.id}: {e}", file=sys.stderr)
            _with_variants(rows, d.identity, families)
            if rows:
                try:
                    behind = _spans_behind(tp, d, params, rows)
                except Exception as e:  # the same leniency as the query above
                    behind = None
                    print(f"[!] {d.id} @intervals: {e}", file=sys.stderr)
                if behind is None:
                    uncounted.append(d.id)
                else:
                    spans.extend(behind)
                if sampled and d.samples_sql:
                    try:
                        _stacks_behind(tp, d, params, rows, package, root)
                    except Exception as e:  # the rows stand without it
                        print(f"[!] {d.id} @samples: {e}", file=sys.stderr)
            # Last of the four: run with its thresholds open, the first query
            # writes its own tables again (`_det_<id>`, see tp._own_tables),
            # and the two queries above read the pieces of these rows.
            opened = _below_bar(tp, d, overrides, sql, rows) if below_bar and not err else None
            entry = {
                "id": d.id,
                "title": d.title,
                "why": d.why,
                # What it read the trace with (#507): `compare` gives no
                # verdict on a row whose detector differs between the sides.
                "sha": d.sha,
                "params": params,
                "params_source": source,
                # What tells one row from another. Written down per report so
                # that merging repeats does not have to guess, and so that a
                # report read back later still knows.
                "identity": list(d.identity),
                "rows": rows,
                "error": err,
                # Did it look: `checked`, and `reason` when it did not.
                **needs_mod.verdict(d.needs, held, err),
            }
            if opened is not None:
                entry[report_mod.OPEN_ROWS] = opened
            if source != "default":
                # What the shipped numbers would have been — the reader can
                # see how far calibration moved them without `explain`.
                entry["defaults"] = {k: d.params[k] for k in overrides if k in d.params}
            results.append(entry)

    # Shipped, and deliberately not run. This used to be everything the
    # config's `detectors:` section happened not to mention, which was the
    # wrong sentence about the wrong thing — see `Config.disabled_detectors`.
    # Now it is what somebody wrote `false` against, and the report says so
    # as a decision rather than as an oversight.
    planned = {d.id for d, _, _ in plan}
    absent = [d.id for d in load_detectors(DETECTOR_DIR) if d.id not in planned]

    if window.get("main_thread"):
        window["main_thread"].update(_in_rows(window, spans, uncounted))
    if window.get("frames"):
        window["frames"]["late"] = _late_frames(results)

    rep = report_mod.build(str(trace), window, results,
                           toolchain=toolchain_info(tp_binary, tp_source),
                           absent=absent, environment=environment,
                           markers=markers, scenario=cfg.scenario_name,
                           budget_ms=budget_ms, recording=recording)
    rep[report_mod.NAMES_ALL] = names
    return rep


def _below_bar(tp, d, overrides: dict | None, sql: str,
               rows: list[dict]) -> list[dict] | None:
    """The detector's rows with its calibrated thresholds at zero and no LIMIT.

    The rows themselves when that is the same query. None when it fails: the
    rows stand without it, and a missing row then reads as absent, as it did.
    """
    try:
        open_sql = d.render_open(overrides)
        return rows if open_sql == sql else tp.query(open_sql)
    except Exception as e:  # the same leniency as the detector's own query
        print(f"[!] {d.id} with its thresholds open: {e}", file=sys.stderr)
        return None


def _marker_globs(cfg: Config) -> tuple[str, list[str]]:
    """The project's own names: the temporary prefix, and the globs first.

    The prefix's glob first, then each slice `domains` in the config lists.
    Their digits were written by someone to tell two things apart, so no
    fold touches them: see `names.runtime_name`.
    """
    prefix = cfg.temp_prefix
    globs = [prefix + "*"]
    for entry in cfg.get("domains") or []:
        name = entry.get("slice") if isinstance(entry, dict) else None
        if name and name not in globs:
            globs.append(str(name))
    return prefix, globs


def _markers_info(tp, cfg: Config) -> dict:
    """Every marker of the project's own, measured — whatever else fired.

    Two kinds of name are the project's: what `domains` in the config lists,
    and what carries the temporary prefix. The detectors read them like any
    other slice and show them only where a threshold says so, which is not
    the question an agent asks about a marker it planted: it wants the
    number, every time. On a real hunt the subagent ran `names` fifteen
    times in a shell loop and wrote a python script to get this table.

    Thread sections and async ones both, because the project's own names
    are usually async — see `_aslice` in context.sql. Self time comes from
    the same child sum the detectors use, so a marker wrapping another
    reads as the difference: the hunt above needed `store_update` minus
    `store_update_locked` to say how long the lock was waited for. The
    total counts an occurrence inside one of the same name once, as part of
    the outer one: a marker in a recursive function doubled it otherwise.

    Grouped by name in the end, with the threads listed: a marker that ran
    on two threads is one marker, and the reader wants one row and the
    fact.
    """
    prefix, globs = _marker_globs(cfg)
    wanted = " OR ".join(f"name GLOB '{sql_value(g)}'" for g in globs)
    rows = tp.query(f"""
        WITH seen AS (
            SELECT s.slice_id, s.name, COALESCE(s.thread_name, 'tid ' || s.tid) AS thread,
                   s.dur, s.unfinished
            FROM _slice_win s WHERE {wanted}
            UNION ALL
            SELECT a.slice_id, a.name, '{ASYNC_THREAD}', a.dur, a.unfinished
            FROM _aslice_win a WHERE {wanted}
        )
        SELECT seen.name AS location, seen.thread AS thread,
               COUNT(*) AS count,
               MAX(seen.unfinished) AS unfinished,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM ancestor_slice(seen.slice_id) a
                                     WHERE a.name = seen.name)
                        THEN 0 ELSE MAX(seen.dur, 0) END) AS total_ns,
               SUM(MAX(seen.dur, 0) - COALESCE(CASE WHEN seen.unfinished = 1
                                                    THEN c.ns_win ELSE c.ns END, 0)) AS self_ns,
               MAX(MAX(seen.dur, 0)) AS max_ns
        FROM seen LEFT JOIN _child_sum c ON c.parent_id = seen.slice_id
        GROUP BY seen.name, seen.thread
    """)
    by_name: dict[str, dict] = {}
    for r in rows:
        row = by_name.setdefault(r["location"], {
            "location": r["location"], "count": 0, "self_ns": 0,
            "total_ns": 0, "max_ns": 0, "threads": set(), "unfinished": False})
        row["count"] += r["count"]
        row["unfinished"] = row["unfinished"] or bool(r["unfinished"])
        row["self_ns"] += r["self_ns"] or 0
        row["total_ns"] += r["total_ns"] or 0
        row["max_ns"] = max(row["max_ns"], r["max_ns"] or 0)
        row["threads"].add(r["thread"])
    out = []
    for row in by_name.values():
        threads = sorted(row["threads"])
        out.append({
            "location": row["location"],
            "count": row["count"],
            "self_ms": round(row["self_ns"] / 1e6, 2),
            "total_ms": round(row["total_ns"] / 1e6, 2),
            "max_ms": round(row["max_ns"] / 1e6, 2),
            "detail": ", ".join(threads[:3]) + (f" +{len(threads) - 3}" if len(threads) > 3 else ""),
        })
        # One that never closed runs to the end of the window: its end did
        # not run — an exception, a suspended coroutine, an end on another
        # thread — or the recording stopped first. Its number is a floor,
        # and without the flag it read as a measurement.
        if row["unfinished"]:
            out[-1]["unfinished"] = True
    out.sort(key=lambda r: (-r["total_ms"], r["location"]))
    # A name the config lists that the window never held is worth a line:
    # the map points at something this scenario does not run, or the name
    # changed under it.
    absent = [g for g in globs[1:]
              if not any(fnmatch.fnmatchcase(r["location"], g) for r in out)]
    # The slice each temporary marker sits directly in, and how long it spent
    # there. A marker under a slice is its child, so that slice's self time
    # drops by the marker's time, and `compare` reads that drop as the slice
    # getting cheaper unless it knows where the time went (#584).
    parents: dict[str, dict[str, float]] = {}
    for r in tp.query(f"""
        SELECT s.name AS marker, p.name AS parent, SUM(MAX(s.dur, 0)) AS ns
        FROM _slice_win s
        JOIN slice c ON c.id = s.slice_id
        JOIN slice p ON p.id = c.parent_id
        WHERE s.name GLOB '{sql_value(globs[0])}' AND p.name != s.name
        GROUP BY s.name, p.name
    """):
        parents.setdefault(r["marker"], {})[r["parent"]] = round((r["ns"] or 0) / 1e6, 2)
    for row in out:
        if parents.get(row["location"]):
            row["parents"] = parents[row["location"]]
    # Every prefixed name in the trace, the window or not: what the markers
    # in the code are held against (`_markers_against_code`). One that ran
    # outside the window is in the build all the same.
    in_trace = sorted({r["name"] for r in tp.query(
        f"SELECT DISTINCT name FROM slice WHERE name GLOB '{sql_value(globs[0])}'")})
    return {"prefix": prefix, "globs": globs, "rows": out, "absent": absent,
            "in_trace": in_trace}


# What the inventory below calls the main thread: its name is the package's
# last fifteen characters, which says nothing about whose thread it is.
MAIN_THREAD = "(main)"


def _slice_names_info(tp, markers: dict) -> dict[str, dict]:
    """Every slice name of the window, thread and async, with its numbers (#610).

    By name as `names.slice_across_builds` folds it, so that the names of two
    builds' traces can be set against each other in `compare`: a name and
    the same with another tid, vsync, fence or line number, or another
    install directory, are one. The project's own markers, from `markers`,
    are kept as written. The threads are folded the same way, the main
    thread named as such and an async section under `(async)`.

    All of them, whatever they took: what `compare` needs of one side is
    whether a name of the other was there at all. `report._merge_names`
    keeps what it needs of it.
    """
    keep = markers.get("prefix")
    own = frozenset(r["location"] for r in markers.get("rows") or [])
    rows = tp.query(f"""
        SELECT s.name AS name,
               CASE WHEN s.is_main_thread = 1 THEN '{MAIN_THREAD}'
                    ELSE COALESCE(s.thread_name, 'tid ' || s.tid) END AS thread,
               SUM(MAX(s.dur, 0)) AS total_ns, MAX(MAX(s.dur, 0)) AS max_ns
        FROM _slice_win s WHERE s.name IS NOT NULL
        GROUP BY 1, 2
        UNION ALL
        SELECT a.name, '{ASYNC_THREAD}', SUM(MAX(a.dur, 0)), MAX(MAX(a.dur, 0))
        FROM _aslice_win a WHERE a.name IS NOT NULL
        GROUP BY a.name
    """)
    out: dict[str, dict] = {}
    for r in rows:
        name = names_mod.slice_across_builds(r["name"], keep=keep, names=own)
        row = out.setdefault(name, {"total_ns": 0, "max_ns": 0, "threads": set()})
        row["total_ns"] += r["total_ns"] or 0
        row["max_ns"] = max(row["max_ns"], r["max_ns"] or 0)
        row["threads"].add(names_mod.slice_across_builds(str(r["thread"])))
    return {name: {"total_ms": round(row["total_ns"] / 1e6, 2),
                   "max_ms": round(row["max_ns"] / 1e6, 2),
                   "threads": sorted(row["threads"])}
            for name, row in out.items()}


def _resolve_process(tp, glob: str, trace: str | None = None, *,
                     source: str = "project.process",
                     flag: bool = False) -> list[dict]:
    """Target process candidates, the fattest by slice count first.

    An Android app usually has more than one process: `com.example.app*` also
    catches `:pushservice` and `:webview`. This used to take whichever came
    first by upid — silently, and often the wrong one. The choice is now
    deliberate and said out loud: to humans on stderr, to the agent in
    report.json.

    `trace` is named in the failure because a run is usually a set. A
    macrobenchmark round of fifteen where one trace carries a truncated
    process name fails on that one and stops everything, and "no process
    matches" without a filename sends the reader to check a config that is
    right about fourteen of them.

    `source` is what gave the mask — `project.process`, `--process`, or
    `default` for `names`' own `*` — and every sentence names that one.
    `flag` is whether the command has `--process`: `analyze` and `calibrate`
    do not, and advice to use it ended in `unrecognized arguments`.
    """
    rows = tp.query(f"""
        SELECT p.upid AS upid, p.pid AS pid, p.name AS name,
               COUNT(s.id) AS slices
        FROM process p
        LEFT JOIN thread t        ON t.upid = p.upid
        LEFT JOIN thread_track tt ON tt.utid = t.utid
        LEFT JOIN slice s         ON s.track_id = tt.id
        WHERE p.name GLOB '{sql_value(glob)}'
        GROUP BY p.upid
        ORDER BY slices DESC, p.upid
    """)
    if not rows:
        where = f" {Path(trace).name}" if trace else ""
        _require_named_processes(tp, trace)
        said = {"--process": f"--process '{glob}'",
                "default": f"the default mask '{glob}'"}.get(
                    source, f"project.process = '{glob}'")
        message = (f"no process in trace{where} matches {said}. Look at the "
                   f"real names: echolot probe <trace>.")
        # The name a trace carries is not always the one the package has.
        # Android names an app's main thread after the last 15 characters of
        # a dotted process name, so `com.example.myapp` can arrive as
        # `m.example.myapp` — which a trailing-wildcard glob does not match
        # either. Seen on one trace out of fifteen from a single
        # macrobenchmark round, where the other fourteen carried the full name.
        tail = glob.rstrip("*")[-15:]
        if getattr(tp, "cut", None):
            # The process list sits at the end of a launch recording, and a
            # cut takes it first: what is left are comm's fifteen characters,
            # and a new mask would only make a report on half a file (#568).
            message += (f" The file is cut short, and the process list it "
                        f"held at its end went with the tail: pull or copy it "
                        f"again rather than change {source}.")
        elif tail and source != "default":
            again = (f"--process '*{tail}'" if source == "--process"
                     else f"project.process = '*{tail}'")
            message += (f" If the name is there but cut to fifteen characters, "
                        f"the trace has it from comm rather than from the "
                        f"process list, and the head is what was cut — try "
                        f"{again}.")
        raise ConfigError(message)
    if len(rows) > 1:
        # A `*` on a real device matches six hundred processes; naming them
        # all is a fifteen-kilobyte line into the agent's window. The next
        # few by slice count are the ones that could have been meant.
        shown = rows[1:1 + _OTHERS_SHOWN]
        others = ", ".join(f"{r['name']} ({r['slices']})" for r in shown)
        rest = len(rows) - 1 - len(shown)
        if rest > 0:
            others += f", … and {rest} more"
        print(
            f"[!] '{glob}' matched {len(rows)} processes. "
            f"Took {rows[0]['name']} ({rows[0]['slices']} slices). "
            f"Others: {others}. Narrow it with "
            f"{'--process or project.process' if flag else 'project.process in the config'}.",
            file=sys.stderr,
        )
    return rows


def _require_named_processes(tp, trace: str | None) -> None:
    """Refuses a file trace_processor opens without complaint and that holds
    no trace data: a report.json, the benchmarkData.json a macrobenchmark
    writes beside its traces, an empty file. No config is wrong about it.

    `probe` asks it first, `analyze` and `names` once no process matched:
    probe on such a file printed an empty process table and exited 0, and
    `probe x && analyze x` went on with a file that holds nothing (#567).
    """
    named = tp.query("SELECT COUNT(*) AS n FROM process "
                     "WHERE name IS NOT NULL AND name != ''")
    if not (named and named[0]["n"]):
        raise ConfigError(
            f"{Path(trace).name if trace else 'the trace'} holds no process "
            f"with a name: it is no trace data, or a recording without the "
            f"process list. A report.json or a macrobenchmark's "
            f"benchmarkData.json opens like a trace and holds none.")


def _setup_context(tp, cfg: Config, upid: int,
                   mask_overrides: dict | None = None, *,
                   trace: str | None = None) -> dict:
    """Prepares the views in two passes and returns the window bounds.

    Between the passes the CLI grabs ts_start/ts_end and substitutes them into
    window.sql as plain numbers. While the window was a view it was recomputed
    on every reference to _slice_win: on a trace with 475k slices the run did
    not finish within ten minutes.

    `mask_overrides` is where the name masks stand for this run — the config's
    plus whatever `--set` moved. It reaches `_claimed_name`, and the default
    is the config alone, which is what a caller with no plan of its own has.

    A scenario with `window: launch` has no anchors to read the two numbers
    from: they come from the launch the platform records (launch.py), with
    what closed the window beside them. `trace` names the file when that
    launch is not there. One with `window: whole`, and any under
    `--whole-trace`, takes the trace's own first and last moments (#519).
    """
    tp.exec_script(render_sql(
        (SQL_DIR / "context.sql").read_text(encoding="utf-8"),
        cfg.context_params(upid),
    ))
    bounds = tp.query("SELECT ts_start, ts_end FROM _window")[0]
    if bounds["ts_start"] is None or bounds["ts_end"] is None:
        # Rendered as `None` into the SQL below, this came out of
        # trace_processor as a schema error and out of the command as a
        # traceback.
        raise ConfigError(
            "the process has no thread slices, so there is nothing to build a "
            "window from. Check project.process and that the app's atrace was "
            "recorded: echolot probe <trace>")
    if cfg.scenario_window == LAUNCH:
        bounds = launch_mod.find(tp, upid, trace)
    elif cfg.scenario_window == WHOLE:
        bounds = launch_mod.whole(tp)
    for phase in ("window.sql", "environment.sql"):
        tp.exec_script(render_sql(
            (SQL_DIR / phase).read_text(encoding="utf-8"),
            {"ts_start": bounds["ts_start"], "ts_end": bounds["ts_end"]},
        ))
    _claim_names(tp, cfg.detector_overrides if mask_overrides is None
                 else mask_overrides)
    _own_composables(tp, cfg)
    _name_families(tp, cfg)
    return bounds


def _own_composables(tp, cfg: Config) -> None:
    """`_compose_own` — the composables in the window that are the project's.

    `composables` ranks the app's own and counts the library's under them,
    and which is which is a question about the checkout: the trace names
    `androidx.compose.material3.Text (Text.kt:…)` and `com.example.app.ui.Feed
    (Feed.kt:42)` alike. So it is answered here, where the config's directory
    is known, and handed to the SQL as a table (see `place.own_composables`).
    The checkout is walked only for a trace that names composables at all.
    """
    names = [r["name"] for r in tp.query(
        "SELECT DISTINCT name FROM _slice_win "
        "WHERE is_main_thread = 1 AND name GLOB '* ([A-Za-z]*.kt:[0-9]*)'")]
    root = cfg.sources if cfg.path or cfg.root_override else None
    own = place_mod.own_composables(names, root, cfg.value(PACKAGE))
    tp.exec_script("DROP TABLE IF EXISTS _compose_own;")
    tp.exec_script("CREATE TABLE _compose_own (name TEXT PRIMARY KEY);")
    if own:
        rows = ", ".join(f"('{sql_value(n)}')" for n in own)
        tp.exec_script(f"INSERT INTO _compose_own (name) VALUES {rows};")


def _name_families(tp, cfg: Config) -> None:
    """`_name_family`: every name in the window whose numbers the runtime
    chose, each with them folded to `#` (#831).

    `Choreographer#doFrame 55112` is the next frame's vsync id, `owner tid:
    15427` the thread that held a lock this time, `pool-3-thread-2` one worker
    of a pool: the same work under a new name on every call, in every run.
    A detector that groups by a name groups by `COALESCE(f.family, name)`
    over this table, so each of them folds the same names the same way,
    before it adds anything up: a pool's workers are one row with all their
    threads counted, in a report of one recording as in a set (#845), and a
    median is taken over every frame rather than over each frame alone
    (#728).

    The names are the window's slices, thread and async, and the threads of
    the process. The fold is `names.runtime_name`, with the project's own
    names kept as written: what carries the temporary prefix, and what the
    config's `domains` lists. Only the names it changes are listed, and a
    name not listed is its own family.
    """
    prefix, globs = _marker_globs(cfg)
    own = globs[1:]
    tp.exec_script("DROP TABLE IF EXISTS _name_family;")
    tp.exec_script("CREATE TABLE _name_family (name TEXT PRIMARY KEY, family TEXT);")
    found = tp.query("""
        SELECT name FROM _slice_win WHERE name GLOB '*[0-9]*'
        UNION SELECT name FROM _aslice_win WHERE name GLOB '*[0-9]*'
        UNION SELECT t.name FROM thread t JOIN _proc p ON t.upid = p.upid
              WHERE t.name GLOB '*[0-9]*'
    """)
    pairs = []
    for r in found:
        name = r["name"]
        if any(fnmatch.fnmatchcase(name, g) for g in own):
            continue
        family = names_mod.runtime_name(name, keep=prefix)
        if family != name:
            pairs.append((name, family))
    for at in range(0, len(pairs), 500):
        rows = ", ".join(f"('{sql_value(n)}', '{sql_value(f)}')"
                         for n, f in pairs[at:at + 500])
        tp.exec_script(f"INSERT INTO _name_family (name, family) VALUES {rows};")


# How many of the names a folded one stands for a row keeps. A frame's
# family stands for every frame of the window, hundreds of vsync ids.
VARIANTS_KEPT = 20


def _families_of(tp) -> dict[str, list[str]]:
    """Each family in `_name_family`, with the names of the window it stands for."""
    out: dict[str, list[str]] = {}
    for r in tp.query("SELECT name, family FROM _name_family ORDER BY name"):
        out.setdefault(r["family"], []).append(r["name"])
    return out


def _with_variants(rows: list[dict], identity: tuple[str, ...],
                   families: dict[str, list[str]]) -> None:
    """A row whose name is a family keeps the names it stands for, in
    `variants`: what the trace called it, before the fold (#831). The names
    of the window under that family, the first `VARIANTS_KEPT` of them.
    """
    if not families:
        return
    for row in rows:
        named = {col: families[v][:VARIANTS_KEPT] for col in identity
                 if isinstance(v := row.get(col), str) and v in families}
        if named:
            row["variants"] = named


def _claim_names(tp, overrides: dict) -> None:
    """`_claimed_name` — every slice name a detector's mask already speaks for.

    Four of the fifteen detectors know the names of what they are looking for
    and say so in a `*name_glob*` param: `*GC`, `Lock contention on a monitor
    lock*`, `binder transaction`, and a composable's `* ([A-Za-z]*.kt:[0-9]*)`.
    `repeated_work` knows no name in advance —
    it asks a question about shape and so has to look at every name there
    is, which means it also looks at names that belong to somebody else.

    Twice that produced a row that was already in the report under its own
    heading, and both times the shape was the same: a slice the platform
    named, under parents the platform named.

        Lock contention on a monitor lock (owner tid: …)
          under  monitor contention with owner … waiters=0 …
          under  monitor contention with owner … waiters=1 …

        ReclaimPhase
          under  NativeAlloc concurrent copying GC
          under  Background concurrent copying GC

    The first was fixed by naming the family, and then the second one turned
    up on the first app nobody had tuned for — because naming families is a
    list somebody has to keep adding to. This is the rule the list was
    approximating: the set has already divided the platform's vocabulary
    between its detectors, and `repeated_work` works the leftovers.

    Built here rather than in the detector because the globs belong to their
    owners and must stay in one place — including the overrides, so that
    widening a mask in `echolot.yml` moves this boundary with it.

    From every shipped detector, not from the ones this run happens to plan.
    Switching `monitor_contention` off is a decision not to ask that question
    today, and it does not turn a lock wait into work somebody did twice.
    """
    globs = [g for _, kind, g in _detector_masks(overrides) if kind == "name"]
    tp.exec_script("DROP TABLE IF EXISTS _claimed_name;")
    if not globs:
        tp.exec_script("CREATE TABLE _claimed_name (name TEXT);")
        return
    where = " OR ".join(f"name GLOB '{sql_value(g)}'" for g in globs)
    # Materialised, not a view: the consumer tests every slice name against
    # it, and a view would re-run the GLOBs each time.
    tp.exec_script(
        f"CREATE TABLE _claimed_name AS "
        f"SELECT DISTINCT name FROM _slice WHERE {where};"
    )


def _near_anchor(tp, glob: str) -> list[str]:
    """The slices of the process an anchor that matched nothing may have meant.

    From the set an anchor is matched against, threads' and async sections
    alike, with each number that differs between recordings written as `*`,
    as `probe` shows a name to write as an anchor.
    """
    from . import anchors
    rows = tp.query("SELECT DISTINCT name FROM _anchor WHERE name IS NOT NULL")
    folded = {names_mod.NUMBER.sub(lambda m: "0x*" if m.group(0).startswith("0x") else "*",
                                    str(r["name"])) for r in rows}
    return anchors.nearest(glob, folded)


def _window_info(tp, cfg: Config, procs: list[dict], launch: dict | None = None) -> dict:
    """Window bounds plus proof that the anchors matched anything at all.

    An anchor that never matched is the most common source of a garbage
    report: the window silently collapses onto the whole trace, every detector
    screams at once, and the agent goes off investigating the wrong thing. Let
    that be visible rather than guessed at.

    `launch` is what `launch.find` returned for a scenario whose window is
    the launch, or `launch.whole` for one that is the whole trace. Such a
    window has no anchors to prove; it says what defined it (`defined_by`),
    and a launch's startup block is that launch.
    """
    rows = tp.query(
        "SELECT ts_start, ts_end, "
        "ROUND((ts_end - ts_start)/1e6, 2) AS duration_ms FROM _window"
    )
    window = dict(rows[0]) if rows else {}
    window["process"] = procs[0]["name"]
    window["pid"] = procs[0]["pid"]
    if len(procs) > 1:
        # Same cap as on stderr: a wide mask must not put hundreds of
        # processes into report.json.
        window["process_alternatives"] = [
            {"name": p["name"], "pid": p["pid"], "slices": p["slices"]}
            for p in procs[1:1 + _OTHERS_SHOWN]
        ]
        window["process_alternatives_total"] = len(procs) - 1

    # Before the anchors rather than after: the budget reads thread states and
    # the window bounds, and the anchor lookup touches neither. Placed here
    # and defined below `_environment_info` so that a second fact added to
    # this function lands somewhere else in the file — two of them arriving on
    # separate branches should not have to be merged by hand.
    window["main_thread"] = _main_thread_budget(tp, window)
    window["crowding"] = _crowding_info(tp)
    window["frames"] = _frames_info(tp, window)
    window["drawn"] = _drawn_info(tp, window)
    window["boot_receivers"] = _boot_receivers(tp, window)

    for key, glob in (("start", cfg.scenario_start), ("end", cfg.scenario_end)):
        if glob == NO_ANCHOR:
            window[f"{key}_anchor"] = None
            continue
        # `_anchor` rather than `_slice`: the same set the window was built
        # from, async sections included. Counting the one and building from
        # the other is how `matches` would say 0 for a window that closed.
        #
        # The end anchor by the rule the window is closed by: an occurrence at
        # or after the start. One only before it — the anchors swapped — left
        # the window running to the end of the trace while `matches: 1` said
        # it had closed. Those are counted apart, so the report can say the
        # name exists, just not where it would end anything.
        hits = tp.query(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(ts >= {window.get('ts_start') or 0}), 0) AS after "
            f"FROM _anchor WHERE name GLOB '{sql_value(glob)}'"
        )
        n, after = (hits[0]["n"], hits[0]["after"]) if hits else (0, 0)
        matches = after if key == "end" else n
        window[f"{key}_anchor"] = {"glob": glob, "matches": matches}
        if n - matches:
            window[f"{key}_anchor"]["before_start"] = n - matches
        elif not n:
            # The names the trace does hold that read like it, so that the
            # warning names `list_shown` for a `list_shown_mark` the code no
            # longer writes, rather than sending the reader to `probe` (#500).
            near = _near_anchor(tp, glob)
            if near:
                window[f"{key}_anchor"]["near"] = near
        # The end anchor the window closed on, still open when the recording
        # stopped: the window ran on to the end of the trace, and the
        # scenario never reached its end inside it.
        if key == "end" and matches:
            first = tp.query(
                f"SELECT a.dur < 0 AS open FROM _anchor a "
                f"WHERE a.name GLOB '{sql_value(glob)}' "
                f"AND a.ts >= (SELECT ts_start FROM _window) ORDER BY a.ts LIMIT 1")
            if first and first[0]["open"]:
                window[f"{key}_anchor"]["unfinished"] = True
    if launch:
        window.update(launch_mod.facts(tp, window, launch))
    window["opened_inside"] = _opened_inside(tp, window)
    window["startup"] = _startup_info(tp, window, procs[0]["name"], launch)
    return window


def _opened_inside(tp, window: dict) -> dict | None:
    """Whether the scenario window opened with the main thread already blocked.

    Slices are safe here — they keep their real duration across the boundary,
    which is what `_slice_win` exists to do. Thread states are not: they are
    clipped, so a main thread that went to sleep 400 ms before the anchor and
    woke 100 ms after it reaches the report as 100 ms of waiting, and the
    reason it was waiting never appears at all.

    That is a partial account rather than a wrong one, and it looks exactly
    like a complete one. Hence a fact in the window rather than a correction
    to the numbers: nothing here moves a measurement, it says which end of the
    stall the window cut off.

    Which states count as a block is `anr_risk`'s question already answered,
    and the answer is reused rather than invented again. `Running` is working.
    `R`, `R+`, `D` and `DK` are unambiguous: ready and denied a CPU, or parked
    in the kernel. `S` is the one that needs deciding, because an idle looper
    waiting on the message queue and a blocking call inside a message look
    identical in the state alone — and the discriminator is whether a slice
    deeper than the anchor is open at that moment.

    Getting this wrong is not theoretical. On a real command-driven scenario
    the main thread sat in `S` for 1615 ms waiting for the user to touch the
    screen; a rule reading the state alone would have called that "already
    blocked" and sent someone looking for a stall that was the app behaving
    correctly.
    """
    start, end = window.get("ts_start"), window.get("ts_end")
    if start is None or end is None:
        return None
    # A state the thread was still in when the recording stopped runs to the
    # end of the window, as in window.sql; dropped, the freeze that never let
    # go was the one block this could not see. The part inside the window
    # stops at its end: a block running past a short window counted its tail
    # as inside, and outweighed what came before the anchor.
    until = f"CASE WHEN ts.dur < 0 THEN {end} ELSE ts.ts + ts.dur END"
    rows = tp.query(f"""
        SELECT ts.state                                          AS state,
               th.name                                           AS thread_name,
               ROUND(({start} - ts.ts) / 1e6, 2)                 AS before_ms,
               ROUND(({until} - ts.ts) / 1e6, 2)                 AS total_ms,
               ROUND((MIN({until}, {end}) - {start}) / 1e6, 2)   AS inside_ms
        FROM thread_state ts
        JOIN thread th ON ts.utid = th.utid
        JOIN _proc p   ON th.upid = p.upid
        WHERE th.tid = p.pid
          AND ts.dur != 0
          AND ts.ts < {start}
          AND {until} > {start}
    """)
    if not rows or rows[0]["state"] == "Running":
        return None
    if rows[0]["state"] not in ("R", "R+", "D", "DK"):
        # Sleeping. Only a block if a message was open at the time — otherwise
        # the looper had reached the queue, which is the app working properly.
        # From `_slice_win`, where a message that never closed runs to the
        # end of the window rather than being no message at all.
        inside_message = tp.query(f"""
            SELECT COUNT(*) AS n
            FROM _slice_win s
            WHERE s.is_main_thread = 1 AND s.depth >= 1
              AND s.ts < {start} AND s.ts + s.dur > {start}
        """)
        if not inside_message or not inside_message[0]["n"]:
            return None
    found = dict(rows[0])
    inside = found["inside_ms"]
    found["material"] = bool(found["before_ms"] >= inside
                             and found["before_ms"] >= compare_mod.FLOOR_MS)
    return found


def _startup_info(tp, window: dict, process: str, launch: dict | None = None) -> dict | None:
    """The app's startup as Perfetto's standard library measures it.

    The budget says where the main thread's time went by thread state. A
    startup has a finer account, and the standard library keeps it:
    `android_startups` finds the launch — cold, warm or hot, from the intent
    to the first frame — and `android_startup_opinionated_breakdown` divides
    every moment of it among reasons: `bind_application`, `binder`, `io`, lock
    waits, the thread states. On a cold start of a large Kotlin app on a phone
    it accounted for all 1,357 ms, across 17 reasons.

    It is the platform's measure and not the scenario's window, so the two
    are set side by side: where the startup began and ended against the
    window, in milliseconds, negative for before. A trace with several
    startups of the app — a benchmark that launches it more than once — has
    them counted, and the one that shares the most time with the window is the
    one described.

    A window that is the launch (`launch`, from `launch.find`) is described
    by the launch it was built from. Picked by overlap, a later launch of the
    same app could be described beside a window it did not build.

    None when the trace holds no startup of this app's package, and when the
    trace_processor has no such module; the second is said on stderr, the way
    a failed detector is.

    The breakdown is included only for a trace that has a startup to break
    down. Its tables are computed as it is included, and on a phone's cold
    start that took 0.77 s against 0.04 s for the startups alone; a scroll or a
    warm scenario has nothing for it to do.
    """
    start, end = window.get("ts_start"), window.get("ts_end")
    if start is None or end is None:
        return None
    package = process.split(":", 1)[0]
    if launch is not None and launch.get("startup_id") is None:
        launch = None  # the whole trace: built from no launch
    own = f" OR startup_id = {int(launch['startup_id'])}" if launch else ""
    try:
        tp.exec_script("INCLUDE PERFETTO MODULE android.startup.startups;")
        startups = tp.query(f"""
            SELECT startup_id, ts, ts_end, dur, startup_type AS type
            FROM android_startups
            WHERE (package = '{sql_value(package)}'{own}) AND dur > 0
            ORDER BY ts
        """)
        if not startups:
            return None

        def shared(s: dict) -> int:
            return max(0, min(s["ts_end"], end) - max(s["ts"], start))

        built = [s for s in startups if launch and s["startup_id"] == launch["startup_id"]]
        chosen = built[0] if built else max(startups, key=lambda s: (shared(s), -s["ts"]))
        tp.exec_script("INCLUDE PERFETTO MODULE android.startup.startup_breakdowns;")
        reasons = tp.query(f"""
            SELECT reason, SUM(dur) AS ns
            FROM android_startup_opinionated_breakdown
            WHERE startup_id = {int(chosen["startup_id"])} AND reason IS NOT NULL
            GROUP BY reason
            ORDER BY ns DESC, reason
        """)
    except Exception as e:  # a trace_processor without the module
        print(f"[!] startup: {e}", file=sys.stderr)
        return None
    out = {
        "type": chosen["type"],
        "dur_ms": round(chosen["dur"] / 1e6, 2),
        "reasons": {r["reason"]: round((r["ns"] or 0) / 1e6, 2) for r in reasons},
        "from_window_start_ms": round((chosen["ts"] - start) / 1e6, 2),
        "from_window_end_ms": round((chosen["ts_end"] - end) / 1e6, 2),
        "in_window_ms": round(shared(chosen) / 1e6, 2),
    }
    if len(startups) > 1:
        out["startups"] = len(startups)
    return out


def _environment_info(tp, package: str = "", mapped: bool = False) -> dict:
    """What the platform was doing to the app, from the views environment.sql left.

    Three blocks, each one either measured or absent. Absent means the trace
    was recorded without that data source — by an older echolot, by a config
    with `runner.environment: false`, or on a kernel that does not carry the
    event — and it is a different answer from "the device was fine". A block
    that could not be measured is `None` and its name goes into `missing`, so
    that a reader who wants to know whether the clock held steady can tell
    "it did" from "nobody looked".

    None of this is a finding. It is the denominator under every duration in
    the report: the same code on a lower clock takes longer, and `compare`
    reads these numbers to avoid calling that a regression.

    `sampling` is the fourth block and works the other way round: `None` is a
    definite answer, "nothing sampled this recording", so it never goes into
    `missing`. See `_sampling_info`. `package` and `mapped` are for what it
    says about the names of the app's frames: the package whose frames they
    are, and whether the build's mapping was handed to trace_processor.
    """
    def one(sql: str) -> dict:
        try:
            rows = tp.query(sql)
        except Exception as e:  # a view over a table this trace has no rows for
            print(f"[!] environment: {e}", file=sys.stderr)
            return {}
        return dict(rows[0]) if rows else {}

    env: dict = {}

    # The clock, weighted by the time our own threads held a core. Weighting
    # matters: the little cores idle at 300 MHz through the whole scenario,
    # and a plain average over CPUs would report that as the machine we ran on.
    cpu = one(
        "SELECT SUM(dur) AS measured_ns, SUM(dur * khz) AS weighted, "
        "       MIN(khz) AS min_khz, MAX(khz) AS max_khz "
        "FROM _freq_on_cpu"
    )
    on_cpu = one("SELECT SUM(dur) AS ns FROM _on_cpu")
    if cpu.get("measured_ns"):
        env["cpu"] = {
            "mean_mhz": round(cpu["weighted"] / cpu["measured_ns"] / 1000.0, 1),
            "min_mhz": round(cpu["min_khz"] / 1000.0, 1),
            "max_mhz": round(cpu["max_khz"] / 1000.0, 1),
            "on_cpu_ms": round((on_cpu.get("ns") or 0) / 1e6, 2),
            # How much of that on-CPU time had a frequency to go with it. Below
            # the whole, the mean is an average over the part we could see.
            "measured_ms": round(cpu["measured_ns"] / 1e6, 2),
        }
    else:
        env["cpu"] = None

    hot = one("SELECT zone, MAX(celsius) AS celsius FROM _thermal_win")
    throttle = one("SELECT device, MAX(level) AS level FROM _throttle_win")
    if hot.get("celsius") is not None or throttle.get("level") is not None:
        env["thermal"] = {
            "max_celsius": round(hot["celsius"], 1)
            if hot.get("celsius") is not None else None,
            "hottest_zone": hot.get("zone"),
            **_sampled_before(one, "_thermal_win"),
            # A hot device is not evidence of anything on its own. A cooling
            # device above zero is the kernel saying it took capacity away.
            "throttled": bool(throttle.get("level")),
            "throttle_device": throttle.get("device") if throttle.get("level") else None,
        }
    else:
        env["thermal"] = None

    avail = one(
        "SELECT MIN(value) AS bytes FROM _meminfo_win WHERE key = 'MemAvailable'")
    faults = one(
        "SELECT MAX(value) - MIN(value) AS n, COUNT(*) AS samples "
        "FROM _vmstat_win WHERE key = 'pgmajfault'")
    if avail.get("bytes") is not None or faults.get("samples"):
        env["memory"] = {
            "available_mb_min": round(avail["bytes"] / 1e6, 1)
            if avail.get("bytes") is not None else None,
            **_sampled_before(one, "_meminfo_win", "key = 'MemAvailable'"),
            # A running total, so the window's cost is the difference across
            # it — and a single sample has no difference to give.
            "major_faults": int(faults["n"])
            if (faults.get("samples") or 0) >= 2 else None,
        }
    else:
        env["memory"] = None

    env["sampling"] = _sampling_info(tp, one)
    if env["sampling"] and env["sampling"]["with_stack"]:
        names = _names_info(tp, package, mapped)
        if names:
            env["sampling"]["names"] = names
    env["compose_names"] = _compose_names_info(tp, one)
    env["compile"] = _compile_info(tp, package, one)
    env["missing"] = sorted(k for k in ("cpu", "thermal", "memory")
                            if env[k] is None)
    return env


def _sampled_before(one, view: str, where: str = "1") -> dict:
    """`{"sampled_before_ms": N}` when no sample of the view fell inside the
    window and the value read is the last one before it opened, N ms before;
    else nothing. A short window between two samples a second apart reads
    the state that held over it, and says how old the reading was (#617)."""
    got = one(f"SELECT (SELECT ts_start FROM _window) - MAX(ts) AS ns "
              f"FROM {view} WHERE {where}")
    ns = got.get("ns")
    return {"sampled_before_ms": round(ns / 1e6, 1)} if ns is not None and ns > 0 else {}


# What ART writes when it opens a dex file, as a child of the
# `OpenDexFilesFromOat(<path>)` it opened it for:
#
#   location=/data/app/~~…/com.example.app-…/oat/arm64/base.odex status=up-to-date filter=speed-profile reason=install-dm
#   location=error status=io-error-no-oat filter=run-from-apk reason=unknown
#
# The second is a build with nothing compiled to open, and its `location`
# names no file at all. That is why the app's own file is told by the
# parent's path rather than by `location`.
_DEX_STATE = re.compile(r"\bstatus=(\S+) filter=(\S+) reason=(\S+)")


def _compile_info(tp, package: str, one) -> dict | None:
    """How ART had compiled the app this trace ran, and what it compiled meanwhile.

    The same code runs at very different speeds depending on it. Compiled
    ahead of time from a profile (`speed-profile`), a launch runs the app's
    hot code as machine code. Verified only (`verify`) or run from the APK
    (`run-from-apk`), the interpreter and the JIT carry it. On an SM-A515F a
    large app's launch took ~1.4 s on its benchmark build and ~8.8 s on a
    debuggable build of the same app that nothing had compiled, and a
    comparison of the two rounds would call every row a regression.

    Three readings, from the `dalvik` atrace category that `collect` records
    by default and a macrobenchmark's trace carries too:

    - `filter`, `reason`, `status`: ART's slice for the app's own dex, read
      over the whole trace, since the process opens it at its start, before
      most windows open. `None` when the trace does not hold it: a process
      that was already running when the recording began.
    - `jit`: the methods the JIT compiled inside the window, and its time.
    - `verified`: the classes ART verified at run time inside the window, a
      class that compiled code would have carried verified already. Its time,
      and the part of it on the main thread, which the user waited for.

    `None` when the trace holds none of the three: a recording without the
    `dalvik` category, or a window where nothing of the kind ran.
    """
    state: dict[str, Any] | None = None
    if package:
        try:
            rows = tp.query(f"""
                SELECT d.name AS name
                FROM _slice d
                JOIN slice x ON x.id = d.slice_id
                JOIN slice o ON o.id = x.parent_id
                WHERE d.name GLOB 'location=* filter=*'
                  AND o.name GLOB 'OpenDexFilesFromOat(*/{sql_value(package)}-*'
                ORDER BY d.ts
                LIMIT 1""")
        except Exception as e:  # a trace_processor without one of the tables
            print(f"[!] environment: {e}", file=sys.stderr)
            rows = []
        found = _DEX_STATE.search(rows[0]["name"] or "") if rows else None
        if found:
            state = {"filter": found.group(2), "reason": found.group(3),
                     "status": found.group(1)}
    jit = one("SELECT COUNT(*) AS n, SUM(dur_win) AS ns FROM _slice_win "
              "WHERE name GLOB 'JIT compiling *'")
    # `VerifyClass ` with the space: `VerifyClassLoaderContextMatch` is ART
    # checking a class loader, not a class. A verification inside another is
    # counted once, by the outer one.
    verified = one("""
        SELECT COUNT(*) AS n, SUM(s.dur_win) AS ns,
               SUM(CASE WHEN s.is_main_thread = 1 THEN s.dur_win ELSE 0 END) AS main_ns
        FROM _slice_win s
        WHERE s.name GLOB 'VerifyClass *'
          AND NOT EXISTS (SELECT 1 FROM ancestor_slice(s.slice_id) a
                          WHERE a.name GLOB 'VerifyClass *')""")
    if state is None and not jit.get("n") and not verified.get("n"):
        return None
    out: dict[str, Any] = dict(state or {"filter": None, "reason": None, "status": None})
    out["jit"] = {"methods": jit.get("n") or 0,
                  "ms": round((jit.get("ns") or 0) / 1e6, 2)}
    out["verified"] = {"classes": verified.get("n") or 0,
                       "ms": round((verified.get("ns") or 0) / 1e6, 2),
                       "main_ms": round((verified.get("main_ns") or 0) / 1e6, 2)}
    return out


# The recording's config asked for the Perfetto SDK's events when it holds a
# `track_event` source: `runner.compose_names` writes one, and so does a
# macrobenchmark run with `perfettoSdkTracing.enable=true`.
_TRACK_EVENT_BLOCK = re.compile(r'name:\s*"track_event"')


def _compose_names_info(tp, one) -> dict | None:
    """Whether composables were named while this trace recorded, read from the trace.

    `asked`: the recording's config held a `track_event` source. `calls`: the
    composable calls on the main thread in the window, in runtime-tracing's
    shape. What `compare` goes by is whether any arrived: the names come with
    the app's Perfetto SDK loaded at its start, which on an SM-A515F cost a
    cold start 67 ms on the median, and a round without them did not pay it.
    A config that asked and a trace with none is an app without
    `runtime-tracing`, or an SDK nobody switched on; the report says so.
    """
    try:
        rows = tp.query("SELECT str_value AS text FROM metadata "
                        "WHERE name = 'trace_config_pbtxt'")
        asked = bool(_TRACK_EVENT_BLOCK.search((rows[0]["text"] if rows else None) or ""))
    except Exception as e:  # a trace_processor without the key
        print(f"[!] environment: {e}", file=sys.stderr)
        asked = False
    calls = one("SELECT COUNT(*) AS n FROM _slice_win WHERE is_main_thread = 1 "
                "AND name GLOB '* ([A-Za-z]*.kt:[0-9]*)'").get("n") or 0
    if not asked and not calls:
        return None
    return {"asked": asked, "calls": calls}


def _names_info(tp, package: str, mapped: bool) -> dict | None:
    """How the app's own sampled methods are named: by name, or as R8 left them.

    A minified build's frames come back as `a.b.c`, and a row that names them
    names nothing a reader can find. The report says how many there are, so
    that a minified build is told to hand over its mapping, and one that did
    is told whether the mapping was this build's: a mapping from another build
    renames some frames wrongly and leaves the rest as they were, and the ones
    left are what gives it away.

    The app's code is what the device installed under the package, and what
    the JIT compiled: every method of the app runs from one or the other.
    None when the samples hold no method of the app's at all.

    Only the frames on this process's own stacks, in the window: every ART
    process has a JIT cache of its own, and another profileable one's
    methods diluted the share behind the minified-names warning.
    """
    where = ["m.name GLOB '*jit-cache*'", "m.name GLOB '*jit-code-cache*'"]
    if package:
        where.append(f"m.name GLOB '*/{sql_value(package)}-*'")
    rows = tp.query(f"""
        WITH RECURSIVE chain(id) AS (
            SELECT DISTINCT callsite_id FROM _samples_win WHERE callsite_id IS NOT NULL
            UNION
            SELECT c.parent_id FROM chain
            JOIN stack_profile_callsite c ON c.id = chain.id
            WHERE c.parent_id IS NOT NULL
        )
        SELECT DISTINCT f.name AS name, NULLIF(f.deobfuscated_name, '') AS real,
               m.name AS file
        FROM chain
        JOIN stack_profile_callsite c ON c.id = chain.id
        JOIN stack_profile_frame f ON f.id = c.frame_id
        JOIN stack_profile_mapping m ON m.id = f.mapping
        WHERE {' OR '.join(where)}
    """)
    methods: dict[str, tuple[bool, bool]] = {}
    for r in rows:
        method = stacks_mod.Frame(r.get("real") or r.get("name"), r.get("file")).method
        if method:
            methods[method] = (
                r.get("real") is not None,
                any(mapping_mod.minified(m) for m in method.split(" | ")))
    if not methods:
        return None
    names = {"methods": len(methods),
             "minified": sum(left for _, left in methods.values())}
    if mapped:
        names["renamed"] = sum(renamed for renamed, _ in methods.values())
    return names


def _sampling_info(tp, one) -> dict | None:
    """Whether callstacks were sampled while this trace recorded, read from the trace.

    From the trace rather than from `runner.sampling`, so that a trace
    recorded some other way — by a macrobenchmark's own config, by hand —
    says the same, and so that a config edited since says nothing false.

    Two sources, because either can be there without the other:

    - `perf_session`: the sampler ran. It is what slows the app, so it is what
      `started` says, and `compare` goes by it.
    - the recording's own config, which the tracing service writes into the
      trace: what was asked for, and at what rate. Asked for and not started
      is a device without a sampler to run, and a reader has to be told
      rather than left to find no samples.

    `samples` and `with_stack` count this process's samples inside the window,
    and the difference between them is the diagnosis: none with a stack is an
    app the sampler was not allowed to unwind, and no samples at all while it
    ran is a process the sampler missed. The report's header says which.
    """
    asked, hz = _sampling_asked(tp)
    started = bool(one("SELECT COUNT(*) AS n FROM perf_session").get("n"))
    if not asked and not started:
        return None
    counts = one("SELECT COUNT(*) AS samples, COUNT(callsite_id) AS with_stack "
                 "FROM _samples_win")
    return {
        "hz": hz,
        "started": started,
        "samples": counts.get("samples") or 0,
        "with_stack": counts.get("with_stack") or 0,
    }


# The recording's config as trace_processor prints it back: protobuf text,
# one field per line. Only the linux.perf block is of interest, and within it
# only the rate — `frequency`, the one field of that name the block has.
_PERF_BLOCK = re.compile(r'name:\s*"linux\.perf"(.*?)(?=^data_sources\s*\{|\Z)',
                         re.MULTILINE | re.DOTALL)
_PERF_RATE = re.compile(r"^\s*frequency:\s*(\d+)\s*$", re.MULTILINE)


def _sampling_asked(tp) -> tuple[bool, int | None]:
    """Whether the recording's config asked for callstack samples, and at what rate.

    A trace without its config — an old perfetto, a trace put together by
    another tool — asked for nothing as far as this can tell. A sampler set
    to a period rather than a rate has no rate to report.
    """
    try:
        rows = tp.query("SELECT str_value AS text FROM metadata "
                        "WHERE name = 'trace_config_pbtxt'")
    except Exception as e:  # a trace_processor without the key
        print(f"[!] environment: {e}", file=sys.stderr)
        return False, None
    text = (rows[0]["text"] if rows else None) or ""
    block = _PERF_BLOCK.search(text)
    if block is None:
        return False, None
    rate = _PERF_RATE.search(block.group(1))
    return True, int(rate.group(1)) if rate else None


# The four things a thread can be doing, in trace_processor's vocabulary.
# Anything it does not recognise lands in `other` rather than being dropped:
# a bucket nobody can see is how a budget starts adding up to less than it
# should without saying why.
STATE_BUCKETS = {
    "on_cpu": ("Running",),
    "waiting_for_cpu": ("R", "R+"),
    "in_kernel": ("D", "DK"),
    "sleeping": ("S",),
}


def _main_thread_budget(tp, window: dict) -> dict | None:
    """Where the window went, for the main thread, as time rather than findings.

    The report has always been able to say "5 detectors of 12 fired" and never
    "and that accounts for 300 ms of your 540". Those are different sentences,
    and only the second one lets a reader call a run clean: a scenario whose
    main thread spent 40% waiting for a CPU and 35% blocked in the kernel is a
    compound problem that arrives today as unrelated rows in different
    sections, if it arrives at all.

    Arithmetic, and deliberately nothing else. No threshold, no judgement, no
    row — every number here is a sum over `_tstate_win`, which is already
    clipped to the window, so the parts cannot exceed the whole by
    construction.

    Strictly the main thread. Adding up `self_ms` across `main_thread_block`
    and comparing that against the window is the tempting version and it is
    wrong twice over: other threads are not in it, and slices nest.

    `sleeping` is left as one bucket on purpose. Idle at the message queue and
    blocked inside a message are both `S`, telling them apart needs the slices
    (see `_opened_inside`), and a budget that starts making that call stops
    being arithmetic.
    """
    duration = window.get("duration_ms")
    if not duration:
        return None
    rows = tp.query("""
        SELECT t.state AS state, SUM(t.dur) AS ns
        FROM _tstate_win t
        CROSS JOIN _proc p
        WHERE t.tid = p.pid AND t.dur > 0
        GROUP BY t.state
    """)
    if not rows:
        return None

    by_state = {r["state"]: (r["ns"] or 0) / 1e6 for r in rows}
    named = {s for states in STATE_BUCKETS.values() for s in states}
    out = {bucket: round(sum(by_state.get(s, 0.0) for s in states), 2)
           for bucket, states in STATE_BUCKETS.items()}
    out["other"] = round(
        sum(ms for state, ms in by_state.items() if state not in named), 2)

    accounted = round(sum(out.values()), 2)
    out["accounted_ms"] = accounted
    out["window_ms"] = duration
    # Short of the window means the thread had no state for part of it — the
    # process started inside the window, or the trace has a hole. A state the
    # thread was still in when the recording stopped is not that: it runs to
    # the end of the window (`_tstate_win`). Worth a number rather than a
    # silent shortfall: it is the difference between "the scenario is
    # explained" and "most of it was not looked at".
    out["accounted_pct"] = round(accounted / duration * 100, 1) if duration else None
    return out


def _frames_info(tp, window: dict) -> dict | None:
    """How many frames the app drew in the window, by the frame timeline.

    The frames `frame_jank` counts as its denominator, kept for the window
    itself. In a frame-paced scenario — a scripted scroll or drag recorded
    for a fixed time — a build that janks draws fewer frames in the same
    window, and every row of work done once a frame shrinks with them: five
    times fewer frames read as fourteen improvements that held (#566).
    `compare` reads this to say so. None without a frame timeline.
    """
    lo, hi = window.get("ts_start"), window.get("ts_end")
    if lo is None or hi is None:
        return None
    try:
        rows = tp.query(f"""
            SELECT (SELECT COUNT(*) FROM actual_frame_timeline_slice) AS seen,
                   (SELECT COUNT(*) FROM actual_frame_timeline_slice a
                    JOIN _proc p ON p.upid = a.upid
                    WHERE a.surface_frame_token IS NOT NULL AND a.dur > 0
                      AND a.ts < {int(hi)} AND a.ts + a.dur > {int(lo)}) AS n
        """)
    except Exception:  # a trace_processor without the table: no timeline
        return None
    if not rows or not rows[0]["seen"]:
        return None
    return {"count": rows[0]["n"]}


def _late_frames(results: list[dict]) -> int | None:
    """How many of the window's frames the app itself made late, by `frame_jank`.

    The frames its rows tag `Self Jank`: the platform's word for a deadline
    the app missed, with the detector's own floors, so the number is the one
    its rows add up to. In a frame-paced scenario this is what moves when the
    window cannot: a drag recorded for 15 s took 14.9 s on every build, while
    the app made 3% of its frames late, then 21%, then 10% after the fix
    (#586). Kept per recording, beside the frames it is a share of, so the
    comparison can read the share of each run. None when `frame_jank` did not
    run or could not look.
    """
    jank = next((d for d in results if d["id"] == "frame_jank"), None)
    if jank is None or jank.get("error") or jank.get("checked") is False:
        return None
    return sum(int(r.get("count") or 0) for r in jank.get("rows") or []
               if str(r.get("detail") or "").startswith("Self Jank"))


# The slices a drawn frame leaves on the app's threads, from the `gfx` and
# `view` atrace categories `collect` records by default.
_DRAWN = "(s.name GLOB 'DrawFrame*' OR s.name GLOB 'Choreographer#doFrame*')"


def _drawn_info(tp, window: dict) -> dict | None:
    """How many frames the app's threads drew in the window, by their slices.

    A scenario that never reached its screen draws nothing: a list behind a
    full-screen error state, swiped for 40 s, left 0 `DrawFrames` on the
    RenderThread of every repeat, and the report blamed an old build for the
    markers that were missing (#536). Read from atrace rather than from the
    frame timeline, which `collect` does not record. None when no process in
    the trace drew anything, which says the categories were not recorded.
    """
    lo, hi = window.get("ts_start"), window.get("ts_end")
    if lo is None or hi is None:
        return None
    try:
        rows = tp.query(f"""
            SELECT (SELECT COUNT(*) FROM slice s WHERE {_DRAWN}) AS seen,
                   (SELECT COUNT(*) FROM slice s
                    JOIN thread_track tt ON tt.id = s.track_id
                    JOIN thread t USING (utid)
                    JOIN _proc p ON p.upid = t.upid
                    WHERE {_DRAWN} AND s.ts < {int(hi)} AND s.ts + s.dur > {int(lo)}) AS n
        """)
    except Exception:  # a trace without thread tracks
        return None
    if not rows or not rows[0]["seen"]:
        return None
    return {"count": rows[0]["n"]}


# The receivers the system runs in an app when it takes the app out of the
# stopped state a force-stop left it in (#910): `BOOT_COMPLETED` and
# `LOCKED_BOOT_COMPLETED`, as ActivityThread names the slice of each.
BOOT_RECEIVERS = "broadcastReceiveComp: *BOOT_COMPLETED*"


def _boot_receivers(tp, window: dict) -> list[dict] | None:
    """The app's `BOOT_COMPLETED` receivers the main thread ran in the window,
    with their time: work of the reset, not of the start (#910).

    On Android 15 `am force-stop` leaves the package stopped, and its next
    start takes it out of that state: system_server sends `PACKAGE_UNSTOPPED`,
    and the app is sent `BOOT_COMPLETED` (and `LOCKED_BOOT_COMPLETED` when it
    is direct-boot aware). The receiver runs on the main thread between
    `bindApplication` and the first activity's start. In 25 of 25 Wikipedia
    cold starts on an SM-F711B it was there, 2.1–6.1 ms; a user whose process
    the system reclaimed gets none of it on the next start. An app that
    schedules work, opens a database or syncs in its boot receiver has that
    measured into every cold start `collect` records, and a hunt would look
    for it in the code as if it were the start.

    By name, from the slices of the window's process. None when there are
    none.
    """
    lo, hi = window.get("ts_start"), window.get("ts_end")
    if lo is None or hi is None:
        return None
    rows = tp.query(f"""
        SELECT name, COUNT(*) AS n, SUM(MAX(dur, 0)) AS ns FROM _slice
        WHERE is_main_thread = 1 AND name GLOB '{BOOT_RECEIVERS}'
          AND ts >= {int(lo)} AND ts < {int(hi)}
        GROUP BY name ORDER BY name
    """)
    return [{"name": r["name"], "count": r["n"], "ms": round(r["ns"] / 1e6, 2)}
            for r in rows] or None


def _crowding_info(tp) -> dict | None:
    """How many of the app's threads wanted a core at once, against how many cores there were.

    The main thread's budget says how long it waited for a CPU, and
    `runnable_starvation` who held the core meanwhile. This is the crowd
    behind both: every thread of the app running or ready to run, counted
    moment by moment across the window. On a large Kotlin app's launch on an
    SM-A515F that was a peak of 27–28 threads on 8 cores, and more threads
    ready than cores for ~250 ms of a ~1.4 s launch — the app crowding the
    device out, not the device being busy.

    `peak` is the most at one moment, `above_cores_ms` how long there were
    more than `cores`, and `threads` how many of the app's threads had a
    state in the window. Arithmetic over `_tstate_win`, like the budget, and
    `None` for a trace with no scheduler data.
    """
    cores = (tp.query("SELECT COUNT(DISTINCT cpu) AS n FROM sched") or [{}])[0].get("n")
    if not cores:
        return None
    # A start and an end at one moment are taken in that order, so that a
    # thread switching from ready to running, or one leaving a core as
    # another takes it, is never two threads at once.
    rows = tp.query(f"""
        WITH busy AS (
            SELECT utid, ts, dur FROM _tstate_win
            WHERE state IN ('Running', 'R', 'R+') AND dur > 0
        ),
        ev AS (
            SELECT ts AS t, 1 AS d FROM busy
            UNION ALL
            SELECT ts + dur AS t, -1 AS d FROM busy
        ),
        level AS (
            SELECT t, SUM(d) OVER (ORDER BY t, d ROWS UNBOUNDED PRECEDING) AS n,
                   LEAD(t) OVER (ORDER BY t, d)                         AS next_t
            FROM ev
        )
        SELECT (SELECT COUNT(DISTINCT utid) FROM _tstate_win)           AS threads,
               MAX(n)                                                   AS peak,
               SUM(CASE WHEN n > {int(cores)} AND next_t > t
                        THEN next_t - t ELSE 0 END)                     AS above_ns
        FROM level""")
    if not rows or rows[0].get("peak") is None:
        return None
    out = {"threads": rows[0]["threads"], "cores": int(cores),
           "peak": rows[0]["peak"],
           "above_cores_ms": round((rows[0]["above_ns"] or 0) / 1e6, 2)}
    outside = _outside_info(tp, int(cores))
    if outside is not None:
        out["outside"] = outside
    return out


def _outside_info(tp, cores: int) -> dict | None:
    """How much of the CPUs' time in the window went to other processes, and to whom.

    The crowd above counts the app's own threads, and `runnable_starvation`
    says who held the main thread's core, which other processes do on a
    quiet device too: 35–52% at rest, 42% with eight busy loops of the shell
    user taking 68% of all eight cores (#596). Neither saw the load, and
    the header put the app's waits down to its own work. So the window's
    capacity, cores times its length, is split here by who ran on it: `pct`
    is the share other processes took, with idle and the app left out, and
    `top` the largest of them, processes of one name and uid together, as
    eight `sh` of the shell user are one load. 13–17% on a quiet cold start.
    """
    rows = tp.query("""
        WITH w AS (SELECT ts_start, ts_end FROM _window),
        ran AS (
            SELECT s.utid,
                   MIN(CASE WHEN s.dur < 0 THEN w.ts_end ELSE s.ts + s.dur END, w.ts_end)
                       - MAX(s.ts, w.ts_start) AS dur
            FROM sched s CROSS JOIN w
            WHERE s.utid != 0 AND s.dur != 0
              AND s.ts < w.ts_end
              AND CASE WHEN s.dur < 0 THEN w.ts_end ELSE s.ts + s.dur END > w.ts_start
        )
        SELECT COALESCE(p.name, t.name)        AS name,
               p.uid                           AS uid,
               COUNT(DISTINCT COALESCE(t.upid, -t.utid)) AS processes,
               SUM(r.dur)                      AS ns
        FROM ran r
        JOIN thread t ON t.utid = r.utid
        LEFT JOIN process p ON p.upid = t.upid
        WHERE t.upid IS NULL OR t.upid != (SELECT upid FROM _proc)
        GROUP BY 1, 2
        ORDER BY ns DESC""")
    span = (tp.query("SELECT ts_end - ts_start AS ns FROM _window") or [{}])[0].get("ns")
    if not span or span <= 0 or not cores:
        return None
    capacity = cores * span
    total = sum(r["ns"] or 0 for r in rows)
    return {"pct": round(total / capacity * 100, 1),
            "top": [{"name": r["name"], "uid": r["uid"], "processes": r["processes"],
                     "pct": round((r["ns"] or 0) / capacity * 100, 1)}
                    for r in rows[:3]]}


def _include_modules(tp, detectors) -> None:
    """The stdlib modules the detectors declared, loaded once for the session.

    A module that is not in this trace_processor is not fatal here: the
    detector that wanted it fails on its own line, with its own name against
    the reason, and the rest of the run is unaffected. `analyze` and
    `calibrate` both load them: calibrate ran without, and once
    `binder_txn` and `gc_pressure` read theirs (#514), every trace failed
    them there and kept their thresholds where they were.
    """
    for module in sorted({m for d in detectors for m in d.modules}):
        try:
            tp.exec_script(f"INCLUDE PERFETTO MODULE {module};")
        except Exception as e:
            print(f"[!] module {module}: {e}", file=sys.stderr)


def _spans_behind(tp, d, params: dict,
                  rows: list[dict]) -> list[tuple[int, int]] | None:
    """The main thread's time behind one detector's rows, from its `@intervals`.

    None when the detector has no such query: its rows are then named as not
    counted rather than counted as nothing, which would be a claim about them.
    """
    sql = d.render_intervals(params)
    if sql is None:
        return None
    _rows_table(tp, rows, d.identity)
    return [(int(r["ts"]), int(r["dur"])) for r in tp.query(sql)
            if r.get("ts") is not None and (r.get("dur") or 0) > 0]


def _stacks_behind(tp, d, params: dict, rows: list[dict], package: str,
                   root: Path | None) -> None:
    """What ran behind one detector's rows, from its `@samples`.

    Every row gets `stacks` — what ran on its samples' stacks and the nearest
    frames of the project's own, with their shares — and the gist at the end
    of its evidence, where a reader of one row looks. A detector whose
    identity holds `detail` keeps it as it was: there `detail` is part of the
    row's name, and a name that changed with the samples would not merge
    across repeats. Its rows carry `stacks` alone.

    Nothing is written until every row has been read, so a failure leaves the
    rows as the first query returned them. See stacks.py.
    """
    sql = d.render_samples(params)
    if sql is None:
        return
    _rows_table(tp, rows, d.identity)
    behind: dict[Any, list[int | None]] = {}
    for r in tp.query(sql):
        c = r.get("callsite_id")
        behind.setdefault(r.get("location"), []).append(None if c is None else int(c))
    wanted = {c for found in behind.values() for c in found if c is not None}
    chains = stacks_mod.chains(tp, wanted)
    ours = stacks_mod.ownership(package, root) if chains else None
    read = [stacks_mod.read(behind.get(row.get("location"), []), chains, ours)
            for row in rows]
    for row, (block, words) in zip(rows, read, strict=True):
        row["stacks"] = block
        if "detail" not in d.identity:
            row["detail"] = f"{row['detail']} · {words}" if row.get("detail") else words


def _rows_table(tp, rows: list[dict], identity: tuple[str, ...] = ()) -> None:
    """One detector's rows as `_rows`, for the queries after its first.

    `location`, `detail` and `state`, the column `runnable_starvation` names
    a row by besides its thread, NULL on a row without one; and every other
    column the detector names its rows by: `binder_txn`'s and `gc_pressure`'s
    `thread` (#514). Where the thread became a column of its own, `detail`
    became evidence, and a row found its way back to its slices by the thread
    or not at all.

    One statement at a time — `exec_script` splits on `;`, and a slice name is
    free to contain one.
    """
    # A name that is not one, from a project's own detector, is left out
    # rather than written into the statement.
    extra = [c for c in identity
             if c not in ("location", "detail", "state") and c.isidentifier()]
    tp.query("DROP TABLE IF EXISTS _rows")
    tp.query("CREATE TABLE _rows AS " + " UNION ALL ".join(
        f"SELECT {_sql_text(r.get('location'))} AS location, "
        f"{_sql_text(r.get('detail'))} AS detail, "
        f"{_sql_text(r.get('state'))} AS state"
        + "".join(f", {_sql_text(r.get(c))} AS {c}" for c in extra) for r in rows))


def _sql_text(value) -> str:
    return "NULL" if value is None else f"'{sql_value(str(value))}'"


def _in_rows(window: dict, spans: list[tuple[int, int]],
             uncounted: list[str]) -> dict:
    """How much of the window the findings stand for, each moment counted once.

    The budget above says where the main thread's time went by state; this
    is the other half of the account, and the one a reader was working out by
    hand. Adding up `self_ms` down the report and dividing by the window is
    the tempting way, and it errs in one direction only: a wait for the disk
    usually falls inside some slice's self time as well as in `io_wait`, a
    binder transaction on the main thread is also the self time of the slice
    it sits in, and on the fixture the sum comes to nearly three windows with
    nothing wrong in any row. So each detector names the stretches its rows
    stand for, and here they are laid on one timeline, clipped to the window,
    and every moment is counted once however many rows describe it.

    The main thread only, like the budget: the window's length is made of
    that thread's time, and a background thread working in parallel explains
    none of it until the main thread waits for it — and then the waiting is
    main-thread time again, counted where a row stands for it.
    """
    start, end = window["ts_start"], window["ts_end"]
    covered, reached = 0, start
    for ts, dur in sorted(spans):
        lo, hi = max(ts, reached), min(ts + dur, end)
        if hi > lo:
            covered += hi - lo
            reached = hi
    return {
        "in_rows_ms": round(covered / 1e6, 2),
        "in_rows_pct": round(covered / (end - start) * 100, 1)
        if end > start else None,
        "in_rows_uncounted": sorted(uncounted),
    }


def _detector_masks(overrides: dict | None = None,
                    off: set[str] | frozenset[str] = frozenset()) -> list[tuple[str, str, str]]:
    """The name masks detectors declare through @param.

    Naming convention: `*name_glob*` is a slice-name mask, `*thread_glob*` a
    thread-name mask, `*skip_glob*` an exclusion. That keeps the mask a single
    source of truth: the SQL substitutes it and `names` knows exactly what the
    detector will see. A detector in `off` is one the config turned off: it
    sees nothing, and what only it would have seen is missed.
    """
    out = []
    for d in load_detectors(DETECTOR_DIR):
        if d.id in off:
            continue
        params = dict(d.params)
        params.update((overrides or {}).get(d.id) or {})
        for key, value in params.items():
            if "skip_glob" in key:
                out.append((d.id, "skip", str(value)))
            elif "thread_glob" in key:
                out.append((d.id, "thread", str(value)))
            elif "name_glob" in key:
                out.append((d.id, "name", str(value)))
    return out
