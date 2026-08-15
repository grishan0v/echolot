#!/usr/bin/env python3
"""echolot — a deterministic layer between the trace and the agent.

Commands:
  doctor                      environment + self-check on a synthetic trace
  init     --into <project>   install the .claude/ layer (skill, agent, commands)
  collect  -c cfg -n 5        capture N traces of one scenario from a device
  domains  --root <repo>      slice-to-code map and instrumentation coverage
  probe    <trace>            what is inside the trace at all (for setup)
  names    <trace>            how slices are named and what the masks see
  calibrate <trace...>        thresholds from known-healthy runs
  analyze  <trace> -c cfg     run the detectors, build a Marker Report
  explain                     list the detectors and their parameters
  reflect  [--last|--all]     the same kind of report over an agent session:
                              how the tool was used, where it got in the way
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

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


def analyze_trace(trace, cfg: Config, tp_binary: str | None = None) -> dict:
    """The core of a run: trace + config → Marker Report.

    Separate from cmd_analyze because it has two callers: the command, which
    reads the config from a file and writes the report to disk, and the
    self-check, which keeps the config in memory and compares the result with
    expectations. Raises ConfigError.
    """
    detectors = load_detectors(DETECTOR_DIR)
    enabled = cfg.enabled_detectors
    if enabled is not None:
        detectors = [d for d in detectors if d.id in enabled]
    if not detectors:
        raise ConfigError("no detectors selected")

    overrides = cfg.detector_overrides
    results = []

    with TraceSession(trace, tp_binary) as tp:
        procs = _resolve_process(tp, cfg.process)
        _setup_context(tp, cfg, procs[0]["upid"])
        window = _window_info(tp, cfg, procs)

        for d in detectors:
            try:
                sql, params = d.render(overrides.get(d.id))
                rows = tp.query(sql)
                err = None
            except Exception as e:  # SQL is version-fragile — never fail the run
                rows, params, err = [], d.params, str(e)
                print(f"[!] {d.id}: {e}", file=sys.stderr)
            results.append({
                "id": d.id,
                "title": d.title,
                "why": d.why,
                "params": params,
                "rows": rows,
                "error": err,
            })

    return report_mod.build(str(trace), window, results,
                            toolchain=toolchain_info(tp_binary))


def cmd_analyze(args) -> int:
    try:
        cfg = Config.load(args.config, args.local)
        tp_binary = _tp_binary(args, cfg)
        _note_local(cfg)
        # Repeats are merged by median: one outlier must not drag the
        # conclusion along, and the "Runs" column separates the reproducible
        # from the one-off.
        reports = [analyze_trace(t, cfg, tp_binary) for t in args.traces]
        rep = report_mod.aggregate(reports)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    w = rep.get("window") or {}
    recorder.note(
        traces=len(args.traces),
        fired=rep["summary"]["fired_ids"],
        window_ms=w.get("duration_ms"),
        start_anchor_matches=(w.get("start_anchor") or {}).get("matches"),
        process_alternatives=len(w.get("process_alternatives") or []),
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        report_mod.to_json(rep), encoding="utf-8")
    (out_dir / "report.md").write_text(
        report_mod.to_markdown(rep), encoding="utf-8")

    print(report_mod.to_markdown(rep))
    print(f"\n→ {out_dir/'report.md'}\n→ {out_dir/'report.json'}",
          file=sys.stderr)
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
        others = ", ".join(f"{r['name']} ({r['slices']})" for r in rows[1:])
        print(
            f"[!] '{glob}' matched {len(rows)} processes. "
            f"Took {rows[0]['name']} ({rows[0]['slices']} slices). "
            f"Others: {others}",
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
        window["process_alternatives"] = [
            {"name": p["name"], "pid": p["pid"], "slices": p["slices"]}
            for p in procs[1:]
        ]
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

_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")


def _family(name: str) -> str:
    """Collapses names that differ only by numbers.

    'Lock contention on a monitor lock (owner tid: 1234)' and the same with tid
    5678 are one phenomenon. Without this the inventory of a real trace runs to
    thousands of rows.
    """
    return _DIGITS.sub("#", _HEX.sub("0x#", name))


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
    if args.config and Path(args.config).exists():
        try:
            cfg_names = Config.load(args.config, getattr(args, "local", None))
            overrides = cfg_names.detector_overrides
            tp_bin = _tp_binary(args, cfg_names)
        except ConfigError as e:
            print(f"config ignored: {e}", file=sys.stderr)

    with TraceSession(args.trace, tp_bin) as tp:
        try:
            procs = _resolve_process(tp, args.process)
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
            fam = families.setdefault(_family(r["name"]), {
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


CLAUDE_DIR = Path(__file__).parent / "claude"


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
    out_dir = Path(args.out)

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
    return 0


def cmd_init(args) -> int:
    """Installs the .claude/ layer into a project.

    The template ships with the package rather than living in the application
    repository: knowledge of how to use the tool belongs to the tool. What ends
    up in the project is a copy you can edit and commit — for your modules,
    your paths, your style.

    Existing files are left alone: a config may have been hand-tuned, and
    silently overwriting it is worse than not delivering one file.
    """
    target = Path(args.into)
    if not target.is_dir():
        print(f"no such directory: {target}", file=sys.stderr)
        return 2

    written, skipped = [], []
    for src in sorted(CLAUDE_DIR.rglob("*")):
        # Hidden files are skipped: macOS drops .DS_Store straight into the
        # template, and in someone else's repository it looks like our litter.
        if src.is_dir() or src.name.startswith("."):
            continue
        dst = target / ".claude" / src.relative_to(CLAUDE_DIR)
        if dst.exists() and not args.force:
            skipped.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        written.append(dst)

    for path in written:
        print(f"  + {path.relative_to(target)}")
    for path in skipped:
        print(f"  = {path.relative_to(target)} (already there, untouched)")

    if skipped and not args.force:
        print("\nNothing existing was overwritten. To update, use `--force`.")
    if written:
        print(f"\nDone. Next: /echolot-setup — build echolot.yml.")
    return 0


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

    sub = p.add_subparsers(dest="cmd", required=True, parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))

    # Ordered by the working flow rather than by when things were written:
    # first make sure the environment computes correctly, then reconnaissance,
    # then analysis.
    dr = sub.add_parser("doctor", help="check the environment and self-check")
    dr.set_defaults(func=cmd_doctor)

    pr = sub.add_parser("probe", help="reconnaissance over a trace")
    pr.add_argument("trace")
    pr.add_argument("--process", help="process name to break down in detail")
    pr.set_defaults(func=cmd_probe)

    nm = sub.add_parser("names",
                        help="slice name inventory and mask coverage")
    nm.add_argument("trace")
    nm.add_argument("--process", default="*", help="GLOB over the process name")
    nm.add_argument("-c", "--config", default="echolot.yml",
                    help="take overridden masks from the config, if present")
    nm.add_argument("--local", help="path to local.yml (defaults to alongside)")
    nm.add_argument("--top", type=int, default=15,
                    help="how many families to show per section")
    nm.add_argument("--min-ms", type=float, default=1.0,
                    help="relevance floor: shorter families are not shown")
    nm.set_defaults(func=cmd_names)

    dom = sub.add_parser("domains",
                         help="slice-to-code map and instrumentation coverage")
    dom.add_argument("--root", default=".", help="repository root")
    dom.add_argument("--top", type=int, default=12,
                     help="how many modules to list when there is none")
    dom.set_defaults(func=cmd_domains)

    col = sub.add_parser("collect", help="capture N traces of one scenario")
    col.add_argument("-c", "--config", default="echolot.yml")
    col.add_argument("--local", help="path to local.yml (defaults to alongside)")
    col.add_argument("-n", "--iterations", type=int,
                     help="how many repeats (default from runner.iterations)")
    col.add_argument("-o", "--out", default=".echolot/traces")
    col.add_argument("--device", help="device serial, when there are several")
    col.set_defaults(func=cmd_collect)

    ini = sub.add_parser("init", help="install the .claude/ layer into a project")
    ini.add_argument("--into", default=".", help="Android project root")
    ini.add_argument("--force", action="store_true",
                     help="overwrite existing files")
    ini.set_defaults(func=cmd_init)

    cal = sub.add_parser(
        "calibrate", help="derive thresholds from known-healthy traces")
    cal.add_argument("traces", nargs="+",
                     help="repeats of ONE scenario on a healthy build")
    cal.add_argument("-c", "--config", default="echolot.yml")
    cal.add_argument("--local", help="path to local.yml (defaults to alongside)")
    cal.add_argument("--min-sample", type=int, default=10,
                     help="below this many values no threshold is derived")
    cal.set_defaults(func=cmd_calibrate)

    an = sub.add_parser("analyze", help="run the detectors")
    an.add_argument("traces", nargs="+",
                    help="one trace, or repeats of one scenario")
    an.add_argument("-c", "--config", default="echolot.yml")
    an.add_argument("--local", help="path to local.yml (defaults to alongside)")
    an.add_argument("-o", "--out", default=".echolot/out")
    an.set_defaults(func=cmd_analyze)

    ex = sub.add_parser("explain", help="list the detectors")
    ex.set_defaults(func=cmd_explain)

    rf = sub.add_parser(
        "reflect", help="a Marker Report over an agent session — for improving the tool")
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
