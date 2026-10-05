#!/usr/bin/env python3
"""echolot — a deterministic layer between the trace and the agent.

Start with `echolot` alone: where this project stands, and the next step.
In Claude Code, `/echolot` does the same and takes that step.

The verbs, and the parser that registers them. The command list in `--help` is
generated from that registration, so the grouping by audience cannot drift from
it — it used to be kept by hand here, and by hand in the README, with argparse
printing a third flat copy underneath.

What a command stands on lives beside it, one file per job:

    config.py    echolot.yml, and local.yml layered on top
    tp.py        the pinned trace_processor, and loading detectors from .sql
    report.py    the Marker Report — report.json and report.md
    compare.py   the delta between two of those
    runner.py    the device: adb, the perfetto config, capturing a scenario
    hunt.py      the open investigation — which question the traces answer
    layer.py     the .claude/ layer: what init installs, and whether it drifted
    state.py     where a project stands, and the one word for what is next
    table.py     the one markdown table every one of these prints through
    mark.py      the first markers for a project with no instrumentation
    domains.py   slice name → a place in the code
    reflect/     the same idea as a report, pointed at an agent session
    selftest.py  what doctor runs; fixture.py builds the trace it runs on
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from rich_argparse import RawDescriptionRichHelpFormatter, RichHelpFormatter

from . import compare as compare_mod
from . import hunt as hunt_mod
from . import codex, layer, recorder, state, table, when
from . import mapping as mapping_mod
from . import report as report_mod
from . import stacks as stacks_mod
from .config import NO_ANCHOR, Config, ConfigError
from .reflect.cli import cmd_reflect
from .sandbox import SandboxError
from .tp import (
    ToolchainError,
    TraceSession,
    load_detectors,
    render_sql,
    resolve_binary_path,
    sql_value,
    toolchain_info,
)

SQL_DIR = Path(__file__).parent / "sql"
DETECTOR_DIR = SQL_DIR / "detectors"
_OTHERS_SHOWN = 5   # processes named besides the chosen one when a mask is wide


def cmd_probe(args) -> int:
    """Raw reconnaissance: processes, threads, the longest slices.

    This is what the agent feeds on during setup, so it can offer the user
    real options instead of inventing them.

    The trace_processor is the one `analyze` would run from here: the flag,
    then `toolchain.tp_binary` from the echolot.yml in this directory, the
    local.yml beside it included, then the pin. probe used to take the flag
    or the pin and nothing else, so with a binary named in local.yml the
    first look at a trace and every report after it came from two different
    trace_processors — and the version is what defines the vocabulary the
    detectors match on (see `cmd_doctor`).
    """
    try:
        return _probe(args, _tp_binary(args, _probe_config(args)))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _probe_config(args) -> Config | None:
    """The config `analyze` would read from here — for the binary it names.

    Only a config that is there is read: outside a project, or before setup
    has written one, there is nothing to follow. One that is there and does
    not load is said, and probe goes on without it, on the flag or on the
    pin. `analyze` stops on the same error; a look at a trace has no reason
    to.
    """
    path = getattr(args, "config", None) or "echolot.yml"
    if not Path(path).exists():
        return None
    try:
        return Config.load(path)
    except ConfigError as e:
        instead = ("the one --tp-binary names" if getattr(args, "tp_binary", None)
                   else "the pinned one")
        print(f"[!] {path} does not load, so a trace_processor it names cannot "
              f"be followed — probe uses {instead}: {e}", file=sys.stderr)
        return None


def _probe(args, tp_binary: str | None) -> int:
    with TraceSession(args.trace, tp_binary) as tp:
        print("## Processes\n")
        # Two counts, because they are two kinds of section. `slices` are
        # the threads' — what the detectors read. `async` are the process's
        # own `beginAsyncSection` spans, on a track of their own: what an
        # app's hand-written markers usually are, and what an agent reading
        # only the first column would report as "the app has no
        # instrumentation".
        _dump(tp, f"""
            SELECT p.name AS process, p.pid, COUNT(s.id) AS slices,
                   (SELECT COUNT(*) FROM slice a
                      JOIN process_track pt ON a.track_id = pt.id
                     WHERE pt.upid = p.upid
                       AND pt.type = '{ASYNC_TRACK}') AS async
            FROM process p
            LEFT JOIN thread t ON t.upid = p.upid
            LEFT JOIN thread_track tt ON tt.utid = t.utid
            LEFT JOIN slice s ON s.track_id = tt.id
            WHERE p.name IS NOT NULL
            GROUP BY p.upid ORDER BY slices DESC LIMIT 15
        """)

        if args.process:
            # A mask that matches nothing used to print an empty table and
            # exit 0. Everything else here shouts when the ground it checked
            # was empty — an anchor that never matched, a detector left out of
            # the config — and an agent that mistypes a process name deserves
            # the same. The table above already lists what is there.
            matched = tp.query(
                f"SELECT COUNT(*) AS n FROM process "
                f"WHERE name GLOB '{sql_value(args.process)}'")
            if not (matched and matched[0]["n"]):
                print(f"\nerror: no process matches '{args.process}' in this "
                      f"trace — pick one from the table above, or widen the "
                      f"mask.", file=sys.stderr)
                return 2
            print(f"\n## Threads of process {args.process}\n")
            # Sorted by CPU rather than slice count: a thread with zero slices
            # and hundreds of milliseconds of Running is precisely the blind
            # spot setup is looking for. By slice count it would sit at the
            # bottom.
            _dump(tp, f"""
                SELECT t.name AS thread, t.tid,
                  (SELECT COUNT(*) FROM slice s
                     JOIN thread_track tt ON s.track_id = tt.id
                    WHERE tt.utid = t.utid) AS slices,
                  (SELECT ROUND(COALESCE(SUM(MAX(s.dur,0)),0)/1e6, 1) FROM slice s
                     JOIN thread_track tt ON s.track_id = tt.id
                    WHERE tt.utid = t.utid) AS sliced_ms,
                  (SELECT ROUND(COALESCE(SUM(ts.dur),0)/1e6, 1)
                     FROM thread_state ts
                    WHERE ts.utid = t.utid AND ts.state = 'Running'
                      AND ts.dur > 0) AS running_ms
                FROM thread t
                JOIN process p ON t.upid = p.upid
                WHERE p.name GLOB '{sql_value(args.process)}'
                ORDER BY running_ms DESC, slices DESC LIMIT 25
            """)

            print("\n## Longest slices (scenario anchor candidates)\n")
            # Thread sections and async ones in one list, because an anchor
            # may be either — and the end of a scenario is usually the
            # second kind: a `beginAsyncSection` from wherever the screen
            # was first drawn. An async section has no thread, and the
            # column says so rather than leaving the cell blank.
            _dump(tp, f"""
                SELECT name AS slice, thread, COUNT(*) AS n,
                       ROUND(MAX(dur)/1e6, 2) AS max_ms
                FROM (
                    SELECT s.name AS name, t.name AS thread, s.dur AS dur
                    FROM slice s
                    JOIN thread_track tt ON s.track_id = tt.id
                    JOIN thread t ON tt.utid = t.utid
                    JOIN process p ON t.upid = p.upid
                    WHERE p.name GLOB '{sql_value(args.process)}'
                    UNION ALL
                    SELECT s.name, '{ASYNC_THREAD}', s.dur
                    FROM slice s
                    JOIN process_track pt ON s.track_id = pt.id
                    JOIN process p ON pt.upid = p.upid
                    WHERE p.name GLOB '{sql_value(args.process)}'
                      AND pt.type = '{ASYNC_TRACK}'
                )
                GROUP BY name ORDER BY max_ms DESC LIMIT 25
            """)
    return 0


# How trace_processor labels the tracks that `Trace.beginAsyncSection` writes
# to, and what this tool prints in the thread column for a section that has
# no thread. The label rather than a blank: an empty cell reads as a thread
# whose name was lost, and this is a section that never had one. See
# `_aslice` in context.sql for why those sections are read at all.
ASYNC_TRACK = "atrace_async_slice"
ASYNC_THREAD = "(async)"


def _tp_binary(args, cfg: Config | None = None) -> str | None:
    """Precedence: the flag, then `toolchain.tp_binary` from the config
    (usually its local.yml), then the pin — `perfetto==` in pyproject.toml."""
    return _tp_binary_source(args, cfg)[0]


def _tp_binary_source(args, cfg: Config | None = None) -> tuple[str | None, str | None]:
    """The binary and who asked for it, since the report names both.

    Two callers want different halves of this and the second one used to be
    guessed at: every custom binary was reported as `--tp-binary` whether or
    not a flag was involved. See `toolchain_info`.

    `(None, None)` is the pin. It is not a path anyone wrote down: the
    `perfetto` version pyproject.toml fixes carries a manifest, and the
    manifest names the trace_processor it downloads. requirements.txt holds
    nothing but `-e .`, though doctor used to name it as the pin.

    `doctor` asks the same question as `analyze`, in the same order, so that
    the binary it vouches for is the one the reports come from.
    """
    from_flag = getattr(args, "tp_binary", None)
    if from_flag:
        return from_flag, "--tp-binary"
    if cfg and cfg.tp_binary:
        return cfg.tp_binary, "toolchain.tp_binary"
    return None, None


def _note_local(cfg: Config) -> None:
    """local.yml changes the result, so its use is announced.

    It is not committed, so two people on the same commit can get diverging
    runs — and the first thing to know is that something was layered on top of
    the project config.
    """
    if cfg.local_path:
        print(f"[i] {cfg.local_path} applied on top of the config",
              file=sys.stderr)


def _note_detectors(cfg: Config) -> None:
    """How many detectors the config actually has an opinion about.

    The section used to double as an allowlist, so a config naming six of ten
    ran six. It no longer does, and that is a change in what an existing
    config means — said out loud rather than discovered in a report that grew
    sections nobody configured. It also answers the question the change
    raises: how do I turn one off now.
    """
    tuned = set(cfg.detector_overrides)
    shipped = {d.id for d in load_detectors(DETECTOR_DIR)}
    off = cfg.disabled_detectors
    rest = shipped - tuned - off
    if tuned and rest:
        print(f"[i] the config tunes {len(tuned)} of {len(shipped)} detectors; "
              f"the other {len(rest)} run on their shipped thresholds. "
              f"To turn one off: `<detector>: false` under `detectors:`.",
              file=sys.stderr)


def _out_dir(out: str, cfg: Config) -> Path:
    """A relative -o is taken from the config's directory, not from cwd.

    The agent runs analyze from wherever the traces are — a build directory
    with a space in its name — and the report used to land there, in a
    `.echolot/out` nobody would look for. The config is what names the
    project; the report goes next to it. A side effect worth having: an ad-hoc
    config in /tmp no longer overwrites the project's report.
    """
    p = Path(out)
    if p.is_absolute() or cfg.path is None:
        return p
    return Path(cfg.path).resolve().parent / p


def _project_root(cfg: Config) -> Path:
    """The directory the config names, which is what "this project" means.

    `analyze` is run from wherever the traces are — the agent calls it inside a
    macrobenchmark's output directory. The report already follows the config
    rather than the working directory (see `_out_dir`); the open investigation
    has to follow it for the same reason, or a hunt is silently left untouched
    and the freshness rule then reports it as abandoned.
    """
    return Path(cfg.path).resolve().parent if cfg.path else Path.cwd()


def project_of(args) -> Path:
    """The same question `_project_root` answers, from the arguments alone.

    `_project_root` needs a loaded config, and the run log is written by
    `main` after the command has returned — including when it returned
    because the config would not load. So the rule is read off the flags
    instead: whatever names a project explicitly, then the config's own
    directory, then the working directory.

    One place rather than a call in each command. A `recorder.at(...)` per
    verb is a list kept by hand, and the verb somebody forgets is the one
    whose runs go missing — which is the failure this is fixing, arriving by
    a different road.
    """
    for flag in ("project", "into"):        # reflect --project, init --into
        named = getattr(args, flag, None)
        if named:
            return Path(named).resolve()
    config = getattr(args, "config", None)
    if config and Path(config).exists():
        return Path(config).resolve().parent
    return Path.cwd()


def parse_set(values: list[str], detectors) -> dict[str, dict]:
    """--set detector.param=value, repeatable, into per-detector overrides.

    Values are read as YAML scalars so `16`, `4.5` and `binder*` arrive typed
    the same way they would from the config. Unknown detectors and parameters
    are refused with the list of valid ones: a silently ignored typo is worse
    than no flag at all.

    A mask is a glob, and a glob is not always YAML: `*GC*` is an alias
    nobody defined, the same shape as the shipped defaults `*GC` and
    `*async*`, and it came out of `yaml.safe_load` as a traceback. So a
    string parameter takes the value as it was typed whenever YAML does not
    read it as a string — `[Gg]` stays a glob rather than becoming a list,
    `on` a word rather than true. A number that does not parse is kept as
    typed too, and `check` refuses it in a sentence.
    """
    import yaml
    known = {d.id: d for d in detectors}
    out: dict[str, dict] = {}
    for item in values:
        key, sep, raw = item.partition("=")
        det, dot, param = key.strip().partition(".")
        if not sep or not dot or not det or not param:
            raise ConfigError(f"--set expects detector.param=value, got '{item}'")
        if det not in known:
            raise ConfigError(
                f"--set: no detector '{det}'. Known: {', '.join(sorted(known))}")
        if param not in known[det].params:
            raise ConfigError(
                f"--set: {det} has no parameter '{param}'. "
                f"It has: {', '.join(sorted(known[det].params))}")
        text = raw.strip()
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError:
            value = text
        if isinstance(known[det].params[param], str) and text and not isinstance(value, str):
            value = text
        out.setdefault(det, {})[param] = value
    return out


def plan_detectors(cfg: Config, *, cli_overrides: dict[str, dict] | None = None,
                   use_defaults: bool = False) -> list[tuple]:
    """Which detectors run, with which overrides, from where.

    Pure — no trace, no trace_processor — so the self-check can pin the
    rules without spinning up a session per case. Returns
    [(detector, overrides, source)] with source one of
    default / config / cli / config+cli.

    Also where a threshold of the wrong kind is refused. That has to happen
    here rather than at render time: `analyze_trace` renders inside a
    `try/except` that turns any failure into one detector's error line, and a
    value the config cannot mean is a config error — the same answer
    `parse_set` already gives a `--set` typo, before a trace is opened.
    """
    detectors = load_detectors(DETECTOR_DIR)
    cli_overrides = cli_overrides or {}
    cfg_overrides = {} if use_defaults else cfg.detector_overrides
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
        overrides = {**from_cfg, **from_cli}
        source = "+".join(
            s for s, on in (("config", bool(from_cfg)), ("cli", bool(from_cli))) if on
        ) or "default"
        # Named per side, so the message points at the file or at the flag
        # rather than at "somewhere in the thresholds".
        for values, whose in ((from_cfg, "from the config"),
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
                  tp_source: str | None = None) -> dict:
    """The core of a run: trace + config → Marker Report.

    Separate from cmd_analyze because it has two callers: the command, which
    reads the config from a file and writes the report to disk, and the
    self-check, which keeps the config in memory and compares the result with
    expectations. Raises ConfigError.

    Thresholds come from three places, in this order: the detector's own
    defaults, the config's `detectors:` section, then `--set` from the command
    line. `--defaults` drops the middle one — every detector, built-in numbers,
    the config untouched. Each detector in the report says which of the three
    it got (`params_source`), so a reader can tell calibrated numbers from
    the shipped ones without opening the config.
    """
    plan = plan_detectors(cfg, cli_overrides=cli_overrides, use_defaults=use_defaults)
    results = []
    package = str(cfg.get("project.package") or "")
    # The build's R8 mapping, read before the trace is opened: a path that is
    # not there is the config's mistake, and is said before any work is done.
    mapping = cfg.mapping
    extra = mapping_mod.packet_for(mapping, package) if mapping else None

    with TraceSession(trace, tp_binary, extra=extra) as tp:
        procs = _resolve_process(tp, cfg.process, str(trace))
        # The masks as this run has them: `--set` moves a boundary the same
        # way the config does, and `_claimed_name` is drawn from where they
        # stand rather than from where the file left them.
        _setup_context(tp, cfg, procs[0]["upid"],
                       {d.id: ov for d, ov, _ in plan if ov})
        window = _window_info(tp, cfg, procs)
        environment = _environment_info(
            tp, package or str(procs[0].get("name") or "").split(":")[0],
            mapped=extra is not None)
        markers = _markers_info(tp, cfg)
        # What ran behind a row is read only where a sampler ran: a trace
        # without samples gets the report it always got. The checkout, for
        # whose code is whose, is the config's directory, and there is none
        # for a config that lives in memory.
        sampled = bool((environment.get("sampling") or {}).get("started"))
        root = Path(cfg.path).resolve().parent if cfg.path else None

        # Stdlib modules the detectors declared, loaded once for the session.
        # A module that is not in this trace_processor is not fatal here: the
        # detector that wanted it fails on its own line below, with its own
        # name against the reason, and the rest of the run is unaffected.
        for module in sorted({m for d, _, _ in plan for m in d.modules}):
            try:
                tp.exec_script(f"INCLUDE PERFETTO MODULE {module};")
            except Exception as e:
                print(f"[!] module {module}: {e}", file=sys.stderr)

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
                rows, params, err = [], d.params, str(e)
                print(f"[!] {d.id}: {e}", file=sys.stderr)
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
            entry = {
                "id": d.id,
                "title": d.title,
                "why": d.why,
                "params": params,
                "params_source": source,
                # What tells one row from another. Written down per report so
                # that merging repeats does not have to guess, and so that a
                # report read back later still knows.
                "identity": list(d.identity),
                "rows": rows,
                "error": err,
            }
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

    return report_mod.build(str(trace), window, results,
                            toolchain=toolchain_info(tp_binary, tp_source),
                            absent=absent, environment=environment,
                            markers=markers)


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
    from .mark import DEFAULT_PREFIX
    prefix = str(cfg.get("instrumentation.temp_prefix") or DEFAULT_PREFIX)
    globs = [prefix + "*"]
    for entry in cfg.get("domains") or []:
        name = entry.get("slice") if isinstance(entry, dict) else None
        if name and name not in globs:
            globs.append(str(name))
    wanted = " OR ".join(f"name GLOB '{sql_value(g)}'" for g in globs)
    rows = tp.query(f"""
        WITH seen AS (
            SELECT s.slice_id, s.name, s.thread_name AS thread, s.dur, s.unfinished
            FROM _slice_win s WHERE {wanted}
            UNION ALL
            SELECT a.slice_id, a.name, '{ASYNC_THREAD}', a.dur, a.unfinished
            FROM _aslice_win a WHERE {wanted}
        )
        SELECT seen.name AS location, seen.thread AS thread,
               COUNT(*) AS count,
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
            "total_ns": 0, "max_ns": 0, "threads": set()})
        row["count"] += r["count"]
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
    out.sort(key=lambda r: (-r["total_ms"], r["location"]))
    # A name the config lists that the window never held is worth a line:
    # the map points at something this scenario does not run, or the name
    # changed under it.
    absent = [g for g in globs[1:]
              if not any(fnmatch.fnmatchcase(r["location"], g) for r in out)]
    return {"prefix": prefix, "globs": globs, "rows": out, "absent": absent}


