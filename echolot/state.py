"""Where a project stands with echolot, and what to do next.

`echolot` with no arguments and `/echolot` in an agent both come here. The
question is the same for both — is the layer installed, is there a config, are
there traces, did doctor pass, is an investigation open — and the answer is one
word from NEXT_KINDS that a caller can switch on.

The facts and the decision are kept apart on purpose. `project_state` gathers
and judges nothing; `next_kind` judges and reads nothing from disk. That is
what lets the self-check pin the routing over made-up states instead of
building a project on disk for every case.
"""

from __future__ import annotations

import json
import math
import os
import shlex
from pathlib import Path, PurePath

from . import codex, hosts, layer, plugin, recorder, runner
from . import hunt as hunt_mod
from .config import Config, ConfigError


def project_state(project: Path, config: str = "echolot.yml",
                  here: Path | None = None) -> dict:
    """Where this project stands with echolot: the facts `status` and `init`
    decide the next step from.

    `here` is the folder the lines to run by hand are run from: the working
    directory of the command that prints them. Without it they are run from
    the project's folder, as the page offers them.

    Everything here is read from disk and the run log; nothing runs.
    """
    st: dict = {"project": project, "shown": shown_from(project, config, here)}
    st["layer_verdict"], st["layer_line"] = layer.one_line(project)
    # The `init` a project with echolot's plugin runs, where the plugin is
    # found: plain `init` beside it installs every skill twice (#598).
    st["plugin_init"] = plugin.init_for(project)
    # And where it is found, but not in Claude Code: there `--for plugin`
    # leaves Claude Code with no skills (#740).
    st["plugin_claude_left_out"] = plugin.claude_left_out(project)
    st["plugin_hosts"] = (layer.plugin_hosts(project)
                          if st["plugin_init"] or st["plugin_claude_left_out"] else None)
    st["hosts"] = hosts.load_choice(project)
    # Where Codex is used: whether a rule lets echolot out of its sandbox.
    said = codex.one_line(project, hosts.keys(project))
    st["codex_line"] = said[1] if said else None
    # Where Cursor has a copy of the plugin from another release: a copy does
    # not follow an upgrade of echolot (#399).
    st["cursor_line"] = plugin.stale_line()

    cfg_path = project / config
    st["config"] = None
    # Which question all of the below is about. None is a normal answer:
    # every project predates its first investigation.
    st["hunt"] = hunt_mod.load(project)
    # One that is there and does not load is said as that, not as none (#580).
    st["hunt_unreadable"] = hunt_mod.unreadable_line(project)
    # Where the latest report is: the config's, per scenario when it names
    # several, else `.echolot/out` as always.
    report_dir = ".echolot/out"
    loaded: Config | None = None
    if cfg_path.exists():
        try:
            cfg = loaded = Config.load(cfg_path)
            refused_by_analyze(cfg)
            # The top-level section, every scenario's; a scenario's own
            # thresholds are said on its line.
            calibrated = bool(cfg.shared_overrides)
            st["config"] = {
                "path": cfg_path, "scenario": cfg.scenario_name,
                "scenarios": cfg.scenarios,
                "process": cfg.get("project.process") or cfg.get("project.package"),
                "thresholds": "from the config" if calibrated else "built-in defaults",
                "local": cfg.local_path is not None,
                "runner": str(cfg.runner.get("mode", "launch")) if cfg.runner else None,
                "sha": cfg.sha,
                "confirmed": cfg.confirmed(),
                "temp_prefix": cfg.get("instrumentation.temp_prefix"),
            }
            # The scenario the next step is about: the open investigation's,
            # else the first. Everything below that names one scenario's
            # traces or report names this one's.
            hunt = st["hunt"] or {}
            theirs = hunt.get("scenario") if hunt.get("status", "open") == "open" else None
            if theirs in cfg.scenarios:
                cfg.select(theirs)
            st["config"]["scenario"] = cfg.scenario_name
            # The runner of that scenario: read before the choice, it was
            # the first scenario's, or the top level's under a scenario that
            # has a runner of its own (#501).
            st["config"]["runner"] = (str(cfg.runner.get("mode", "launch"))
                                      if cfg.runner else None)
            report_dir = cfg.report_dir
        except ConfigError as e:
            st["config"] = {"path": cfg_path, "error": str(e)}
            loaded = None

    traces_dir = project / ".echolot" / "traces"
    # Each modification time read once, and a file that will not stat left
    # out: a dangling symlink matched the glob and ended `echolot` with a
    # traceback, and so could a trace `collect` moved aside in between.
    stamps: dict[Path, float] = {}
    for pat in ("*.perfetto-trace", "*.pftrace"):
        for p in (traces_dir.glob(pat) if traces_dir.is_dir() else []):
            try:
                stamps[p] = p.stat().st_mtime
            except OSError:
                continue
    # The earlier rounds `collect` and `hunt` moved into subdirectories of
    # their own. Globbed at the top only, eleven of them read as "none"
    # (#501).
    sets: dict[str, float] = {}
    for d in (traces_dir.iterdir() if traces_dir.is_dir() else []):
        try:
            if not d.is_dir() or d.is_symlink():
                continue
            held = [f.stat().st_mtime for pat in ("*.perfetto-trace", "*.pftrace")
                    for f in d.glob(pat)]
        except OSError:
            continue
        if held:
            sets[d.name] = max(held)
    # The repeats of the config's scenario, named apart: the directory keeps
    # every scenario's set side by side, and the next step names this one's
    # alone.
    scenario = (st.get("config") or {}).get("scenario")
    mine = sorted(f".echolot/traces/{p.name}" for p in stamps
                  if (m := runner._ITERATION.match(p.name))
                  and m.group("scenario") == scenario) if scenario else None
    st["traces"] = {"dir": traces_dir, "count": len(stamps),
                    "scenario": None if mine is None else len(mine),
                    "files": mine,
                    "newest": max(stamps.values(), default=None),
                    "sets": len(sets),
                    "last_set": max(sets, key=lambda k: sets[k]) if sets else None}
    st["starts"] = starts(list(stamps), scenario, mine) if mine is not None else None
    st["collect"] = collect_state(project)

    st["report"] = _report(project / report_dir / "report.json")
    # A config that names several scenarios: each one's traces and latest
    # report, for `status` to list. None for one scenario, which prints as
    # it always has.
    st["scenarios"] = None
    st["old_report"] = None
    if loaded is not None and loaded.get("scenarios") is not None:
        focus = loaded.scenario_name
        st["scenarios"] = []
        for name in loaded.scenarios:
            loaded.select(name)
            files = sorted(f".echolot/traces/{p.name}" for p in stamps
                           if (m := runner._ITERATION.match(p.name))
                           and m.group("scenario") == name)
            st["scenarios"].append({
                "name": name, "focus": name == focus, "files": files,
                "own_thresholds": bool(loaded.scenario_overrides),
                "report": _report(project / loaded.report_dir / "report.json")})
        loaded.select(focus)
        # The report a single-scenario config left at `.echolot/out/`, from
        # before it named several: they go to `.echolot/out/<scenario>/`
        # now, and its scenario read "no report yet" with it one level up
        # (#501). Named on its scenario's line, with where it belongs.
        st["old_report"] = _old_report(project, [sc["name"] for sc in st["scenarios"]])

    # The last self-check, whichever command ran it: `init` runs one too and
    # its facts carry `checks` like a doctor's. Read off `doctor` alone, a
    # passing check inside `init` left `next` pointing at the doctor before it.
    st["last_doctor"] = st["last_analyze"] = None
    for run in recorder.read(project / recorder.LOG_FILE):
        if run.get("cmd") == "doctor" or "checks" in (run.get("facts") or {}):
            st["last_doctor"] = _self_check(run)
        elif run.get("cmd") == "analyze":
            st["last_analyze"] = run
    return st


