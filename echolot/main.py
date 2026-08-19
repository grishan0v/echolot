#!/usr/bin/env python3
"""echolot — a deterministic layer between the trace and the agent.

The command list in `--help` is generated from the registration below, so the
grouping by audience cannot drift from it. It used to be kept by hand here,
and by hand in the README, and argparse printed a third flat copy underneath.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from . import compare as compare_mod
from . import hunt as hunt_mod
from . import recorder
from . import report as report_mod
from .config import NO_ANCHOR, Config, ConfigError
from .tp import (
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
    """
    with TraceSession(args.trace, args.tp_binary) as tp:
        print("## Processes\n")
        _dump(tp, """
            SELECT p.name AS process, p.pid, COUNT(s.id) AS slices
            FROM process p
            LEFT JOIN thread t ON t.upid = p.upid
            LEFT JOIN thread_track tt ON tt.utid = t.utid
            LEFT JOIN slice s ON s.track_id = tt.id
            WHERE p.name IS NOT NULL
            GROUP BY p.upid ORDER BY slices DESC LIMIT 15
        """)

        if args.process:
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

            print(f"\n## Longest slices (scenario anchor candidates)\n")
            _dump(tp, f"""
                SELECT s.name AS slice, t.name AS thread,
                       COUNT(*) AS n,
                       ROUND(MAX(s.dur)/1e6, 2) AS max_ms
                FROM slice s
                JOIN thread_track tt ON s.track_id = tt.id
                JOIN thread t ON tt.utid = t.utid
                JOIN process p ON t.upid = p.upid
                WHERE p.name GLOB '{sql_value(args.process)}'
                GROUP BY s.name ORDER BY max_ms DESC LIMIT 25
            """)
    return 0


def _tp_binary(args, cfg: Config | None = None) -> str | None:
    """Precedence: the flag, then local.yml, then the pin in requirements."""
    return getattr(args, "tp_binary", None) or (cfg.tp_binary if cfg else None)


