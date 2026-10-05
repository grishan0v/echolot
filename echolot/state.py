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
import os
import shlex
from pathlib import Path

from . import codex, hosts, layer, recorder, runner
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
                "confirmed": cfg.confirmed(),
                "temp_prefix": cfg.get("instrumentation.temp_prefix"),
            }
        except ConfigError as e:
            st["config"] = {"path": cfg_path, "error": str(e)}

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
def collect_state(project: Path) -> dict | None:
    """Where the last `collect` stands, from the file it writes as it runs.

    Four answers: running, interrupted, failed, or nothing worth saying. A
    run whose process is gone and whose file says it never finished was
    interrupted — the terminal closed, the agent's harness killed it — and
    that reads differently from a failure that said why.
    """
    from .runner import PROGRESS_FILE
    path = project / PROGRESS_FILE
    if not path.exists():
        return None
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(p, dict) or "started" not in p:
        return None
    if p.get("finished") is None:
        pid = p.get("pid")
        alive = isinstance(pid, int) and _alive(pid)
        p["status"] = "running" if alive else "interrupted"
    else:
        p["status"] = "failed" if p.get("exit") else "done"
    return p


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
        return f"running: {scenario}, {where}, started {when.ago(p.get('started'))}"
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


# The vocabulary, not the routing order — `next_kind` below is that. The
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
    return "Codex's sandbox" if host == "codex" else "the agent's sandbox"


def _door(st: dict) -> str:
    """How this project's agent is reached, in its own words.

    Leading with "/echolot in Claude Code" on a project that has just declined
    the layer names a command its human does not have.
    """
    if st.get("layer_verdict") == "opted-out":
        if "plugin" in (st.get("hosts") or []):
            return "the plugin's echolot skill, or `echolot guide"
        return "`echolot guide"
    return "/echolot in Claude Code, or `echolot guide"


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
    traces = st["traces"]
    if not (traces["count"] if traces.get("scenario") is None else traces["scenario"]):
        return (f"{_door(st)} hunt` — or by hand: "
                f"echolot collect -c echolot.yml -n 5")
    return (f"{_door(st)} hunt` — or by hand: "
            f"{analyze_line(traces.get('files'))}")


def analyze_line(files: list[str] | None) -> str:
    """`echolot analyze` over the repeats of one scenario, each file named.

    `.echolot/traces/` keeps every scenario's set, since re-recording one
    leaves the others where they are. `*.perfetto-trace` took `scroll`'s
    repeats along with `coldStart`'s after the config switched, and every
    median in the report mixed the two. And named rather than globbed, as
    the guides tell an agent to: Codex keeps a line with a glob inside its
    sandbox, where trace_processor cannot start. With no scenario to name
    the files by, the line says what to put there.
    """
    if not files:
        return ("echolot analyze <each trace of the scenario in "
                ".echolot/traces, named> -c echolot.yml")
    return ("echolot analyze " + " ".join(shlex.quote(f) for f in files)
            + " -c echolot.yml")


def repeats(scenario: str, n: int) -> list[str]:
    """The files `collect -n <n>` writes for a scenario, by name."""
    return [f".echolot/traces/{scenario}_iter{i:03d}.perfetto-trace" for i in range(n)]

