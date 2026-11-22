"""What `echolot ui` reads before it draws a page: whether the setup is ready,
what is next, the settings with where each comes from, and the reports there are.

Nothing here judges on its own. Every answer is one the CLI already gives, and
is asked of the function the CLI stands on:

    where the project is     state.project_state, state.next_kind, state.by_hand
    the words of `echolot`   cmd_status itself, its printed lines read by name
    the repository           scan.describe, and scan.render for its words
    the phone                runner.pick_device, the one `collect` records on
    the thresholds           plan_detectors
    the investigations       hunt.history, hunt.archived_reports

The page puts a plain sentence in front and shows the CLI's own words behind
it, so that it never says what the CLI would not say.

Reading only. No file is written and no command is started; the phone is asked
for nothing but the list `adb devices` prints.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import hunt as hunt_mod
from . import layer, plain, runner, state, when
from . import main as cli
from . import scan as scan_mod
from .config import LAUNCH, NO_ANCHOR, Config, ConfigError
from .pages import report_page

GREEN, RED, GREY = "green", "red", "grey"
# A tone is never colour alone: what it means, in words.
STATE = {GREEN: "ready", RED: "in the way", GREY: "not checked"}


@dataclass
class Sources:
    """What is read from outside the project's files: the repository with the
    devices adb lists, and the phone `collect` would pick. The real ones are
    the functions behind `scan` and `collect`; a test hands in doubles."""
    scan: Callable[[Path], scan_mod.Facts]
    pick_device: Callable[[str | None], str]


def real_sources() -> Sources:
    return Sources(scan=lambda root: scan_mod.describe(root, devices=True),
                   pick_device=runner.pick_device)


# ---------------------------------------------------------------------------
# the CLI's own words


# `cmd_status` prints; its lines are caught here. One at a time: the stream it
# prints to is the process's, and a second page drawn at the same moment would
# write into the first one's.
_PRINTING = threading.Lock()


def status_says(config: str, tp_binary: str | None = None) -> tuple[str, dict[str, list[str]]]:
    """What `echolot` prints here: its first line, and the lines under it by
    the word each opens with (`config`, `doctor`, `next`, …).

    Read off the command itself and not rebuilt from the state, so the words
    are `status`'s by construction. The recap it adds after a blank line, for
    an agent to ask "carry on, or start new?", is not a line of the table.
    """
    out = io.StringIO()
    with _PRINTING, contextlib.redirect_stdout(out):
        cli.cmd_status(argparse.Namespace(config=config, next=False, tp_binary=tp_binary))
    lines = out.getvalue().splitlines()
    rows: dict[str, list[str]] = {}
    for text in lines[1:]:
        if not text.strip():
            break
        key, _, value = text.partition(" ")
        rows.setdefault(key, []).append(value.strip())
    return (lines[0] if lines else ""), rows


def scan_says(facts: scan_mod.Facts | None) -> dict[str, list[str]]:
    """What `echolot scan` prints of these facts, by what a line is about:
    `app` (the module line), `profileable`, `start` (which variant to start
    from), `devices`, `notes`. The Markdown marks are taken off."""
    said: dict[str, list[str]] = {}
    if facts is None:
        return said
    section = "top"
    for text in scan_mod.render(facts).splitlines():
        if text.startswith("## "):
            section = text[3:].strip().lower()
            if section == "a config to start from":
                break
            continue
        text = text.strip()
        if not text or text.startswith("|"):
            continue
        clean = text[2:] if text.startswith("- ") else text
        clean = clean.replace("**", "").strip("_")
        if section == "top" and text.startswith("App module"):
            said.setdefault("app", []).append(clean)
        elif section == "top" and text.startswith("profileable:"):
            said.setdefault("profileable", []).append(clean)
        elif section == "variants" and text.startswith("Start from"):
            said.setdefault("start", []).append(clean)
        elif section in ("devices", "notes", "benchmarks"):
            said.setdefault(section, []).append(clean)
    return said


def _sentence(text: str) -> str:
    """A line of the CLI's as the end of a sentence of the page's."""
    text = " ".join(str(text).split())
    return text if text.endswith((".", "!", "?")) else text + "."


def _line(tone: str, lead: str, text: str, behind: list[tuple[str, str]]) -> dict[str, Any]:
    return {"tone": tone, "state": STATE[tone], "lead": lead, "text": text, "behind": behind}


# ---------------------------------------------------------------------------
# ready: this computer, the phone, the app


def computer(st: dict, says: dict[str, list[str]]) -> dict[str, Any]:
    """This computer, from the last self-check as `echolot` reads it off the
    run log. Red on the fact `next` sends a person to `doctor` for, and on no
    other; a self-check that never ran here is not known, which is grey."""
    behind = [("echolot", f"doctor: {said}") for said in says.get("doctor", [])]
    last = st.get("last_doctor")
    if not last:
        return _line(GREY, "This computer", "has not been checked yet. `echolot doctor` checks that "
                     "it computes correctly, and `echolot init` runs the same check.", behind)
    if (last.get("facts") or {}).get("failed"):
        return _line(RED, "This computer", "is not ready: its last self-check did not pass, and no "
                     "report from it can be trusted until one does. Run `echolot doctor` in a terminal "
                     "and fix what it names.", behind)
    return _line(GREEN, "This computer", "is ready.", behind)


def phone(cfg: Config | None, facts: scan_mod.Facts | None, said: dict[str, list[str]],
          src: Sources) -> dict[str, Any]:
    """The phone `collect` would record on: `runner.device` when the config
    names one, else the one adb lists. When there is none to pick, the
    sentence is the one `collect` stops with."""
    behind = [("echolot scan", f"devices: {s}") for s in said.get("devices", [])]
    wanted = None
    if cfg is not None:
        with contextlib.suppress(ConfigError):
            wanted = cfg.runner.get("device")
    try:
        serial = src.pick_device(str(wanted) if wanted else None)
    except runner.RunnerError as e:
        return _line(RED, "The phone", f"is not ready: {_sentence(e.gist)}", behind)
    listed = next((d for d in ((facts.devices if facts else None) or []) if d.get("serial") == serial), None)
    what = (listed or {}).get("model") or serial
    if (listed or {}).get("emulator"):
        what += ", an emulator"
    return _line(GREEN, "The phone", f"is connected: {what}.", behind)


def app_name(cfg: Config | None, facts: scan_mod.Facts | None) -> str | None:
    """What the app is called here: the package the config measures, else the
    one the build script installs."""
    if cfg is not None and cfg.get("project.package"):
        return str(cfg.get("project.package"))
    if facts is not None and facts.app:
        chosen = scan_mod.preferred(facts.variants) or {}
        return chosen.get("application_id") or facts.app.get("application_id")
    return None


def app(cfg: Config | None, facts: scan_mod.Facts | None, said: dict[str, list[str]]) -> dict[str, Any]:
    """The app, from `scan`: whether a recording of it will show the app at
    all (profileable), and which build to measure."""
    name = app_name(cfg, facts)
    behind = [("echolot scan", s) for key in ("profileable", "start") for s in said.get(key, [])]
    found = facts.app if facts is not None else None
    if not found:
        behind += [("echolot scan", s) for s in said.get("notes", [])[:1]]
        if name:
            return _line(GREY, name, "is named in the config. Its code is not in this folder, so "
                         "echolot cannot say which build of it to measure.", behind)
        return _line(RED, "The app", "was not found: no Android project with a launcher Activity is in "
                     "this folder. Start `echolot ui` in the folder of the app's project.", behind)
    lead = name or "The app"
    if not (found.get("profileable") or found.get("profileable_by")):
        return _line(RED, lead, "cannot be measured yet: a release-like build of it shows nothing of the "
                     "app in a recording. Add `<profileable android:shell=\"true\" />` inside "
                     f"`<application>` in `{found['manifest']}`, then build and install it again.", behind)
    chosen = scan_mod.preferred(facts.variants)
    if chosen is None:
        return _line(GREEN, lead, "can be measured. Which build to take could not be read from "
                     "its build script.", behind)
    if chosen.get("debuggable") is True:
        # `preferred` puts a debuggable variant last: when it comes first,
        # every variant is one.
        return _line(RED, lead, "has only debuggable builds, and a debuggable build runs slower and "
                     "differently: what is measured on it is not what a user gets. Add a build type "
                     "that is not debuggable, the way a `benchmark` one is.", behind)
    return _line(GREEN, lead, f"can be measured on the `{chosen['name']}` build.", behind)


# ---------------------------------------------------------------------------
# the next step


_AGENT = {
    "claude": ("/echolot", "Paste it into Claude Code opened on this project."),
    "plugin": (None, "Ask the agent that has echolot's plugin: its echolot skill takes this step."),
    "any": ("echolot guide", "Run it for your agent: it prints how the agent takes this step."),
}
_TERMINAL = "Run it in a terminal opened in the project's folder, then press Refresh."


def next_step(st: dict, says: dict[str, list[str]], cfg: Config | None) -> dict[str, Any]:
    """What to do next, as a plain sentence and a command to copy.

    Which step it is comes from `state.next_kind`, the command from the
    functions `next` builds its own line from, and that line is kept beside
    them: the page says the step in plain words and shows the CLI's.
    """
    kind = state.next_kind(st)
    step: dict[str, Any] = {"kind": kind, "text": "", "command": None, "how": _TERMINAL, "note": None, "red": None,
                            "behind": [("echolot", f"next: {said}") for said in says.get("next", [])]}
    ask, how_agent = _AGENT[state.door(st)]
    if kind == "upgrade":
        step.update(text="This project's files for the agent were written by a newer echolot than the "
                         f"one running here. Upgrade it: {layer.UPGRADE}. Then press Refresh.",
                    how=None)
    elif kind == "init":
        fresh = st.get("layer_verdict") == "absent"
        step.update(text=("echolot is not set up in this project yet. This installs its files for the "
                          "agent and checks this computer." if fresh else
                          "echolot's files in this project are from an older version. This brings them "
                          "up to date."),
                    command="echolot init")
    elif kind == "doctor":
        step.update(text="This computer's last self-check did not pass, so no report made here can be "
                         "trusted. Run it again and fix what it names.",
                    command="echolot doctor")
    elif kind == "setup":
        step.update(text="echolot does not know this app yet: the project has no `echolot.yml`. An agent "
                         "writes it from the repository and one trial recording.",
                    command=ask if ask != "echolot guide" else "echolot guide setup", how=how_agent)
    elif kind == "fix-config":
        step.update(text="`echolot.yml` is there and does not load, so nothing can be read from it. "
                         "Fix what the line below names, then press Refresh.",
                    how=None)
    elif kind == "resume-or-new":
        question = (st.get("hunt") or {}).get("question") or "the earlier question"
        step.update(text=f"An investigation is open: \"{question}\". An agent asks whether to carry on "
                         "with it or to start a new one.",
                    command=ask, how=how_agent)
    else:
        if kind == "init-force":
            step["note"] = ("One thing only you can settle first: some files under `.claude/` differ from "
                            "the ones this echolot installs and may carry edits made here. `echolot init` "
                            "lists them, and `echolot init --all` overwrites them: keep the edits with git "
                            "before that.")
        elif kind == "fix-settings":
            step["note"] = ("`.claude/settings.json` does not parse, so echolot cannot add its permission "
                            "to it. Fix it by hand when you can; the step below works without it.")
        what, command = state.by_hand(st)
        step["what"] = what
        scenario = (st.get("config") or {}).get("scenario") or "the scenario"
        if what == "collect":
            mode = ""
            if cfg is not None:
                with contextlib.suppress(ConfigError):
                    mode = str(cfg.runner.get("mode", "launch"))
            step.update(text=(f"There are no recordings of `{scenario}` yet. This runs its macrobenchmark "
                              "on the phone and gathers the recordings it writes."
                              if mode == "gradle" else
                              f"There are no recordings of `{scenario}` yet. This opens the app on the "
                              "phone a few times and records each run. Nothing in your code changes."),
                        command=command)
        else:
            count = (st.get("traces") or {}).get("scenario")
            if count is None:
                count = (st.get("traces") or {}).get("count")
            step.update(text=f"The recordings of `{scenario}` are there, {count} of them. This reads them "
                             "into a report and says in plain words what is fine and what needs attention.",
                        command=command)
    return step


# ---------------------------------------------------------------------------
# all settings: what the simple page decides, and what stands behind each


_SOURCE = {"default": "built-in", "config": "set in the config", "scenario": "set for this scenario"}


def _source_words(source: str) -> str:
    return " and ".join(_SOURCE.get(part, part) for part in source.split("+"))


def _provenance(cfg: Config, key: str) -> str:
    """What follows an anchor a person confirmed in the config (`Config.confirmed`)."""
    where = "scenario" if cfg.get("scenarios") is None else f"scenarios.{cfg.scenario_name}"
    if f"{where}.{key}.name" in cfg.confirmed():
        return " Confirmed by you in the config."
    return ""


def settings(st: dict, head: str, says: dict[str, list[str]], cfg: Config | None,
             facts: scan_mod.Facts | None, said: dict[str, list[str]]) -> list[dict[str, Any]]:
    """The rows of "All settings", read-only: a name, a sentence, and what
    `echolot` and `echolot scan` say behind it."""
    rows: list[dict[str, Any]] = []

    def row(label: str, text: str, behind: list[tuple[str, str]] | None = None,
            more: list[str] | None = None) -> None:
        rows.append({"label": label, "text": text, "more": more or [], "behind": behind or []})

    status_config = [("echolot", f"config: {s}") for s in says.get("config", [])]
    known = st.get("config")
    if known is None:
        row("Config", "None yet: the project has no `echolot.yml`. The rows below are what "
            "`echolot scan` reads from the repository to start one from.", status_config)
    elif known.get("error"):
        row("Config", f"`echolot.yml` does not load: {_sentence(known['error'])}", status_config)
    else:
        names = known.get("scenarios") or []
        text = f"`echolot.yml`, scenario `{known['scenario']}`."
        if len(names) > 1:
            others = ", ".join(f"`{n}`" for n in names if n != known["scenario"])
            text = (f"`echolot.yml` names {len(names)} scenarios. This page is about `{known['scenario']}`: "
                    f"the open investigation's, else the first. The others: {others}.")
        if known.get("local"):
            text += " `local.yml` beside it is applied on top."
        row("Config", text, status_config)

    # The build to measure, from the repository.
    found = facts.app if facts is not None else None
    start = [("echolot scan", s) for s in said.get("start", [])]
    if found:
        chosen = scan_mod.preferred(facts.variants)
        if chosen:
            # `measure` is scan's answer to "measure on it?", in its words:
            # "yes — release-like and profileable", "no — debuggable: …".
            others = [f"`{v['name']}`: {v['measure']}" for v in facts.variants if v["name"] != chosen["name"]]
            row("Build to measure", f"`{chosen['name']}`. Measure on it: {_sentence(chosen['measure'])}"
                + (f" It installs as `{chosen['application_id']}`." if chosen.get("application_id") else ""),
                start,
                more=[f"The other builds, and whether to measure on each. {'; '.join(others)}."] if others else [])
        else:
            row("Build to measure", "The build script names no variant echolot can read.",
                [("echolot scan", s) for s in said.get("notes", [])])
    else:
        row("Build to measure", "Not known: the app's code is not in this folder.",
            [("echolot scan", s) for s in said.get("notes", [])[:1]])

    noted = [("echolot scan", s) for s in said.get("notes", [])]
    if noted and found:
        row("What scan noted", "Things about the repository and the devices that may need a look.", noted)

    if cfg is None:
        if known is None and facts is not None and found:
            row("A config to start from", "What `echolot scan` proposes, every value with where it came from.",
                more=["\n".join(scan_mod.skeleton(facts))])
        row("Toolchain", _toolchain(head))
        return rows

    # How the scenario runs on the phone.
    try:
        section = cfg.runner
        mode = str(section.get("mode", "launch"))
    except ConfigError as e:
        section, mode = {}, ""
        row("How it runs on the phone", _sentence(str(e)))
    if mode == "launch":
        row("How it runs on the phone", "echolot stops the app and starts it again itself, once per run "
            "(`runner.mode: launch`).", status_config)
    elif mode == "command":
        row("How it runs on the phone", "A command of the project starts it, once per run "
            f"(`runner.mode: command`): `{section.get('command') or 'none named'}`.", status_config)
    elif mode == "gradle":
        task = " ".join(str(x) for x in [section.get("gradle", "./gradlew"), section.get("gradle_task") or "?",
                                         *(section.get("gradle_args") or [])])
        row("How it runs on the phone", "A macrobenchmark of the project runs it (`runner.mode: gradle`): "
            f"`{task}`.", status_config + [("echolot scan", s) for s in said.get("benchmarks", [])[:1]])
    elif mode:
        row("How it runs on the phone", f"`runner.mode: {mode}`, which echolot does not know.", status_config)

    # Where the window opens and closes.
    try:
        if cfg.scenario_window == LAUNCH:
            row("What is measured", "The launch itself, as the phone records it (`window: launch`): from "
                "the moment the app is asked to start. The recording decides the end: the launch's end "
                "as the platform records it, or the app's `reportFullyDrawn` when that comes later. "
                "The report says which.")
        else:
            first, last = cfg.scenario_start, cfg.scenario_end
            row("The window opens at", ("The start of the recording: the config names no start."
                                        if first == NO_ANCHOR else f"`{first}`.{_provenance(cfg, 'start')}"))
            row("The window closes at", ("The end of the recording: the config names no end."
                                         if last == NO_ANCHOR else f"`{last}`.{_provenance(cfg, 'end')}"))
    except ConfigError as e:
        row("What is measured", _sentence(str(e)))

    # How many runs, and how long each records.
    if mode == "gradle":
        row("Runs", "The macrobenchmark decides: it runs as many as its own `measureRepeated` asks for, "
            "and every recording it writes is gathered.")
    elif mode:
        named = "iterations" in section
        runs = section.get("iterations", 5)
        text = f"{runs}, from `runner.iterations`." if named else f"{runs}, the default."
        if named and runs != 5:
            text += " A command that says `-n` asks for its own number: `-n` wins."
        row("Runs", text)
        if mode in ("launch", "command"):
            ms = section.get("duration_ms", 12000)
            where = "from `runner.duration_ms`" if "duration_ms" in section else "the default"
            row("Each run records for", f"{_seconds(ms)}, {where}.")

    # The time limit.
    try:
        budget = cfg.scenario_budget_ms
    except ConfigError as e:
        row("Time limit", _sentence(str(e)))
    else:
        # The line to edit comes after what the row reads: where the page
        # may rewrite the line it wrote, its field stands there instead
        # (`echolot/time_limit.py`).
        under = "scenario" if cfg.get("scenarios") is None else f"scenarios.{cfg.scenario_name}"
        if budget is None:
            row("Time limit", "None is set. The report then says how long the scenario takes and holds "
                "that against nothing.", more=[f"A line under `{under}:` in `echolot.yml` sets one, in "
                                               "milliseconds: `budget_ms: 2000` is 2 seconds."])
        else:
            row("Time limit", f"{plain.hum(budget)} (`budget_ms: {budget:g}`). The report's first line "
                "holds the scenario against it.",
                more=[f"To change it, edit that line under `{under}:` in `echolot.yml`."])

    # The thresholds, and where each comes from.
    try:
        plan = cli.plan_detectors(cfg)
        off = sorted(cfg.disabled_detectors)
    except ConfigError as e:
        row("Thresholds", _sentence(str(e)), status_config)
    else:
        tuned = sum(1 for _, _, source in plan if source != "default")
        total = len(plan)
        text = (f"All {total} checks run on their built-in thresholds." if not tuned else
                f"{tuned} of {total} checks run on thresholds set in the config, the other {total - tuned} "
                "on the built-in ones.")
        if off:
            text += f" {len(off)} more {'is' if len(off) == 1 else 'are'} switched off in the config."
        each = []
        for det, overrides, source in plan:
            # A pattern is not a threshold: only the numbers, as on the report's panel.
            numbers = []
            for key, value in {**det.params, **overrides}.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                shipped = det.params.get(key)
                moved = (f", built-in is {shipped:g}" if key in overrides and shipped != value
                         and isinstance(shipped, (int, float)) and not isinstance(shipped, bool) else "")
                numbers.append(f"{key} {value:g}{moved}")
            each.append({"id": det.id, "title": det.plain_title, "numbers": " · ".join(numbers) or "no threshold",
                         "source": _source_words(source)})
        each += [{"id": name, "title": "", "numbers": "", "source": "switched off in the config"} for name in off]
        rows.append({"label": "Thresholds", "text": text, "more": [], "behind": status_config, "each": each})

    row("Toolchain", _toolchain(head))
    return rows


def _seconds(ms: Any) -> str:
    try:
        return f"{float(ms) / 1000:g} s"
    except (TypeError, ValueError):
        return f"{ms} ms"


def _toolchain(head: str) -> str:
    """The versions of `echolot`'s first line, without the folder it ends with."""
    parts = [p.strip() for p in head.split(" · ")]
    return " · ".join(parts[:2]) or "unknown"