def cmd_analyze(args) -> int:
    try:
        cfg = Config.load(args.config, args.local)
        tp_binary, tp_source = _tp_binary_source(args, cfg)
        _note_local(cfg)
        if not args.defaults:
            _note_detectors(cfg)
        cli_overrides = parse_set(args.set or [], load_detectors(DETECTOR_DIR))
        if args.defaults:
            print("[i] --defaults: the config's detectors section is ignored, "
                  "every detector runs with its built-in thresholds",
                  file=sys.stderr)
        # Repeats are merged by median: one outlier must not drag the
        # conclusion along, and the "Runs" column separates the reproducible
        # from the one-off.
        reports = [analyze_trace(t, cfg, tp_binary, cli_overrides=cli_overrides,
                                 use_defaults=args.defaults, tp_source=tp_source)
                   for t in args.traces]
        rep = report_mod.aggregate(reports)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        recorder.failed(str(e))
        return 2

    # After the merge rather than per trace: the evidence a merged row
    # carries is the worst repeat's, and that is the string worth placing.
    # The checkout is the config's directory — where `analyze` was run from
    # is a macrobenchmark's output directory as often as not.
    from . import place as place_mod
    placed = place_mod.annotate(rep, _project_root(cfg))
    if placed:
        recorder.note(placed=placed)

    # Which config made this report. Without it the next reader of
    # report.json cannot tell the project's run from one against an ad-hoc
    # config in /tmp — they look the same.
    rep["config"] = {
        "path": str(Path(cfg.path).resolve()) if cfg.path else None,
        "sha": cfg.sha,
        "local": str(Path(cfg.local_path).resolve()) if cfg.local_path else None,
        "defaults": bool(args.defaults),
        "set": cli_overrides or None,
    }

    w = rep.get("window") or {}
    recorder.note(
        traces=len(args.traces),
        fired=rep["summary"]["fired_ids"],
        window_ms=w.get("duration_ms"),
        start_anchor_matches=(w.get("start_anchor") or {}).get("matches"),
        end_anchor_matches=(w.get("end_anchor") or {}).get("matches"),
        process_alternatives=len(w.get("process_alternatives") or []),
    )
    if args.defaults or cli_overrides:
        recorder.note(
            thresholds="defaults" if args.defaults else "config+cli",
            overrides=sorted(f"{d}.{p}" for d, ps in cli_overrides.items() for p in ps),
        )

    out_dir = _out_dir(args.out, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        report_mod.to_json(rep), encoding="utf-8")
    (out_dir / "report.md").write_text(
        report_mod.to_markdown(rep), encoding="utf-8")

    root = _project_root(cfg)
    # A value a person confirmed, changed since the investigation opened:
    # the report is about a window they did not agree to. Said before the
    # report, where a reader who stops at its first lines still sees it, and
    # logged, so `reflect` can say it without a transcript (#196).
    _warn_confirmed_changed(root, cfg)
    print(report_mod.to_markdown(rep))
    print(f"\n→ {out_dir/'report.md'}\n→ {out_dir/'report.json'}",
          file=sys.stderr)
    # `touched_at` has to mean work, not "when someone last typed echolot":
    # the freshness rule that decides whether to ask stands on it.
    hunt_mod.touch(root, analyze=True)
    # .echolot/out/report.json is always the latest and every analyze
    # overwrites it, including one belonging to a different question. The
    # investigation keeps its own copy of each.
    kept = hunt_mod.record_report(root, out_dir)
    if kept is not None:
        print(f"→ {kept}  (this investigation's copy)", file=sys.stderr)
    return 0


def _warn_confirmed_changed(root: Path, cfg: Config) -> list[dict[str, Any]]:
    """Say each value a person confirmed that differs from what they confirmed.

    Only inside an open investigation, which recorded the values when it
    opened; outside one there is nothing to hold the config to.
    """
    hunt = hunt_mod.load(root)
    if not hunt or hunt.get("status") != "open":
        return []
    changed = hunt_mod.confirmed_changed(hunt, cfg.confirmed())
    for c in changed:
        print(f"[!] {c['field']} changed since this investigation opened: a "
              f"person confirmed {c['was']!r}, and the config now says "
              f"{c['now']!r}.\n    This report measures what they did not "
              f"confirm. The value is theirs to change: ask them.",
              file=sys.stderr)
    if changed:
        recorder.note(confirmed_changed=changed)
    return changed


def _reports_of_hunt(project: Path, ident: str) -> list[Path]:
    """Every report an investigation kept, in the order it wrote them."""
    found = hunt_mod.find(project, ident)
    if found is None:
        raise ConfigError(
            f"no investigation matching '{ident}'. `echolot hunt --list` shows "
            f"every one this project has had.")
    home = hunt_mod.home(project, found)
    reports = sorted((home / "reports").glob("*.json")) if home else []
    if len(reports) < 2:
        raise ConfigError(
            f"investigation {found.get('n')} has {len(reports)} report(s) — "
            f"a comparison needs two. Each `echolot analyze` inside an open "
            f"investigation files one.")
    return reports


def _compare_pair(args, project: Path) -> tuple[Path, Path]:
    """Which two reports, from what the caller gave.

    The bare form is the one an agent uses inside the loop: it changed
    something, re-recorded, and wants to know what that did. Naming two paths
    is the form CI uses, where nothing is "open".
    """
    paths = [Path(p) for p in (args.paths or [])]
    if len(paths) > 2:
        raise ConfigError("compare takes at most two reports")
    if len(paths) == 2:
        return paths[0], paths[1]

    latest = _out_dir(args.out, args.cfg) / "report.json" if args.cfg else \
        Path(".echolot/out/report.json")
    if len(paths) == 1:
        # One path is "against what I just measured": the named report is the
        # older side, because that is the direction of every question asked
        # here — what did the change do.
        return paths[0], latest

    if args.hunt:
        reports = _reports_of_hunt(project, str(args.hunt))
        return reports[0], reports[-1]

    open_hunt = hunt_mod.load(project)
    if open_hunt and open_hunt.get("status") == "open":
        reports = _reports_of_hunt(project, str(open_hunt.get("n")))
        return reports[-2], reports[-1]

    raise ConfigError(
        "nothing to compare. Name two reports, or `--hunt <n>` for an "
        "investigation's first against its last. With an investigation open, "
        "`echolot compare` on its own takes its previous round against the "
        "latest.")


def _load_report(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"report not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON ({e})") from e
    if data.get("kind") == "comparison":
        raise ConfigError(
            f"{path} is a comparison, not a Marker Report. Compare two reports "
            f"written by `echolot analyze`.")
    if "detectors" not in data:
        raise ConfigError(f"{path}: not a Marker Report — no `detectors` section")
    return data


def cmd_compare(args) -> int:
    """The delta between two Marker Reports."""
    try:
        args.cfg = None
        with contextlib.suppress(ConfigError):
            args.cfg = Config.load(args.config, args.local)
        project = _project_root(args.cfg) if args.cfg else Path.cwd()

        before_path, after_path = _compare_pair(args, project)
        cmp = compare_mod.build(
            _load_report(before_path), _load_report(after_path),
            before_path=str(before_path), after_path=str(after_path),
            floor_ms=args.floor_ms, floor_ratio=args.floor_pct / 100.0,
            temp_prefix=(args.cfg.get("instrumentation.temp_prefix")
                         if args.cfg else None))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        recorder.failed(str(e))
        return 2

    s = cmp["summary"]
    recorder.note(comparable=cmp["comparable"], moved=s["moved"],
                  appeared=s["appeared"], vanished=s["vanished"],
                  warnings=[w["id"] for w in cmp["warnings"]])

    text = compare_mod.to_markdown(cmp)
    print(text)

    # Next to the report it is about, by the same rule: a relative path is
    # taken from the config's directory, so running this from wherever the
    # traces are does not scatter output across build directories.
    if args.cfg is not None:
        out_dir = _out_dir(args.out, args.cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "comparison.json").write_text(
            report_mod.to_json(cmp), encoding="utf-8")
        (out_dir / "comparison.md").write_text(text, encoding="utf-8")
        print(f"\n\u2192 {out_dir/'comparison.md'}\n\u2192 {out_dir/'comparison.json'}",
              file=sys.stderr)
    else:
        print("\n[i] no config found, so nothing was written to disk — "
              "the comparison above is the whole output", file=sys.stderr)
    return 0


def _resolve_process(tp, glob: str, trace: str | None = None) -> list[dict]:
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
        # The name a trace carries is not always the one the package has.
        # Linux truncates comm to 15 characters and keeps the TAIL, so
        # `com.example.myapp` can arrive as `m.example.myapp` — which a
        # trailing-wildcard glob does not match either. Seen on one trace out
        # of fifteen from a single macrobenchmark round, where the other
        # fourteen carried the full name.
        tail = glob.rstrip("*")[-15:]
        raise ConfigError(
            f"no process in trace{where} matches project.process = '{glob}'. "
            f"Look at the real names: echolot probe <trace>. If the name is "
            f"there but cut to fifteen characters, the trace has it from "
            f"comm rather than from the process list, and the head is what "
            f"was cut — try project.process = '*{tail}'."
        )
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
            f"Others: {others}. Narrow it with --process or project.process.",
            file=sys.stderr,
        )
    return rows


def _setup_context(tp, cfg: Config, upid: int,
                   mask_overrides: dict | None = None) -> dict:
    """Prepares the views in two passes and returns the window bounds.

    Between the passes the CLI grabs ts_start/ts_end and substitutes them into
    window.sql as plain numbers. While the window was a view it was recomputed
    on every reference to _slice_win: on a trace with 475k slices the run did
    not finish within ten minutes.

    `mask_overrides` is where the name masks stand for this run — the config's
    plus whatever `--set` moved. It reaches `_claimed_name`, and the default
    is the config alone, which is what a caller with no plan of its own has.
    """
    tp.exec_script(render_sql(
        (SQL_DIR / "context.sql").read_text(encoding="utf-8"),
        cfg.context_params(upid),
    ))
    bounds = tp.query("SELECT ts_start, ts_end FROM _window")[0]
    for phase in ("window.sql", "environment.sql"):
        tp.exec_script(render_sql(
            (SQL_DIR / phase).read_text(encoding="utf-8"),
            {"ts_start": bounds["ts_start"], "ts_end": bounds["ts_end"]},
        ))
    _claim_names(tp, cfg.detector_overrides if mask_overrides is None
                 else mask_overrides)
    return bounds


def _claim_names(tp, overrides: dict) -> None:
    """`_claimed_name` — every slice name a detector's mask already speaks for.

    Three of the thirteen detectors know the names of what they are looking for
    and say so in a `*name_glob*` param: `*GC`, `Lock contention on a monitor
    lock*`, `binder transaction`. `repeated_work` knows no name in advance —
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


def _window_info(tp, cfg: Config, procs: list[dict]) -> dict:
    """Window bounds plus proof that the anchors matched anything at all.

    An anchor that never matched is the most common source of a garbage
    report: the window silently collapses onto the whole trace, every detector
    screams at once, and the agent goes off investigating the wrong thing. Let
    that be visible rather than guessed at.
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

    for key, glob in (("start", cfg.scenario_start), ("end", cfg.scenario_end)):
        if glob == NO_ANCHOR:
            window[f"{key}_anchor"] = None
            continue
        # `_anchor` rather than `_slice`: the same set the window was built
        # from, async sections included. Counting the one and building from
        # the other is how `matches` would say 0 for a window that closed.
        hits = tp.query(
            f"SELECT COUNT(*) AS n FROM _anchor WHERE name GLOB '{sql_value(glob)}'"
        )
        window[f"{key}_anchor"] = {
            "glob": glob,
            "matches": hits[0]["n"] if hits else 0,
        }
        # The end anchor the window closed on, still open when the recording
        # stopped: the window ran on to the end of the trace, and the
        # scenario never reached its end inside it.
        if key == "end" and window[f"{key}_anchor"]["matches"]:
            first = tp.query(
                f"SELECT a.dur < 0 AS open FROM _anchor a "
                f"WHERE a.name GLOB '{sql_value(glob)}' "
                f"AND a.ts >= (SELECT ts_start FROM _window) ORDER BY a.ts LIMIT 1")
            if first and first[0]["open"]:
                window[f"{key}_anchor"]["unfinished"] = True
    window["opened_inside"] = _opened_inside(tp, window)
    window["startup"] = _startup_info(tp, window, procs[0]["name"])
    return window


