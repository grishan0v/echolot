"""Check the app: what one press of the button in `echolot ui` runs, and how a
run is read back from the files it leaves.

A press runs what a person would run by hand, as the commands they are:

    echolot collect -c echolot.yml -n 3
    echolot analyze .echolot/traces/<scenario>_iter*.perfetto-trace -c echolot.yml --html
    echolot compare .echolot/out/before.json        (once a report was there before)

so the report is the one the terminal gives. They run as a job
(`echolot/job.py`), a process beside the server, and nothing about a run is
kept in the server: this module reads it from the job's file and from
`.echolot/log/collect.json`, the file `collect` writes as it goes and
`echolot` reads its `collect` line from. That file says how many launches
there are and how many are done, once per launch, which is all a page needs to
tick them off; `collect` gained no output of its own for this.

Three things are decided here and nowhere else:

- what the button is on this visit (`offer`): record and read, or read the
  recordings that are already there, for each scenario of the config; how long
  that takes, from the config; and why it cannot run, from the ready lines and
  the next step the start page already has;
- what a press starts (`start`): the commands, and the copy of the report
  before it that `compare` is given;
- what a run looks like now (`reading`): a row per launch and per step, and,
  once it ended, what was kept and what the command that failed said.

Nothing is written to `echolot.yml`: the scenario and the number of launches
go to the commands as arguments.
"""

from __future__ import annotations

import contextlib
import glob as glob_mod
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import hunt as hunt_mod
from . import job, ready, state, stats, when
from .config import LAUNCH, NO_ANCHOR, Config, ConfigError
from .pages import report_page

JOB = "check"
RECORD, READ = "record", "read"

# How many launches one press records unless "All settings" says otherwise.
# Three is the fewest that shows a spread, and it keeps a first check short.
LAUNCHES = 3
MOST = 20

# What a launch takes beside its recording (the app stopped, the recorder
# started, the file pulled off the phone), and what reading one recording
# takes: 2.3 s each over 10 MB recordings on a laptop, measured on one, three
# and five of them.
AROUND_S = 4.0
READ_S = 2.5
# How long a run that ended stays on the start page as the last thing that
# happened. After a day it is no news: the page shows the project as it stands.
NEWS_S = 24 * 3600.0

LABEL = {"first": "Check the app", "again": "Check again", READ: "Read the recordings"}


class Refusal(Exception):
    """A press that starts nothing: the page's title, a sentence, a status."""

    def __init__(self, title: str, text: str, status: int = 409):
        super().__init__(text)
        self.title, self.text, self.status = title, text, status


# ---------------------------------------------------------------------------
# how long it takes, in words