def _note_local(cfg: Config) -> None:
    """local.yml changes the result, so its use is announced.

    It is not committed, so two people on the same commit can get diverging
    runs — and the first thing to know is that something was layered on top of
    the project config.
    """
    if cfg.local_path:
        print(f"[i] {cfg.local_path} applied on top of the config",
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


def parse_set(values: list[str], detectors) -> dict[str, dict]:
    """--set detector.param=value, repeatable, into per-detector overrides.

    Values are read as YAML scalars so `16`, `4.5` and `binder*` arrive typed
    the same way they would from the config. Unknown detectors and parameters
    are refused with the list of valid ones: a silently ignored typo is worse
    than no flag at all.
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
        out.setdefault(det, {})[param] = yaml.safe_load(raw.strip())
    return out


def plan_detectors(cfg: Config, *, cli_overrides: dict[str, dict] | None = None,
                   use_defaults: bool = False) -> list[tuple]:
    """Which detectors run, with which overrides, from where.

    Pure — no trace, no trace_processor — so the self-check can pin the
    rules without spinning up a session per case. Returns
    [(detector, overrides, source)] with source one of
    default / config / cli / config+cli.
    """
    detectors = load_detectors(DETECTOR_DIR)
    cli_overrides = cli_overrides or {}
    cfg_overrides = {} if use_defaults else cfg.detector_overrides
    enabled = None if use_defaults else cfg.enabled_detectors
    if enabled is not None:
        # A detector named on the command line is asked for explicitly, even
        # when the config's list leaves it out.
        enabled = set(enabled) | set(cli_overrides)
        detectors = [d for d in detectors if d.id in enabled]
    if not detectors:
        raise ConfigError("no detectors selected")
    plan = []
    for d in detectors:
        from_cfg = dict(cfg_overrides.get(d.id) or {})
        from_cli = dict(cli_overrides.get(d.id) or {})
        overrides = {**from_cfg, **from_cli}
        source = "+".join(
            s for s, on in (("config", bool(from_cfg)), ("cli", bool(from_cli))) if on
        ) or "default"
        plan.append((d, overrides, source))
    return plan


def analyze_trace(trace, cfg: Config, tp_binary: str | None = None, *,
                  cli_overrides: dict[str, dict] | None = None,
                  use_defaults: bool = False) -> dict:
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

    with TraceSession(trace, tp_binary) as tp:
        procs = _resolve_process(tp, cfg.process)
        _setup_context(tp, cfg, procs[0]["upid"])
        window = _window_info(tp, cfg, procs)

        for d, overrides, source in plan:
            try:
                sql, params = d.render(overrides)
                rows = tp.query(sql)
                err = None
            except Exception as e:  # SQL is version-fragile — never fail the run
                rows, params, err = [], d.params, str(e)
                print(f"[!] {d.id}: {e}", file=sys.stderr)
            entry = {
                "id": d.id,
                "title": d.title,
                "why": d.why,
                "params": params,
                "params_source": source,
                "rows": rows,
                "error": err,
            }
            if source != "default":
                # What the shipped numbers would have been — the reader can
                # see how far calibration moved them without `explain`.
                entry["defaults"] = {k: d.params[k] for k in overrides if k in d.params}
            results.append(entry)

    return report_mod.build(str(trace), window, results,
                            toolchain=toolchain_info(tp_binary))


def cmd_analyze(args) -> int:
    try:
        cfg = Config.load(args.config, args.local)
        tp_binary = _tp_binary(args, cfg)
        _note_local(cfg)
        cli_overrides = parse_set(args.set or [], load_detectors(DETECTOR_DIR))
        if args.defaults:
            print("[i] --defaults: the config's detectors section is ignored, "
                  "every detector runs with its built-in thresholds",
                  file=sys.stderr)
        # Repeats are merged by median: one outlier must not drag the
        # conclusion along, and the "Runs" column separates the reproducible
        # from the one-off.
        reports = [analyze_trace(t, cfg, tp_binary, cli_overrides=cli_overrides,
                                 use_defaults=args.defaults)
                   for t in args.traces]
        rep = report_mod.aggregate(reports)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

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

    print(report_mod.to_markdown(rep))
    print(f"\n→ {out_dir/'report.md'}\n→ {out_dir/'report.json'}",
          file=sys.stderr)
    # `touched_at` has to mean work, not "when someone last typed echolot":
    # the freshness rule that decides whether to ask stands on it.
    root = _project_root(cfg)
    hunt_mod.touch(root, analyze=True)
    # .echolot/out/report.json is always the latest and every analyze
    # overwrites it, including one belonging to a different question. The
    # investigation keeps its own copy of each.
    kept = hunt_mod.record_report(root, out_dir)
    if kept is not None:
        print(f"→ {kept}  (this investigation's copy)", file=sys.stderr)
    return 0


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


def _resolve_process(tp, glob: str) -> list[dict]:
    """Target process candidates, the fattest by slice count first.

    An Android app usually has more than one process: `com.example.app*` also
    catches `:pushservice` and `:webview`. This used to take whichever came
    first by upid — silently, and often the wrong one. The choice is now
    deliberate and said out loud: to humans on stderr, to the agent in
    report.json.
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
        raise ConfigError(
            f"no process in the trace matches project.process = '{glob}'. "
            f"Look at the real names: echolot probe <trace>"
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


def _setup_context(tp, cfg: Config, upid: int) -> dict:
    """Prepares the views in two passes and returns the window bounds.

    Between the passes the CLI grabs ts_start/ts_end and substitutes them into
    window.sql as plain numbers. While the window was a view it was recomputed
    on every reference to _slice_win: on a trace with 475k slices the run did
    not finish within ten minutes.
    """
    tp.exec_script(render_sql(
        (SQL_DIR / "context.sql").read_text(encoding="utf-8"),
        cfg.context_params(upid),
    ))
    bounds = tp.query("SELECT ts_start, ts_end FROM _window")[0]
    tp.exec_script(render_sql(
        (SQL_DIR / "window.sql").read_text(encoding="utf-8"),
        {"ts_start": bounds["ts_start"], "ts_end": bounds["ts_end"]},
    ))
    return bounds


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
    for key, glob in (("start", cfg.scenario_start), ("end", cfg.scenario_end)):
        if glob == NO_ANCHOR:
            window[f"{key}_anchor"] = None
            continue
        hits = tp.query(
            f"SELECT COUNT(*) AS n FROM _slice WHERE name GLOB '{sql_value(glob)}'"
        )
        window[f"{key}_anchor"] = {
            "glob": glob,
            "matches": hits[0]["n"] if hits else 0,
        }
    return window


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
    overrides, tp_bin = {}, args.tp_binary
    process = args.process
    if args.config and Path(args.config).exists():
        try:
            cfg_names = Config.load(args.config, getattr(args, "local", None))
            overrides = cfg_names.detector_overrides
            tp_bin = _tp_binary(args, cfg_names)
            # Without --process the fattest process wins, and on a real
            # device that is surfaceflinger, not the app. When the config is
            # right there, its process is the obvious default.
            if process is None:
                try:
                    process = cfg_names.process
                    print(f"[i] --process taken from {args.config}: {process}",
                          file=sys.stderr)
                except ConfigError:
                    pass
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)
    if process is None:
        process = "*"

    with TraceSession(args.trace, tp_bin) as tp:
        try:
            procs = _resolve_process(tp, process)
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        upid = procs[0]["upid"]
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
        if not rows:
            print("_this process has no slices in the trace_")
            return 0

        covered, skipped = _name_coverage(tp, upid, _detector_masks(overrides))

        families: dict[str, dict] = {}
        for r in rows:
            fam = families.setdefault(report_mod.family(r["name"]), {
                "n": 0, "ns": 0, "threads": set(), "dets": set(), "skips": set(),
            })
            fam["n"] += r["n"]
            fam["ns"] += r["total_ns"] or 0
            fam["threads"].add(r["thread"])
            fam["dets"].update(covered.get(r["name"], set()))
            fam["skips"].update(skipped.get(r["name"], set()))

        print(
            "The 'mask' column covers only detectors that search by slice "
            "NAME. `main_thread_block`, `runnable_starvation` and "
            "`uninstrumented_cpu` are structural, names mean nothing to them, "
            "and a dash here does not mean nobody will find the slice."
        )

        floor_ns = args.min_ms * 1e6
        assigned: set[str] = set()
        missed: list[tuple[str, str, dict]] = []
        for title, keywords in BUCKETS:
            picked = []
            for fam, data in families.items():
                if fam in assigned:
                    continue
                # The section is decided by the slice NAME and nothing else.
                # Thread names used to go through the same sieve — and then
                # `merge`, `wait` and `releaseBuffer` drifted into "Binder /
                # IPC" merely because they ran on `binder:*` threads, while
                # `Thread::Init` landed under garbage collection because one of
                # its threads happened to be HeapTaskDaemon. A thread says
                # WHERE code ran, not what it did.
                if any(k in fam.lower() for k in keywords):
                    assigned.add(fam)
                    if data["ns"] >= floor_ns:
                        picked.append((fam, data))
            if not picked:
                continue
            picked.sort(key=lambda x: -x[1]["ns"])
            print(f"\n## {title}\n")
            _families_table(picked[:args.top])
            _note_dropped(len(picked), args.top)
            # Something excluded on purpose (skip_glob) is not a miss.
            missed += [
                (title, f, d) for f, d in picked
                if not d["dets"] and not d["skips"]
            ]

        rest = [(f, d) for f, d in families.items()
                if f not in assigned and d["ns"] >= floor_ns]
        if rest:
            rest.sort(key=lambda x: -x[1]["ns"])
            print(f"\n## Everything else\n")
            _families_table(rest[:args.top])
            _note_dropped(len(rest), args.top)

        print("\n## Missed by the masks\n")
        if not missed:
            print("_Everything resembling GC, locks or binder is covered._")
        else:
            print("These families sit in sections the detectors are "
                  "responsible for, yet no mask sees them. If there is a real "
                  "problem among them, widen the mask in `echolot.yml`.\n")
            missed.sort(key=lambda x: -x[2]["ns"])
            head = ["section", "family", "N", "total, ms"]
            body = [
                [title, _clip(fam), str(d["n"]), f"{d['ns']/1e6:.1f}"]
                for title, fam, d in missed[:args.top]
            ]
            _rows_out(head, body)
            _note_dropped(len(missed), args.top)
    return 0


def _note_dropped(total: int, shown: int) -> None:
    """A truncated list is announced out loud.

    A silently cut table reads as "this is all there is", and the agent
    considers the section closed.
    """
    if total > shown:
        print(f"\n_Showing {shown} of {total}; the rest are shorter. "
              f"Full list: `--top {total}`._")


def _families_table(items) -> None:
    head = ["family", "N", "total, ms", "threads", "mask"]
    body = []
    for fam, data in items:
        threads = sorted(data["threads"])
        shown = ", ".join(threads[:2]) + (f" +{len(threads)-2}" if len(threads) > 2 else "")
        marks = sorted(data["dets"])
        marks += [f"{d} (excluded)" for d in sorted(data["skips"])]
        body.append([
            _clip(fam),
            str(data["n"]),
            f"{data['ns']/1e6:.1f}",
            _clip(shown, 34),
            ", ".join(marks) or "—",
        ])
    _rows_out(head, body)


def _clip(text: str, width: int = 58) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def _rows_out(head: list[str], body: list[list[str]]) -> None:
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join("---" for _ in head) + "|")
    for row in body:
        print("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")