# When the part of a block that fell outside the window is worth saying.
#
# The rule is a comparison rather than a threshold: warn once more of the
# block happened before the window than inside it, which is the point where
# the number in the report is at most half the truth. Nothing to calibrate and
# nothing that changes with the length of the scenario — a share of the window
# would let 300 ms of hidden stall pass unmentioned on a twelve-second
# recording and shout about 2 ms on a twenty-millisecond one.
#
# The floor underneath it is `compare.FLOOR_MS`, the smallest movement this
# tool already refuses to call a finding.


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
    start = window.get("ts_start")
    if start is None:
        return None
    rows = tp.query(f"""
        SELECT ts.state                              AS state,
               th.name                               AS thread_name,
               ROUND(({start} - ts.ts) / 1e6, 2)     AS before_ms,
               ROUND(ts.dur / 1e6, 2)                AS total_ms
        FROM thread_state ts
        JOIN thread th ON ts.utid = th.utid
        JOIN _proc p   ON th.upid = p.upid
        WHERE th.tid = p.pid
          AND ts.dur > 0
          AND ts.ts < {start}
          AND ts.ts + ts.dur > {start}
    """)
    if not rows or rows[0]["state"] == "Running":
        return None
    if rows[0]["state"] not in ("R", "R+", "D", "DK"):
        # Sleeping. Only a block if a message was open at the time — otherwise
        # the looper had reached the queue, which is the app working properly.
        inside_message = tp.query(f"""
            SELECT COUNT(*) AS n
            FROM _slice s
            WHERE s.is_main_thread = 1 AND s.depth >= 1 AND s.dur > 0
              AND s.ts < {start} AND s.ts + s.dur > {start}
        """)
        if not inside_message or not inside_message[0]["n"]:
            return None
    found = dict(rows[0])
    inside = found["total_ms"] - found["before_ms"]
    found["inside_ms"] = round(inside, 2)
    found["material"] = bool(found["before_ms"] >= inside
                             and found["before_ms"] >= compare_mod.FLOOR_MS)
    return found


def _startup_info(tp, window: dict, process: str) -> dict | None:
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
    try:
        tp.exec_script("INCLUDE PERFETTO MODULE android.startup.startups;")
        startups = tp.query(f"""
            SELECT startup_id, ts, ts_end, dur, startup_type AS type
            FROM android_startups
            WHERE package = '{sql_value(package)}' AND dur > 0
            ORDER BY ts
        """)
        if not startups:
            return None

        def shared(s: dict) -> int:
            return max(0, min(s["ts_end"], end) - max(s["ts"], start))

        chosen = max(startups, key=lambda s: (shared(s), -s["ts"]))
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
    env["missing"] = sorted(k for k in ("cpu", "thermal", "memory")
                            if env[k] is None)
    return env


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
    """
    where = ["m.name GLOB '*jit-cache*'", "m.name GLOB '*jit-code-cache*'"]
    if package:
        where.append(f"m.name GLOB '*/{sql_value(package)}-*'")
    rows = tp.query(f"""
        SELECT DISTINCT f.name AS name, NULLIF(f.deobfuscated_name, '') AS real,
               m.name AS file
        FROM stack_profile_frame f
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


def _spans_behind(tp, d, params: dict,
                  rows: list[dict]) -> list[tuple[int, int]] | None:
    """The main thread's time behind one detector's rows, from its `@intervals`.

    None when the detector has no such query: its rows are then named as not
    counted rather than counted as nothing, which would be a claim about them.
    """
    sql = d.render_intervals(params)
    if sql is None:
        return None
    _rows_table(tp, rows)
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
    _rows_table(tp, rows)
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


def _rows_table(tp, rows: list[dict]) -> None:
    """One detector's rows as `_rows`, for the queries after its first.

    One statement at a time — `exec_script` splits on `;`, and a slice name is
    free to contain one.
    """
    tp.query("DROP TABLE IF EXISTS _rows")
    tp.query("CREATE TABLE _rows AS " + " UNION ALL ".join(
        f"SELECT {_sql_text(r.get('location'))} AS location, "
        f"{_sql_text(r.get('detail'))} AS detail" for r in rows))


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


# Inventory sections. The keywords are deliberately broad: the job is to show
# what the detector masks MISS, and one extra row in a section is cheaper than
# an undiscovered problem.
BUCKETS = (
    # Keywords are matched ONLY against the slice name. Order matters: the
    # first matching section claims the family, which is why 'allocating' sits
    # under garbage collection and pulls waitWhileAllocatingLocked there even
    # though its name also contains 'lock'. That is waiting on the collector,
    # not contention.
    # 'allocating' rather than 'alloc': otherwise all graphics buffer work
    # (allocateBuffers, IAllocator::allocate) would land here too.
    # 'collection' rather than 'collect': otherwise ProfileSaver::CollectClasses
    # comes along, and it has nothing to do with garbage collection.
    ("Garbage collection", ("gc", "garbage", "collection", "allocating", "heap")),
    # 'locked' rather than 'lock': the word 'block' also contains 'lock', and
    # that is how 'LZ4 decompress block' ended up under contention. The real
    # names are either '...Locked' methods or 'lock contention', which the word
    # 'contention' already catches.
    ("Locks and waiting", ("contention", "monitor", "locked", "mutex", "futex")),
    ("Binder / IPC", ("binder", "transact", "ipc")),
)

def _detector_masks(overrides: dict | None = None) -> list[tuple[str, str, str]]:
    """The name masks detectors declare through @param.

    Naming convention: `*name_glob*` is a slice-name mask, `*thread_glob*` a
    thread-name mask, `*skip_glob*` an exclusion. That keeps the mask a single
    source of truth: the SQL substitutes it and `names` knows exactly what the
    detector will see.
    """
    out = []
    for d in load_detectors(DETECTOR_DIR):
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


def _name_coverage(tp, upid: int, masks):
    """Slice name → (who will see it, who excluded it on purpose).

    The distinction matters: a name dropped by `skip_glob` is a decision, not a
    miss. Lumping them together means calling people to fix what is not broken.
    SQLite evaluates the GLOB itself, so the answer is exact rather than
    something that merely resembles GLOB.
    """
    positive: dict[str, set[str]] = defaultdict(set)
    negative: dict[str, set[str]] = defaultdict(set)
    for det, kind, glob in masks:
        column = "t.name" if kind == "thread" else "s.name"
        rows = tp.query(f"""
            SELECT DISTINCT s.name AS name
            FROM slice s
            JOIN thread_track tt ON s.track_id = tt.id
            JOIN thread t        ON tt.utid = t.utid
            WHERE t.upid = {upid} AND {column} GLOB '{sql_value(glob)}'
        """)
        bucket = negative if kind == "skip" else positive
        bucket[det].update(r["name"] for r in rows)

    covered: dict[str, set[str]] = defaultdict(set)
    skipped: dict[str, set[str]] = defaultdict(set)
    for det, names in positive.items():
        dropped = negative.get(det, set())
        for name in names - dropped:
            covered[name].add(det)
        for name in names & dropped:
            skipped[name].add(det)
    return covered, skipped


def cmd_names(args) -> int:
    """The inventory of slice names in a trace and what the detectors see.

    Answers the one question that cannot be settled by reading SQL: how ART on
    THIS device names GC, locks and binder — and whether the detector masks
    land on those names.
    """
    overrides, tp_bin, prefix, process = _names_setup(args)
    try:
        session = TraceSession(args.trace, tp_bin)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    with session as tp:
        try:
            procs = _resolve_process(tp, process)
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        upid = procs[0]["upid"]
        if not args.json:
            print(f"Process: `{procs[0]['name']}` (pid {procs[0]['pid']})\n")

        rows = tp.query(f"""
            SELECT s.name AS name, t.name AS thread, COUNT(*) AS n,
                   SUM(MAX(s.dur, 0)) AS total_ns
            FROM slice s
            JOIN thread_track tt ON s.track_id = tt.id
            JOIN thread t        ON tt.utid = t.utid
            WHERE t.upid = {upid}
            GROUP BY s.name, t.name
        """)
        # The process's own async sections, under a thread name that says
        # there is none. They are listed with the rest because the question
        # this command answers — "what is this slice called here" — is the
        # one an anchor is chosen by, and the app's markers are usually
        # async. The masks below do not apply to them: no detector reads a
        # section that belongs to no thread, and the dash in the mask column
        # is exact for once.
        async_rows = tp.query(f"""
            SELECT s.name AS name, '{ASYNC_THREAD}' AS thread, COUNT(*) AS n,
                   SUM(MAX(s.dur, 0)) AS total_ns
            FROM slice s
            JOIN process_track pt ON s.track_id = pt.id
            WHERE pt.upid = {upid} AND pt.type = '{ASYNC_TRACK}'
            GROUP BY s.name
        """)
        rows += async_rows
        if not rows:
            print("_this process has no slices in the trace_")
            return 0

        covered, skipped = _name_coverage(tp, upid, _detector_masks(overrides))
        families = group_families(rows, covered, skipped, keep=prefix)

    # A filter over the family name, because that is what the reader was
    # doing with grep — over a table whose cells had been cut to fit a
    # terminal, so the grep missed what the cut had taken. On a real hunt the
    # subagent set COLUMNS=300 by hand, which never was the reason.
    pattern = None
    if args.grep:
        try:
            pattern = re.compile(args.grep, re.IGNORECASE)
        except re.error as e:
            print(f"error: --grep {args.grep!r} is not a regular expression: {e}",
                  file=sys.stderr)
            return 2
    # Cells are cut to fit a terminal, and only a terminal: a pipe or an
    # agent gets the whole name, and so does `--wide` on a screen.
    wide = args.wide or args.json or not sys.stdout.isatty()
    sections, missed = _name_sections(families, args.min_ms * 1e6, pattern)
    if args.json:
        _names_json(args, procs[0], async_rows, sections, missed)
    else:
        _names_text(args, async_rows, pattern, len(families), sections, missed, wide)
    return 0


def _names_setup(args) -> tuple[dict, Any, str, str]:
    """The masks' overrides, the binary, the markers' prefix and the process
    `names` reads: from the config when one is named and there."""
    from .mark import DEFAULT_PREFIX

    overrides, tp_bin = {}, args.tp_binary
    process = args.process
    # The markers this project plants. Their numbers were chosen to tell two
    # things apart, so they are the one kind of name `family` must not fold —
    # see there.
    prefix = DEFAULT_PREFIX
    if args.config and Path(args.config).exists():
        try:
            cfg_names = Config.load(args.config, getattr(args, "local", None))
            overrides = cfg_names.detector_overrides
            tp_bin = _tp_binary(args, cfg_names)
            prefix = str(cfg_names.get("instrumentation.temp_prefix") or prefix)
            if process is None:
                process = _names_process(args.config, cfg_names)
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)
    return overrides, tp_bin, prefix, "*" if process is None else process


def _names_process(path: str, cfg: Config) -> str | None:
    """The config's process, said aloud, or None when it names none.

    Without --process the fattest process wins, and on a real device that is
    surfaceflinger, not the app. When the config is right there, its process
    is the obvious default.
    """
    try:
        process = cfg.process
    except ConfigError:
        return None
    print(f"[i] --process taken from {path}: {process}", file=sys.stderr)
    return process


def _name_sections(families: dict, floor_ns: float, pattern) -> tuple[list, list]:
    """The families in BUCKETS' sections, then "Everything else"; and the
    ones a bucket holds that no mask sees, the biggest first."""
    assigned: set[str] = set()
    sections: list[tuple[str, list[tuple[str, dict]]]] = []
    missed: list[tuple[str, str, dict]] = []
    for title, keywords in BUCKETS:
        picked = _bucket(families, keywords, assigned, floor_ns, pattern)
        if not picked:
            continue
        picked.sort(key=lambda x: -x[1]["ns"])
        sections.append((title, picked))
        # Something excluded on purpose (skip_glob) is not a miss.
        missed += [(title, f, d) for f, d in picked if not d["dets"] and not d["skips"]]

    rest = [(f, d) for f, d in families.items()
            if f not in assigned and d["ns"] >= floor_ns
            and (pattern is None or pattern.search(f))]
    if rest:
        rest.sort(key=lambda x: -x[1]["ns"])
        sections.append(("Everything else", rest))
    missed.sort(key=lambda x: -x[2]["ns"])
    return sections, missed


def _bucket(families: dict, keywords, assigned: set[str], floor_ns: float,
            pattern) -> list[tuple[str, dict]]:
    """The families one section takes, marked as taken, and of them the ones it shows."""
    picked = []
    for fam, data in families.items():
        # The section is decided by the slice NAME and nothing else.
        # Thread names used to go through the same sieve — and then
        # `merge`, `wait` and `releaseBuffer` drifted into "Binder /
        # IPC" merely because they ran on `binder:*` threads, while
        # `Thread::Init` landed under garbage collection because one of
        # its threads happened to be HeapTaskDaemon. A thread says
        # WHERE code ran, not what it did.
        if fam in assigned or not any(k in fam.lower() for k in keywords):
            continue
        assigned.add(fam)
        if data["ns"] >= floor_ns and (pattern is None or pattern.search(fam)):
            picked.append((fam, data))
    return picked


def _names_json(args, proc: dict, async_rows: list, sections: list, missed: list) -> None:
    def family_json(fam: str, d: dict) -> dict:
        return {"family": fam, "n": d["n"], "total_ms": round(d["ns"] / 1e6, 1),
                "threads": sorted(d["threads"]), "detectors": sorted(d["dets"]),
                "excluded": sorted(d["skips"])}
    print(json.dumps({
        "process": proc["name"], "pid": proc["pid"],
        "async_sections": sum(r["n"] for r in async_rows),
        "grep": args.grep,
        "sections": [{"title": title, "shown": len(items[:args.top]), "total": len(items),
                      "families": [family_json(f, d) for f, d in items[:args.top]]}
                     for title, items in sections],
        "missed": [{"section": title, **family_json(f, d)}
                   for title, f, d in missed[:args.top]],
    }, ensure_ascii=False, indent=2))


def _names_text(args, async_rows: list, pattern, families: int, sections: list,
                missed: list, wide: bool) -> None:
    if async_rows:
        print(f"{sum(r['n'] for r in async_rows)} async section(s) on "
              f"{len(async_rows)} name(s), shown as thread `{ASYNC_THREAD}`: "
              f"an anchor may name them and this inventory lists them; "
              f"the detectors read thread slices and never see them.\n")
    print(
        "The 'mask' column covers only detectors that search by slice "
        "NAME. `main_thread_block`, `runnable_starvation` and "
        "`uninstrumented_cpu` are structural, names mean nothing to them, "
        "and a dash here does not mean nobody will find the slice."
    )
    if pattern is not None:
        shown = sum(len(items) for _, items in sections)
        print(f"\n_Only families matching `{args.grep}`: {shown} of {families}._")
    for title, items in sections:
        print(f"\n## {title}\n")
        _families_table(items[:args.top], wide=wide)
        _note_dropped(len(items), args.top)

    print("\n## Missed by the masks\n")
    if not missed:
        print("_Everything resembling GC, locks or binder is covered._"
              if pattern is None else "_Nothing matching is missed by a mask._")
        return
    print("These families sit in sections the detectors are "
          "responsible for, yet no mask sees them. If there is a real "
          "problem among them, widen the mask in `echolot.yml`.\n")
    table.show([
        {"section": title, "family": fam if wide else _clip(fam), "N": d["n"],
         "total, ms": f"{d['ns']/1e6:.1f}"}
        for title, fam, d in missed[:args.top]
    ])
    _note_dropped(len(missed), args.top)


def group_families(rows, covered, skipped, keep: str | None = None) -> dict:
    """Slice-name rows into families, with the planted markers left alone.

    Pure, so the one rule that matters here can be pinned without a trace:
    a name the agent wrote keeps its digits, and everything else folds.
    """
    families: dict[str, dict] = {}
    for r in rows:
        fam = families.setdefault(report_mod.family(r["name"], keep=keep), {
            "n": 0, "ns": 0, "threads": set(), "dets": set(), "skips": set(),
        })
        fam["n"] += r["n"]
        fam["ns"] += r["total_ns"] or 0
        fam["threads"].add(r["thread"])
        fam["dets"].update(covered.get(r["name"], set()))
        fam["skips"].update(skipped.get(r["name"], set()))
    return families