def _self_check(run: dict) -> dict:
    """A self-check's line with the facts `status`, `next` and the start page
    read off it only as doctor writes them: `failed` a list, `checks` a
    count, `sandbox` a name. A `failed` that is a number was counted with
    `len`, and a `sandbox` that is a list looked up as a key (#555)."""
    facts = run.get("facts")
    if not facts:
        return run
    kinds = {"failed": list, "checks": int, "sandbox": str}
    kept = {k: v for k, v in facts.items()
            if k not in kinds or _holds(v, kinds[k])}
    return {**run, "facts": kept}


def _report(rep: Path) -> dict | None:
    """What `status` says of a report.json: when, how many runs, what fired.

    None when there is none; `error` when it is there and does not read.
    That is a file that is no JSON; one that is JSON and no object, where
    the first key read off a list, a number or a string ended `echolot` in a
    traceback (#460); and one that holds, in a field read here, what
    `analyze` never writes there (`_as_written`), or detectors and rows
    that `report` and `compare` cannot read (`misshapen`, #555).
    """
    if not rep.exists():
        return None
    try:
        r = json.loads(rep.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        r = None
    if not isinstance(r, dict) or not _as_written(r) or ("detectors" in r and misshapen(r)):
        return {"path": rep, "error": "unreadable"}
    s, c = r.get("summary") or {}, r.get("config") or {}
    return {
        "path": rep, "generated_at": r.get("generated_at"),
        "fired": s.get("detectors_fired"), "run": s.get("detectors_run"),
        "runs": len(r.get("traces") or []) or 1,
        "config_sha": c.get("sha"), "defaults": c.get("defaults"),
        # Which echolot wrote it, for `status` to say when it was another
        # (#507). Text, or nothing for a report from before it was kept.
        "echolot": _stamp(r.get("toolchain")),
        "window": window_warning(r.get("window")),
        "scenario": r.get("scenario") if isinstance(r.get("scenario"), str) else None,
    }


def _stamp(toolchain) -> str | None:
    """`toolchain.echolot` when it is text; None for anything else."""
    v = toolchain.get("echolot") if isinstance(toolchain, dict) else None
    return v if isinstance(v, str) else None


def window_warning(w: object) -> str | None:
    """The first thing report.md warns about the window, in a few words; None
    when its window is the scenario's.

    The summary is what an agent decides on, and a report whose end anchor
    matched in none of five traces, its window the whole trace, read there
    as healthy as any other (#501). The same checks report.md opens with:
    an anchor that matched nowhere or in some repeats only, an end that
    never closed, and repeats whose windows differ several-fold.
    """
    if not isinstance(w, dict):
        return None
    for key, label in (("start_anchor", "start"), ("end_anchor", "end")):
        a = w.get(key)
        if not isinstance(a, dict):
            continue
        missed = a.get("missed")
        if isinstance(missed, str) and missed:
            return f"{label} anchor not found in {missed} runs: their window is the whole trace"
        if a.get("matches") == 0:
            return f"{label} anchor not found: the window is the whole trace"
        if a.get("unfinished") is True:
            return f"{label} anchor never closed: the window runs to the end of the trace"
    low, high = w.get("duration_ms_min"), w.get("duration_ms_max")
    if (isinstance(low, (int, float)) and isinstance(high, (int, float))
            and not isinstance(low, bool) and not isinstance(high, bool)
            and low > 0 and high > 2 * low):
        return f"repeat windows diverged {high / low:.1f}x"
    return None


def _old_report(project: Path, names: list[str]) -> dict | None:
    """`.echolot/out/report.json` left by a config that named one scenario,
    when the config now names several and that scenario has no report of
    its own yet: which scenario it is, and where it belongs."""
    rep = _report(project / ".echolot" / "out" / "report.json")
    if not rep or rep.get("error") or rep.get("scenario") not in names:
        return None
    own = project / ".echolot" / "out" / rep["scenario"] / "report.json"
    if own.exists():
        return None
    return rep


def _as_written(r: dict) -> bool:
    """Whether each field `_report` reads holds what `analyze` writes there,
    or nothing: an object for `summary` and `config`, a list for `traces`,
    text for `generated_at` and `config.sha`, a whole number for each count
    of detectors, and true or false for `config.defaults`. Those are the
    types it has written since the first release.

    A summary that is a list, a count that is text, a `generated_at` that
    is a number: each ended `echolot`, `status --next` or the start page of
    `echolot ui` in a traceback (#470), and a file that got through printed
    runs no analysis made: `"traces": "ab"` read as two. Such a file was
    edited by hand or written by another tool under this name, and it is
    said the way a file that is no JSON is: it does not read, run analyze
    again. Nothing in it is shown, since what still reads describes no
    analysis either.
    """
    s, c = r.get("summary"), r.get("config")
    return (_holds(s, dict) and _holds(c, dict) and _holds(r.get("traces"), list)
            and _holds(r.get("generated_at"), str)
            and _holds((s or {}).get("detectors_fired"), int)
            and _holds((s or {}).get("detectors_run"), int)
            and _holds((c or {}).get("sha"), str)
            and _holds((c or {}).get("defaults"), bool))


def misshapen(data: dict) -> str | None:
    """The first part of a report that does not hold what `analyze` writes
    there, in words, or None.

    `analyze` writes `detectors` as a list of objects, each with its `id` as
    text and its `rows` as a list of objects, and `markers` as an object
    whose `rows` and `absent` are lists. Anything else came from a hand edit
    or another tool, and ended `report`, `report --html`, `compare` and the
    report page of `echolot ui` in a traceback or a 500 (#555): `"detectors":
    "x"` read a letter as a detector, `"rows": {"a": 1}` asked for row 0.
    """
    dets = data.get("detectors")
    if not isinstance(dets, list):
        return f"`detectors` is {kind_of(dets)}, not a list"
    for i, d in enumerate(dets):
        if not isinstance(d, dict):
            return f"detector {i + 1} is {kind_of(d)}, not an object"
        name = d.get("id")
        if not isinstance(name, str):
            return f"detector {i + 1} has no `id` as text"
        rows = d.get("rows")
        if not isinstance(rows, list):
            return f"the rows of {name} are {kind_of(rows)}, not a list"
        if not all(isinstance(r, dict) for r in rows):
            return f"a row of {name} is not an object"
    m = data.get("markers")
    if m is None:
        return None
    if not isinstance(m, dict):
        return f"`markers` is {kind_of(m)}, not an object"
    for key in ("rows", "absent"):
        if m.get(key) is not None and not isinstance(m[key], list):
            return f"`markers.{key}` is {kind_of(m[key])}, not a list"
    if not all(isinstance(r, dict) for r in m.get("rows") or []):
        return "a row of `markers` is not an object"
    return None


def kind_of(value: object) -> str:
    """What a JSON value is, in the words a reader of the file would use."""
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "true or false"
    return {dict: "an object", list: "a list", str: "text", int: "a number",
            float: "a number"}.get(type(value), type(value).__name__)


def _holds(value: object, kind: type) -> bool:
    """`value` is a `kind`, or there is none. True and false are no count,
    though Python takes a bool for an int."""
    if value is None:
        return True
    if isinstance(value, bool):
        return kind is bool
    return isinstance(value, kind)


def starts(traces: list[Path], scenario: str, files: list[str]) -> dict | None:
    """How the repeats of the scenario's set started, and which recordings of
    it were set aside as not cold, from what `collect` kept beside each
    (#619); None when it kept nothing of the kind, as for a set it recorded
    before it kept this, or a trace it did not record.

    Read off the files, with no device: the round's output is gone by the
    time anyone asks, cut by `tail` or scrolled away, and recording the
    round again was the one way left to read it back.
    """
    by_name = {p.name: p for p in traces}
    states: list[str | None] = []
    for f in files:
        rec = runner.launch_record(by_name.get(Path(f).name, Path(f)))
        if rec is not None:
            states.append(rec.get("state") or None)
    aside = []
    for p in sorted(traces, key=lambda p: p.name):
        m = runner._ASIDE.match(p.name)
        rec = runner.launch_record(p) if m and m.group("scenario") == scenario else None
        if rec is not None and rec.get("cold") is False:
            aside.append({"name": p.name, "state": rec.get("state")})
    if not aside and not any(states):
        return None
    return {"scenario": scenario, "repeats": len(files), "states": states, "aside": aside}


def starts_line(st: dict) -> str | None:
    """The `starts` line of `status`: the launch state of each repeat in the
    scenario's set, counted, and the recordings set aside, named (#619)."""
    s = st.get("starts")
    if not s:
        return None
    from collections import Counter
    counts = Counter(s["states"])
    bits = [f"{n} {state or 'with no launch state'}" for state, n in counts.most_common()]
    if s["repeats"] > len(s["states"]):
        bits.append(f"{s['repeats'] - len(s['states'])} recorded before collect kept it")
    line = f"{s['scenario']}: " + ", ".join(bits)
    aside = s["aside"]
    if aside:
        shown = [f"{a['name']}" + (f" ({a['state']})" if a.get("state") else "")
                 for a in aside[:3]]
        more = f" and {len(aside) - 3} more" if len(aside) > 3 else ""
        line += f" · set aside, not cold: {', '.join(shown)}{more}"
    return line


def collect_state(project: Path) -> dict | None:
    """Where the last `collect` stands, from the file it writes as it runs.

    Four answers: running, interrupted, failed, or nothing worth saying. A
    run whose process is gone and whose file says it never finished was
    interrupted — the terminal closed, the agent's harness killed it — and
    that reads differently from a failure that said why.

    A file that does not parse, is no object, or holds in a field `status`
    reads what `collect` never writes there (`_as_collect_writes`) is
    nothing worth saying.
    """
    from .runner import PROGRESS_FILE
    path = project / PROGRESS_FILE
    if not path.exists():
        return None
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(p, dict) or not _as_collect_writes(p):
        return None
    if p.get("finished") is None:
        pid = p.get("pid")
        alive = isinstance(pid, int) and _alive(pid)
        p["status"] = "running" if alive else "interrupted"
    else:
        p["status"] = "failed" if p.get("exit") else "done"
    return p


# The largest process id `os.kill` takes: it hands the number on as a C int.
_PID_MAX = 2**31 - 1


def _as_collect_writes(p: dict) -> bool:
    """Whether each field read off the progress file holds what `collect`
    writes there, or nothing: a time for `started`, which it always writes,
    and for `finished` once the run ended; a process id for `pid`; a whole
    number for `done` and `exit`, and for `hunt`, the investigation the run
    was started inside; text for `scenario`, `cooling` and `error`. Every
    echolot that writes this file, 0.8.0 on, writes those: the times from
    `time.time()`, the process id from `os.getpid()`.

    Text in `started`, or in `finished` with `exit` set, ended `echolot` and
    the start page of `echolot ui` in a traceback, and so did `Infinity`,
    which Python's json reads and no clock writes. A pid past what `os.kill`
    takes ended `status --next` as well, and 0 or -1 read as a collect still
    running, since `os.kill` then asks the process's own group or every
    process (#474). Such a file was edited by hand or written by another
    tool under this name. It says nothing about a collect, and it reads as
    a file that does not parse does: as none. The next `collect` writes it
    anew.

    `iterations` is not held to a type: `collect` writes the count it was
    handed before it checks it, so `runner.iterations: five` leaves `"five"`
    in a run that failed at once, and the failure's line says why.
    """
    pid = p.get("pid")
    return (_moment(p.get("started"))
            and (p.get("finished") is None or _moment(p["finished"]))
            and (pid is None or (_holds(pid, int) and 0 < pid <= _PID_MAX))
            and _holds(p.get("done"), int) and _holds(p.get("exit"), int)
            and _holds(p.get("hunt"), int)
            and all(_holds(p.get(k), str) for k in ("scenario", "cooling", "error")))


def _moment(value: object) -> bool:
    """A time as `time.time()` gives it: a number of seconds a float holds.
    Not true or false, which Python takes for numbers, nor what json reads
    and no clock writes: `Infinity` and `NaN`, and a whole number too long
    to become a float, which `when.ago` subtracts from one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _alive(pid: int) -> bool:
    """Whether a process with this pid is still running.

    Signal 0 asks that on POSIX. On Windows 0 is `CTRL_C_EVENT`, and
    `os.kill` sends it to the console's processes instead of asking
    anything, so there the process is opened and its exit code read.
    """
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) \
                and code.value == 259                    # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def collect_line(st: dict) -> str | None:
    """The `collect` line of `status`, or None when the traces line says it all.

    A run in flight is the one thing an agent waiting on a background task
    wants to know, and until this line the answer was the trace count from
    the run before. A failure is shown while it is the latest word — once
    a newer set of traces exists it is history, and the traces line speaks.
    """
    p = st.get("collect")
    if not p:
        return None
    from . import when
    status = p.get("status")
    scenario = p.get("scenario") or "the scenario"
    if status == "running":
        if p.get("iterations"):
            where = f"{p.get('done', 0)}/{p['iterations']} iterations"
        else:
            where = "the macrobenchmark drives the iterations"
        # Between two repeats, waiting for `runner.cooldown`: minutes in
        # which nothing else moves, and which read as a hang without it.
        cooling = f", cooling: {p['cooling']}" if p.get("cooling") else ""
        return f"running: {scenario}, {where}{cooling}, started {when.ago(p.get('started'))}"
    if status == "interrupted":
        done = f" at {p.get('done', 0)}/{p['iterations']}" if p.get("iterations") else ""
        return (f"interrupted{done}, started {when.ago(p.get('started'))} — "
                f"the process is gone and the run never finished")
    if status == "failed":
        newest = (st.get("traces") or {}).get("newest")
        if newest and p.get("finished") and newest > p["finished"]:
            return None
        why = p.get("error") or "no reason recorded"
        return f"failed {when.ago(p.get('finished'))}: {why}"
    return None


# The next step as one word — what `/echolot` in Claude Code switches on —
# and as the line a person reads, both from the same decision. This is the
# vocabulary, not the routing order — `next_kind` below is that. The
# order here is the one `status --help` lists, and it is also what keeps that
# help the same under Rich and under argparse at 80 columns: argparse breaks a
# line at a hyphen and Rich does not, so a hyphenated word that lands at a
# line's end renders two ways (tests/test_cli_help.py). Adding a word, check
# that test before settling where it goes.
NEXT_KINDS = ("upgrade", "init", "init-force", "doctor", "setup", "fix-config",
              "resume-or-new", "fix-settings", "hunt")


def next_kind(st: dict) -> str:
    # `opted-out` falls through on purpose: nothing to install and nothing
    # wrong, so the next step is whatever the config says.
    #
    # Before everything, a layer a newer echolot wrote. The agent is about
    # to read files that describe what that release can do, every step
    # after this one would be taken with the older one, and `init` refuses
    # to touch it — so there is nothing to run, and a person upgrades.
    if st["layer_verdict"] == "newer":
        return "upgrade"
    # Then what `init` does on its own. The agent is about to read this
    # layer, and `init` touches nothing the project edited, so there is
    # nothing to ask.
    # Not in a directory that is only a copy of the config, for an analysis
    # away from the project: `init` would install the layer into a scratch
    # directory (#501).
    if st["layer_verdict"] == "stale" or (st["layer_verdict"] == "absent"
                                          and not (st.get("config")
                                                   and config_copy_only(st["project"]))):
        return "init"
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
    # Two things about the layer only a person can settle, asked last and
    # before a hunt rather than first, because neither answer may stop the
    # work: files edited here that `--all` would overwrite (keep them, and the
    # hunt goes on), and a settings.json that does not parse (fix it or not,
    # the hunt goes on). Asked first, "keep my edits" had nowhere to lead —
    # `next` said `init-force` again — and an unreadable settings.json read
    # as stale sent the agent round `init`, which cannot fix it, for good.
    if st["layer_verdict"] == "differs":
        return "init-force"
    if st["layer_verdict"] == "unreadable":
        return "fix-settings"
    return "hunt"


def whose_sandbox(host: str) -> str:
    """The `sandbox` fact doctor logs, as the words `status` and `next` use."""
    return {"codex": "Codex's sandbox", "cursor": "Cursor's sandbox"}.get(
        host, "the agent's sandbox")


# What a project's own checkout holds at its root, and a directory holding
# only a copy of `echolot.yml` does not.
_PROJECT_FILES = ("settings.gradle", "settings.gradle.kts", "build.gradle",
                  "build.gradle.kts", "gradlew", ".git", ".claude")


def config_copy_only(project: Path) -> bool:
    """A directory with no `.claude/`, no checkout and no build of its own:
    a copy of the config, for reading traces away from the project."""
    try:
        return not any((project / name).exists() for name in _PROJECT_FILES)
    except OSError:
        return False


def door(st: dict) -> str:
    """Which way in this project chose: `claude`, `plugin` or `any`.

    One decision for every line that names it — `status`, `init` and `hunt`
    — so a project set up for the plugin is not told about any agent in one
    place and the plugin's skill in another.
    """
    if st.get("layer_verdict") == "opted-out":
        return "plugin" if "plugin" in (st.get("hosts") or []) else "any"
    return "claude"


def _door(st: dict) -> str:
    """How this project's agent is reached, in its own words.

    Leading with "/echolot in Claude Code" on a project that has just declined
    the layer names a command its human does not have.
    """
    return {"plugin": "the plugin's echolot skill, or `echolot guide",
            "any": "`echolot guide"}.get(door(st), "/echolot in Claude Code, or `echolot guide")


def hunt_door(st: dict) -> str:
    """The way to the loop, for the line `echolot hunt` closes with."""
    return {"plugin": "the plugin's echolot skill, or any agent after `echolot guide hunt`",
            "any": "any agent, after `echolot guide hunt`"}.get(
        door(st), "`/echolot` in Claude Code, or any agent after `echolot guide hunt`")


def next_step(st: dict) -> str:
    """One line: what to do next, from the state. Shared by status and init."""
    kind = next_kind(st)
    if kind == "upgrade":
        return (f"upgrade echolot: {layer.UPGRADE}, then `echolot` again — "
                f"until then this one leaves the .claude/ layer alone (the "
                f"layer line says why)")
    if kind == "init":
        if st["layer_verdict"] == "absent":
            if st.get("plugin_init"):
                line = (f"{st['plugin_init']} — the echolot plugin is in "
                        f"{st['plugin_hosts']} and brings the skills, so .claude/ "
                        f"stays out; then the plugin's echolot skill")
                if st.get("plugin_claude_left_out"):
                    # Which agent runs this is not known here, and the two
                    # want different commands: both, and which is whose.
                    line += (". In Claude Code, which has no echolot plugin: "
                             "`echolot init`, which installs the .claude/ layer it reads")
                return line
            if st.get("plugin_claude_left_out"):
                # In Claude Code, with the plugin in another agent only.
                return (f"echolot init — installs the .claude/ layer for Claude "
                        f"Code, which has no echolot plugin (it is in "
                        f"{st['plugin_hosts']}); then /echolot in Claude Code")
            # Nothing found is no proof there is no plugin: a person who
            # has it reads which line is theirs.
            return ("echolot init — installs the .claude/ layer; then /echolot in "
                    "Claude Code. With the echolot plugin installed: `echolot init "
                    "--for plugin` instead, which keeps .claude/ out")
        return "echolot init — brings the .claude/ layer up to date (the agent reads it)"
    if kind == "doctor":
        # The fact `echolot`'s doctor line reads, read the same way. A
        # self-check that never started is logged with `checks: 0` beside its
        # one `failed` entry (main.NOT_RUN): nothing in it was checked, so
        # nothing failed — it did not run.
        facts = (st.get("last_doctor") or {}).get("facts") or {}
        if facts.get("checks") == 0 and facts.get("sandbox"):
            return (f"run echolot outside {whose_sandbox(facts['sandbox'])}, "
                    f"then `echolot doctor` — the last self-check could not "
                    f"get trace_processor a port on localhost")
        what = "did not run" if facts.get("checks") == 0 else "failed"
        return (f"echolot doctor — the last self-check {what}; no report is "
                f"trustworthy until it passes")
    if kind == "setup":
        return (f"{_door(st)} setup` — echolot.yml from the repository "
                f"and a probe trace")
    if kind == "fix-config":
        return f"fix {_config_name(st)} — it does not load: {st['config']['error']}"
    if kind == "resume-or-new":
        # The question itself is on the hunt line and heads the recap below:
        # said a third time here, it was the longest thing on screen (#501).
        n = (st.get("hunt") or {}).get("n")
        which = f"investigation #{n}" if n else "the open investigation"
        return (f'{_door(st)}` — the agent asks whether to carry on with {which} '
                f'or start a new one')
    if kind == "init-force":
        return ("echolot init --all — overwrites .claude/ files that may carry "
                "edits made here (`echolot init` lists them): ask first, keep "
                "the edits with git. Then: " + _hunt_step(st))
    if kind == "fix-settings":
        return ("fix .claude/settings.json by hand — echolot cannot add its "
                "permission to a file that does not parse (the layer line "
                "says what to merge in). Then: " + _hunt_step(st))
    return _hunt_step(st)


def _config_name(st: dict) -> str:
    """The config's file name, as the `config` line of `status` says it."""
    path = (st.get("config") or {}).get("path")
    return Path(path).name if path else "echolot.yml"


def _hunt_step(st: dict) -> str:
    # While a collect runs, the step by hand is to wait for it: a second
    # `collect` on the same phone is what the line offered (#501).
    if (st.get("collect") or {}).get("status") == "running":
        return (f"{_door(st)} hunt` — or by hand: wait for the running collect "
                f"to finish (the collect line says how far it got)")
    return f"{_door(st)} hunt` — or by hand: {by_hand(st)[1]}"


def refused_by_analyze(cfg: Config) -> None:
    """Raise the ConfigError `analyze` would stop on before it opens a trace.

    `status` checked that the file loads, and `analyze` checks the fields it
    is about to use: an empty file, `budget_ms: "abc"`, a threshold given as
    text or an anchor block with no name read as a sound config with the
    usual next step, and `analyze` then refused it (#583). These checks need
    no trace, so they run here too, for every scenario, the ones not being
    measured included.
    """
    from .main import DETECTOR_DIR, plan_detectors  # main imports this module
    from .tp import load_detectors

    if not cfg.raw:
        raise ConfigError("the file is empty: it names no app and no scenario")
    shipped = load_detectors(DETECTOR_DIR)
    for name in cfg.scenarios:
        one = Config(cfg.raw, cfg.path, cfg.local_path).select(name)
        _ = one.process, one.scenario_start, one.scenario_end, one.scenario_budget_ms
        plan_detectors(one, detectors=shipped)


def shown_from(project: Path, config: str = "echolot.yml",
               here: Path | None = None) -> dict:
    """How a line to run names the config and the project's files: `config`
    for `-c`, and `base`, the project's folder seen from `here`, before each
    `.echolot/...` path; "" when `here` is the project's folder.

    The lines said `-c echolot.yml` and `.echolot/traces/...` whatever
    config was given and wherever the command ran: with `perf.yml`, or from
    `app/` with `-c ../echolot.yml`, the line pasted back failed with
    "config not found" (#719).
    """
    def rel(target: Path, start: Path) -> str:
        try:
            return PurePath(os.path.relpath(target, start)).as_posix()
        except (OSError, ValueError):  # another drive: no relative path to it
            return target.as_posix()
    try:
        root = project.resolve()
        start = (here or project).resolve()
    except OSError:
        return {"config": Path(config).name, "base": ""}
    base = rel(root, start)
    return {"config": rel(root / config, start), "base": "" if base == "." else base}


def by_hand(st: dict) -> tuple[str, str]:
    """The step of the loop a person takes without an agent: the command's
    name and the line to run. `collect` while the scenario has no traces,
    `analyze` over them once it has.

    What `next` says after "or by hand", and what `echolot ui` offers to copy.
    With an investigation open, the step is its next one, and the line runs
    inside it (`--hunt N`): `next` says `resume-or-new` rather than this
    when it is not clear that the human is still on it. Its step is
    `collect` while the traces there were recorded outside it.
    """
    traces = st["traces"]
    # With several scenarios, the one this step is about, by name: the open
    # investigation's, else the first (see `project_state`).
    scenario = (st.get("config") or {}).get("scenario") if st.get("scenarios") else None
    hunt = st.get("hunt") or {}
    n = hunt.get("n") if hunt.get("status") == "open" else None
    shown = st.get("shown") or {}
    # Traces the last collect recorded outside the open investigation are
    # not its evidence: `hunt` says so, and analysed with `--hunt N` they
    # were filed as its round all the same (#720). The step is to record
    # inside it.
    outside = n is not None and hunt_mod.recorded_outside(hunt, st.get("collect"))
    if outside or not (traces["count"] if traces.get("scenario") is None else traces["scenario"]):
        return "collect", collect_line_for(scenario, (st.get("config") or {}).get("runner"), n,
                                           shown=shown)
    return "analyze", analyze_line(traces.get("files"), scenario, n, shown=shown)


def _config_arg(shown: dict | None) -> str:
    return " -c " + shlex.quote((shown or {}).get("config") or "echolot.yml")


def collect_line_for(scenario: str | None = None, runner: str | None = None,
                     hunt: int | None = None, shown: dict | None = None) -> str:
    """`echolot collect` for a scenario, named when the config has several.

    No `-n` for a macrobenchmark (`runner.mode: gradle`): its own
    `measureRepeated` decides how many times it runs, and `collect` says the
    flag does not reach it. The line offered it all the same (#520, #551).

    `hunt` is the investigation the step is part of: `--hunt N` files the
    run under it, and without it the run is outside any (#498).

    `shown` is how the config is named from where the line is run
    (`shown_from`).
    """
    count = "" if runner == "gradle" else " -n 5"
    return f"echolot collect{_config_arg(shown)}{count}" + (
        f" --scenario {shlex.quote(scenario)}" if scenario else "") + _inside(hunt)


def _inside(hunt: int | None) -> str:
    return f" --hunt {hunt}" if hunt else ""


def analyze_line(files: list[str] | None, scenario: str | None = None,
                 hunt: int | None = None, shown: dict | None = None) -> str:
    """`echolot analyze` over the repeats of one scenario, each file named.

    `.echolot/traces/` keeps every scenario's set, since re-recording one
    leaves the others where they are. `*.perfetto-trace` took `scroll`'s
    repeats along with `coldStart`'s after the config switched, and every
    median in the report mixed the two. And named rather than globbed, as
    the guides tell an agent to: Codex keeps a line with a glob inside its
    sandbox, where trace_processor cannot start. With no scenario to name
    the files by, the line says what to put there.

    `files` are named from the project's folder; `shown` (`shown_from`)
    says how to reach them, and the config, from where the line is run.
    """
    named = (f" --scenario {shlex.quote(scenario)}" if scenario else "") + _inside(hunt)
    base = (shown or {}).get("base") or ""

    def there(f: str) -> str:
        return (PurePath(base) / f).as_posix() if base else f
    if not files:
        return (f"echolot analyze <each trace of the scenario in "
                f"{there('.echolot/traces')}, named>" + _config_arg(shown) + named)
    return ("echolot analyze " + " ".join(shlex.quote(there(f)) for f in files)
            + _config_arg(shown) + named)


def repeats(scenario: str, n: int) -> list[str]:
    """The files `collect -n <n>` writes for a scenario, by name."""
    return [f".echolot/traces/{scenario}_iter{i:03d}.perfetto-trace" for i in range(n)]

