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
from pathlib import Path

from . import codex, hosts, layer, plugin, recorder, runner
from . import hunt as hunt_mod
from .config import Config, ConfigError


def project_state(project: Path, config: str = "echolot.yml") -> dict:
    """Where this project stands with echolot: the facts `status` and `init`
    decide the next step from.

    Everything here is read from disk and the run log; nothing runs.
    """
    st: dict = {"project": project}
    st["layer_verdict"], st["layer_line"] = layer.one_line(project)
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
                    "newest": max(stamps.values(), default=None)}
    st["collect"] = collect_state(project)

    st["report"] = _report(project / report_dir / "report.json")
    # A config that names several scenarios: each one's traces and latest
    # report, for `status` to list. None for one scenario, which prints as
    # it always has.
    st["scenarios"] = None
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
        "tool_gap": _tool_gap(r.get("toolchain")),
    }


def _tool_gap(toolchain: object) -> str | None:
    from .report import tool_gap
    return tool_gap(toolchain)


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
    number for `done` and `exit`; text for `scenario`, `cooling` and
    `error`. Every echolot that writes this file, 0.8.0 on, writes those:
    the times from `time.time()`, the process id from `os.getpid()`.

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
    if st["layer_verdict"] in ("absent", "stale"):
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
            return "echolot init — installs the .claude/ layer; then /echolot in Claude Code"
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
        return f"fix echolot.yml — it does not load: {st['config']['error']}"
    if kind == "resume-or-new":
        q = (st.get("hunt") or {}).get("question") or "the earlier question"
        return (f'{_door(st)}` — the agent asks whether to carry on with "{q}" '
                f'or start a new investigation')
    if kind == "init-force":
        return ("echolot init --all — overwrites .claude/ files that may carry "
                "edits made here (`echolot init` lists them): ask first, keep "
                "the edits with git. Then: " + _hunt_step(st))
    if kind == "fix-settings":
        return ("fix .claude/settings.json by hand — echolot cannot add its "
                "permission to a file that does not parse (the layer line "
                "says what to merge in). Then: " + _hunt_step(st))
    return _hunt_step(st)


def _hunt_step(st: dict) -> str:
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


def by_hand(st: dict) -> tuple[str, str]:
    """The step of the loop a person takes without an agent: the command's
    name and the line to run. `collect` while the scenario has no traces,
    `analyze` over them once it has.

    What `next` says after "or by hand", and what `echolot ui` offers to copy.
    """
    traces = st["traces"]
    # With several scenarios, the one this step is about, by name: the open
    # investigation's, else the first (see `project_state`).
    scenario = (st.get("config") or {}).get("scenario") if st.get("scenarios") else None
    if not (traces["count"] if traces.get("scenario") is None else traces["scenario"]):
        return "collect", collect_line_for(scenario, (st.get("config") or {}).get("runner"))
    return "analyze", analyze_line(traces.get("files"), scenario)


def collect_line_for(scenario: str | None = None, runner: str | None = None) -> str:
    """`echolot collect` for a scenario, named when the config has several.

    No `-n` for a macrobenchmark (`runner.mode: gradle`): its own
    `measureRepeated` decides how many times it runs, and `collect` says the
    flag does not reach it. The line offered it all the same (#520, #551).
    """
    count = "" if runner == "gradle" else " -n 5"
    return f"echolot collect -c echolot.yml{count}" + (
        f" --scenario {shlex.quote(scenario)}" if scenario else "")


def analyze_line(files: list[str] | None, scenario: str | None = None) -> str:
    """`echolot analyze` over the repeats of one scenario, each file named.

    `.echolot/traces/` keeps every scenario's set, since re-recording one
    leaves the others where they are. `*.perfetto-trace` took `scroll`'s
    repeats along with `coldStart`'s after the config switched, and every
    median in the report mixed the two. And named rather than globbed, as
    the guides tell an agent to: Codex keeps a line with a glob inside its
    sandbox, where trace_processor cannot start. With no scenario to name
    the files by, the line says what to put there.
    """
    named = f" --scenario {shlex.quote(scenario)}" if scenario else ""
    if not files:
        return ("echolot analyze <each trace of the scenario in "
                ".echolot/traces, named> -c echolot.yml" + named)
    return ("echolot analyze " + " ".join(shlex.quote(f) for f in files)
            + " -c echolot.yml" + named)


def repeats(scenario: str, n: int) -> list[str]:
    """The files `collect -n <n>` writes for a scenario, by name."""
    return [f".echolot/traces/{scenario}_iter{i:03d}.perfetto-trace" for i in range(n)]