def _note_dropped(total: int, shown: int) -> None:
    """A truncated list is announced out loud.

    A silently cut table reads as "this is all there is", and the agent
    considers the section closed.
    """
    if total > shown:
        print(f"\n_Showing {shown} of {total}; the rest are shorter. "
              f"Full list: `--top {total}`._")


def _families_table(items, wide: bool = False) -> None:
    """`wide` keeps every cell whole — for a pipe, an agent, or on request."""
    rows = []
    for fam, data in items:
        threads = sorted(data["threads"])
        shown = ", ".join(threads if wide else threads[:2]) \
            + (f" +{len(threads)-2}" if len(threads) > 2 and not wide else "")
        marks = sorted(data["dets"])
        marks += [f"{d} (excluded)" for d in sorted(data["skips"])]
        rows.append({
            "family": fam if wide else _clip(fam),
            "N": data["n"],
            "total, ms": f"{data['ns']/1e6:.1f}",
            "threads": shown if wide else _clip(shown, 34),
            "mask": ", ".join(marks) or "—",
        })
    table.show(rows)


def _clip(text: str, width: int = 58) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def _hunt_config(project: Path, config: str
                 ) -> tuple[str | None, str | None, dict[str, Any]]:
    """Scenario name, config hash and the confirmed values — what an
    investigation is opened against.

    An investigation records what it was opened against so that `drift` can
    later say "the scenario changed" instead of the human having to remember,
    and `analyze` can say which value a person confirmed has changed since.

    No config at all is `(None, None)`: nothing names a scenario yet. A config
    that is there and does not load raises, for `cmd_hunt` to refuse on. It
    used to come back as `(None, None)` too, in silence — and since
    `Config.load` checks `detectors:`, one malformed entry under it is enough
    to get there.
    """
    path = project / config
    if not path.exists():
        return None, None, {}
    cfg = Config.load(path)
    return cfg.scenario_name, cfg.sha, cfg.confirmed()


def cmd_hunt(args) -> int:
    """The investigation: which question the traces on disk are about.

    One noun, one home. This used to be four hidden flags on `status`, which
    made a reporting command mutate state and gave the concept no name a
    person could find. `status` reports; `hunt` is the investigation.

    The word means the same here and in Claude Code. `echolot hunt "<q>"` does
    the half a shell can do — opens the investigation, moves the previous set
    of traces aside, says what the last one left behind — and names the half
    it cannot: the loop needs an agent. `/echolot hunt <q>` does both.
    """
    project = Path.cwd()
    question = " ".join(args.question) if args.question else None
    conclusion = args.done
    config = getattr(args, "config", "echolot.yml")

    if args.list:
        for line in hunt_mod.list_rows(project):
            print(line)
        return 0

    if args.show:
        h = hunt_mod.find(project, args.show)
        if not h:
            print(f"no investigation matches {args.show!r} — `echolot hunt --list` "
                  f"shows them all", file=sys.stderr)
            return 1
        for line in hunt_mod.detail(h, project):
            print(line)
        return 0

    if question:
        # A config that does not load is refused rather than opened around.
        # The scenario it names is what picks the previous set out of
        # .echolot/traces. Without it the investigation opened with no
        # scenario for `drift` to compare, the old traces stayed where this
        # question's would land, and the one open before it was closed as
        # abandoned — all for a question whose first step, `collect` or
        # `analyze`, stops on the same config. The Claude path opens here too
        # (echolot-hunt.md), and an agent goes on after a command that
        # succeeded: exit 2 stops it where the fix is, the way `fix-config`
        # stops `/echolot` at the door. Refused before anything is touched,
        # so asking again once the config loads loses nothing.
        try:
            scenario, sha, confirmed = _hunt_config(project, config)
        except (ConfigError, OSError) as e:
            print(f"error: {config} does not load: {e}", file=sys.stderr)
            print("Nothing was opened and no traces were moved aside. Fix the "
                  "config, then ask again.", file=sys.stderr)
            recorder.failed(f"{config} does not load: {e}")
            return 2
        # The whole point of the feature: a new investigation must not start
        # on the previous one's traces. Nothing is deleted — the set moves
        # aside exactly the way `collect` moves it between rounds.
        aside = None
        if scenario:
            from . import runner
            aside = runner.set_aside(project / ".echolot" / "traces", scenario,
                                     log=lambda m: print(m, file=sys.stderr))
            if aside is not None:
                # Relative to the project, so the record survives the tree
                # being moved or cloned somewhere else.
                with contextlib.suppress(ValueError):
                    aside = aside.resolve().relative_to(project.resolve())
        h = hunt_mod.open_new(project, question,
                              since=getattr(args, "hunt_since", None),
                              scenario=scenario, config_sha=sha,
                              traces_aside=aside, confirmed=confirmed)
        print(f'opened #{h["n"]}: "{h["question"]}"')
        if h.get("since"):
            print(f'  after: {h["since"]}')
        # Instrumentation the previous investigation never took out would
        # otherwise become this one's starting conditions.
        left = hunt_mod.leftovers(project)
        if left["markers"]:
            print(f'\n[!] {left["markers"]} {left["prefix"]} marker(s) left in '
                  f'{len(left["files"])} file(s) by the previous investigation.',
                  file=sys.stderr)
            by_hand = left["markers"] - left["removable"]
            how = f'`echolot mark --remove` takes out {left["removable"]}'
            if by_hand:
                how += f', the other {by_hand} were added by hand and go by hand'
            print(f'    {how}.', file=sys.stderr)
        recorder.note(hunt="opened", scenario=scenario,
                      leftover_markers=left["markers"])
        # The half a shell cannot do. Said every time rather than only when
        # something looks wrong: this is the command a person reaches for
        # first, and it is where the two surfaces have to line up out loud.
        # Through the door this project chose: a project that declined Claude
        # Code was being sent to a command it does not have.
        from . import hosts as hosts_mod
        door = ("`/echolot` in Claude Code, or any agent after `echolot guide hunt`"
                if hosts_mod.wants_claude(project)
                else "any agent, after `echolot guide hunt`")
        print(f"\nNext, the loop, which needs an agent: {door}.", file=sys.stderr)
        print("By hand: echolot collect -c echolot.yml -n 5, then "
              + state.analyze_line(state.repeats(scenario, 5) if scenario else None),
              file=sys.stderr)
        return 0

    if conclusion:
        h = hunt_mod.conclude(project, conclusion)
        if not h:
            print("no investigation is open", file=sys.stderr)
            return 1
        print(f'concluded: "{h["question"]}"')
        recorder.note(hunt="concluded")
        return 0

    if args.resume:
        if not hunt_mod.load(project):
            print("no investigation is open", file=sys.stderr)
            return 1
        hunt_mod.touch(project)
        recorder.note(hunt="resumed")

    # Bare `echolot hunt`, and the tail of --resume: what is open, in full.
    st = state.project_state(project, config)
    if not st.get("hunt"):
        print('no investigation is open — `echolot hunt "<what regressed>"` '
              'opens one')
        return 0
    for line in hunt_mod.recap(st["hunt"], st, root=project):
        print(line)
    return 0


def cmd_status(args) -> int:
    """`echolot` with nothing after it: where things stand, and the next step.

    The tool can tell a first visit from a return: is the layer here and
    current, is there a config, are there traces, when did doctor last pass.
    That fork used to live in the README as prose; now the tool prints the
    branch that applies. Two commands are all a person needs to know —
    `echolot init` and `echolot` — and the agent knows the rest.
    """
    project = Path.cwd()
    st = state.project_state(project, getattr(args, "config", "echolot.yml"))
    if getattr(args, "next", False):
        # One word for the skill to switch on; the prose is for people.
        print(state.next_kind(st))
        return 0
    info = toolchain_info(getattr(args, "tp_binary", None))
    print(f"echolot {recorder.version()} · trace_processor "
          f"{info.get('trace_processor') or 'unknown'} · {Path.cwd()}")

    lines: list[tuple[str, str]] = []
    lines.append(("layer", st["layer_line"].split(": ", 1)[1]))
    if st.get("codex_line"):
        lines.append(("codex", st["codex_line"]))
    cfg = st["config"]
    if cfg is None:
        lines.append(("config", "none — no echolot.yml here"))
    elif cfg.get("error"):
        lines.append(("config", f"echolot.yml does not load: {cfg['error']}"))
    else:
        bits = [f"scenario {cfg['scenario']}", f"thresholds {cfg['thresholds']}"]
        if cfg.get("runner"):
            bits.append(f"runner {cfg['runner']}")
        if cfg.get("local"):
            bits.append("local.yml applied")
        lines.append(("config", "echolot.yml · " + " · ".join(bits)))
    lines.append(("hunt", hunt_mod.summary_line(st.get("hunt"), st)))
    collecting = state.collect_line(st)
    if collecting:
        lines.append(("collect", collecting))
    tr = st["traces"]
    if tr["count"]:
        lines.append(("traces", f"{tr['count']} in .echolot/traces, newest {when.ago(tr['newest'])}"))
    else:
        lines.append(("traces", "none in .echolot/traces"))
    rep = st["report"]
    if rep and not rep.get("error"):
        made = when.ago(when.iso_epoch(rep.get("generated_at")))
        what = (f"{rep['fired']} of {rep['run']} detectors fired"
                if rep.get("run") is not None else "")
        note = ""
        if rep.get("defaults"):
            note = " · made with --defaults"
        elif cfg and cfg.get("sha") and rep.get("config_sha") and rep["config_sha"] != cfg["sha"]:
            note = " · made with an older config"
        lines.append(("report", f".echolot/out/report.json, {made} · {rep['runs']} run(s) · {what}{note}"))
    else:
        lines.append(("report", "none yet"))
    d = st["last_doctor"]
    if d:
        facts = d.get("facts") or {}
        failed = facts.get("failed") or []
        ago = when.ago(when.iso_epoch(d.get("ts")))
        if facts.get("checks") == 0 and facts.get("sandbox"):
            # The why is known, and it is not the machine. Said here, the
            # door can pass it on without running into the same refusal
            # again to learn it.
            lines.append(("doctor", f"{ago}, the self-check did not run: "
                                    f"{state.whose_sandbox(facts['sandbox'])} "
                                    f"refused trace_processor a port on "
                                    f"localhost — echolot has to run outside "
                                    f"it, and `echolot doctor` says how"))
        elif facts.get("checks") == 0:
            # No check ran, so there is no count to give. A self-check that
            # could not start is logged with `checks: 0` and one entry in
            # `failed` (see `NOT_RUN`), and that entry was printed as "1
            # check(s) FAILED" — a tally of a run that never happened.
            lines.append(("doctor", f"{ago}, the self-check did not run — "
                                    f"run `echolot doctor` to see why"))
        else:
            lines.append(("doctor", f"{ago}, "
                          + (f"{len(failed)} check(s) FAILED" if failed else "passed")))
    else:
        lines.append(("doctor", "never run here"))
    width = max(len(k) for k, _ in lines)
    for k, v in lines:
        print(f"{k.ljust(width)}  {v}")
    print(f"{'next'.ljust(width)}  {state.next_step(st)}")

    # Everything needed to answer "carry on, or start new?" in one call, so
    # the agent asks the human without a second round trip.
    if state.next_kind(st) == "resume-or-new":
        print()
        for line in hunt_mod.recap(st.get("hunt"), st, root=project):
            print(line)
    return 0


def cmd_guide(args) -> int:
    """How to work with this tool, printed by the package that implements it.

    The `.claude/` layer is copied into a project and therefore drifts: the
    package moves on, the copy does not, and `init` has to be re-run. It is
    also invisible to every client that is not Claude Code, which is how a
    Cursor user ends up with a tool that "sometimes follows the instructions"
    — the model finds SKILL.md by chance while reading the repository, or it
    does not.

    Printed guidance has neither problem. It cannot be stale, and any client
    that can run a command can read it.
    """
    topic = (args.topic or "overview").lower()
    topics = layer.guide_topics()
    if topic not in topics:
        print(f"no guide for {topic!r}. There is: {', '.join(sorted(topics))}",
              file=sys.stderr)
        return 2
    print(layer.guide_text(topics[topic]))
    return 0


def cmd_anr(args) -> int:
    """A thread dump from the field, read the way the report reads a trace.

    Reconnaissance rather than an investigation, and that is the whole reason
    it is its own verb. It reads a file and prints; no hunt is opened, and
    the one thing written is its line in .echolot/log/runs.jsonl — the line
    `main` appends for every command unless ECHOLOT_NO_RECORD is set. That is
    what makes it composable — a folder of exports from the console goes
    through it in one loop, and out of ten reports the two worth chasing are
    the ones that name a lock chain. Opening ten investigations to learn that
    would be the wrong shape.
    """
    from . import anr as anr_mod

    path = Path(args.report)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    source = anr_mod.detect(text)
    if source is None:
        print(f"nothing in {path} announces a thread the way a source this "
              f"reader knows does. It reads a Crashlytics export, a Play "
              f"Console ANR cluster, and the ART dump that `dumpsys dropbox "
              f"--print data_app_anr` and the files under /data/anr/ carry.",
              file=sys.stderr)
        return 2
    report = anr_mod.parse(text, source)
    if not report.threads:
        print(f"{path} reads as {source.name} and yields no threads.",
              file=sys.stderr)
        return 2

    # The repository is optional on purpose. A report read anywhere still
    # answers what froze; pointed at a checkout it also says where to open.
    code = None
    root = Path(args.root).resolve()
    if root.is_dir():
        code = anr_mod.locate(report, root)

    found = anr_mod.chains(report)
    recorder.note(anr=source.name, threads=len(report.threads),
                  chains=len(found),
                  # Zero chains from a file with no lock notes is the file's
                  # limit, not the freeze's; the log says which it was.
                  lock_notes=report.lock_notes,
                  blocks_main=any(c.blocks_main for c in found),
                  placed=len(code[0]) if code else 0)

    print(anr_mod.to_json(report, code) if args.json
          else anr_mod.render(report, code))
    return 0


def cmd_domains(args) -> int:
    """The slice-to-code map plus instrumentation coverage.

    A slice name is a string literal that survives minification, so the map can
    be assembled mechanically. What is left for a human is fixing the wording
    rather than searching the repository — and blind repository scanning is the
    main context eater.
    """
    from . import domains as domains_mod

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    sites, stats = domains_mod.scan(root)
    for line in domains_mod.render(sites, stats, root, limit=args.top):
        print(line)
    return 0


