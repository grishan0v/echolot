"""Set a project up from `echolot ui`: the first `echolot.yml`, and what
`echolot init` installs, at one press of **Set it up**.

A person who has never opened a terminal for echolot gets from the start page
to a first press of **Check the app** here. Nothing is decided in this module
that a command does not decide already:

    what the config holds     scan.skeleton, the draft `echolot scan` prints
    whether it can be drafted the draft itself, read back: its window is the
                              launch, its package was read, no value is a `?`
    echolot's own files       `echolot init`, run as the command it is
    where the project stands  state.next_kind, which the start page reads too

What a press writes:

- `echolot.yml`, the text `scan` drafts with one line more when a time limit
  was given (`scenario.budget_ms`). Created, never replaced: a file that is
  there, or that appears between the page and the press, stops the press.
- what `echolot init` writes, where the project has never seen echolot. It
  runs as a job (`echolot/job.py`), a process beside the server, because its
  self-check takes about a minute and pages go on being drawn meanwhile. The
  page lists those files before the press (`init_writes`); a test holds the
  list to what `init` then does.

Two things are settled here and said on the page.

What a person confirms. The page shows the app's name as it installs and the
build that will be measured, read by `scan`, and asks whether that is their
app. That is what a newcomer can recognise. The press is the answer, and no
value in the file is marked `confirmed_by_user`: the draft keeps `scan`'s own
marks (`_source: derived`, with the evidence), because the person saw two
names and judged neither the process pattern nor the runner's numbers. An
agent that later finds one of those wrong may still put it right.

The time limit is written when the file is created, as one line with a comment
of the page's own (`LIMIT_SET`, or `LIMIT_OFF` where none was given). A config
is a file a person edits and may hold comments, and echolot has no writer that
keeps them; a second place for the limit would be a second source of truth
that neither `analyze` in a terminal nor CI reads. So afterwards the page
changes that one line and nothing else, while it still reads as the page
wrote it (`echolot/time_limit.py`), and shows the line to edit where it does
not.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from . import check, codex, hosts, ignore, job, layer, plain, ready, recorder, state, when
from . import scan as scan_mod
from .config import LAUNCH as LAUNCH_WINDOW
from .config import Config, ConfigError
from .config import PACKAGE
from .runner import MODE

JOB = "setup"
STEP = "init"
CONFIG = "echolot.yml"

# The one answer the page offers to "What do you want to check?", by the name
# its form sends. Anything else is the agent's setup.
LAUNCH = "launch"

# A time limit, in seconds, as the field takes it. A launch that takes longer
# than a minute is past any limit worth setting, and the draft records 20 s.
LIMIT_MIN_S, LIMIT_MAX_S = 0.1, 60.0
_LIMIT = re.compile(r"[0-9]{1,2}(?:[.,][0-9]{1,3})?")

# A line of `scan` about one benchmark class: its name, the file, what it measures.
_CLASS_LINE = re.compile(r"`[\w.]+` \(")

HEADER = ("# Written by `echolot ui` from what `echolot scan` read in this repository.\n"
          "# Every value says where it came from (`_source`, `_evidence`). The file is yours to edit.\n\n")

# The line of the time limit, under the scenario's window: the number with a
# comment of the page's own, or, with no limit, the same key commented out.
# `echolot/time_limit.py` rewrites this one line later as it is written here,
# seconds and all, and any other `budget_ms` line by its number alone: other
# words here would leave the lines the page wrote before them with comments
# that a press no longer brings up to date.
LIMIT_SET = "  budget_ms: {ms}  # the time limit, {words}: change the number to change it"
LIMIT_OFF = ("  # budget_ms: {ms}  # a time limit in milliseconds: take the first `#` off, and the report holds "
             "the launch against it")
LIMIT_SHOWN = 2000

Refusal = check.Refusal


# ---------------------------------------------------------------------------
# the draft: what `scan` proposes, and whether the page can write it as it is


def _fresh(facts: scan_mod.Facts) -> scan_mod.Facts:
    """The same facts with notes of their own: `skeleton` adds a note to the
    ones it is given, and a page asks for the draft more than once."""
    return dataclasses.replace(facts, notes=list(facts.notes))


def _unfilled(node: Any, where: str = "") -> list[str]:
    """Every value of a draft that is a question mark: one `scan` left for
    whoever reads a probe."""
    if isinstance(node, dict):
        return [found for key, value in node.items() if not str(key).startswith("_")
                for found in _unfilled(value, f"{where}.{key}" if where else str(key))]
    if isinstance(node, list):
        return [found for value in node for found in _unfilled(value, where)]
    return [where] if node == "?" else []


def _said(facts: scan_mod.Facts, *about: str) -> list[tuple[str, str]]:
    """What `echolot scan` prints of these facts, for the lines named
    (`ready.scan_says`): the page's own sentence stands over them."""
    says = ready.scan_says(_fresh(facts))
    return [("echolot scan", line) for key in about for line in says.get(key, [])]