def about(seconds: float) -> str:
    """A stretch of time the way a person says it: to five seconds under a
    minute, to half a minute after."""
    if seconds < 55:
        return f"about {max(5, int(round(seconds / 5.0)) * 5)} seconds"
    if seconds >= 285:
        return f"about {int(round(seconds / 60.0))} minutes"
    whole, half = divmod(int(round(seconds / 30.0)), 2)
    if whole == 1:
        return "about a minute and a half" if half else "about a minute"
    return f"about {whole} and a half minutes" if half else f"about {whole} minutes"


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _whole(value: Any) -> int | None:
    """A number of launches: a whole number, one or more. None for anything
    else. `runner.iterations: five` is a config that loads, and `collect`
    writes "five" into its progress file before it stops and says so."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _noun(mode: str) -> tuple[str, str]:
    """What one repeat is called: the app is launched by echolot itself, or a
    command of the project runs the scenario."""
    return ("launch", "launches") if mode == "launch" else ("run", "runs")


def _record_s(section: dict[str, Any], n: int) -> float:
    try:
        each = float(section.get("duration_ms", 12000)) / 1000.0
    except (TypeError, ValueError):
        each = 12.0
    return n * (each + AROUND_S)


def hint(do: str, mode: str, n: int | None, section: dict[str, Any], *, unread: int = 0,
         before: int | None = None) -> str:
    """The line beside the button: how many launches, about how long, and that
    the code is not touched. `before` is how many launches the report before
    it was made from, when a comparison follows."""
    then = ", then a comparison with the report before it" if before else ""
    if do == READ:
        return (f"{_counted(unread, 'recording is', 'recordings are')} already here. Reading "
                f"{'it' if unread == 1 else 'them'} takes {about(unread * READ_S)}{then}. The phone is not needed.")
    if mode == "gradle":
        return ("This runs the project's macrobenchmark, which decides how many launches it takes: often a few "
                f"minutes{then}. Nothing in your code changes.")
    one, many = _noun(mode)
    if not n:
        # No number to count with: the config's own does not read as one,
        # and `collect` says so the moment it is started.
        return f"As many {many} as `runner.iterations` in the config says{then}. Nothing in your code changes."
    text = f"{_counted(n, one, many)}, {about(_record_s(section, n) + n * READ_S)}{then}. Nothing in your code changes."
    if section.get("cooldown"):
        text += f" The phone may wait to cool down between {many}."
    if before and stats.cut(before, n) is None:
        # `compare`'s own rule: too few a side to be sure of any move.
        text += " To tell a change from chance it takes four launches a side: All settings has the number."
    return text


# ---------------------------------------------------------------------------
# what the button is on this visit


def _files(project: Path, shown: list[str] | None) -> list[Path]:
    return [project / name for name in shown or ()]


def _newest(paths: list[Path]) -> float:
    stamps = []
    for path in paths:
        with contextlib.suppress(OSError):
            stamps.append(path.stat().st_mtime)
    return max(stamps, default=0.0)


def _reads(report: dict[str, Any] | None) -> bool:
    return bool(report) and not report.get("error")


def _unread(project: Path, files: list[str] | None, report: dict[str, Any] | None) -> int:
    """How many recordings of a scenario wait to be read: the ones that are
    there when no report is, or when they are newer than the report."""
    paths = _files(project, files)
    if not paths:
        return 0
    if not _reads(report):
        return len(paths)
    return len(paths) if _newest(paths) > _newest([report["path"]]) else 0


def _measures(cfg: Config) -> str:
    """What a scenario measures, in one sentence for its card."""
    try:
        if cfg.scenario_window == LAUNCH:
            return "The launch of the app, as the phone records it."
        first, last = cfg.scenario_start, cfg.scenario_end
    except ConfigError:
        return "What it measures does not read from the config."
    opens = "the start of the recording" if first == NO_ANCHOR else f"`{first}`"
    closes = "the end of the recording" if last == NO_ANCHOR else f"`{last}`"
    return f"The stretch from {opens} to {closes}."


def _stands(unread: int, report: dict[str, Any] | None) -> str:
    """Where a scenario stands, for its card."""
    if unread:
        return f"{_counted(unread, 'recording waits', 'recordings wait')} to be read."
    if _reads(report):
        made = ready._ago(when.iso_epoch(report.get("generated_at")))
        return f"Last checked {made}." if made else "It has a report."
    return "Not checked yet."


def _stops_all(st: dict[str, Any], run: dict[str, Any] | None = None) -> str | None:
    """What keeps any press from starting, in a sentence: the step the start
    page names under "What to do next" has to come first, or something is
    at work on the recordings already."""
    kind = state.next_kind(st)
    if kind == "upgrade":
        return "Not yet: this project's files were written by a newer echolot. Upgrade it first."
    if kind == "init":
        return "Not yet: echolot is not set up in this project. The step under What to do next comes first."
    if kind == "doctor":
        return ("Not yet: this computer's last self-check did not pass, so no report made here could be "
                "trusted. The step under What to do next comes first.")
    if kind == "setup":
        return ("Not yet: echolot does not know this app. It needs an `echolot.yml`, and the step under "
                "What to do next writes one.")
    if kind == "fix-config":
        return "Not yet: `echolot.yml` does not load. What to do next names the line to fix."
    hunt = st.get("hunt") or {}
    if hunt and (hunt.get("status") or "open") == "open":
        asked = f" (\"{hunt['question']}\")" if hunt.get("question") else ""
        return (f"Not now: an investigation is open{asked}, and an agent works with this app's recordings. "
                "A check from here would be mixed into its rounds. Its answer comes first.")
    collect = st.get("collect") or {}
    if collect.get("status") == "running":
        of = f" of `{collect['scenario']}`" if collect.get("scenario") else ""
        far = (f"{collect.get('done') or 0} of {collect['iterations']} recorded so far" if collect.get("iterations")
               else "a macrobenchmark is at work")
        if run is not None and _own(run, collect):
            return (f"Not now: the recording{of} of a check that was cut short is still running, {far}. "
                    "It ends by itself. Then press Refresh.")
        return (f"Not now: a recording{of} is already running, started from a terminal or by an agent: {far}. "
                "Wait for it to end, then press Refresh.")
    return None


def _stops_recording(lines: list[dict[str, Any]]) -> str | None:
    """What keeps a recording from starting, and only a recording: the phone,
    or an app that a recording would not show. The sentence is the ready
    line's own first one; the line itself, marked red, says what to do."""
    for line in lines[1:]:
        if line["tone"] == ready.RED:
            said = f"{line['lead']} {line['text']}"
            first = re.split(r"(?<=[.!?])\s", said, maxsplit=1)[0]
            return first if first == said else f"{first} The line marked red says what to do."
    return None