def cmd_mark(args) -> int:
    """Where the first temporary markers go — and putting them there.

    Bound to the platform's vocabulary only (manifest, lifecycle, API calls,
    one call hop from setContent), so the answer is the same on any project
    and says "not found" where it cannot see. See mark.py.
    """
    from . import mark as mark_mod

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2
    package, allowed, prefix = None, [], mark_mod.DEFAULT_PREFIX
    if args.config and Path(args.config).exists():
        try:
            cfg = Config.load(args.config, getattr(args, "local", None))
            package = cfg.get("project.package") or cfg.get("project.process")
            allowed = list(cfg.get("instrumentation.allowed") or [])
            prefix = str(cfg.get("instrumentation.temp_prefix") or prefix)
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)

    if args.remove:
        touched, kept = mark_mod.remove(root)
        for rel, n in touched:
            print(f"  - {rel}: {n} line(s)")
        # A tagged line in any other shape than --apply's is left where it
        # is, and named: it carries more than a marker, which deleting it
        # would take along, and "nothing found" over a tree that still has
        # it reads as clean.
        for rel, line, text in kept:
            print(f"  ! {rel}:{line}: {text[:120]}")
        if touched:
            print(f"removed markers from {len(touched)} file(s)")
        elif not kept:
            print("no `echolot:mark` lines found under this root")
        if kept:
            print(f"left {len(kept)} line(s) with `{mark_mod.TAG}` on them that are not "
                  f"in the shape --apply writes — each carries something besides the "
                  f"marker, so take the marker out by hand")
        recorder.note(removed_files=len(touched), kept_tagged=len(kept))
        return 0

    if getattr(args, "pools", False):
        # The third way in, and the only one that starts from the report
        # rather than from the code: a thread the JDK named, which nothing in
        # the repository is called. See `mark.plan_pools`.
        pl = mark_mod.plan_pools(root, allowed=allowed)
    elif args.from_anr:
        # The targets come from a freeze that happened rather than from where
        # instrumentation usually belongs. Everything after this — rendering,
        # --apply, --remove — is the same code and the same tag.
        from . import anr as anr_mod

        source = Path(args.from_anr)
        if not source.is_file():
            print(f"no such file: {source}", file=sys.stderr)
            return 2
        text = source.read_text(encoding="utf-8", errors="replace")
        if anr_mod.detect(text) is None:
            print(f"{source} is not a report this reader knows", file=sys.stderr)
            return 2
        report = anr_mod.parse(text)
        placed, missing = anr_mod.locate(report, root)
        # A frame placed in one of several files of its name is a guess, and
        # a marker in the wrong one measures a file the build never ran —
        # `src/debug` beside `src/release`. Those are named, not marked.
        pl = mark_mod.plan_from_anr(
            root, [(f.symbol, f.file, f.line) for f in placed if f.exact],
            prefix=prefix, allowed=allowed, unplaced=len(missing),
            version=report.head.get("Version") or report.head.get("Package"))
        for f in placed:
            if not f.exact:
                pl.notes.append(
                    f"{f.symbol} — {len(f.others) + 1} files of that name could "
                    f"be it: {', '.join([f.file, *f.others])}. Nothing in the "
                    f"frame says which one was built, so it is left to mark by hand")
    else:
        pl = mark_mod.plan(root, package=package, allowed=allowed, prefix=prefix,
                           module=args.module)
    if args.json:
        print(json.dumps(pl.to_dict(), ensure_ascii=False, indent=2))
    else:
        for line in mark_mod.render(pl):
            print(line)
    recorder.note(proposals=len(pl.proposals),
                  applicable=sum(1 for p in pl.proposals if p.applicable),
                  ambiguity=len(pl.ambiguity))
    if pl.ambiguity:
        return 2

    if args.apply:
        done, unreadable = mark_mod.apply(root, pl)
        print()
        for rel, markers in done:
            print(f"  + {rel}: {', '.join(markers)}")
        for rel in unreadable:
            print(f"  ! {rel}: not valid UTF-8 — skipped. Marking it "
                  f"mechanically would put the lines at the wrong offsets.",
                  file=sys.stderr)
        print(f"applied {sum(len(m) for _, m in done)} marker(s) in {len(done)} file(s); "
              f"every inserted line ends with `{mark_mod.TAG}` — `echolot mark --remove` "
              f"takes them out" if done else "nothing applicable to apply")
        recorder.note(applied=sum(len(m) for _, m in done),
                      unreadable=len(unreadable))
    return 0


def cmd_collect(args) -> int:
    """N repeats of one scenario.

    Repeating is not belt-and-braces. A single run cannot tell a regression
    from a random spike, and both threshold calibration and report aggregation
    stand on the distribution across repeats. Below three there is hardly any
    point.
    """
    from . import runner

    # The runner section and the process are read here, inside the same
    # handler as the load: `runner: gradle` and a config naming no package
    # both used to pass the load and end in a traceback on the next line.
    try:
        cfg = Config.load(args.config, args.local)
        section = cfg.runner
        package = cfg.get("project.package") or cfg.process
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        recorder.failed(f"config error: {e}")
        return 2

    mode = str(section.get("mode", "launch"))
    iterations = (args.iterations if args.iterations is not None
                  else section.get("iterations", 5))
    out_dir = _out_dir(args.out, cfg)
    project = _project_root(cfg)
    # project_root follows the config, as -o and the investigation already
    # do: a relative path is taken from the directory echolot.yml is in. It
    # used to be taken from wherever `echolot` was started, so the same
    # config ran gradle in two different places depending on the shell.
    root = Path(str(section.get("project_root") or ".")).expanduser()
    section = {**section, "project_root": str(root if root.is_absolute()
                                              else project / root)}
    device = args.device or section.get("device")
    # Where the run stands, for whoever asks while it runs — `status` reads
    # it. Written before the first iteration and after the last, with why
    # when it ended badly.
    progress = runner.Progress(project / runner.PROGRESS_FILE)

    _note_local(cfg)
    if mode == "gradle" and (args.iterations is not None or "iterations" in section):
        # The count lives in the benchmark's own measureRepeated, and every
        # trace it writes is gathered. Said, because `-n 1` is the advice for
        # a first run in the other two modes, and here it would buy a whole
        # round while looking like one iteration.
        print(f"[!] {'-n' if args.iterations is not None else 'runner.iterations'} "
              f"does not reach the macrobenchmark: it runs as many iterations "
              f"as its own measureRepeated asks for, and every trace it writes "
              f"is collected.", file=sys.stderr)
    try:
        results = runner.collect(
            package=str(package),
            out_dir=out_dir,
            iterations=iterations,
            section=section,
            device=str(device) if device else None,
            name=cfg.scenario_name,
            log=lambda m: print(m, file=sys.stderr),
            # The set pushed aside is the previous round of this same
            # investigation — its baseline, and what the next report is
            # compared against.
            on_set_aside=lambda d: hunt_mod.record_traces(project, d),
            progress=progress.update,
        )
    except runner.RunnerError as e:
        print(f"collection error: {e}", file=sys.stderr)
        # The log is what `reflect` reads after the session, the file is
        # what `status` reads during it, and both lead with the gist: the
        # line that names the cause, and the hint when there is one. The
        # first line alone was "the scenario command returned 1:" after
        # every gradle failure, and the log's 800 characters ran out in the
        # middle of gradle's output, before the hint.
        said = str(e).strip()
        recorder.failed(f"collection error: {e.gist}"
                        + ("" if said == e.gist else f"\n{said}"))
        progress.update(finished=time.time(), exit=2, error=e.gist)
        return 2
    progress.update(finished=time.time(), exit=0, traces=len(results))

    # `TotalTime: 0` is the system saying it timed no start — the activity
    # was already in front, and nothing was launched. It used to go into the
    # spread below as a divisor, and the ZeroDivisionError came after the
    # traces were pulled and before their paths were printed.
    timed = [r.get("total_time_ms") for r in results
             if isinstance(r.get("total_time_ms"), int)]
    times = [t for t in timed if t > 0]
    if len(times) < len(timed):
        print(f"\nam start -W: {len(timed) - len(times)} of {len(timed)} "
              f"launches report TotalTime: 0 — the system timed no start "
              f"there, usually because the activity was already in front.",
              file=sys.stderr)
    if len(times) > 1:
        spread = (max(times) - min(times)) / min(times) * 100
        print(f"\nam start -W: from {min(times)} to {max(times)} ms "
              f"(spread {spread:.0f}%)", file=sys.stderr)
        if spread > 30:
            print("That spread is wide — the device is under load or has not "
                  "settled. Thresholds from such runs will be noisy.",
                  file=sys.stderr)

    for r in results:
        print(r["path"])
    hunt_mod.touch(project, collect=True)
    return 0


def _private_repo(args, target: Path):
    """(private, repository) for this run of `init`.

    `--private` or `--shared` when given, else what this clone chose last
    time. Outside a git repository there is nothing to keep from git: said,
    and the install is the usual one. `--shared` takes echolot's block out of
    the exclude file and leaves the files where they are; from then on they
    are the team's to commit.
    """
    from . import exclude as exclude_mod
    from . import hosts as hosts_mod

    asked = getattr(args, "private", None)
    if asked is False:
        repo = exclude_mod.find(target)
        if repo is not None and exclude_mod.remove(repo):
            print(f"\n  ↓ {repo.show()}: echolot's block taken out — what a private "
                  f"install wrote is still here, and git sees it now")
        return False, None
    if not (asked or hosts_mod.load_private(target)):
        return False, None
    repo = exclude_mod.find(target)
    if repo is None:
        print("\n  · --private: this directory is not inside a git repository, so "
              "there is nothing to keep from\n    git — installing as usual")
        return False, None
    return True, repo


def _private_targets(target: Path, chosen: list) -> list[Path]:
    """Every file a private `init` may write, to ask git which of them it tracks."""
    root = target / ".claude"
    files = [root / layer.merged_into(str(src.relative_to(layer.CLAUDE_DIR)), True)
             for src in layer.template_files()]
    files.append(root / layer.LAYER_MANIFEST)
    files += [target / h.path for h in chosen if h.path and h.key != "claude"]
    return files


def _keep_from_git(repo, patterns: list[str]) -> None:
    from . import exclude as exclude_mod
    said = exclude_mod.write(repo, patterns)
    print(f"\n  {said}")
    if not said.startswith("!"):
        print("  A private install: git sees none of it in this clone. "
              "`echolot init --shared`\n  hands it to the team.")