GUIDE_DIR = Path(__file__).resolve().parent / "guide"
CLAUDE_DIR = Path(__file__).parent / "claude"
# What `init` installed, file by file: the manifest lets `doctor` tell a file
# the project customised from one the package has since moved on from.
LAYER_MANIFEST = "echolot-layer.json"


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def layer_files() -> list[Path]:
    """The template, minus hidden files (macOS drops .DS_Store into it)."""
    return [p for p in sorted(CLAUDE_DIR.rglob("*"))
            if p.is_file() and not p.name.startswith(".")]


def _install_pointers(project: Path, chosen: list) -> None:
    """Tell the other clients this tool exists.

    `.claude/` is a Claude Code mechanism, and in Cursor or Codex it is an
    invisible directory. The CLI worked there all along — it is a program —
    but nothing pointed an agent at it, so the instructions were followed only
    when the model happened to read the file while looking around. Each client
    gets a few lines saying "run `echolot guide`"; the knowledge stays in the
    package rather than being copied per client.
    """
    from . import hosts as hosts_mod

    stubs = [h for h in chosen if h.key != "claude"]
    if not stubs:
        return

    print()
    manual = []
    for host in stubs:
        what, dest = hosts_mod.write_stub(project, host)
        rel = dest.relative_to(project)
        if what == "exists-without-ours":
            manual.append(rel)
            print(f"  ≠ {rel} exists and is yours — left alone")
        elif what == "current":
            print(f"  = {rel} ({host.title}, current)")
        else:
            print(f"  {'↑' if what == 'updated' else '+'} {rel} ({host.title})")

    if manual:
        print(f"\nAdd this to {', '.join(str(m) for m in manual)} so the agent "
              f"finds the tool:\n")
        for line in hosts_mod.BODY.strip().split("\n")[:4]:
            print(f"    {line}")
        print("    …  (`echolot guide` prints the rest)")


def _read_manifest(root: Path) -> dict:
    p = root / LAYER_MANIFEST
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(root: Path, files: dict[str, str]) -> None:
    old = _read_manifest(root)
    merged = dict(old.get("files") or {})
    merged.update(files)
    (root / LAYER_MANIFEST).write_text(json.dumps({
        "echolot": recorder.version(),
        "files": dict(sorted(merged.items())),
    }, indent=2) + "\n", encoding="utf-8")


def layer_status(project: Path) -> dict | None:
    """The project's .claude/ layer against the package's template.

    None when there is no layer here. Otherwise one row per template file:

        current      identical to the template
        stale        untouched since install, and the template has moved on
        customised   edited in the project, the template has not moved
        conflict     edited in the project AND the template has moved on
        differs      not identical, and no manifest to say which of the two
        missing      the template has it, the project does not

    The manifest is what makes stale and customised distinguishable; a layer
    installed before it existed can only be "differs".
    """
    root = project / ".claude"
    if not (root / "skills" / "echolot" / "SKILL.md").exists():
        return None
    manifest = _read_manifest(root)
    installed = manifest.get("files") or {}
    rows = []
    for src in layer_files():
        rel = str(src.relative_to(CLAUDE_DIR))
        dst = root / rel
        t_sha = _sha(src)
        if not dst.exists():
            state = "missing"
        else:
            d_sha = _sha(dst)
            if d_sha == t_sha:
                state = "current"
            elif rel in installed:
                was = installed[rel]
                if d_sha == was:
                    state = "stale"
                elif t_sha == was:
                    state = "customised"
                else:
                    state = "conflict"
            else:
                state = "differs"
        rows.append({"file": rel, "state": state})
    return {
        "rows": rows,
        "manifest": bool(installed),
        "installed_by": manifest.get("echolot"),
    }


def _layer_line(project: Path) -> tuple[str, str]:
    """(verdict, one line) about the project's .claude/ layer — for -q."""
    status = layer_status(project)
    if status is None:
        # Absent because this project said it does not use Claude Code is a
        # different fact from absent because nobody ran init. Without the
        # distinction, `next` would ask for `echolot init` forever on a
        # project that had just declined the layer.
        from . import hosts as hosts_mod
        if not hosts_mod.wants_claude(project):
            chosen = hosts_mod.load_choice(project) or []
            named = ", ".join(hosts_mod.BY_KEY[k].title for k in chosen) or "nothing"
            return "opted-out", f"layer: not installed — this project points {named} at echolot"
        return "absent", "layer: none installed here (`echolot init`)"
    by_state: dict[str, int] = {}
    for r in status["rows"]:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    needs = {k: v for k, v in by_state.items()
             if k in ("stale", "conflict", "differs", "missing")}
    if not needs:
        return "current", f"layer: current ({len(status['rows'])} files)"
    what = ", ".join(f"{v} {k}" for k, v in needs.items())
    # stale and missing files `init` updates on its own; files that differ
    # with no manifest to say why, or that were edited here, need --force.
    if set(needs) <= {"stale", "missing"}:
        return "stale", f"layer: STALE — {what} → `echolot init`"
    return "differs", f"layer: STALE — {what} → `echolot init --force`"