def offer(project: Path, st: dict[str, Any], lines: list[dict[str, Any]],
          run: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the button is now: one choice per scenario of the config, each
    with what a press does (`do`), its label, the line beside it for every
    number of launches, and why it cannot run, when it cannot."""
    out: dict[str, Any] = {"why": _stops_all(st, run), "choices": [], "launches": LAUNCHES, "most": MOST}
    known = st.get("config")
    if not known or known.get("error"):
        return out
    several = bool(st.get("scenarios")) and len(st["scenarios"]) > 1
    each = ([(sc["name"], sc["files"], sc["report"], bool(sc["focus"])) for sc in st["scenarios"]] if st.get("scenarios")
            else [(known["scenario"], (st.get("traces") or {}).get("files"), st.get("report"), True)])
    no_phone = _stops_recording(lines)
    for name, files, report, focus in each:
        try:
            cfg = Config.load(known["path"], scenario=name)
            section = dict(cfg.runner)
            mode = str(section.get("mode", "launch"))
        except ConfigError:
            # The config loaded a moment ago, every scenario with it: it was
            # changed since, and the next reading says how it stands.
            continue
        unread = _unread(project, files, report)
        do = READ if unread else RECORD
        before = int(report.get("runs") or 1) if _reads(report) else None
        counted = do == RECORD and mode != "gradle"
        hints = ({str(n): hint(do, mode, n, section, before=before) for n in range(1, MOST + 1)} if counted else {})
        report_dir = project / cfg.report_dir
        choice = {"name": name, "mode": mode}
        runs = steps(project, str(known["path"]), choice, do, LAUNCHES if counted else None,
                     report_dir / "before.json" if before else None, several)
        out["choices"].append({
            "name": name, "chosen": focus, "do": do, "mode": mode, "unread": unread, "counted": counted,
            "runs": [step["shown"] for step in runs],
            "section": section, "iterations": None if mode == "gradle" else _whole(section.get("iterations", 5)),
            "label": LABEL[READ] if do == READ else LABEL["again" if before else "first"],
            "text": f"{_measures(cfg)} {_stands(unread, report)}",
            "hints": hints,
            "hint": hints.get(str(LAUNCHES)) or hint(do, mode, None, section, unread=unread, before=before),
            # What keeps this choice from being pressed, and what keeps it
            # from being recorded anew where a press reads.
            "why": no_phone if do == RECORD else None, "no_record": no_phone,
            "report": report["path"] if _reads(report) else None,
            "report_dir": report_dir,
        })
    return out


def chosen(found: dict[str, Any], name: str | None = None) -> dict[str, Any] | None:
    """The choice a press is about: the one named, else the one the page is about."""
    choices = found.get("choices") or []
    if name is not None:
        return next((c for c in choices if c["name"] == name), None)
    return next((c for c in choices if c["chosen"]), choices[0] if choices else None)


# ---------------------------------------------------------------------------
# a press


def asked(form: dict[str, str]) -> dict[str, Any]:
    """What a press asks for, from the form it sent: `what` is `record:<scenario>`
    or `read:<scenario>`, `anew` asks to record where the page offered to
    read, `launches` is a whole number, `hunt` the investigation whose first
    report the new one is set against. Each may be left out."""
    do, _, scenario = (form.get("what") or "").partition(":")
    if do not in ("", RECORD, READ):
        raise Refusal("This press cannot be read", "The page sent something echolot does not know. "
                      "Go back to the start page and press the button there.", 400)
    launches = None
    text = (form.get("launches") or "").strip()
    if text:
        if not re.fullmatch(r"[0-9]{1,3}", text) or not 1 <= int(text) <= MOST:
            raise Refusal("That is not a number of launches",
                          f"The number of launches has to be a whole number from 1 to {MOST}. "
                          "Go back, put it right under All settings and press the button again.", 400)
        launches = int(text)
    hunt = (form.get("hunt") or "").strip()
    if hunt and not re.fullmatch(r"[0-9]{1,9}", hunt):
        raise Refusal("This press cannot be read", "The page named an investigation echolot cannot find. "
                      "Go back to the start page and press the button there.", 400)
    if form.get("anew") or hunt:
        do = RECORD
    return {"do": do or None, "scenario": scenario or None, "launches": launches, "hunt": hunt or None}


def _shown(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve()))
    except (OSError, ValueError):
        return str(path)


def steps(project: Path, config: str, choice: dict[str, Any], do: str, launches: int | None,
          before: Path | None, several: bool) -> list[dict[str, Any]]:
    """The commands of one press, each as the job runs it (`argv`) and as a
    person would type it in the project's folder (`shown`)."""
    echolot = [sys.executable, "-m", "echolot"]
    named = ["--scenario", choice["name"]] if several else []
    shown_c = ["-c", Path(config).name]
    out = []
    if do == RECORD:
        n = ["-n", str(launches)] if launches and choice["mode"] != "gradle" else []
        out.append({"id": "collect", "argv": [*echolot, "collect", "-c", config, *n, *named],
                    "shown": shlex.join(["echolot", "collect", *shown_c, *n, *named])})
    # Each recording by name, as a shell would put them there for this
    # pattern: the files `collect` names after the scenario, and no other
    # scenario's.
    traces = project / ".echolot" / "traces"
    pattern = f"{choice['name']}_iter*.perfetto-trace"
    files = str(Path(glob_mod.escape(str(traces))) / (glob_mod.escape(choice["name"]) + "_iter*.perfetto-trace"))
    out.append({"id": "analyze", "argv": [*echolot, "analyze", {"glob": files}, "-c", config, *named, "--html"],
                "shown": " ".join(["echolot", "analyze", f".echolot/traces/{pattern}",
                                   shlex.join([*shown_c, *named, "--html"])])})
    if before is not None:
        out.append({"id": "compare", "argv": [*echolot, "compare", str(before), "-c", config, *named],
                    "shown": shlex.join(["echolot", "compare", _shown(project, before), *shown_c, *named])})
    return out


def start(project: Path, config: str, found: dict[str, Any], run: dict[str, Any] | None, press: dict[str, Any], *,
          spawn: Callable[[Path, str, dict[str, Any]], Any] = job._spawn) -> dict[str, Any]:
    """Start what a press asks for, or raise `Refusal` with the reason.

    `found` is `offer` as it is now and `run` the last run as `job.read`
    gives it: the press is held against the project as it stands at this
    moment, not as the page that sent it saw it.
    """
    if run is not None and run["status"] == job.RUNNING:
        raise Refusal("A check is already running", f"{running_words(run, state.collect_state(project))} This press "
                      "started nothing: one check runs at a time. The start page shows how far it is.")
    if found.get("why"):
        raise Refusal("The check cannot start", found["why"])
    choice = chosen(found, press["scenario"])
    if choice is None:
        raise Refusal("No such scenario", "The config names no scenario by that name. Go back to the start "
                      "page and press the button there.", 400)
    do = press["do"] or choice["do"]
    if do == READ and not choice.get("unread"):
        raise Refusal("There is nothing to read", f"No recordings of `{choice['name']}` wait to be read. "
                      "Go back to the start page: its button says what a press does now.")
    if do == RECORD and (choice.get("why") or choice.get("no_record")):
        raise Refusal("The check cannot start", choice.get("why") or choice["no_record"])
    several = len(found["choices"]) > 1
    # From the answer's page the press is the line that page gives to copy,
    # `collect` with the config's own number; from the start page it names one.
    launches = None
    if do == RECORD and choice["mode"] != "gradle" and not press["hunt"]:
        launches = press["launches"] or LAUNCHES

    before: Path | None = None
    if press["hunt"]:
        # Against the app as it was before anyone touched it: the first
        # report the investigation filed, where it stands.
        hunt = hunt_mod.find(project, press["hunt"])
        home = hunt_mod.home(project, hunt) if hunt else None
        filed = hunt_mod.archived_reports(project, hunt) if hunt else {}
        first = min((n for n, rep in filed.items() if rep is not None), default=None)
        if home is None or first is None:
            raise Refusal("No such investigation", "It filed no report to compare the new one with. "
                          "Go back to the start page and press the button there.", 400)
        before = home / "reports" / f"{first:03d}.json"
    elif choice.get("report"):
        # `analyze` writes the new report over the last one, so the last one
        # is copied beside it first: that copy is what `compare` is given.
        before = choice["report_dir"] / "before.json"
        try:
            shutil.copyfile(choice["report"], before)
        except OSError as e:
            raise Refusal("The check did not start", "The report before it could not be kept for the comparison: "
                          f"{e.strerror or e}.", 500) from None
    plan = steps(project, config, choice, do, launches, before, several)
    what = {"do": do, "scenario": choice["name"], "mode": choice["mode"],
            "launches": (launches or choice.get("iterations")) if do == RECORD else None,
            "report": str(choice["report_dir"] / "report.json"), "before": str(before) if before else None}
    try:
        return job.start(project, JOB, plan, what, spawn=spawn)
    except job.Refused as e:
        raise Refusal("The check did not start", f"echolot could not start it: {e}.", 500) from None


def stop(project: Path) -> bool:
    """Ask the running check to stop. False when none is running."""
    return job.stop(project, JOB)


# ---------------------------------------------------------------------------
# a run, read back from its files


def _step(run: dict[str, Any], ident: str) -> dict[str, Any] | None:
    return next((s for s in run.get("steps") or [] if s.get("id") == ident), None)


def _own(run: dict[str, Any], collect: dict[str, Any] | None) -> dict[str, Any]:
    """`collect.json` when it is of this run's recording: written after the
    recording's command started and, once that ended, not after it. An older
    file is the run before; this run's `collect` has not written its own yet."""
    step = _step(run, "collect")
    if not step or not collect or not step.get("started"):
        return {}
    began = collect.get("started") or 0
    if began + 1 < step["started"] or (step.get("finished") and began > step["finished"] + 1):
        return {}
    return collect


def _recorded(run: dict[str, Any], collect: dict[str, Any] | None) -> tuple[int, int | None]:
    """How many launches of this run are recorded, and out of how many (None
    where a macrobenchmark decides, and where the number is no number)."""
    step = _step(run, "collect")
    if not step:
        return 0, None
    own = _own(run, collect)
    total = _whole(own.get("iterations")) or _whole((run.get("what") or {}).get("launches"))
    if (run.get("what") or {}).get("mode") == "gradle":
        total = None
    done = int(own.get("done") or 0)
    if step.get("status") == job.DONE and total:
        done = total
    return done, total


def _state(run: dict[str, Any], step: dict[str, Any]) -> str:
    """A step as a row shows it: `wait`, `now`, `done`, `failed`, `stopped`,
    or `cut` for one that was running when the run's process went away."""
    status = step.get("status")
    if status == job.RUNNING:
        return "now" if run["status"] == job.RUNNING else "cut"
    return {job.DONE: "done", job.FAILED: "failed", job.STOPPED: "stopped"}.get(status, "wait")


_ENDED = {"failed": "did not finish", "stopped": "stopped", "cut": "cut short"}


def rows(run: dict[str, Any], collect: dict[str, Any] | None) -> list[dict[str, str]]:
    """The run as a list, every row there from the start: one per launch, one
    for reading the recordings, one for the comparison when there is one."""
    what = run.get("what") or {}
    out: list[dict[str, str]] = []
    step = _step(run, "collect")
    if step:
        how = _state(run, step)
        done, total = _recorded(run, collect)
        if total is None:
            got = _own(run, collect).get("traces")
            note = {"now": "running…", "done": f"{_counted(got, 'recording', 'recordings')} gathered" if got else "done"}
            name = "The macrobenchmark" if what.get("mode") == "gradle" else "The recording"
            out.append({"state": how, "name": name, "note": note.get(how) or _ENDED.get(how, "")})
        else:
            one = _noun(str(what.get("mode") or "launch"))[0].capitalize()
            cooling = _own(run, collect).get("cooling")
            for i in range(1, total + 1):
                name = f"{one} {i} of {total}"
                if i <= done:
                    out.append({"state": "done", "name": name, "note": "recorded"})
                elif i == done + 1 and how != "wait":
                    note = "waiting for the phone to cool down…" if cooling else "recording…"
                    out.append({"state": how, "name": name, "note": note if how == "now" else _ENDED.get(how, "")})
                else:
                    out.append({"state": "wait", "name": name, "note": ""})
    for ident, name, going in (("analyze", "Reading the recordings", "reading…"),
                               ("compare", "Comparing with the report before it", "comparing…")):
        step = _step(run, ident)
        if step:
            how = _state(run, step)
            out.append({"state": how, "name": name,
                        "note": {"now": going, "done": "done", "wait": ""}.get(how) or _ENDED.get(how, "")})
    return out


def running_words(run: dict[str, Any], collect: dict[str, Any] | None) -> str:
    """What is running, in a sentence: for a second press that is refused,
    and for a screen reader while the list moves."""
    scenario = (run.get("what") or {}).get("scenario")
    about_what = f" of `{scenario}`" if scenario else ""
    now = next((r for r in rows(run, collect) if r["state"] == "now"), None)
    if now is None:
        return f"A check{about_what} is running."
    return f"A check{about_what} is running: {now['name'][0].lower() + now['name'][1:]}, {now['note'].rstrip('…')}."


def _kept(done: int) -> str:
    if not done:
        return "No recording was finished"
    return f"{_counted(done, 'recording is', 'recordings are')} kept"


def compare_says(report_dir: Path, since: float) -> str | None:
    """What `compare` itself said of the comparison it wrote there, in its
    own words: the line with the scenario's time before and after, and the
    line that counts what moved. None when no comparison was written since."""
    path = report_dir / "comparison.md"
    try:
        if path.stat().st_mtime + 1 < since:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    kept = [" ".join(line.replace("**", "").split()) for line in text.splitlines()
            if line.startswith(("Scenario window:", "Moved "))]
    return "; ".join(kept) or None


def _tally(report_path: Path) -> list[tuple[str, int, str]] | None:
    """How many checks stand at each level in a report, as the report page
    counts them. None for a file that is no report."""
    report = ready._report(report_path)
    if report is None:
        return None
    found = [c["kind"] for c in report_page._checks(report)]
    words = (("red", "needs attention", "need attention"), ("yellow", "worth a look", "worth a look"),
             ("green", "is fine", "are fine"), ("grey", "not checked", "not checked"))
    return [(kind, found.count(kind), one if found.count(kind) == 1 else many)
            for kind, one, many in words if found.count(kind)]


def _report_of(run: dict[str, Any]) -> Path | None:
    """The report a run was to write, where its file names one."""
    named = (run.get("what") or {}).get("report")
    return Path(named) if isinstance(named, str) and named else None


def _newer(run: dict[str, Any], st: dict[str, Any], now: float) -> bool:
    """Whether what this run says is history: something happened in the
    project after it (a recording started elsewhere, a report written since),
    or it ended more than a day ago. A run's last word is shown while it is
    the latest, and while it is news."""
    collect = st.get("collect")
    if collect and not _own(run, collect) and (collect.get("started") or 0) > run.get("started", 0):
        return True
    if run.get("finished"):
        mark = run["finished"]
    else:
        going = next((s for s in run.get("steps") or [] if s.get("status") == job.RUNNING), None)
        mark = (going or {}).get("started") or run.get("started", 0)
    if now - mark > NEWS_S:
        return True
    report = _report_of(run)
    return report is not None and _newest([report]) > mark + 1


def reading(project: Path, st: dict[str, Any], run: dict[str, Any] | None,
            now: float | None = None) -> dict[str, Any] | None:
    """The last run as the start page shows it, or None when there is none to
    show: none was started here, or what it would say is history.

    While it runs: the rows, and whether it was asked to stop. Once it ended:
    the rows as they stood, a sentence of the page's own on how it ended and
    what was kept, the words of the command that failed, and the counts of the
    new report when there is one.
    """
    if run is None:
        return None
    collect = st.get("collect")
    status = run["status"]
    if status != job.RUNNING and _newer(run, st, time.time() if now is None else now):
        return None
    what = run.get("what") or {}
    view: dict[str, Any] = {"status": status, "scenario": what.get("scenario"), "do": what.get("do"),
                            "rows": rows(run, collect), "words": "", "title": "", "text": "", "said": None,
                            "todo": "", "counts": None, "report": False, "compare": None, "stopping": False,
                            "note": ""}
    if status == job.RUNNING:
        view["words"] = running_words(run, collect)
        with contextlib.suppress(OSError):
            view["stopping"] = job.stop_path(project, JOB).read_text(encoding="utf-8").strip() == str(run.get("pid"))
        if view["stopping"]:
            view["note"] = "Stopping: the launch in progress is being ended."
        elif _state(run, _step(run, "collect") or {}) == "now" and what.get("mode") != "gradle":
            view["note"] = "You can watch the phone: the app opens and closes by itself."
        return view

    done, _ = _recorded(run, collect)
    ended = next((s for s in run.get("steps") or [] if s.get("status") in (job.RUNNING, job.FAILED, job.STOPPED)), None)
    where = (ended or {}).get("id")
    report_path = _report_of(run)
    compared = _step(run, "compare")
    read_step = _step(run, "analyze")
    if read_step and read_step.get("status") == job.DONE and report_path is not None:
        view["counts"] = _tally(report_path)
        view["report"] = view["counts"] is not None
    if compared and compared.get("status") == job.DONE and report_path is not None:
        view["compare"] = compare_says(report_path.parent, compared.get("started") or 0)

    if status == job.DONE:
        view["title"] = "Done. The short version:" if view["report"] else "Done."
        return view
    if status in (job.STOPPED, job.INTERRUPTED):
        view["title"] = "Stopped." if status == job.STOPPED else "This check was cut short."
        gone = ("" if status == job.STOPPED else
                "The process that ran it is gone: `echolot ui` was stopped, the terminal was closed, or the "
                "computer went to sleep. ")
        if where == "analyze":
            kept = "The recordings are kept, and no report was written from them."
        elif where == "compare":
            kept = "The new report is ready, and no comparison was made."
        elif where == "collect" or (where is None and _step(run, "collect")):
            kept = f"{_kept(done)}, and nothing was analysed."
        else:
            kept = "Nothing was read."
        view["text"] = gone + kept
        return view

    # It failed: the page's own sentence, then the command's words as it said them.
    if where == "collect":
        view["title"] = "The recording did not finish."
        view["text"] = (f"{_counted(done, 'recording was', 'recordings were')} finished before that and "
                        f"{'is' if done == 1 else 'are'} kept. " if done else "") + "Nothing was analysed."
        view["todo"] = "What the command says above is the reason. Put that right, then press the button again."
    elif where == "analyze":
        view["title"] = "Reading the recordings did not finish."
        view["text"] = "The recordings are kept, and no report was written from them."
        view["todo"] = "What the command says above is the reason. Put that right, then press the button again."
    elif where == "compare":
        view["title"] = "The new report is ready. The comparison was not made."
        view["text"] = "The report stands as it is; only the comparison with the one before it is missing."
    else:
        view["title"] = "The check did not finish."
    if ended is not None:
        view["said"] = {"who": ended.get("shown") or where or "", "lines": job.said(project, JOB, str(where)),
                        "log": ended.get("log") or ""}
    return view


def view(project: Path, st: dict[str, Any], lines: list[dict[str, Any]],
         run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the start page shows of the button: what it offers now, and
    the last run. `run` is the job's state when the caller has just read it."""
    run = run if run is not None else job.read(project, JOB)
    return {"offer": offer(project, st, lines, run), "run": reading(project, st, run), "job": run}


def progress(project: Path) -> dict[str, Any] | None:
    """The run alone, for a page that is already drawn and asks how far it is:
    read from the two files and from nothing else."""
    run = job.read(project, JOB)
    if run is None:
        return None
    return {"status": run["status"], "rows": rows(run, state.collect_state(project)),
            "words": running_words(run, state.collect_state(project)) if run["status"] == job.RUNNING else ""}