def draft(facts: scan_mod.Facts | None) -> dict[str, Any]:
    """The config `scan` drafts for this repository, and whether a press may
    write it: `text` and `raw` when it may, else `plain`, a sentence of the
    page's, over `says`, the words of `scan` it stands on."""
    if facts is None:
        return {"can": False, "plain": "echolot could not read this folder, so it cannot tell which app is here.",
                "says": []}
    if not facts.app:
        return {"can": False, "no_app": True, "says": [("echolot scan", note) for note in facts.notes[:1]],
                "plain": "echolot found no Android app in this folder: no module here has a screen that the "
                         "launcher opens. Start `echolot ui` in the folder of the app's project."}
    lines = scan_mod.skeleton(_fresh(facts))
    text = "\n".join(lines) + "\n"
    raw = _reads(text)
    if raw is None:
        # A name in the build files that is no plain word can leave a draft
        # that is no config. Nothing is written from it.
        return {"can": False, "says": _said(facts, "app"),
                "plain": "The config `echolot scan` drafts for this project does not read as one, so the page "
                         "does not write it."}
    project, scenario = raw["project"], raw["scenario"]
    if project.get("_source") != "derived":
        return {"can": False, "says": _said(facts, "app") + [("echolot scan", str(project.get("_evidence") or ""))],
                "plain": "echolot found the app and could not read its name from the build files, the name it "
                         "installs under on a phone. Without it a recording cannot be told from any other app's."}
    window = scenario.get("window")
    if (window.get("name") if isinstance(window, dict) else window) != LAUNCH_WINDOW:
        # Of what `scan` says of the benchmarks, the module and what each
        # class measures: the lines that say why. The rest is for an agent.
        said = _said(facts, "benchmarks")
        return {"can": False, "says": said[:1] + [line for line in said[1:] if _CLASS_LINE.match(line[1])],
                "plain": "This project has a macrobenchmark that measures something of its own, and where that "
                         "begins and ends has to be picked from a trial recording."}
    left = _unfilled(raw)
    if left:
        return {"can": False, "says": _said(facts, "benchmarks", "notes"),
                "plain": "The draft has a value echolot could not fill in from the repository: "
                         + ", ".join(f"`{key}`" for key in left) + "."}
    chosen = scan_mod.preferred(facts.variants)
    several = next((note for note in facts.notes if note.startswith("several modules declare a launcher")), None)
    return {"can": True, "text": text, "raw": raw, "package": str(project["package"]),
            "variant": (chosen or {}).get("name"), "script": facts.app.get("build_script"),
            "mode": str((raw.get("runner") or {}).get("mode") or "launch"),
            "noted": [("echolot scan", several)] if several else []}