def cmd_init(args) -> int:
    """Installs the .claude/ layer into a project.

    The template ships with the package rather than living in the application
    repository: knowledge of how to use the tool belongs to the tool. What ends
    up in the project is a copy you can edit and commit — for your modules,
    your paths, your style.

    Idempotent, and the one command a person has to know. First time: the
    layer goes in. Any later time: files untouched since install are brought
    up to date, files the project edited are left alone unless `--all`,
    and the environment is checked (`doctor -q`). It ends with the next step,
    the same line `echolot` with no arguments prints.

    It also puts `.echolot/` and `local.yml` into the project's .gitignore at
    a git root, and says which two lines to add anywhere else — see
    `ignore.py` for why that is init's job and not the reader's.

    Which agents it points at: `--for` when given, else the choice this
    project saved in `.echolot/hosts.json`, else what the tree shows evidence
    of — and on a terminal, a picker that starts from that.

    One file is not a copy of ours: `.claude/settings.json` is the project's,
    and echolot only adds its permission to it. See `layer.MERGED`.

    One layer it does not touch at all: one a newer echolot wrote. It says
    so, names the upgrade, and exits 1 having written none of the above —
    see `layer.ahead` for what it used to do instead.

    `--private` installs the same files for this clone alone: every path it
    writes goes into the repository's own ignore file, and no file git
    tracks is written. See `exclude.py`.
    """
    target = Path(args.into)
    if not target.is_dir():
        print(f"no such directory: {target}", file=sys.stderr)
        return 2

    from . import hosts as hosts_mod

    spec = getattr(args, "for_hosts", None)
    chosen = hosts_mod.parse(spec) if spec else None
    if spec and chosen is None:
        print(f"unknown client in --for {spec!r}. There is: "
              f"{', '.join(h.key for h in hosts_mod.HOSTS)}, or `all`",
              file=sys.stderr)
        return 2

    # Before the first write, and whatever the flags say: `--all` is about
    # files edited here, not about undoing a teammate's upgrade. None of
    # what `init` writes is written — not the layer, not the .gitignore
    # lines, not the pointers for other agents, not the saved choice of
    # agents — because the release that wrote the layer may write every one
    # of them differently, and this one cannot know how. The line that says
    # why is the same one `status` and `doctor` print.
    if layer.ahead(target):
        verdict, line = layer.one_line(target)
        said = ("init: refused — .claude/, .gitignore and the pointers for "
                "other agents are left as they are.\n" + line)
        print(said, file=sys.stderr)
        recorder.note(layer=verdict)
        recorder.failed(said)
        return 1

    if chosen is None:
        # What the project chose last time, when it chose: declining Claude
        # Code is a state, and detecting afresh on every run put it back.
        chosen = hosts_mod.starting_set(target)
        # Two gates, both required. The parser is the only thing that turns
        # `interactive` on, so the self-check's bare Namespace can never
        # prompt; and even then there has to be a terminal on both ends.
        if getattr(args, "interactive", False) and hosts_mod.interactive(sys.stdout):
            chosen = hosts_mod.pick(
                chosen, found={h.key for h in hosts_mod.detect(target)})
    from . import exclude as exclude_mod
    private, repo = _private_repo(args, target)
    hosts_mod.save_choice(target, chosen, private=private)

    # Before the layer, and whatever client was chosen: the traces and the
    # machine-local config are echolot's own leavings, and a repository is
    # where they must not end up. A private install keeps them in its block,
    # with echolot.yml, which setup writes later: a pattern works before its
    # file exists.
    hidden: list[str] = []
    tracked: set[Path] = set()
    if private:
        hidden = [repo.pattern(target / ".echolot", directory=True),
                  repo.pattern(target / "local.yml"), repo.pattern(target / "echolot.yml")]
        tracked = exclude_mod.tracked(repo, _private_targets(target, chosen))
    else:
        from . import ignore as ignore_mod
        ignored = ignore_mod.ensure(target)
        if ignored:
            print(f"\n  {ignored}")

    if not any(h.key == "claude" for h in chosen):
        if layer.audit(target) is not None:
            # An earlier init put it in. It is left exactly as it is: the
            # files may be what teammates without the plugin work from, and
            # `echolot` says what to do about them.
            print("\n" + layer.one_line(target)[1])
        elif any(h.key == "plugin" for h in chosen):
            print("\nThe skills come with the echolot plugin — .claude/ stays "
                  "out of this project.")
        else:
            print("\nClaude Code not selected — .claude/ stays out of this project.")
        whole = layer.install_pointers(target, chosen, force=getattr(args, "force", False),
                                       tracked=tracked)
        if private:
            _keep_from_git(repo, hidden + [repo.pattern(f) for f in whole])
        print("\nAny agent: `echolot guide`. The choice is kept — a plain "
              "`echolot init` points at\nthe same agents again; `--for` "
              "changes it, and `echolot init --for all` adds the rest.")
        recorder.note(hosts=[h.key for h in chosen], layer="skipped")
        return 0

    root = target / ".claude"
    before = layer.audit(target)
    states = {r["file"]: r["state"] for r in (before or {}).get("rows", [])}

    written, updated, same, kept, overwritten = [], [], [], [], []
    folded, unmergeable, left = [], [], []
    installed: dict[str, str] = {}
    for src in layer.template_files():
        rel = str(src.relative_to(layer.CLAUDE_DIR))
        # The file it lands in: settings.json is merged into
        # settings.local.json when private. Named that way in what is printed.
        dst = root / layer.merged_into(rel, private)
        shown = dst.relative_to(root).as_posix()
        was = states.get(rel)
        if dst in tracked:
            left.append((shown, layer.contribution(src) if rel in layer.MERGED else None))
            continue
        if dst.exists():
            if rel in layer.MERGED:
                # settings.json belongs to the project — its hooks, its
                # plugins, its own permissions. echolot adds a line to it and
                # copies nothing over it, `--all` included: the flag says
                # "overwrite the copies of my files", not "throw away yours".
                verdict, text = layer.merge(src, dst)
                if verdict == "current":
                    same.append(shown)
                elif verdict == "unreadable":
                    unmergeable.append((shown, layer.contribution(src)))
                else:
                    dst.write_text(text, encoding="utf-8")
                    folded.append(shown)
                continue
            if was == "current":
                same.append(rel)
                installed[rel] = layer.sha(src)
                continue
            # Untouched since install and the template moved on: ours to
            # update, no flag needed. Anything the project may have edited
            # (customised, conflict, or differs with no manifest to tell)
            # waits for --all.
            if was == "stale":
                updated.append(rel)
            elif not args.force:
                kept.append(rel)
                continue
            else:
                overwritten.append(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        if rel not in updated:
            written.append(shown)
        installed[rel] = layer.sha(src)

    for rel in written:
        flag = "!" if rel in overwritten else "+"
        print(f"  {flag} .claude/{rel}" + (
            "  (was edited in the project — overwritten, carry the edits over)"
            if rel in overwritten else ""))
    for rel in updated:
        print(f"  ↑ .claude/{rel} (updated)")
    for rel in folded:
        print(f"  ↑ .claude/{rel} (echolot's permission added; the rest of "
              f"the file is the project's and was kept)")
    for rel, wanted in unmergeable:
        print(f"  ! .claude/{rel} is not valid JSON — left alone rather than "
              f"replaced.\n      Merge this into it by hand: {wanted}")
    if same and (written or updated or kept):
        for rel in same:
            print(f"  = .claude/{rel} (current)")
    elif same:
        print(f"  = {len(same)} files current")
    for rel in kept:
        print(f"  ≠ .claude/{rel} (already there and {states.get(rel, 'differs')}, "
              f"untouched)")
    for shown, wanted in left:
        print(f"  ≠ .claude/{shown} (tracked by git — a private install leaves it "
              f"alone)" + (f"\n      Merge this into it by hand: {wanted}" if wanted else ""))

    manifest = root / layer.LAYER_MANIFEST
    if (written or updated or same or folded) and manifest not in tracked:
        # Only what was verified against the template goes into the manifest;
        # a file left untouched keeps whatever the old manifest said about it.
        layer.write_manifest(root, installed)

    # Only the kept files that may be behind the package are worth `--all`:
    # edited here and changed there since (conflict), or with nothing to say
    # which (differs). A customised one is behind nothing — the edit is the
    # whole difference — and pointing `--all` at it offered to throw the edit
    # away for no gain.
    behind = [rel for rel in kept if states.get(rel, "differs") in layer.BY_ALL]
    if behind:
        # `--all` does not choose, so the customised ones are named as well:
        # they go with the rest, which is part of what the person is asked.
        import textwrap
        also = len(kept) - len(behind)
        print("\n" + textwrap.fill(
            f"{len(behind)} file(s) above differ from the package's, and the "
            f"difference may be an edit made here; they were kept. `echolot "
            f"init --all` overwrites them"
            + (f", and the {also} customised one(s) with them" if also else "")
            + " — ask whoever edited them first, and carry the edits over from "
              "git after.", width=80, break_on_hyphens=False,
            break_long_words=False))

    whole = layer.install_pointers(target, chosen, force=getattr(args, "force", False),
                                   tracked=tracked)
    if private:
        # Every file of the layer that is here and not the team's, the
        # manifest, and what the pointers wrote whole.
        ours = [root / layer.merged_into(str(src.relative_to(layer.CLAUDE_DIR)), True)
                for src in layer.template_files()] + [manifest]
        _keep_from_git(repo, [repo.pattern(f) for f in ours
                              if f.exists() and f not in tracked]
                       + [repo.pattern(f) for f in whole] + hidden)
    recorder.note(hosts=[h.key for h in chosen], private=private)
    if before is None:
        print("\nLayer installed.")
    elif written or updated or folded:
        print("\nLayer updated.")
    elif kept or unmergeable:
        apart = [f"the {len(kept)} kept above"] if kept else []
        apart += [f".claude/{rel}, which needs a hand" for rel, _ in unmergeable]
        print(f"\nLayer current, apart from {' and '.join(apart)}.")
    else:
        print("\nLayer is current.")
    recorder.note(written=len(written), updated=len(updated), kept=len(kept),
                  overwritten=len(overwritten), merged=len(folded),
                  unmergeable=[rel for rel, _ in unmergeable])

    # The environment, briefly, and where to go from here. The doctor lines
    # are the same three `doctor -q` prints; a failure is said and the exit
    # code carries it, but the layer is installed regardless — a broken
    # trace_processor is not a reason to leave the project without the skill.
    #
    # The trace_processor checked is the one `analyze` would run in the
    # project just installed into, chosen the way `doctor` chooses it: the
    # flag, then `toolchain.tp_binary` from the echolot.yml there and the
    # local.yml beside it, then the pin. This check used to take the flag or
    # the pin and nothing else, so a project whose local.yml named its own
    # binary was vouched for on one its reports never touched. The files are
    # the project's, not the working directory's: `--into` can name a
    # project somewhere else.
    if not getattr(args, "no_doctor", False):
        print()
        cfg = _doctor_config(argparse.Namespace(
            config=str(target / "echolot.yml"), local=None,
            tp_binary=getattr(args, "tp_binary", None)))
        tp_binary, source = _tp_binary_source(args, cfg)
        code, ran = _doctor_quiet(args, toolchain_info(tp_binary, source),
                                  project=target, origin=_binary_origin(source, cfg))
    else:
        code, ran = 0, False
    if code:
        # In the words `echolot` uses for a doctor that did the same. A
        # self-check that never started — trace_processor not downloaded,
        # asserts switched off, a binary that is not there — checked
        # nothing: it did not run, and "failed" sent the reader looking above
        # for a check that never happened.
        what = "failed" if ran else "did not run"
        print(f"\nnext  echolot doctor — the self-check {what} (see above); until "
              f"it passes, no report from this environment can be trusted")
    else:
        print(f"\nnext  {state.next_step(state.project_state(target))}")
    return code


def cmd_calibrate(args) -> int:
    """Thresholds from a healthy run instead of numbers pulled from thin air.

    An absolute threshold is brittle: 16 ms on a flagship and on a budget phone
    are different things, and the config does not travel between devices. Here
    thresholds are derived from the distribution on known-healthy traces: the
    detectors run with their thresholds opened up, a statistic is taken per
    column, and a safety factor is applied.

    The run deliberately does NOT edit the config itself: it prints a ready
    section and a human looks at the numbers and decides. Thresholds define
    what counts as normal, and that decision is not handed to a script.
    """
    try:
        cfg = Config.load(args.config, args.local)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    tp_binary = _tp_binary(args, cfg)
    _note_local(cfg)
    detectors = [d for d in load_detectors(DETECTOR_DIR) if d.calibrations]
    if not detectors:
        print("no detector declared @calibrate", file=sys.stderr)
        return 2

    # Before the first trace is opened. `calibrate` is where people iterate on
    # the thresholds section, so it is where a value of the wrong kind is most
    # likely to be typed — and it does not go through `plan_detectors`. The
    # section's own shape is refused here too: `detectors:` written as a
    # list was read outside this handler and came out as a traceback.
    try:
        overrides = cfg.detector_overrides
        for d in detectors:
            d.check(overrides.get(d.id), "from the config")
    except (ConfigError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    pooled: dict[str, list[dict]] = {d.id: [] for d in detectors}
    windows: list[float] = []

    for trace in args.traces:
        try:
            session = TraceSession(trace, tp_binary)
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        with session as tp:
            try:
                procs = _resolve_process(tp, cfg.process)
            except ConfigError as e:
                print(f"{trace}: {e}", file=sys.stderr)
                return 2
            bounds = _setup_context(tp, cfg, procs[0]["upid"])
            windows.append((bounds["ts_end"] - bounds["ts_start"]) / 1e6)
            for d in detectors:
                try:
                    pooled[d.id] += tp.query(d.render_open(overrides.get(d.id)))
                except Exception as e:
                    print(f"[!] {d.id} on {trace}: {e}", file=sys.stderr)

    spread = ""
    if len(windows) > 1 and max(windows) > 2 * min(windows):
        spread = ("\n# WARNING: the windows diverged more than twofold. You "
                  "calibrate on\n# repeats of ONE scenario; mixing a cold start "
                  "with a minute of\n# scrolling yields thresholds for nothing.")

    print(f"# The detectors section, derived from {len(args.traces)} "
          f"known-healthy runs.")
    print("# Scenario window: " + ", ".join(f"{w:.0f} ms" for w in windows)
          + spread)
    print("#")
    print("# The numbers are a statistic over a healthy run plus a margin.")
    print("# This is not a finished config but a proposal: thresholds define")
    print("# what counts as normal, and that is not a script's decision.")
    print("detectors:")

    skipped = 0
    for d in detectors:
        rows = pooled[d.id]
        print(f"  {d.id}:")
        for c in d.calibrations:
            values = [r[c.column] for r in rows if r.get(c.column) is not None]
            need = max(args.min_sample, c.needs())
            if len(values) < need:
                # A statistic over a handful of values is not a statistic but a
                # random number wearing the look of a justified one. Staying
                # quiet is more honest.
                skipped += 1
                print(f"    # {c.param}: kept the default "
                      f"({d.params[c.param]}) — sample {len(values)}, "
                      f"needs at least {need}")
                continue
            raw = c.value(values)
            value = raw * c.factor
            value = int(round(value)) if c.column == "count" \
                else round(value, 1)
            # A degenerate tail: the sample is large enough, but the Nth value
            # is already near zero. Such a "threshold" means "report
            # everything" — that is, not a threshold. There is nowhere for a
            # number to come from when a healthy run barely feeds this detector.
            if value < 1:
                skipped += 1
                print(f"    # {c.param}: kept the default "
                      f"({d.params[c.param]}) — {c.expr}={raw:.2f}, the tail "
                      f"of the distribution is degenerate")
                continue
            print(f"    {c.param}: {value}"
                  f"    # {c.expr}={raw:.1f} × {c.factor}, "
                  f"sample {len(values)}")

    if skipped:
        print(f"\n# Thresholds left uncalibrated: {skipped}. Not a failure — on"
              f"\n# a healthy run these phenomena are simply rare. Either keep"
              f"\n# the defaults or add more traces and repeat.")
    return 0


# What `failed` holds for a self-check that never ran. Every reader of the
# fact counts it or tests it for truth — `echolot` says how many checks
# failed, `state.next_kind` sends the next step back to doctor, reflect
# notes it — and a self-check that could not start proves as little as one
# that failed, so it goes in the same shape. One entry, since it is the whole
# self-check that did not run rather than any check in it: `checks: 0`
# beside it says so, and `error` carries the sentence doctor printed.
NOT_RUN = "the self-check did not run"

# Every self-check is an `assert`, and `python -O` — or PYTHONOPTIMIZE in the
# environment, which is easy to have without knowing — compiles them out. A
# check made only of asserts then passes without looking at anything, and one
# with working code inside an assert fails for want of what that code would
# have done. A healthy machine read "2 of 143 FAIL". The fixture report with
# every detector's rows emptied failed 33 checks, and 23 under the flag: ten
# passed a report with nothing in it. Neither number is about the pipeline,
# so under that flag doctor gives no verdict at all.
ASSERTS_SKIPPED = ("self-check: refused — every check is an assert, and this "
                   "Python was told to skip asserts (-O or PYTHONOPTIMIZE), so "
                   "nothing would be checked.")


def _refused_without_asserts() -> bool:
    """True, having said why, when this Python skips `assert` statements.

    First thing in both doctors, before a line of the environment: the
    refusal is the whole answer, and one sentence cannot be pushed off the
    screen by a `| head`. `init` ends in `_doctor_quiet` and gets it from
    there. Recorded like any self-check that did not run — see `NOT_RUN`.
    """
    if not sys.flags.optimize:
        return False
    print(ASSERTS_SKIPPED)
    _record_not_run(ASSERTS_SKIPPED)
    return True


def _record_not_run(reason: str, info: dict | None = None,
                    error: BaseException | None = None) -> None:
    """A self-check that never ran goes into the run log as one that failed.

    It used to go in as nothing. doctor printed "could not run", exited 1 and
    noted no checks at all, and `echolot` then read that line as "doctor 0s
    ago, passed" — with `next` pointing past the one command that had just
    said no report could be trusted.

    When a sandbox is what stopped it, that goes in as a fact of its own,
    `sandbox`, with whose it was: `status` and `next` say it from the log,
    and a sentence is not something to match on.
    """
    recorder.note(checks=0, failed=[NOT_RUN])
    if info is not None:
        recorder.note(trace_processor=info.get("trace_processor"))
    if isinstance(error, SandboxError):
        recorder.note(sandbox=error.host or "unknown")
    recorder.failed(reason)


def _no_trace_processor(e: ToolchainError, info: dict) -> int:
    """Both doctors, when the trace_processor they check never arrived.

    Not a check that failed, and not "could not run" with a curl command
    line after it: the binary every check runs on could not be downloaded,
    and the error says why and what to do about it. The same sentence and
    the same exit 2 as any other command that opens a trace — `init` ends in
    this too. Recorded like any self-check that did not run, so `echolot`
    sends the next step back here.
    """
    sys.stdout.flush()
    print(f"error: {e}", file=sys.stderr)
    _record_not_run(str(e), info)
    return 2


def _doctor_config(args) -> Config | None:
    """The config `analyze` would read from here — for the binary it names.

    doctor used to read none. With `toolchain.tp_binary` in a local.yml,
    `analyze` ran on that binary while doctor self-checked the pinned one:
    a pass about a trace_processor the project's reports never touched.

    Only a config that is there is read, the rule `names` follows too:
    outside a project there is nothing to follow. One that is there and does
    not load is said, and the check goes on without it — on the flag, or on
    the pin. `analyze` stops on the same error, so this is where it is first
    heard, not where it has to be handled.
    """
    path = getattr(args, "config", None) or "echolot.yml"
    if not Path(path).exists():
        return None
    try:
        return Config.load(path, getattr(args, "local", None))
    except ConfigError as e:
        instead = ("the one --tp-binary names" if getattr(args, "tp_binary", None)
                   else "the pinned one")
        print(f"[!] the config does not load, so a trace_processor it names "
              f"cannot be followed — doctor checks {instead}: {e}", file=sys.stderr)
        return None


def _binary_origin(source: str | None, cfg: Config | None) -> str | None:
    """Who asked for a custom binary, down to the file when it was a file.

    `toolchain.tp_binary` names a key, and two files can hold it: local.yml
    is merged over echolot.yml. The reader wants the one to open.
    """
    if source != "toolchain.tp_binary" or cfg is None:
        return source
    holder = cfg.path
    if cfg.local_path:
        import yaml
        with contextlib.suppress(OSError, yaml.YAMLError):
            local = yaml.safe_load(Path(cfg.local_path).read_text(encoding="utf-8"))
            if isinstance(local, dict) and Config(local).tp_binary:
                holder = cfg.local_path
    return f"toolchain.tp_binary in {holder}"


def cmd_doctor(args) -> int:
    """Facts about the environment plus proof that it computes correctly.

    It does not check "are the dependencies installed" — pip fails loudly
    without us, and TraceSession already carries a clear message about a
    missing perfetto. The value is elsewhere. First: the trace_processor
    version becomes visible, and that version defines the vocabulary the
    detectors match on. Second: the self-check on a synthetic trace shows not
    the presence of tools but the correctness of answers.

    The trace_processor it checks is the one `analyze` would run from here:
    the flag, then `toolchain.tp_binary` from the config, then the pin.
    """
    import platform as py_platform

    if _refused_without_asserts():
        return 1

    cfg = _doctor_config(args)
    tp_binary, source = _tp_binary_source(args, cfg)
    info = toolchain_info(tp_binary, source)
    origin = _binary_origin(source, cfg)

    if getattr(args, "quiet", False):
        code, _ = _doctor_quiet(args, info, origin=origin)
        return code

    print("## Environment\n")
    facts = [
        ("python", py_platform.python_version()),
        ("platform", f"{py_platform.system()} / {py_platform.machine()}"),
        ("perfetto", info.get("perfetto_package") or "unknown"),
        ("PyYAML", _pkg_version("PyYAML")),
        ("rich-argparse", _pkg_version("rich-argparse")),
    ]
    tp_version = info.get("trace_processor") or "unknown"
    if tp_binary:
        tp_version += "  ← custom binary, the pin in pyproject.toml is bypassed"
    facts.append(("trace_processor", tp_version))
    width = max(len(k) for k, _ in facts)
    for key, value in facts:
        print(f"  {key.ljust(width)}  {value}")
    try:
        # On a first run this is the download, said on stderr as it starts.
        # The self-check below opens the same path and does not ask again.
        binary = resolve_binary_path(tp_binary)
    except ToolchainError as e:
        return _no_trace_processor(e, info)
    if binary:
        print(f"\n  binary: {binary}")
        if tp_binary:
            # Who asked for it, beside the path: a flag typed for this run,
            # or a line in a file that is normally gitignored.
            print(f"  (from {origin})")
        else:
            print("  (the name is a SHA-256 prefix: contents verified on download)")

    # Before the self-check, not after: agents run `doctor | head -30`, and
    # forty lines of "ok" pushed this off the screen — a layer that said
    # "there is no runner yet" while the binary had one went unnoticed for a
    # whole session. Not a check that can fail: a project may have edited its
    # copy on purpose.
    verdict = layer.print_status(Path.cwd())
    recorder.note(layer=verdict)
    # Right before the self-check, which is what the sandbox refuses first:
    # whether a rule lets echolot out, and whether Codex reads it here.
    said = codex.print_status(Path.cwd(), layer.hosts.keys(Path.cwd()))
    if said:
        recorder.note(codex=said)

    print("\n## Self-check on a synthetic trace\n")
    try:
        from . import selftest
        results = selftest.run(tp_binary)
    except Exception as e:
        print(f"  could not run: {e}")
        if isinstance(e, SandboxError):
            # Nothing is known to be wrong with the install: the self-check
            # never reached its first query. "The environment is broken" sent
            # a reader to reinstall what a sandbox had stopped.
            print("\nNothing was checked. Run echolot outside the sandbox, "
                  "then check again.")
        else:
            print("\nThe environment is broken. Until this is fixed, no report "
                  "from it can be trusted.")
        _record_not_run(f"self-check could not run: {e}", info, e)
        return 1

    failed = [(name, why) for name, why in results if why]
    for name, why in results:
        print(f"  ok    {name}" if not why else f"  FAILS {name}\n          {why}")
    recorder.note(checks=len(results), failed=[name for name, _ in failed],
                  trace_processor=info.get("trace_processor"))

    print()
    if failed:
        print(f"Mismatches: {len(failed)} of {len(results)}. "
              f"Reports from this environment cannot be trusted.")
        return 1
    print(f"All {len(results)} checks passed — the pipeline computes correctly.")
    return 0


def _doctor_quiet(args, info: dict, project: Path | None = None,
                  origin: str | None = None) -> tuple[int, bool]:
    """`doctor -q`: three lines, and every failure. Same exit code.

    For a subagent, a CI step, a `| head`: the full report is ten kilobytes
    of "ok" that a second reader in the same session pays for again. Here
    the verdicts stay and the evidence goes. `init` calls it too, for the
    project it just installed into.

    The binary checked is the one `info` names and no other, so the first
    line and the verdict under it cannot be about two different
    trace_processors. `origin` is who asked for a custom one, when the caller
    knows it more exactly than `info["source"]` does — the file, not only
    the key.

    Returned beside the exit code: whether the self-check ran at all. Exit 1
    is a check that failed and also a self-check that never started, and
    `init`, which ends in this, says which of the two it was.
    """
    import platform as py_platform

    if _refused_without_asserts():
        return 1, False
    tp = info.get("trace_processor") or "unknown"
    src = (f" (custom binary from {origin or info.get('source')})"
           if info.get("binary") else "")
    print(f"echolot {recorder.version()} · trace_processor {tp}{src} · "
          f"perfetto {info.get('perfetto_package') or 'unknown'} · "
          f"python {py_platform.python_version()}")
    here = project or Path.cwd()
    verdict, line = layer.one_line(here)
    recorder.note(layer=verdict)
    print(line)
    # A fourth line where Codex is used: the self-check below is what its
    # sandbox refuses, and this says whether anything lets echolot out.
    said = codex.one_line(here, layer.hosts.keys(here))
    if said:
        recorder.note(codex=said[0])
        print(f"codex: {said[1]}")
    try:
        from . import selftest
        results = selftest.run(info.get("binary"))
    except ToolchainError as e:
        return _no_trace_processor(e, info), False
    except Exception as e:
        print(f"self-check: could not run — {e}")
        _record_not_run(f"self-check: could not run — {e}", info, e)
        return 1, False
    failed = [(name, why) for name, why in results if why]
    recorder.note(checks=len(results), failed=[name for name, _ in failed],
                  trace_processor=info.get("trace_processor"))
    if failed:
        print(f"self-check: {len(failed)} of {len(results)} FAIL — reports from "
              f"this environment cannot be trusted")
        for name, why in failed:
            print(f"  FAILS {name}\n          {why}")
        return 1, True
    print(f"self-check: {len(results)} of {len(results)} passed")
    return 0, True


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def cmd_scan(args) -> int:
    """What the repository says about itself — the facts setup starts from.

    The app module and its applicationId, the variants and which one to
    measure on, the macrobenchmark and what it measures, the gradle task
    that runs it, the devices attached, and a config to start from with
    every value saying where it came from. An agent used to read the build
    scripts for this, and one glob caught a `.class` file on the way. Reads
    and prints; the one thing written is its line in .echolot/log/runs.jsonl
    — the line `main` appends for every command unless ECHOLOT_NO_RECORD is
    set.
    """
    from . import scan as scan_mod

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2
    facts = scan_mod.describe(root, devices=not args.no_devices)
    recorder.note(app=bool(facts.app), variants=len(facts.variants),
                  benchmarks=len(facts.benchmarks),
                  devices=None if facts.devices is None else len(facts.devices))
    if args.json:
        print(json.dumps(scan_mod.to_json(facts), ensure_ascii=False, indent=2))
    else:
        print(scan_mod.render(facts))
    return 0


def cmd_report(args) -> int:
    """Views of a report that is already on disk.

    On a real hunt the agent cut report.json up sixteen times with jq and
    python one-liners — the keys, the window, which detectors fired with
    which thresholds, the top rows of one detector with the evidence cut
    short. Each is a view, and each one-liner put a window's worth of json
    into the context to get at a line. Reads and prints; the one thing
    written is its line in .echolot/log/runs.jsonl — the line `main` appends
    for every command unless ECHOLOT_NO_RECORD is set.
    """
    project = project_of(args)
    path = Path(args.report) if args.report else project / ".echolot" / "out" / "report.json"
    try:
        rep = _load_report(path)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        recorder.failed(str(e))
        return 2
    detectors = list(args.detector or [])
    known = {d["id"] for d in rep.get("detectors") or []}
    unknown = [d for d in detectors if d not in known]
    if unknown:
        print(f"error: no detector {', '.join(unknown)} in {path} — it has: "
              f"{', '.join(sorted(known))}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report_mod.select(rep, detectors, args.top, args.window, args.markers),
                         ensure_ascii=False, indent=2))
        return 0
    parts = []
    if args.window:
        parts.append(report_mod.window_view(rep))
    if args.markers:
        parts.append(report_mod.markers_view(rep, top=args.top or 15))
    for det in detectors:
        parts.append(report_mod.detector_view(rep, det, top=args.top or 5, wide=args.wide))
    if not parts:
        parts.append(report_mod.overview(rep))
    print("\n\n".join(parts))
    return 0


def cmd_explain(args) -> int:
    for d in load_detectors(DETECTOR_DIR):
        print(f"{d.id}\n  {d.title}")
        if d.why:
            print(f"  why: {d.why}")
        if d.params:
            print(f"  params: {d.params}")
        print()
    return 0


def _dump(tp, sql: str) -> None:
    """A raw query, straight to stdout as a table. Reconnaissance only."""
    table.show(tp.query(" ".join(sql.split())))


# Who each verb is for. Three audiences share one CLI, and until this was
# structural the split lived only in prose: `echolot --help` showed twelve
# equal verbs, and a person reasonably tried `probe` and got a wall of
# reconnaissance meant for an agent.
# The order verbs are read in, which is neither registration order nor
# alphabetical. A verb missing from here is caught by the self-check.
# The agent's half is ordered by the working flow. `anr` sits at its head
# because a report from the field arrives before there is a trace to probe.
ORDER = ("status", "init", "hunt", "doctor", "collect", "analyze", "compare",
         "guide", "report", "scan", "anr", "probe", "names", "domains", "mark",
         "calibrate", "explain", "reflect")

GROUP_TITLES = {
    "yours": ("Yours", None),
    "pipeline": ("The pipeline", "for CI, and for traces by hand"),
    "agent": ("The agent's",
              "an agent runs these — `guide` is how one that is not Claude Code "
              "learns the rest"),
    "tool": ("Improving the tool", None),
}


def _describe(entries: list[tuple[str, str, str, str]]) -> str:
    """The header of `echolot --help`, grouped by who types the command."""
    out = ["echolot — a deterministic layer between the trace and the agent.",
           "Start with `echolot` alone: where this project stands, and the next step. "
           "In Claude Code, `/echolot` does the same and takes that step.", ""]
    width = max(len(f"{name} {usage}".rstrip()) for _, name, usage, _ in entries)
    for group, (title, note) in GROUP_TITLES.items():
        rows = sorted((e for e in entries if e[0] == group),
                      key=lambda e: ORDER.index(e[1]) if e[1] in ORDER else 99)
        if not rows:
            continue
        out.append(f"{title}:" + (f"  ({note})" if note else ""))
        for _, name, usage, help_text in rows:
            call = f"{name} {usage}".rstrip()
            out.append(f"  {call.ljust(width)}  {help_text}")
        out.append("")
    return "\n".join(out).rstrip()


class _Versions(argparse.Action):
    """`echolot --version`: the line `doctor -q` opens with, and nothing else.

    What an issue asks for first. The trace_processor is the pin, read off
    the manifest the way `toolchain_info` reads it and never downloaded, so
    the answer comes offline and on a first run as well. A `--tp-binary` or a
    `local.yml` does not change it: which binary a project runs is `doctor`'s
    question.

    Printed from inside the parser, which exits before `main` points the run
    log anywhere. Asking for the version is not a run: it would otherwise
    leave a line in .echolot/log/runs.jsonl, and create .echolot/, in
    whatever directory it happened to be typed.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        import platform as py_platform
        info = toolchain_info()
        print(f"echolot {recorder.version()} · trace_processor "
              f"{info.get('trace_processor') or 'unknown'} · perfetto "
              f"{info.get('perfetto_package') or 'unknown'} · python "
              f"{py_platform.python_version()}")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    # rich-argparse title-cases the section headings — "Usage:", "Options:",
    # "Positional Arguments:". Every other Python program on the same machine
    # writes them the way argparse does, and colour is the whole reason the
    # formatter is here; the wording is not part of the deal. `str` leaves the
    # headings alone. Set on the base class, which the Raw variant inherits.
    RichHelpFormatter.group_name_formatter = str

    p = argparse.ArgumentParser(
        prog="echolot", description=__doc__,
        # Without Raw, argparse collapses the newlines and the command list in
        # the header congeals into a single paragraph.
        formatter_class=RawDescriptionRichHelpFormatter)
    p.add_argument("--tp-binary", help="path to your own trace_processor_shell")
    p.add_argument("-V", "--version", action=_Versions,
                   help="print the versions — echolot, the trace_processor it "
                        "pins, perfetto, python — and exit")

    # The same flag is also allowed AFTER the subcommand: `doctor --tp-binary X`
    # is how nine people out of ten will write it. SUPPRESS is mandatory, or the
    # subparser overwrites the global value with its own None when the flag is
    # absent.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tp-binary", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    # `echolot` alone is `echolot status`: where things stand, and the next
    # step. The subcommand is not required so that the bare form works.
    # Deliberately NOT the Raw variant here. The header above is a table built
    # by hand and its line breaks are the layout; a subcommand's description is
    # an English paragraph written as a wrapped Python string, and Raw would
    # print it as one 290-character line for `anr` to hard-wrap mid-word.
    sub = p.add_subparsers(dest="cmd", required=False, metavar="<command>",
                           help="one of the above", parser_class=(
        lambda **kw: argparse.ArgumentParser(
            parents=[common], formatter_class=RichHelpFormatter, **kw)))
    p.set_defaults(func=cmd_status, cmd="status")

    # Each verb declares its audience here and nowhere else, and the header
    # above is generated from it. No `help=` reaches add_parser: a subparser
    # with one gets listed a second time, ungrouped, under "positional
    # arguments" — and `help=argparse.SUPPRESS` does not prevent that, it
    # prints the literal ==SUPPRESS== instead.
    entries: list[tuple[str, str, str, str]] = []

    def add(name: str, group: str, usage: str, help_text: str, **kw):
        entries.append((group, name, usage, help_text))
        return sub.add_parser(name, **kw)

    stt = add("status", "yours", "", "where this project stands, and the next step")
    stt.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    stt.add_argument("--next", action="store_true",
                     help="print only the next step, one word: "
                          + " | ".join(state.NEXT_KINDS) + " — what /echolot switches on")
    stt.set_defaults(func=cmd_status)

    # The investigation, with a name a person can find. `hunt` means the same
    # word in the shell and after /echolot: here it does the half a shell can
    # do and names the half it cannot.
    hn = add("hunt", "yours", "[<question>]",
             "the investigation: open one, or say what is open")
    hn.add_argument("question", nargs="*",
                    help="what regressed, in your words — opens a new investigation")
    hn.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    hn.add_argument("--since", dest="hunt_since", metavar="CHANGE",
                    help="after which change: a commit, a bump, a date, or 'unknown'")
    hn.add_argument("--resume", action="store_true",
                    help="carry on with the open one")
    hn.add_argument("--done", metavar="CONCLUSION",
                    help="record what it came to, and close it")
    hn.add_argument("--list", action="store_true",
                    help="every investigation this project has had")
    hn.add_argument("--show", metavar="N|WORDS",
                    help="one of them in full, by number or by part of its question")
    hn.set_defaults(func=cmd_hunt)

    # Ordered by the working flow rather than by when things were written:
    # first make sure the environment computes correctly, then reconnaissance,
    # then analysis.
    dr = add("doctor", "yours", "",
             "environment + self-check on a synthetic trace; exit 0 passed, "
             "1 failed or could not run, 2 trace_processor not downloaded")
    dr.add_argument("-q", "--quiet", action="store_true",
                    help="three lines and the failures, same exit code — for "
                         "subagents and CI")
    # Hidden, like status's: read only for `toolchain.tp_binary`, so that the
    # binary doctor vouches for is the one `analyze` runs from here.
    dr.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    dr.add_argument("--local", help=argparse.SUPPRESS)
    dr.set_defaults(func=cmd_doctor)

    pr = add("probe", "agent", "<trace>", "what is inside the trace at all")
    pr.add_argument("trace")
    pr.add_argument("--process", help="process name to break down in detail")
    pr.set_defaults(func=cmd_probe)

    nm = add("names", "agent", "<trace>", "how slices are named and what the masks see")
    nm.add_argument("trace")
    nm.add_argument("--process", default=None,
                    help="GLOB over the process name (default: project.process "
                         "from the config, else the process with most slices)")
    nm.add_argument("-c", "--config", default="echolot.yml",
                    help="take the process and overridden masks from the config, "
                         "if present")
    nm.add_argument("--local", help="path to local.yml (defaults to alongside)")
    nm.add_argument("--top", type=int, default=15,
                    help="how many families to show per section")
    nm.add_argument("--min-ms", type=float, default=1.0,
                    help="relevance floor: shorter families are not shown")
    nm.add_argument("--grep", metavar="REGEX",
                    help="only families whose name matches, case-insensitive — "
                         "instead of a grep over a table whose cells were cut")
    nm.add_argument("--wide", action="store_true",
                    help="do not cut cells to fit a terminal (a pipe never cuts them)")
    nm.add_argument("--json", action="store_true",
                    help="the same inventory as json: sections, families, threads, masks")
    nm.set_defaults(func=cmd_names)

    an_r = add("anr", "agent", "<report>", "a thread dump from the field: the lock chain, and who was working",
               description="Reads an ANR report — an export from Crashlytics, "
                    "an ANR cluster from Play Console, or the device's own "
                    "record from `adb shell dumpsys dropbox --print "
                    "data_app_anr` — and prints what it found: the monitor "
                    "everything was queued behind, what the main thread was "
                    "doing, and the few threads that were not idle. Reads and "
                    "prints; opens no investigation, and writes only its own "
                    "line in .echolot/log/runs.jsonl under the working "
                    "directory.")
    an_r.add_argument("report", help="the report file")
    an_r.add_argument("--root", default=".",
                      help="repository root, to place the frames in files and "
                           "tell the app's own code from its libraries by the "
                           "packages its sources declare (default: the current "
                           "directory)")
    an_r.add_argument("--json", action="store_true",
                      help="the same findings in the shape an agent walks")
    an_r.set_defaults(func=cmd_anr)

    dom = add("domains", "agent", "--root <repo>", "slice-to-code map and instrumentation coverage")
    dom.add_argument("--root", default=".", help="repository root")
    dom.add_argument("--top", type=int, default=12,
                     help="how many modules to list when there is none")
    dom.set_defaults(func=cmd_domains)

    mk = add("mark", "agent", "[--apply|--remove]", "the first temporary markers for a project with none",
             description="Proposes the first AGENTTMP_ markers for a project with no "
                    "instrumentation, from the platform's vocabulary only: the "
                    "manifest's launcher Activity and Application class, their "
                    "onCreate, setContent, one call hop from it, Room and DI entry "
                    "points. Each row says where it comes from. --apply inserts "
                    "begin/end pairs tagged `// echolot:mark`; --remove deletes "
                    "exactly those lines.")
    mk.add_argument("--root", default=".", help="repository root")
    mk.add_argument("-c", "--config", default="echolot.yml",
                    help="for project.package (which app module) and instrumentation.allowed")
    mk.add_argument("--local", help="path to local.yml (defaults to alongside)")
    mk.add_argument("--module", help="the app module when several declare a launcher, e.g. :app")
    mk.add_argument("--pools", action="store_true",
                    help="instead of markers: where a thread or pool is created "
                         "with the JDK's default naming, so the report says "
                         "`pool-7-thread-1` where a name would do")
    mk.add_argument("--from-anr", metavar="REPORT",
                    help="take the targets from an ANR report's frames instead "
                         "of the manifest — what was on the stack when it froze")
    mk.add_argument("--apply", action="store_true", help="insert the applicable markers")
    mk.add_argument("--remove", action="store_true",
                    help="delete the lines --apply wrote under --root; a line "
                         "that carries the `// echolot:mark` tag in any other "
                         "shape is listed with its file and line, and left "
                         "for you to clean by hand")
    mk.add_argument("--json", action="store_true", help="the plan as JSON")
    mk.set_defaults(func=cmd_mark)

    col = add("collect", "pipeline", "-n 5", "capture N traces of one scenario from a device")
    col.add_argument("-c", "--config", default="echolot.yml")
    col.add_argument("--local", help="path to local.yml (defaults to alongside)")
    col.add_argument("-n", "--iterations", type=int,
                     help="how many repeats (default from runner.iterations)")
    col.add_argument("-o", "--out", default=".echolot/traces")
    col.add_argument("--device", help="device serial, when there are several")
    col.set_defaults(func=cmd_collect)

    ini = add("init", "yours", "", "install or update the .claude/ layer; checks the environment",
             description="The one command to know. First time: installs the .claude/ "
                    "layer. Later: brings untouched files up to date, keeps the "
                    "ones you edited, runs the environment check, and says what "
                    "to do next.")
    ini.add_argument("--into", default=".", help="Android project root")
    # `--all`, and `--force` as the older spelling. The flag brings every
    # file of the layer up to date, the ones this project edited included —
    # they are echolot's copies, under the project's git. Named for what it
    # does rather than for insistence: an agent's harness that screens shell
    # commands for harm refused `init --force` twice on a real project, and
    # the person had to type it. Outside `.claude/` it touches one file, and
    # only where Codex was chosen: `.codex/rules/echolot.rules`, which is
    # echolot's alone as well. settings.json is merged under both.
    ini.add_argument("--all", dest="force", action="store_true",
                     help="update every file of the layer, the ones you edited too "
                          "(they are echolot's copies, under your git), and "
                          "Codex's rule with them")
    ini.add_argument("--force", dest="force", action="store_true", help=argparse.SUPPRESS)
    # The clients are read off the list `init` knows, so a new one cannot be
    # left out of the help the way gemini was.
    ini.add_argument("--for", dest="for_hosts", metavar="CLIENTS",
                     help="which agents to point at the tool: "
                          + ", ".join(h.key for h in layer.hosts.HOSTS)
                          + " — comma-separated, or `all`. Default: the choice "
                            "this project saved last time, else whichever it "
                            "shows evidence of")
    # Only the parser turns prompting on: cmd_init is also called directly,
    # by the self-check, with a Namespace that has none of these.
    ini.set_defaults(interactive=True)
    ini.add_argument("--no-input", dest="interactive", action="store_false",
                     help="never ask: keep the saved choice, or take the "
                          "detected set the first time (already implied "
                          "without a terminal)")
    ini.add_argument("--no-doctor", action="store_true",
                     help="skip the environment check at the end")
    # Named for whom the install is for. `--silent` read as "prints less",
    # beside `doctor -q`; `--local` is already a path to local.yml in every
    # command that takes one. Neither given: what this clone chose last time.
    who = ini.add_mutually_exclusive_group()
    who.add_argument("--private", dest="private", action="store_true", default=None,
                     help="for this clone only: everything init writes goes into "
                          "the repository's own ignore file, and no file git "
                          "tracks is written; kept for later runs")
    who.add_argument("--shared", dest="private", action="store_false",
                     help="for the team, the default: the layer and the "
                          ".gitignore lines are meant to be committed; undoes "
                          "--private")
    ini.set_defaults(func=cmd_init)

    cal = add("calibrate", "agent", "<trace...>", "thresholds from known-healthy runs")
    cal.add_argument("traces", nargs="+",
                     help="repeats of ONE scenario on a healthy build")
    cal.add_argument("-c", "--config", default="echolot.yml")
    cal.add_argument("--local", help="path to local.yml (defaults to alongside)")
    cal.add_argument("--min-sample", type=int, default=10,
                     help="below this many values no threshold is derived")
    cal.set_defaults(func=cmd_calibrate)

    an = add("analyze", "pipeline", "<trace...>", "run the detectors, build a Marker Report",
             description="Run the detectors over one trace or repeats of one "
                    "scenario and write the Marker Report. Thresholds: the "
                    "detector's defaults, then the config's detectors section, "
                    "then --set; --defaults skips the config's section.")
    an.add_argument("traces", nargs="+",
                    help="one trace, or repeats of one scenario")
    an.add_argument("-c", "--config", default="echolot.yml",
                    help="the project config (default: echolot.yml in cwd)")
    an.add_argument("--local", help="path to local.yml (defaults to alongside)")
    an.add_argument("-o", "--out", default=".echolot/out",
                    help="where report.md and report.json go; a relative path "
                         "is taken from the config's directory (default: "
                         ".echolot/out next to the config)")
    an.add_argument("--set", action="append", default=[],
                    metavar="DETECTOR.PARAM=VALUE",
                    help="override one threshold for this run only, e.g. "
                         "--set main_thread_block.min_slice_ms=16; repeatable")
    an.add_argument("--defaults", action="store_true",
                    help="ignore the config's detectors section: every detector, "
                         "built-in thresholds. To see what the shipped numbers "
                         "say without touching the config")
    an.set_defaults(func=cmd_analyze)

    cp = add("compare", "pipeline", "[<before.json> [<after.json>]]",
             "what changed between two Marker Reports",
             description="The delta between two reports, sorted by how much "
                    "each row moved. With an investigation open and no "
                    "arguments: its previous round against the latest. One "
                    "path: that report against .echolot/out/report.json. "
                    "Two: exactly those.")
    cp.add_argument("paths", nargs="*",
                    help="the older report first, then the newer one")
    cp.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    cp.add_argument("--local", help=argparse.SUPPRESS)
    cp.add_argument("--hunt", metavar="N|WORDS",
                    help="an investigation's first report against its last")
    cp.add_argument("-o", "--out", default=".echolot/out",
                    help="where comparison.md and comparison.json go")
    cp.add_argument("--floor-ms", type=float, default=compare_mod.FLOOR_MS,
                    metavar="MS",
                    help="movement below this many ms is not a row "
                         f"(default: {compare_mod.FLOOR_MS:g})")
    cp.add_argument("--floor-pct", type=float,
                    default=compare_mod.FLOOR_RATIO * 100, metavar="PCT",
                    help="and below this share of the earlier value "
                         f"(default: {compare_mod.FLOOR_RATIO * 100:g})")
    cp.set_defaults(func=cmd_compare)

    gd = add("guide", "agent", "[<topic>]",
             "how to work with this tool — for any agent, not only Claude Code")
    # Read off the topics `guide` itself reads: the list written out by hand
    # went on without `anr` once it shipped.
    gd.add_argument("topic", nargs="?",
                    help="overview (default), " + ", ".join(sorted(
                        t for t in layer.guide_topics() if t != "overview")))
    gd.set_defaults(func=cmd_guide)

    ex = add("explain", "agent", "", "the detectors and their parameters")
    ex.set_defaults(func=cmd_explain)

    sc = add("scan", "agent", "[--root <repo>]",
             "what the repository says about itself: app, variants, benchmark, devices, a config to start from",
             description="The facts setup starts from, read off the tree rather than "
                         "by an agent reading build scripts: the app module and its "
                         "applicationId, flavours × build types and which variant to "
                         "measure on, the module with a MacrobenchmarkRule with its tests "
                         "and the sections it measures, the gradle tasks that run it, "
                         "the devices attached — and an echolot.yml to start from, every "
                         "value saying where it came from. Writes only its own line in "
                         ".echolot/log/runs.jsonl under the working directory.")
    sc.add_argument("--root", default=".", help="repository root (default: the current directory)")
    sc.add_argument("--no-devices", action="store_true", help="do not ask adb")
    sc.add_argument("--json", action="store_true", help="the facts as json")
    sc.set_defaults(func=cmd_scan)

    rp = add("report", "agent", "[--detector <id>] [--top N]",
             "views of the last report: one detector's rows, the window, the markers",
             description="Reads a Marker Report that is already on disk and prints "
                         "one view of it: an overview of what fired, one "
                         "detector's rows with the evidence kept short, the "
                         "window and the device, or the markers. Writes only its "
                         "own line in .echolot/log/runs.jsonl. Default: "
                         ".echolot/out/report.json next to the config.")
    rp.add_argument("report", nargs="?", help="a report.json (default: the last one)")
    rp.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    rp.add_argument("--detector", "-d", action="append", metavar="ID",
                    help="this detector's rows, longest first; repeatable")
    rp.add_argument("--top", type=int, metavar="N",
                    help="how many rows (default: 5 for a detector, 15 for markers)")
    rp.add_argument("--window", action="store_true",
                    help="the window, the anchors, the main thread and the device")
    rp.add_argument("--markers", action="store_true",
                    help="the project's own names, measured")
    rp.add_argument("--wide", action="store_true",
                    help="do not cut the evidence column")
    rp.add_argument("--json", action="store_true",
                    help="the same selection as json, rows cut to --top")
    rp.set_defaults(func=cmd_report)

    rf = add("reflect", "tool", "[--last|--all]", "the same kind of report over an agent session")
    pick = rf.add_mutually_exclusive_group()
    pick.add_argument("--last", action="store_true",
                      help="the newest session that used echolot (default)")
    pick.add_argument("--session", metavar="ID",
                      help="a session id, or its first characters")
    pick.add_argument("--since", metavar="2h",
                      help="every session that used echolot in the last 2h / 30m / 3d")
    pick.add_argument("--all", action="store_true",
                      help="every session that used echolot, plus a summary")
    rf.add_argument("--list", action="store_true",
                    help="only list the candidate sessions, write nothing")
    rf.add_argument("--project", metavar="ROOT",
                    help="the application project the agent worked in (default: .)")
    rf.add_argument("--from-log", action="store_true",
                    help="read .echolot/log/runs.jsonl only, ignoring any "
                         "agent transcript — what every client but Claude Code "
                         "and Codex gets by default")
    rf.add_argument("--transcripts", metavar="DIR",
                    help="a Claude Code transcript directory, if not "
                         "~/.claude/projects/<slug>; Codex sessions are then "
                         "left out")
    rf.add_argument("-c", "--config", default="echolot.yml",
                    help="the project config, for the protocol checks")
    rf.add_argument("--local", help="path to local.yml (defaults to alongside)")
    rf.add_argument("-o", "--out", default=".echolot/reflect")
    rf.set_defaults(func=cmd_reflect)

    p.description = _describe(entries)
    return p


# What this tool prints that a legacy code page has no room for: the arrows
# of `status` and `init`, the ≠ of a file init kept, the ⚠ of a report, the
# ✓ of init's picker. The last two are in no Windows code page at all.
OWN_SYMBOLS = "→↑≠⚠✓"


def _streams_that_carry_the_output() -> None:
    """Standard streams that cannot carry what this tool prints, made to.

    On Windows, Python 3.10–3.14 encode redirected output in the ANSI code
    page — cp1252 and its neighbours — and UTF-8 becomes the default only in
    3.15 (PEP 686). Behind a pipe, `init` wrote .gitignore and hosts.json,
    reached its first `↑` and died with a UnicodeEncodeError, the layer not
    installed; `echolot` and `doctor -q` died on the `→` of a stale layer.
    Agents and CI always read this tool through a pipe.

    UTF-8, rather than the same code page with `errors="replace"`, which
    would also have stopped the crash, because of who reads a pipe. An agent
    does, and the agents `init` points at this tool read a command's output
    as UTF-8; so does a CI log, and so does whatever parses `--json`, which
    RFC 8259 puts in UTF-8 between systems. To every one of them a replaced
    character is data lost without a word: a slice name, a path or a glob
    comes back with a `?` in it, the agent pastes it into the next command,
    and the tool is asked about a name that is in no trace. UTF-8 carries
    every character as itself, and it is what 3.15 does anyway, so a reader
    that works with 3.15 works with this.

    `backslashreplace` on the way out is for what even UTF-8 cannot carry —
    a lone surrogate out of an undecodable file name — so that nothing
    printed can raise. stdin is read once, by init's picker, from a terminal
    only; it is decoded as UTF-8 the same way, with `replace`, since the
    picker understands nothing but ASCII and a stray byte is then one more
    word it ignores instead of a traceback.

    A stream that can carry the symbols is left as it is: UTF-8 everywhere
    else, and the Windows console, which Python has written in UTF-16 since
    3.6. So is one that is not a real stream — a StringIO a test or the
    self-check put there, a stdin that is closed. Nothing here may stop the
    command it runs in front of, so nothing here raises.
    """
    for stream, errors in ((sys.stdout, "backslashreplace"),
                           (sys.stderr, "backslashreplace"),
                           (sys.stdin, "replace")):
        try:
            OWN_SYMBOLS.encode(stream.encoding)
            continue
        except Exception:
            pass
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors=errors)


def main(argv=None) -> int:
    # First: argparse prints too — a usage line, `--help` — and the first
    # character a stream cannot carry is a traceback wherever it comes.
    _streams_that_carry_the_output()
    args = build_parser().parse_args(argv)
    # Every invocation leaves one line in .echolot/log/runs.jsonl — the tool's
    # own record of what was asked and how it went, independent of whichever
    # agent (or human) was typing. `echolot reflect` reads it later.
    #
    # Which project's log, decided before the command runs: it has to hold
    # even when the command fails, and a failure is exactly the line worth
    # keeping.
    recorder.at(project_of(args))
    started = time.time()
    try:
        code = args.func(args)
    except BaseException as e:
        recorder.record(args, argv, started, exit_code=1, error=e)
        raise
    recorder.record(args, argv, started, exit_code=code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
