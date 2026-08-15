#!/usr/bin/env python3
"""echolot — a deterministic layer between the trace and the agent.

You:
  echolot                     where this project stands, and the next step
  init                        install or update the .claude/ layer; checks the
                              environment; the one command to know
  analyze  <trace...> -c cfg  run the detectors, build a Marker Report — for CI
                              and for looking at traces by hand
  collect  -c cfg -n 5        capture N traces of one scenario from a device
  doctor                      environment + self-check, exit 0/1; -q for three lines

The agent (through /echolot-setup and /echolot-hunt in Claude Code):
  probe    <trace>            what is inside the trace at all
  names    <trace>            how slices are named and what the masks see
  domains  --root <repo>      slice-to-code map and instrumentation coverage
  calibrate <trace...>        thresholds from known-healthy runs
  explain                     the detectors and their parameters

Improving the tool:
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

    st["last_doctor"] = st["last_analyze"] = None
    for run in recorder.read(project / recorder.LOG_FILE):
        if run.get("cmd") == "doctor":
            st["last_doctor"] = run
        elif run.get("cmd") == "analyze":
            st["last_analyze"] = run
    return st


def next_step(st: dict) -> str:
    """One line: what to do next, from the state. Shared by status and init."""
    if st["layer_verdict"] == "absent":
        return "echolot init — installs the .claude/ layer; then /echolot-setup in Claude Code"
    if st["layer_verdict"] == "stale":
        return "echolot init — brings the .claude/ layer up to date (the agent reads it)"
    if st["layer_verdict"] == "differs":
        return ("echolot init --force — the .claude/ layer differs from the package's and "
                "nothing says whether you edited it; --force overwrites, keep your edits with git")
    d = st.get("last_doctor")
    if d and d.get("facts", {}).get("failed"):
        return "echolot doctor — the last self-check failed; no report is trustworthy until it passes"
    cfg = st.get("config")
    if not cfg:
        return "/echolot-setup in Claude Code — builds echolot.yml from the repository and a probe trace"
    if cfg.get("error"):
        return f"fix echolot.yml — it does not load: {cfg['error']}"
    if not st["traces"]["count"]:
        return "/echolot-hunt in Claude Code (it captures traces), or: echolot collect -c echolot.yml -n 5"
    return ("/echolot-hunt in Claude Code, or by hand: "
            "echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml")


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


def cmd_status(args) -> int:
    """`echolot` with nothing after it: where things stand, and the next step.

    The tool can tell a first visit from a return: is the layer here and
    current, is there a config, are there traces, when did doctor last pass.
    That fork used to live in the README as prose; now the tool prints the
    branch that applies. Two commands are all a person needs to know —
    `echolot init` and `echolot` — and the agent knows the rest.
    """
    st = project_state(Path.cwd(), getattr(args, "config", "echolot.yml"))
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
    sub = p.add_subparsers(dest="cmd", required=False, parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))
    p.set_defaults(func=cmd_status, cmd="status")

    stt = sub.add_parser("status", help="where this project stands, and the next step")
    stt.add_argument("-c", "--config", default="echolot.yml", help=argparse.SUPPRESS)
    stt.set_defaults(func=cmd_status)

    # Ordered by the working flow rather than by when things were written:
    # first make sure the environment computes correctly, then reconnaissance,
    # then analysis.
    dr = sub.add_parser("doctor", help="check the environment and self-check")
    dr.add_argument("-q", "--quiet", action="store_true",
                    help="three lines and the failures, same exit code — for "
                         "subagents and CI")
    dr.set_defaults(func=cmd_doctor)

    pr = sub.add_parser("probe", help="reconnaissance over a trace")
    pr.add_argument("trace")
    pr.add_argument("--process", help="process name to break down in detail")
    pr.set_defaults(func=cmd_probe)

    nm = sub.add_parser("names",
                        help="slice name inventory and mask coverage")
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

    ini = sub.add_parser(
        "init", help="install or update the .claude/ layer, check the environment",
        description="The one command to know. First time: installs the .claude/ "
                    "layer. Later: brings untouched files up to date, keeps the "
                    "ones you edited, runs the environment check, and says what "
                    "to do next.")
    ini.add_argument("--into", default=".", help="Android project root")
    ini.add_argument("--force", action="store_true",
                     help="overwrite files the project has edited too")
    ini.add_argument("--no-doctor", action="store_true",
                     help="skip the environment check at the end")
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

    an = sub.add_parser(
        "analyze", help="run the detectors",
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