def _reads(text: str) -> dict[str, Any] | None:
    """A draft as the loader reads it, or None for one that is no config:
    not YAML, not the sections a config has, or refused by `Config` where
    `collect` and `analyze` would first ask it something."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict) or not all(isinstance(raw.get(key), dict) for key in ("project", "scenario", "runner")):
        return None
    cfg = Config(raw)
    try:
        cfg._detectors()
        cfg.select(None)
        if not isinstance(raw["project"].get("package"), str) or not cfg.process:
            return None
        for asked in ("scenario_window", "scenario_budget_ms", "runner", "scenario_name"):
            getattr(cfg, asked)
    except ConfigError:
        return None
    return raw


def limit_line(limit_ms: int | None) -> str:
    """The line of the time limit as the page writes it, without its end."""
    if limit_ms:
        return LIMIT_SET.format(ms=limit_ms, words=plain.hum_limit(limit_ms))
    return LIMIT_OFF.format(ms=LIMIT_SHOWN)


def limit_ms(text: str) -> int | None:
    """A time limit as the field takes it, in whole milliseconds: seconds from
    `LIMIT_MIN_S` to `LIMIT_MAX_S`, a point or a comma for the decimal, None
    for an empty field. `ValueError` for anything else."""
    text = (text or "").strip()
    if not text:
        return None
    seconds = float(text.replace(",", ".")) if _LIMIT.fullmatch(text) else None
    if seconds is None or not LIMIT_MIN_S <= seconds <= LIMIT_MAX_S:
        raise ValueError(text)
    return int(round(seconds * 1000))


def config_text(drafted: dict[str, Any], limit_ms: int | None = None) -> str:
    """The file a press writes: the draft, and under the scenario's window the
    time limit, as a line that says what it is. With no limit the line is
    there as a comment, for whoever sets one later."""
    line = limit_line(limit_ms)
    out, inside = [], False
    for text in drafted["text"].splitlines():
        out.append(text)
        if text.startswith("scenario:"):
            inside = True
        elif inside and text.startswith("  window:"):
            out.append(line)
            inside = False
    written = HEADER + "\n".join(out) + "\n"
    # The file is the draft and that line, and nothing else: read back, it
    # says what the draft said.
    raw = yaml.safe_load(written)
    wanted = {**drafted["raw"], "scenario": {**drafted["raw"]["scenario"], **({"budget_ms": limit_ms} if limit_ms else {})}}
    if raw != wanted:
        raise ValueError("the config to write does not read back as the draft")
    return written


# ---------------------------------------------------------------------------
# what `echolot init` writes into a project that has never seen echolot


def untouched(project: Path, st: dict[str, Any]) -> bool:
    """Whether echolot has never been set up here: no layer, no saved choice
    of agents, none of its lines in a file for another agent. Only then does
    a press run `echolot init`: what it writes is then new files, which a
    page can list before it writes them. Anywhere else `init` brings files
    up to date, and says itself which."""
    if st.get("layer_verdict") != "absent" or hosts.load_choice(project) is not None or hosts.load_private(project):
        return False
    if (project / codex.RULE_PATH).exists():
        return False
    for host in hosts.HOSTS:
        if host.key == "claude" or not host.path or not host.pointer:
            continue
        with contextlib.suppress(OSError):
            if hosts.MARKER in (project / host.path).read_bytes().decode("utf-8", "replace"):
                return False
    return True


def init_writes(project: Path) -> list[dict[str, Any]]:
    """Every file `echolot init` writes into a project it has never seen, in
    groups a person can read: `{"title", "files": [(path, note)]}`. Asked of
    the pieces `init` itself is made of (the agents it finds, the template,
    the lines for `.gitignore`), and read-only."""
    chosen = hosts.starting_set(project)
    groups: list[dict[str, Any]] = []
    root = project / ".claude"
    agent: list[tuple[str, str]] = []
    if any(h.key == "claude" for h in chosen):
        for src in layer.template_files():
            rel = src.relative_to(layer.CLAUDE_DIR).as_posix()
            dst = root / rel
            if not dst.exists():
                agent.append((f".claude/{rel}", ""))
            elif rel in layer.MERGED and layer.merge(src, dst)[0] not in ("current", "unreadable"):
                # The project's own file: echolot adds its permission and keeps the rest.
                agent.append((f".claude/{rel}", "yours already: one permission is added to it"))
        agent.append((f".claude/{layer.LAYER_MANIFEST}", ""))
    for host in chosen:
        if host.key == "claude" or not host.path:
            continue
        if not host.pointer:
            agent.append((codex.RULE_SHOWN, ""))
        elif not (project / host.path).exists():
            agent.append((host.path, ""))
    if agent:
        groups.append({"title": "For an agent that works with echolot: what it reads to know the tool. "
                                "You need none of them to check the app from this page.", "files": agent})
    path = project / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        text = None
    wanted = ignore.missing(text) if text is not None else []
    if wanted and (project / ".git").exists():
        # Appended to the project's own file, or the file made where there is none.
        note = "" if not path.exists() else "yours already: " + (
            "two lines are added to it" if len(wanted) > 1 else "one line is added to it")
        groups.append({"title": "So that recordings never reach git.", "files": [(".gitignore", note)]})
    groups.append({"title": "echolot's own notes, in its working folder: which agents it found here, and a log of "
                            "what it ran.",
                   "files": [(hosts.CHOICE_FILE.as_posix(), ""),
                             (job.state_path(Path(), JOB).as_posix(), ""),
                             (job.log_path(Path(), JOB, STEP).as_posix(), ""),
                             (recorder.LOG_FILE.as_posix(), "")]})
    return groups


# ---------------------------------------------------------------------------
# what the start page shows


def _only_config(config: str, project: Path) -> bool:
    """Whether the config this server reads is `echolot.yml` in the project's
    folder: the one file a press may create. Another name was asked for with
    `-c`, and the page writes no file under a name somebody typed."""
    with contextlib.suppress(OSError, ValueError):
        return (project / config).resolve() == (project / CONFIG).resolve()
    return False


def _loaded(st: dict[str, Any]) -> Config | None:
    known = st.get("config")
    if not known or known.get("error"):
        return None
    with contextlib.suppress(ConfigError):
        return Config.load(known["path"], scenario=known["scenario"])
    return None


def _other(st: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """Anything but the launch stays the agent's setup: one sentence, and the
    command that leads there. It is the one `next` names for a project with
    no config, by the door this project chose; where echolot was never set
    up, the way to an agent starts with `echolot init`."""
    if step.get("kind") == "init":
        return {"text": "Something else, one screen or one action? An agent sets that up, and that way starts in a "
                        "terminal, with this command.",
                "command": "echolot init",
                "how": "Run it in the project's folder. `echolot` then says how to ask the agent."}
    ask, how = ready._AGENT[state.door(st)]
    return {"text": "Something else, one screen or one action? An agent sets that up: it learns from the code how "
                    "to get there and where the wait begins and ends.",
            "command": "echolot guide setup" if ask == "echolot guide" else ask, "how": how}


def offer(project: Path, config: str, st: dict[str, Any], facts: scan_mod.Facts | None,
          step: dict[str, Any]) -> dict[str, Any] | None:
    """What the page offers where the project has no config: the question with
    its one answer and what a press writes, or why it cannot be set up from
    here. None where another step comes first (a newer echolot's files, a
    self-check that did not pass, an `init` that brings older files up to
    date) or where the config is not `echolot.yml`: the page is then as it
    was, and that step is said under "What to do next"."""
    if st.get("config") is not None or not _only_config(config, project) or (project / CONFIG).exists():
        return None
    kind = state.next_kind(st)
    runs_init = kind == "init" and untouched(project, st)
    if kind != "setup" and not runs_init:
        return None
    drafted = draft(facts)
    if not drafted["can"]:
        return {"state": "cannot", "plain": drafted["plain"], "says": drafted["says"],
                "no_app": bool(drafted.get("no_app"))}
    gradle = drafted["mode"] == "gradle"
    writes = [{"title": "What to measure, and how. Its text is below.", "files": [(CONFIG, "")]}]
    if runs_init:
        writes += init_writes(project)
    return {
        "state": "offer", "init": runs_init, "package": drafted["package"], "variant": drafted["variant"],
        "script": drafted["script"], "noted": drafted["noted"], "writes": writes,
        "config": config_text(drafted), "limit": (LIMIT_MIN_S, LIMIT_MAX_S), "other": _other(st, step),
        "choices": [{
            "name": LAUNCH, "title": "How the app opens",
            "text": ("The project's own macrobenchmark opens the app, and echolot reads what it records."
                     if gradle else
                     "From the tap on its icon until the first screen is there. echolot can set this up by itself."),
        }],
        "hint": ("Writes `echolot.yml` and echolot's own files into this project, then checks this computer: "
                 "about a minute. Nothing in your code changes." if runs_init else
                 "Writes `echolot.yml` into this project. Nothing in your code changes."),
    }


def by_hand(st: dict[str, Any], facts: scan_mod.Facts | None, step: dict[str, Any]) -> dict[str, Any] | None:
    """A config that is there and names no scenario: a check of it measures
    everything a recording holds. The page writes over no file, so it shows
    the lines that make the launch its scenario, for a person to add."""
    cfg = _loaded(st)
    if cfg is None or cfg.get("scenario") is not None or cfg.get("scenarios") is not None:
        return None
    drafted = draft(facts)
    if not drafted["can"]:
        return {"state": "cannot", "plain": drafted["plain"], "says": drafted["says"], "exists": True}
    # Only the sections the file does not have yet: a key written twice is no YAML.
    wanted = [key for key in ("project", "scenario", "runner") if cfg.get(key) is None]
    blocks, keep = [], False
    for text in config_text(drafted).split(HEADER, 1)[1].splitlines():
        if text and not text.startswith((" ", "#")):
            keep = text.split(":", 1)[0] in wanted
            if keep and blocks:
                blocks.append("")
        if keep and text:
            blocks.append(text)
    return {"state": "by-hand", "lines": "\n".join(blocks) + "\n", "other": _other(st, step),
            "sections": [key for key in wanted if key != "scenario"]}


def first_check(project: Path, st: dict[str, Any], lines: list[dict[str, Any]]) -> str | None:
    """What stands between a config and the first press of **Check the app**,
    where the page can tell: the build that is measured has to be on the
    phone. echolot records the build that is there and installs none (in
    `launch` mode), and it does not ask the phone which one that is. Said
    until the scenario has been recorded once."""
    cfg = _loaded(st)
    if cfg is None or state.next_kind(st) not in ("hunt", "init-force", "fix-settings"):
        return None
    traces = st.get("traces") or {}
    if st.get("report") or st.get("collect") or traces.get("count") or st.get("hunt"):
        return None
    try:
        if str(cfg.value(MODE)) != "launch":
            return None
    except ConfigError:
        return None
    package = str(cfg.value(PACKAGE) or "")
    try:
        tasks = scan_mod.install_tasks(project, package)
    except OSError:
        tasks = []
    what = f"`{package}`" if package else "the app"
    # The first task is the build to measure on first, as `scan` ranks them.
    how = (f"Run that build from Android Studio once, or install it with `./gradlew {tasks[0]}` in a terminal."
           if tasks else "Run that build from Android Studio once.")
    text = (f"Before the first check, {what} has to be on the phone as the build you want measured: echolot opens "
            f"what is installed there, and installs nothing. {how}")
    if any(line["tone"] == "red" for line in lines[1:2]):
        text += " And the phone has to be connected: the line marked red says how."
    return text


# ---------------------------------------------------------------------------
# a press


def asked(form: dict[str, str]) -> dict[str, Any]:
    """What a press asks for, from the form it sent: `what` is one of the
    names the page offered, `limit` a number of seconds or nothing. Neither
    is used as a path, a command or a piece of the file: the name picks the
    draft, and the number is written as a whole number of milliseconds."""
    if form.get("what") != LAUNCH:
        raise Refusal("This press cannot be read", "The page sent a choice echolot does not offer. Go back to the "
                      "start page and choose there.", 400)
    try:
        return {"what": LAUNCH, "limit_ms": limit_ms(form.get("limit") or "")}
    except ValueError:
        raise Refusal("That is not a time limit", f"The limit is a number of seconds from {LIMIT_MIN_S:g} to "
                      f"{LIMIT_MAX_S:g}, like 2 or 2.5. Leave the field empty to set none. Go back, put it right "
                      "and press Set it up again. Nothing was written.", 400) from None


def steps(project: Path) -> list[dict[str, Any]]:
    """`echolot init` as the job runs it and as a person would type it: the
    echolot that serves the page, asking nobody anything."""
    return [{"id": STEP, "argv": [sys.executable, "-m", "echolot", "init", "--into", str(project), "--no-input"],
             "shown": "echolot init"}]


def start(project: Path, config: str, st: dict[str, Any], facts: scan_mod.Facts | None, step: dict[str, Any],
          press: dict[str, Any], *, spawn: Callable[[Path, str, dict[str, Any]], Any] = job._spawn) -> None:
    """Do what a press asks for, or raise `Refusal` with the reason. The
    press is held against the project as it is now, not as the page saw it:
    `st` and `facts` were read for this request."""
    run = job.read(project, JOB)
    if run is not None and run["status"] == job.RUNNING:
        raise Refusal("Setting up is already under way", "This press started nothing. The start page shows how "
                      "far it is.")
    if (project / CONFIG).exists() or st.get("config") is not None:
        drafted = draft(facts)
        with contextlib.suppress(OSError, UnicodeDecodeError, ValueError):
            if drafted["can"] and (project / CONFIG).read_text(encoding="utf-8") == config_text(drafted, press["limit_ms"]):
                # The same press, sent twice: the file it asks for is there, to the letter.
                return
        raise Refusal("echolot.yml is already there", "This project has an `echolot.yml`, and the page never "
                      "writes over one: it may hold what somebody set by hand. Nothing was written. Go back to "
                      "the start page: it reads the file that is there.")
    found = offer(project, config, st, facts, step)
    if found is None:
        raise Refusal("This project cannot be set up from here now", "Something changed since the page was "
                      "drawn, and another step comes first. Nothing was written. The start page says which.")
    if found["state"] != "offer":
        raise Refusal("This project cannot be set up from here", f"{found['plain']} Nothing was written.")
    text = config_text(draft(facts), press["limit_ms"])
    path = project / CONFIG
    try:
        # Created, or nothing: a file that appeared this instant stays as it is.
        with open(path, "x", encoding="utf-8", newline="\n") as out:
            out.write(text)
    except FileExistsError:
        raise Refusal("echolot.yml is already there", "An `echolot.yml` appeared in this project while the page "
                      "was open, and the page never writes over one. Nothing was written. Go back to the start "
                      "page: it reads the file that is there.") from None
    except OSError as e:
        raise Refusal("echolot.yml could not be written", f"{e.strerror or e}. Nothing was written.", 500) from None
    if not found["init"]:
        return
    try:
        job.start(project, JOB, steps(project), {"config": CONFIG}, spawn=spawn)
    except job.Refused as e:
        raise Refusal("Setting up stopped half way", f"`echolot.yml` is written. echolot's own files are not: "
                      f"the command that installs them could not be started ({e}). The start page says how "
                      "to run it in a terminal.", 500) from None


# ---------------------------------------------------------------------------
# a setup under way, read back from its files


def busy(project: Path) -> bool:
    """Whether a setup is under way here. A check waits for it: `init` is
    still checking this computer, and what it finds decides whether a report
    made here can be trusted."""
    run = job.read(project, JOB)
    return run is not None and run["status"] == job.RUNNING


def rows(run: dict[str, Any]) -> list[dict[str, str]]:
    """The setup as a list, both rows there from the start: the config, which
    is written before the job starts, and `echolot init`."""
    step = next((s for s in run.get("steps") or [] if s.get("id") == STEP), {})
    how = check._state(run, step)
    note = {"now": "under way, about a minute…", "done": "done", "wait": ""}.get(how) or check._ENDED.get(how, "")
    return [{"state": "done", "name": "The config, echolot.yml", "note": "written"},
            {"state": how, "name": "echolot's own files, and a check of this computer", "note": note}]


def _stale(run: dict[str, Any], st: dict[str, Any], now: float) -> bool:
    """Whether a setup that ended badly is history: the step it left open was
    taken since (the next step is no longer `init` or the self-check, or a
    self-check ran after it), or it ended more than a day ago."""
    if state.next_kind(st) not in ("init", "doctor"):
        return True
    ended = run.get("finished") or run.get("started") or 0
    if now - ended > check.NEWS_S:
        return True
    last = when.iso_epoch((st.get("last_doctor") or {}).get("ts"))
    return last is not None and last > ended + 1


def reading(project: Path, st: dict[str, Any], run: dict[str, Any] | None,
            now: float | None = None) -> dict[str, Any] | None:
    """The setup as the start page shows it: its rows while it runs, and,
    where it ended badly and that is still the last word, what was written
    and what `echolot init` said. None where there is nothing to show: none
    was started here, it ended well (the page is then the button's), or what
    it would say is history."""
    if run is None:
        return None
    status = run["status"]
    if status == job.RUNNING:
        return {"state": "running", "rows": rows(run),
                "words": "Setting up: echolot's own files, and a check of this computer.",
                "note": "This takes about a minute: echolot checks on this computer that it computes correctly. "
                        "The page moves on by itself."}
    if status == job.DONE or _stale(run, st, time.time() if now is None else now):
        return None
    step = next((s for s in run.get("steps") or [] if s.get("id") == STEP), {})
    cut = status != job.FAILED
    return {
        "state": "ended", "status": status, "rows": rows(run),
        "title": "Setting up was cut short." if cut else "Setting up did not finish.",
        "text": ("The process that ran it is gone: `echolot ui` was stopped, the terminal was closed, or the "
                 "computer went to sleep. `echolot.yml` is written, and echolot's own files may be half there."
                 if cut else
                 "`echolot.yml` is written. `echolot init`, which installs echolot's own files and checks this "
                 "computer, ended with an error."),
        "said": None if cut else {"who": step.get("shown") or "echolot init",
                                  "lines": job.said(project, JOB, STEP), "log": step.get("log") or ""},
        "todo": ("The step under What to do next finishes it." if cut else
                 "What it says above is the reason. The step under What to do next takes it from here."),
    }


def view(project: Path, config: str, st: dict[str, Any], facts: scan_mod.Facts | None, step: dict[str, Any],
         lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Everything the start page shows of setting a project up, or None where
    it has nothing to add to the page as it is."""
    found = (reading(project, st, job.read(project, JOB)) or offer(project, config, st, facts, step)
             or by_hand(st, facts, step))
    # A config that waits for its scenario has its own thing to do first.
    first = None if found is not None and found["state"] == "by-hand" else first_check(project, st, lines)
    if found is None and first is None:
        return None
    return {**(found or {"state": None}), "first": first}


def progress(project: Path) -> dict[str, Any] | None:
    """The setup alone, for a page that is already drawn and asks how far it
    is: read from the job's file and from nothing else."""
    run = job.read(project, JOB)
    if run is None:
        return None
    going = run["status"] == job.RUNNING
    return {"status": run["status"], "rows": rows(run),
            "words": "Setting up: echolot's own files, and a check of this computer." if going else ""}