def _print_layer_status(project: Path) -> str | None:
    """The doctor section; returns the one-word verdict for the run log."""
    status = layer_status(project)
    print("\n## The .claude/ layer in this project\n")
    if status is None:
        print("  none installed here. `echolot init` puts the skill, the agent "
              "and the commands into ./.claude/")
        return "absent"
    by_state: dict[str, list[str]] = {}
    for r in status["rows"]:
        by_state.setdefault(r["state"], []).append(r["file"])
    total = len(status["rows"])
    counts = ", ".join(f"{len(v)} {k}" for k, v in by_state.items())
    print(f"  {total} template files: {counts}")
    for state in ("stale", "conflict", "customised", "differs", "missing"):
        for rel in by_state.get(state, []):
            print(f"    {state:<10} {rel}")
    if status["installed_by"]:
        print(f"  installed by echolot {status['installed_by']}, "
              f"this is {recorder.version()}")
    needs_update = set(by_state) & {"stale", "conflict", "differs", "missing"}
    if not needs_update:
        print("  the layer is current.")
        return "current"
    if not status["manifest"]:
        print("  installed before echolot kept a manifest, so a file that differs "
              "cannot be told\n  customised from stale. `echolot init --force` "
              "overwrites; keep the project's edits with git.")
    else:
        print("  → `echolot init --force` updates it. Customised files are "
              "listed above and are\n    overwritten too — carry the edits "
              "over afterwards.")
    return "stale"


# -------------------------------------------------------------- status

def project_state(project: Path, config: str = "echolot.yml") -> dict:
    """Where this project stands with echolot: the facts `status` and `init`
    decide the next step from.

    Everything here is read from disk and the run log; nothing runs.
    """
    st: dict = {"project": project}
    st["layer_verdict"], st["layer_line"] = _layer_line(project)

    cfg_path = project / config
    st["config"] = None
    if cfg_path.exists():
        try:
            cfg = Config.load(cfg_path)
            calibrated = bool(cfg.detector_overrides)
            st["config"] = {
                "path": cfg_path, "scenario": cfg.scenario_name,
                "process": cfg.get("project.process") or cfg.get("project.package"),
                "thresholds": "from the config" if calibrated else "built-in defaults",
                "local": cfg.local_path is not None,
                "runner": str(cfg.runner.get("mode", "launch")) if cfg.runner else None,
                "sha": cfg.sha,
            }
        except ConfigError as e:
            st["config"] = {"path": cfg_path, "error": str(e)}

    traces_dir = project / ".echolot" / "traces"
    traces = [p for pat in ("*.perfetto-trace", "*.pftrace")
              for p in traces_dir.glob(pat)] if traces_dir.is_dir() else []
    st["traces"] = {"dir": traces_dir, "count": len(traces),
                    "newest": max((p.stat().st_mtime for p in traces), default=None)}

    st["report"] = None
    rep = project / ".echolot" / "out" / "report.json"
    if rep.exists():
        try:
            r = json.loads(rep.read_text(encoding="utf-8"))
            s = r.get("summary") or {}
            c = r.get("config") or {}
            st["report"] = {
                "path": rep, "generated_at": r.get("generated_at"),
                "fired": s.get("detectors_fired"), "run": s.get("detectors_run"),
                "runs": len(r.get("traces") or []) or 1,
                "config_sha": c.get("sha"), "defaults": c.get("defaults"),
            }
        except (OSError, ValueError):
            st["report"] = {"path": rep, "error": "unreadable"}

    # Which question all of the above is about. None is a normal answer:
    # every project predates its first investigation.
    st["hunt"] = hunt_mod.load(project)

    st["last_doctor"] = st["last_analyze"] = None
    for run in recorder.read(project / recorder.LOG_FILE):
        if run.get("cmd") == "doctor":
            st["last_doctor"] = run
        elif run.get("cmd") == "analyze":
            st["last_analyze"] = run
    return st


# The next step as one word — what `/echolot` in Claude Code switches on —
# and as the line a person reads. Both from the same decision.
NEXT_KINDS = ("init", "init-force", "doctor", "setup", "fix-config",
              "resume-or-new", "hunt")


def next_kind(st: dict) -> str:
    # `opted-out` falls through on purpose: nothing to install and nothing
    # wrong, so the next step is whatever the config says.
    if st["layer_verdict"] == "absent":
        return "init"
    if st["layer_verdict"] == "stale":
        return "init"
    if st["layer_verdict"] == "differs":
        return "init-force"
    d = st.get("last_doctor")
    if d and d.get("facts", {}).get("failed"):
        return "doctor"
    cfg = st.get("config")
    if not cfg:
        return "setup"
    if cfg.get("error"):
        return "fix-config"
    # There is an investigation open, it left traces or a report behind, and
    # enough time has passed that the human may have come back for something
    # else entirely. The CLI does not ask — it says the answer is open, and
    # the agent puts the question with the recap `status` prints below.
    if hunt_mod.needs_choice(st.get("hunt"), st):
        return "resume-or-new"
    return "hunt"


def _door(st: dict) -> str:
    """How this project's agent is reached, in its own words.

    Leading with "/echolot in Claude Code" on a project that has just declined
    the layer names a command its human does not have.
    """
    if st.get("layer_verdict") == "opted-out":
        return "`echolot guide"
    return "/echolot in Claude Code, or `echolot guide"


def next_step(st: dict) -> str:
    """One line: what to do next, from the state. Shared by status and init."""
    kind = next_kind(st)
    if kind == "init":
        if st["layer_verdict"] == "absent":
            return "echolot init — installs the .claude/ layer; then /echolot in Claude Code"
        return "echolot init — brings the .claude/ layer up to date (the agent reads it)"
    if kind == "init-force":
        return ("echolot init --force — the .claude/ layer differs from the package's and "
                "nothing says whether you edited it; --force overwrites, keep your edits with git")
    if kind == "doctor":
        return "echolot doctor — the last self-check failed; no report is trustworthy until it passes"
    if kind == "setup":
        return (f"{_door(st)} setup` — echolot.yml from the repository "
                f"and a probe trace")
    if kind == "fix-config":
        return f"fix echolot.yml — it does not load: {st['config']['error']}"
    if kind == "resume-or-new":
        q = (st.get("hunt") or {}).get("question") or "the earlier question"
        return (f'/echolot in Claude Code — it will ask whether to carry on with '
                f'"{q}" or start a new investigation')
    if not st["traces"]["count"]:
        return (f"{_door(st)} hunt` — or by hand: "
                f"echolot collect -c echolot.yml -n 5")
    return (f"{_door(st)} hunt` — or by hand: "
            f"echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml")