# ---------------------------------------------------------------------------
# the reports there are


_UNIT = {"s": "second", "m": "minute", "h": "hour", "d": "day"}


def _spelled(short: str) -> str:
    """A length of time as the CLI writes it (`20m`, `2h`, `96d`), in words:
    20 minutes, 2 hours, 96 days. The number and the unit stay the CLI's."""
    m = re.fullmatch(r"(\d+)([smhd])", str(short).strip())
    if not m:
        return str(short)
    n = int(m.group(1))
    return f"{n} {_UNIT[m.group(2)]}{'' if n == 1 else 's'}"


def _ago(epoch: float | None) -> str | None:
    """`when.ago` in words: "96 days ago". None for a time that is not known."""
    said = when.ago(epoch)
    return f"{_spelled(said[:-4])} ago" if said.endswith(" ago") else None


def _counted(n: int, one: str, many: str) -> str:
    return f"one {one}" if n == 1 else f"{n} {many}"


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n) or f"{n} times"


def _report(path: Path) -> dict[str, Any] | None:
    """A report with its plain reading, or None for a file that is no report.
    One written before those keys existed is read as `report --plain` reads it."""
    try:
        report = report_page._read(cli._load_report(path))
    except (ConfigError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return report if isinstance(report.get("verdict"), dict) else None


def report_words(rep: dict, report: dict[str, Any], said: str) -> str:
    """What `echolot` says of a report in one line of shorthand, as sentences:
    when it was made and from how many launches, how many checks found
    something, and whether the settings changed since.

    The facts are the ones that line is printed from (`state._report`), a
    detector is a check and a recording a launch or a run, as in the plain
    reading. Whether the config changed since is read off the line itself:
    the rule is `status`'s.
    """
    ctx = plain._Ctx(report)
    made = _ago(when.iso_epoch(rep.get("generated_at")))
    runs = _counted(int(rep.get("runs") or 1), ctx.noun, ctx.plural)
    out = [f"Made {made} from {runs}." if made else f"Made from {runs}."]
    total, found = rep.get("run"), int(rep.get("fired") or 0)
    if isinstance(total, int) and total > 0:
        if found == 0:
            out.append("The one check found nothing." if total == 1 else f"None of the {total} checks found anything.")
        elif found >= total:
            out.append("The one check found something." if total == 1 else f"Each of the {total} checks found something.")
        else:
            out.append(f"{found} of the {total} checks found something.")
    if "made with --defaults" in said:
        out.append("It was made on the built-in thresholds, not the config's.")
    elif "made with an older config" in said:
        out.append("The settings have changed since.")
    return " ".join(out)


def latest(st: dict) -> list[dict[str, Any]]:
    """The last report of each scenario: `key` is the scenario when the config
    has several, None for the one report. `plain` is what the page says of
    it, `said` the line `echolot` prints, kept for "All settings"."""
    project, known = st["project"], st.get("config")
    usable = known if known and not known.get("error") else None
    each = ([(sc["name"], sc["report"]) for sc in st["scenarios"]] if st.get("scenarios")
            else [(None, st.get("report"))])
    out = []
    for name, rep in each:
        entry: dict[str, Any] = {"key": name, "scenario": name or (usable or {}).get("scenario"),
                                 "path": None, "verdict": None,
                                 "plain": "No report yet. It appears here once its recordings are read.",
                                 "said": cli._report_text(rep, usable, project, "no report yet")}
        if rep:
            report = None if rep.get("error") else _report(rep["path"])
            if report is None:
                # A file that is there and is no report has no page to open.
                entry["plain"] = ("A report file is there and does not read. Run `echolot analyze` again "
                                  "to write it anew.")
            else:
                entry.update(verdict=report["verdict"], path=rep["path"],
                             plain=report_words(rep, report, entry["said"]))
        out.append(entry)
    return out


def hunt_words(h: dict[str, Any]) -> str:
    """What `hunt --list` says an investigation did, as sentences: how many
    times the app was recorded, how many reports it kept, and how long it has
    been open or when it ended. The counts are the record's, the length of
    time and the date are `hunt._ran_for` and `hunt._when`."""
    recorded, kept = int(h.get("collects") or 0), int(h.get("reports") or 0)
    first = f"The app was recorded {_times(recorded)}" if recorded else "The app was not recorded"
    first += (" and no report was kept." if not kept else
              " and one report was kept." if kept == 1 else f" and {kept} reports were kept.")
    ran = hunt_mod._ran_for(h)
    if (h.get("status") or "open") == "open":
        second = f"It is open, with {_spelled(ran)} of work so far." if ran else "It is open."
    else:
        second = ("It ended" + (f" on {hunt_mod._when(h['closed_at'])}" if h.get("closed_at") else "")
                  + (f", after {_spelled(ran)}" if ran else "") + ".")
    return f"{first} {second}"


def investigations(project: Path) -> list[dict[str, Any]]:
    """Every investigation, newest first, as `hunt --list` reads them, each
    with the numbers of the reports it filed: `reports` the ones that read,
    `unread` the ones that are there and do not."""
    out = []
    for h in hunt_mod.history(project):
        filed = hunt_mod.archived_reports(project, h) if h.get("n") else {}
        out.append({
            "n": h.get("n"), "current": bool(h.get("current")),
            "when": hunt_mod._when(h.get("opened_at")), "status": h.get("status") or "open",
            "question": h.get("question") or "", "plain": hunt_words(h),
            "conclusion": h.get("conclusion") if isinstance(h.get("conclusion"), str) else None,
            # What its own page shows, for the link to it: the agent's answer
            # (a verdict, or the text it was closed with), the rounds so far
            # of one still open, the rounds of one that was left.
            "page": ("Agent's answer" if isinstance(h.get("verdict"), dict) or h.get("status") == "concluded"
                     else "The rounds so far" if (h.get("status") or "open") == "open" else "Its rounds"),
            "reports": [k for k, data in sorted(filed.items()) if data is not None and "detectors" in data],
            "unread": [k for k, data in sorted(filed.items()) if data is None or "detectors" not in data],
        })
    return out


def behind(ready: list[dict[str, Any]], step: dict[str, Any], says: dict[str, list[str]],
           project: Path, any_investigation: bool) -> list[dict[str, Any]]:
    """The rows of "All settings" that are no setting: what `echolot` prints
    behind each sentence the page says in its own words. The three ready
    lines, the next step, the recordings and reports, the investigations."""
    rows = [{"label": line["lead"], "text": line["state"].capitalize() + ".", "behind": line["behind"]}
            for line in ready]
    if step.get("behind"):
        rows.append({"label": "The next step", "text": "", "behind": step["behind"]})
    files = [("echolot", f"{key}: {said}") for key in ("traces", "report", "scenario") for said in says.get(key, [])]
    if files:
        rows.append({"label": "Recordings and reports", "text": "", "behind": files})
    hunts = [("echolot", f"hunt: {said}") for said in says.get("hunt", [])]
    if any_investigation:
        hunts += [("echolot hunt --list", " ".join(line.split())) for line in hunt_mod.list_rows(project)]
    if hunts:
        rows.append({"label": "Investigations", "text": "", "behind": hunts})
    return rows


def archived(project: Path, n: int, k: int) -> tuple[dict[str, Any], Path] | None:
    """Report `k` of investigation `n` and the file it is, or None when there
    is no such report to read."""
    found = hunt_mod.find(project, str(n))
    home = hunt_mod.home(project, found)
    if found is None or home is None:
        return None
    data = hunt_mod.archived_reports(project, found).get(k)
    if not isinstance(data, dict) or "detectors" not in data:
        return None
    return data, home / "reports" / f"{k:03d}.json"


# ---------------------------------------------------------------------------
# everything the start page shows


def lines(project: Path, config: str, src: Sources, st: dict | None = None,
          tp_binary: str | None = None) -> list[dict[str, Any]]:
    """The three ready lines alone (this computer, the phone, the app), for
    whoever has to know whether a recording can start and draws no start page."""
    return read(project, config, src, tp_binary, st)["ready"]


def read(project: Path, config: str, src: Sources, tp_binary: str | None = None,
         st: dict | None = None) -> dict[str, Any]:
    """The start page's whole reading, taken anew every time it is asked for.
    `st` is the project's state when the caller has just read it."""
    st = st if st is not None else state.project_state(project, config)
    head, says = status_says(config, tp_binary)
    known = st.get("config")
    cfg = None
    if known and not known.get("error"):
        with contextlib.suppress(ConfigError):
            cfg = Config.load(known["path"], scenario=known["scenario"])
    try:
        facts = src.scan(project)
    except (OSError, runner.RunnerError, ValueError):
        facts = None
    said = scan_says(facts)
    ready = [computer(st, says), phone(cfg, facts, said, src), app(cfg, facts, said)]
    step = next_step(st, says, cfg)
    if step.get("what") == "collect" and any(line["tone"] == RED for line in ready[1:]):
        # `next` does not ask the phone or the repository. A recording does.
        # Kept apart from the step's own note: a page with the button says
        # this beside the button instead.
        step["red"] = "Fix what is marked red first: a recording needs the phone, and an app that can be measured."
    past = investigations(project)
    return {
        "project": project, "name": app_name(cfg, facts),
        "scenario": (known or {}).get("scenario") if known and not known.get("error") else None,
        "ready": ready, "next": step,
        "settings": settings(st, head, says, cfg, facts, said),
        "behind": behind(ready, step, says, project, bool(past)),
        "latest": latest(st), "investigations": past,
        # What `scan` read, for whoever draws from it beside this reading.
        "facts": facts,
    }