def _ago(epoch: float | None) -> str:
    if not epoch:
        return "never"
    delta = time.time() - epoch
    if delta < 90:
        return f"{int(delta)}s ago"
    if delta < 5400:
        return f"{int(delta // 60)}m ago"
    if delta < 172800:
        return f"{delta / 3600:.0f}h ago"
    return f"{delta / 86400:.0f}d ago"


def _iso_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _hunt_config(project: Path, config: str) -> tuple[str | None, str | None]:
    """Scenario name and config hash, best effort — a broken config is not fatal.

    An investigation records what it was opened against so that `drift` can
    later say "the scenario changed" instead of the human having to remember.
    """
    try:
        cfg = Config.load(project / config)
        return cfg.scenario_name, cfg.sha
    except (ConfigError, OSError):
        return None, None


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
        scenario, sha = _hunt_config(project, config)
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
                              traces_aside=aside)
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
        print("\nThe hunt itself needs an agent: run `/echolot` in Claude Code.",
              file=sys.stderr)
        print("By hand: echolot collect -c echolot.yml -n 5, then echolot analyze "
              ".echolot/traces/*.perfetto-trace", file=sys.stderr)
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
    st = project_state(project, config)
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
    st = project_state(project, getattr(args, "config", "echolot.yml"))
    if getattr(args, "next", False):
        # One word for the skill to switch on; the prose is for people.
        print(next_kind(st))
        return 0
    info = toolchain_info(getattr(args, "tp_binary", None))
    print(f"echolot {recorder.version()} · trace_processor "
          f"{info.get('trace_processor') or 'unknown'} · {Path.cwd()}")

    lines: list[tuple[str, str]] = []
    lines.append(("layer", st["layer_line"].split(": ", 1)[1]))
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
    lines.append(("hunt", hunt_mod.summary_line(st.get("hunt"))))
    tr = st["traces"]
    if tr["count"]:
        lines.append(("traces", f"{tr['count']} in .echolot/traces, newest {_ago(tr['newest'])}"))
    else:
        lines.append(("traces", "none in .echolot/traces"))
    rep = st["report"]
    if rep and not rep.get("error"):
        made = _ago(_iso_epoch(rep.get("generated_at")))
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
        failed = (d.get("facts") or {}).get("failed") or []
        lines.append(("doctor", f"{_ago(_iso_epoch(d.get('ts')))}, "
                      + (f"{len(failed)} check(s) FAILED" if failed else "passed")))
    else:
        lines.append(("doctor", "never run here"))
    width = max(len(k) for k, _ in lines)
    for k, v in lines:
        print(f"{k.ljust(width)}  {v}")
    print(f"{'next'.ljust(width)}  {next_step(st)}")

    # Everything needed to answer "carry on, or start new?" in one call, so
    # the agent asks the human without a second round trip.
    if next_kind(st) == "resume-or-new":
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
    path = GUIDE_DIR / f"{topic}.md"
    if not path.exists():
        available = sorted(p.stem for p in GUIDE_DIR.glob("*.md"))
        print(f"no guide for {topic!r}. There is: {', '.join(available)}",
              file=sys.stderr)
        return 2
    print(path.read_text(encoding="utf-8").rstrip())
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
        touched = mark_mod.remove(root)
        for rel, n in touched:
            print(f"  - {rel}: {n} line(s)")
        print(f"removed markers from {len(touched)} file(s)" if touched
              else "no `echolot:mark` lines found under this root")
        recorder.note(removed_files=len(touched))
        return 0

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
        done = mark_mod.apply(root, pl)
        print()
        for rel, markers in done:
            print(f"  + {rel}: {', '.join(markers)}")
        print(f"applied {sum(len(m) for _, m in done)} marker(s) in {len(done)} file(s); "
              f"every inserted line ends with `{mark_mod.TAG}` — `echolot mark --remove` "
              f"takes them out" if done else "nothing applicable to apply")
        recorder.note(applied=sum(len(m) for _, m in done))
    return 0


def cmd_collect(args) -> int:
    """N repeats of one scenario.

    Repeating is not belt-and-braces. A single run cannot tell a regression
    from a random spike, and both threshold calibration and report aggregation
    stand on the distribution across repeats. Below three there is hardly any
    point.
    """
    from . import runner

    try:
        cfg = Config.load(args.config, args.local)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    section = cfg.runner
    package = cfg.get("project.package") or cfg.process
    iterations = args.iterations or int(section.get("iterations", 5))
    out_dir = _out_dir(args.out, cfg)

    policy = str(section.get("reset_policy", "force-stop"))
    if policy not in ("force-stop", "none"):
        # pm clear changes the scenario rather than repeating it: a cold start
        # with an empty database and a user's cold start are different things.
        print(f"[!] reset_policy: {policy} is not supported. Available: "
              f"force-stop (cold) and none (warm). Using force-stop.",
              file=sys.stderr)

    _note_local(cfg)
    try:
        results = runner.collect(
            package=str(package),
            out_dir=out_dir,
            iterations=iterations,
            section=section,
            device=args.device or section.get("device"),
            name=cfg.scenario_name,
            log=lambda m: print(m, file=sys.stderr),
            # The set pushed aside is the previous round of this same
            # investigation — its baseline, and what the next report is
            # compared against.
            on_set_aside=lambda d: hunt_mod.record_traces(_project_root(cfg), d),
        )
    except runner.RunnerError as e:
        print(f"collection error: {e}", file=sys.stderr)
        return 2

    times = [r["total_time_ms"] for r in results
             if isinstance(r.get("total_time_ms"), int)]
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
    hunt_mod.touch(_project_root(cfg), collect=True)
    return 0


def cmd_init(args) -> int:
    """Installs the .claude/ layer into a project.

    The template ships with the package rather than living in the application
    repository: knowledge of how to use the tool belongs to the tool. What ends
    up in the project is a copy you can edit and commit — for your modules,
    your paths, your style.

    Idempotent, and the one command a person has to know. First time: the
    layer goes in. Any later time: files untouched since install are brought
    up to date, files the project edited are left alone unless `--force`,
    and the environment is checked (`doctor -q`). It ends with the next step,
    the same line `echolot` with no arguments prints.
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
    if chosen is None:
        chosen = hosts_mod.detect(target)
        # Two gates, both required. The parser is the only thing that turns
        # `interactive` on, so the self-check's bare Namespace can never
        # prompt; and even then there has to be a terminal on both ends.
        if getattr(args, "interactive", False) and hosts_mod.interactive(sys.stdout):
            chosen = hosts_mod.pick(chosen)
    hosts_mod.save_choice(target, chosen)

    if not any(h.key == "claude" for h in chosen):
        print("\nClaude Code not selected — .claude/ stays out of this project.")
        _install_pointers(target, chosen)
        print("\nAny agent: `echolot guide`. `echolot init --for all` adds the rest.")
        recorder.note(hosts=[h.key for h in chosen], layer="skipped")
        return 0

    root = target / ".claude"
    before = layer_status(target)
    states = {r["file"]: r["state"] for r in (before or {}).get("rows", [])}

    written, updated, same, kept, overwritten = [], [], [], [], []
    installed: dict[str, str] = {}
    for src in layer_files():
        rel = str(src.relative_to(CLAUDE_DIR))
        dst = root / rel
        state = states.get(rel)
        if dst.exists():
            if state == "current":
                same.append(rel)
                installed[rel] = _sha(src)
                continue
            # Untouched since install and the template moved on: ours to
            # update, no flag needed. Anything the project may have edited
            # (customised, conflict, or differs with no manifest to tell)
            # waits for --force.
            if state == "stale":
                updated.append(rel)
            elif not args.force:
                kept.append(rel)
                continue
            else:
                overwritten.append(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        if rel not in updated:
            written.append(rel)
        installed[rel] = _sha(src)

    for rel in written:
        flag = "!" if rel in overwritten else "+"
        print(f"  {flag} .claude/{rel}" + (
            "  (was edited in the project — overwritten, carry the edits over)"
            if rel in overwritten else ""))
    for rel in updated:
        print(f"  ↑ .claude/{rel} (updated)")
    if same and (written or updated or kept):
        for rel in same:
            print(f"  = .claude/{rel} (current)")
    elif same:
        print(f"  = {len(same)} files current")
    for rel in kept:
        print(f"  ≠ .claude/{rel} (already there and {states.get(rel, 'differs')}, "
              f"untouched)")

    if written or updated or same:
        # Only what was verified against the template goes into the manifest;
        # a file left untouched keeps whatever the old manifest said about it.
        _write_manifest(root, installed)

    if kept:
        print(f"\n{len(kept)} file(s) differ from the template and were kept. "
              f"`echolot init --force` overwrites them; carry your edits over after.")

    _install_pointers(target, chosen)
    recorder.note(hosts=[h.key for h in chosen])
    if before is None:
        print("\nLayer installed.")
    elif written or updated:
        print("\nLayer updated.")
    elif kept:
        print(f"\nLayer current, apart from the {len(kept)} kept above.")
    else:
        print("\nLayer is current.")
    recorder.note(written=len(written), updated=len(updated), kept=len(kept),
                  overwritten=len(overwritten))

    # The environment, briefly, and where to go from here. The doctor lines
    # are the same three `doctor -q` prints; a failure is said and the exit
    # code carries it, but the layer is installed regardless — a broken
    # trace_processor is not a reason to leave the project without the skill.
    if not getattr(args, "no_doctor", False):
        print()
        code = _doctor_quiet(args, toolchain_info(getattr(args, "tp_binary", None)),
                             project=target)
    else:
        code = 0
    if code:
        print("\nnext  echolot doctor — the self-check failed (see above); until it "
              "passes, no report from this environment can be trusted")
    else:
        print(f"\nnext  {next_step(project_state(target))}")
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

    overrides = cfg.detector_overrides
    pooled: dict[str, list[dict]] = {d.id: [] for d in detectors}
    windows: list[float] = []

    for trace in args.traces:
        with TraceSession(trace, tp_binary) as tp:
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


def cmd_doctor(args) -> int:
    """Facts about the environment plus proof that it computes correctly.

    It does not check "are the dependencies installed" — pip fails loudly
    without us, and TraceSession already carries a clear message about a
    missing perfetto. The value is elsewhere. First: the trace_processor
    version becomes visible, and that version defines the vocabulary the
    detectors match on. Second: the self-check on a synthetic trace shows not
    the presence of tools but the correctness of answers.
    """
    import platform as py_platform

    info = toolchain_info(args.tp_binary)
    binary = resolve_binary_path(args.tp_binary)

    if getattr(args, "quiet", False):
        return _doctor_quiet(args, info)

    print("## Environment\n")
    facts = [
        ("python", py_platform.python_version()),
        ("platform", f"{py_platform.system()} / {py_platform.machine()}"),
        ("perfetto", info.get("perfetto_package") or "unknown"),
        ("PyYAML", _pkg_version("PyYAML")),
    ]
    tp_version = info.get("trace_processor") or "unknown"
    if info.get("source") == "--tp-binary":
        tp_version += "  ← custom binary, the requirements.txt pin is bypassed"
    facts.append(("trace_processor", tp_version))
    width = max(len(k) for k, _ in facts)
    for key, value in facts:
        print(f"  {key.ljust(width)}  {value}")
    if binary:
        print(f"\n  binary: {binary}")
        if not args.tp_binary:
            print("  (the name is a SHA-256 prefix: contents verified on download)")

    # Before the self-check, not after: agents run `doctor | head -30`, and
    # forty lines of "ok" pushed this off the screen — a layer that said
    # "there is no runner yet" while the binary had one went unnoticed for a
    # whole session. Not a check that can fail: a project may have edited its
    # copy on purpose.
    layer = _print_layer_status(Path.cwd())
    recorder.note(layer=layer)

    print("\n## Self-check on a synthetic trace\n")
    try:
        from . import selftest
        results = selftest.run(args.tp_binary)
    except Exception as e:
        print(f"  could not run: {e}")
        print("\nThe environment is broken. Until this is fixed, no report "
              "from it can be trusted.")
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


def _doctor_quiet(args, info: dict, project: Path | None = None) -> int:
    """`doctor -q`: three lines, and every failure. Same exit code.

    For a subagent, a CI step, a `| head`: the full report is six kilobytes
    of "ok" that a second reader in the same session pays for again. Here
    the verdicts stay and the evidence goes. `init` calls it too, for the
    project it just installed into.
    """
    import platform as py_platform

    tp = info.get("trace_processor") or "unknown"
    src = " (custom binary)" if info.get("source") == "--tp-binary" else ""
    print(f"echolot {recorder.version()} · trace_processor {tp}{src} · "
          f"perfetto {info.get('perfetto_package') or 'unknown'} · "
          f"python {py_platform.python_version()}")
    layer, line = _layer_line(project or Path.cwd())
    recorder.note(layer=layer)
    print(line)
    try:
        from . import selftest
        results = selftest.run(getattr(args, "tp_binary", None))
    except Exception as e:
        print(f"self-check: could not run — {e}")
        return 1
    failed = [(name, why) for name, why in results if why]
    recorder.note(checks=len(results), failed=[name for name, _ in failed],
                  trace_processor=info.get("trace_processor"))
    if failed:
        print(f"self-check: {len(failed)} of {len(results)} FAIL — reports from "
              f"this environment cannot be trusted")
        for name, why in failed:
            print(f"  FAILS {name}\n          {why}")
        return 1
    print(f"self-check: {len(results)} of {len(results)} passed")
    return 0


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def cmd_explain(args) -> int:
    for d in load_detectors(DETECTOR_DIR):
        print(f"{d.id}\n  {d.title}")
        if d.why:
            print(f"  why: {d.why}")
        if d.params:
            print(f"  params: {d.params}")
        print()
    return 0


def cmd_reflect(args) -> int:
    """The Marker Report over an agent session instead of a trace.

    Reads the agent's transcript (Claude Code for now) plus the tool's own
    `runs.jsonl`, compresses them into facts and signals, and writes
    `.echolot/reflect/<session>.md` and `.json`. Run it from the application
    project the agent worked in — that is where Claude Code keyed the
    transcript, and where echolot.yml and the recorder log live.
    """
    from datetime import datetime, timezone

    from .reflect import claude_code
    from .reflect import facts as facts_mod
    from .reflect import render as reflect_render
    from .reflect import signals as signals_mod

    project = Path(args.project or ".").resolve()
    if args.transcripts:
        tdir = Path(args.transcripts).expanduser()
    else:
        tdir = claude_code.project_dir(project)
    if tdir is None or not tdir.is_dir():
        looked = claude_code.PROJECTS_ROOT / claude_code.slug_candidates(project)[0]
        print(f"error: no Claude Code transcripts for {project}\n"
              f"  looked in: {looked}\n"
              f"  Run from the application project the agent worked in, or "
              f"point --transcripts at the directory.", file=sys.stderr)
        return 2

    since = _parse_since(args.since) if args.since else None
    refs = claude_code.list_sessions(tdir)
    if since is not None:
        refs = [r for r in refs if r.mtime >= since]
    if args.session:
        refs = [r for r in refs if r.id.startswith(args.session)]
        if not refs:
            print(f"error: no session starting with '{args.session}' in {tdir}",
                  file=sys.stderr)
            return 2

    picked = []
    for ref in refs:
        session = claude_code.read_session(ref.path)
        # An explicit id is taken as is; otherwise only sessions that used the
        # tool for real work count — a session that merely ran `reflect` is
        # not worth reflecting on.
        if not args.session and not claude_code.involves_echolot(session):
            continue
        picked.append((ref, session))
        if not (args.all or args.list or args.session or since is not None):
            break   # --last: the newest one is enough

    if not picked:
        print(f"nothing to reflect on: no session under {tdir} used echolot"
              + (f" since {args.since}" if args.since else ""), file=sys.stderr)
        return 1

    if args.list:
        print(f"{'session':10} {'started (UTC)':17} {'dur':>7} {'echolot':>7} "
              f"{'hunt':>4}  first prompt")
        for ref, s in picked:
            subs = claude_code.echolot_subcommands(s)
            hunts = sum(1 for a in s.subagents if a.type == "perf-hunter")
            first = next((t.text for t in s.turns if t.role == "user" and t.kind == "text"), "")
            dur = s.duration_s()
            print(f"{ref.id[:8]:10} {(s.started or '')[:16].replace('T', ' '):17} "
                  f"{_fmt_dur(dur):>7} {len(subs):>7} {hunts:>4}  "
                  f"{' '.join(first.split())[:60]}")
        return 0

    cfg = None
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute() and project != Path.cwd():
        cfg_path = project / cfg_path
    if cfg_path.exists():
        try:
            cfg = Config.load(cfg_path, args.local)
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)
    runs = recorder.read(project / recorder.LOG_FILE)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = project / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    written = []
    for ref, session in picked:
        facts = facts_mod.gather(session, cfg, runs)
        sigs = signals_mod.run(session, facts, cfg)
        rep = reflect_render.build(session, facts, sigs)
        stem = ref.id[:8]
        (out_dir / f"{stem}.json").write_text(
            reflect_render.to_json(rep), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(
            reflect_render.to_markdown(rep), encoding="utf-8")
        written += [out_dir / f"{stem}.md", out_dir / f"{stem}.json"]
        reports.append(rep)

    recorder.note(sessions=len(reports),
                  warn=sum(r["summary"]["signals"].get("warn", 0) for r in reports))

    if len(reports) == 1:
        print(reflect_render.to_markdown(reports[0]))
    else:
        summary = _reflect_summary(reports)
        (out_dir / "summary.md").write_text(summary, encoding="utf-8")
        (out_dir / "summary.json").write_text(json.dumps({
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sessions": [{
                "session": r["source"]["session"], "started": r["context"]["started"],
                "duration_s": r["context"]["duration_s"], "summary": r["summary"],
            } for r in reports],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        written += [out_dir / "summary.md", out_dir / "summary.json"]
        print(summary)

    print("\n" + "\n".join(f"→ {p}" for p in written), file=sys.stderr)
    return 0


def _parse_since(text: str) -> float:
    """`2h`, `30m`, `3d` → epoch seconds of the cut-off."""
    import time as _time
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip())
    if not m:
        raise SystemExit(f"error: --since expects e.g. 2h, 30m, 3d; got '{text}'")
    n, unit = int(m.group(1)), m.group(2)
    return _time.time() - n * {"m": 60, "h": 3600, "d": 86400}[unit]


def _fmt_dur(seconds) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 60}m" if s < 3600 else f"{s // 3600}h{(s % 3600) // 60:02d}"


def _reflect_summary(reports: list[dict]) -> str:
    """Several sessions on one page: a row each, then how often each signal fires."""
    out = ["# Reflect Summary", "",
           f"{len(reports)} session(s), newest first.", ""]
    rows = []
    freq: dict[str, list[int]] = {}
    for r in reports:
        s = r["summary"]
        hunts = r.get("hunts") or []
        rows.append({
            "session": r["source"]["session"][:8],
            "started": (r["context"].get("started") or "")[:16].replace("T", " "),
            "dur": _fmt_dur(r["context"].get("duration_s")),
            "echolot": s["echolot_calls"],
            "hunts": len(hunts),
            "rounds": ", ".join(str(h["rounds"]) for h in hunts) or "—",
            "confidence": ", ".join(str(h.get("confidence") or "?") for h in hunts) or "—",
            "warn": s["signals"].get("warn", 0),
            "warn ids": ", ".join(s.get("warn_ids") or []),
        })
        for sig in r["signals"]:
            freq.setdefault(f"{sig['severity']} {sig['id']}", []).append(1)
    out.append(_md_table(rows))
    out.append("")
    out.append("## Signals by frequency")
    out.append("")
    out.append(_md_table([{"signal": k, "sessions": len(v)}
                          for k, v in sorted(freq.items(), key=lambda kv: (-len(kv[1]), kv[0]))]))
    return "\n".join(out)


def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "_empty_"
    cols = list(rows[0].keys())
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |"
            for r in rows]
    return "\n".join([head, sep, *body])


def _dump(tp, sql: str) -> None:
    rows = tp.query(" ".join(sql.split()))
    if not rows:
        print("_empty_")
        return
    cols = list(rows[0].keys())
    print("| " + " | ".join(cols) + " |")
    print("|" + "|".join("---" for _ in cols) + "|")
    for r in rows:
        print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")


# Who each verb is for. Three audiences share one CLI, and until this was
# structural the split lived only in prose: `echolot --help` showed twelve
# equal verbs, and a person reasonably tried `probe` and got a wall of
# reconnaissance meant for an agent.
# The order verbs are read in, which is neither registration order nor
# alphabetical. A verb missing from here is caught by the self-check.
ORDER = ("status", "init", "hunt", "doctor", "collect", "analyze", "compare",
         "guide", "probe", "names", "domains", "mark", "calibrate", "explain",
         "reflect")

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
    out = ["echolot — a deterministic layer between the trace and the agent.", ""]
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="echolot", description=__doc__,
        # Without Raw, argparse collapses the newlines and the command list in
        # the header congeals into a single paragraph.
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tp-binary", help="path to your own trace_processor_shell")

    # The same flag is also allowed AFTER the subcommand: `doctor --tp-binary X`
    # is how nine people out of ten will write it. SUPPRESS is mandatory, or the
    # subparser overwrites the global value with its own None when the flag is
    # absent.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tp-binary", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    # `echolot` alone is `echolot status`: where things stand, and the next
    # step. The subcommand is not required so that the bare form works.
    sub = p.add_subparsers(dest="cmd", required=False, metavar="<command>",
                           help="one of the above", parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))
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
                          + " | ".join(NEXT_KINDS) + " — what /echolot switches on")
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
    dr = add("doctor", "yours", "", "environment + self-check on a synthetic trace, exit 0/1")
    dr.add_argument("-q", "--quiet", action="store_true",
                    help="three lines and the failures, same exit code — for "
                         "subagents and CI")
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
    nm.set_defaults(func=cmd_names)

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
    mk.add_argument("--apply", action="store_true", help="insert the applicable markers")
    mk.add_argument("--remove", action="store_true",
                    help="delete every line tagged `// echolot:mark` under --root")
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
    ini.add_argument("--force", action="store_true",
                     help="overwrite files the project has edited too")
    ini.add_argument("--for", dest="for_hosts", metavar="CLIENTS",
                     help="which agents to point at the tool: claude, agents, "
                          "cursor, copilot — comma-separated, or `all`. "
                          "Default: whichever this project shows evidence of")
    # Only the parser turns prompting on: cmd_init is also called directly,
    # by the self-check, with a Namespace that has none of these.
    ini.set_defaults(interactive=True)
    ini.add_argument("--no-input", dest="interactive", action="store_false",
                     help="never ask, take the detected set "
                          "(already implied without a terminal)")
    ini.add_argument("--no-doctor", action="store_true",
                     help="skip the environment check at the end")
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
    gd.add_argument("topic", nargs="?",
                    help="overview (default), setup, hunt")
    gd.set_defaults(func=cmd_guide)

    ex = add("explain", "agent", "", "the detectors and their parameters")
    ex.set_defaults(func=cmd_explain)

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
    rf.add_argument("--transcripts", metavar="DIR",
                    help="transcript directory, if not ~/.claude/projects/<slug>")
    rf.add_argument("-c", "--config", default="echolot.yml",
                    help="the project config, for the protocol checks")
    rf.add_argument("--local", help="path to local.yml (defaults to alongside)")
    rf.add_argument("-o", "--out", default=".echolot/reflect")
    rf.set_defaults(func=cmd_reflect)

    p.description = _describe(entries)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Every invocation leaves one line in .echolot/log/runs.jsonl — the tool's
    # own record of what was asked and how it went, independent of whichever
    # agent (or human) was typing. `echolot reflect` reads it later.
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
