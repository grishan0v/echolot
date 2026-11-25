"""Dig deeper from `echolot ui`: the person's own agent, started without a
window on the checks they ticked, and read back from the files it leaves.

echolot has no model and starts none of its own. A press runs what a person
would run by hand in a terminal:

    echolot hunt "<the question>" --since unknown       the investigation
    claude -p "<the request>" --output-format json …    their own Claude Code

The first is run by the server, before anything else, and the second as a job
(`echolot/job.py`), a process beside the server, so that pages go on being
drawn for the tens of minutes it takes. Two clients can be started (#449,
#454), each through an adapter of the table at the end of this module:
Claude Code, whose parts are the functions of this module, and Codex, whose
parts are `echolot/dig_codex.py`. Cursor stays on the request to copy.

What is decided here and nowhere else, for Claude Code:

- whether Claude Code can be started from here (`find`): installed, new
  enough to be told that nobody can answer, and signed in;
- the grant (`rules`) and the command line (`command`), built by the server
  from the config and never from what a browser sent. No permission is
  bypassed: `dontAsk` refuses whatever the rules do not allow, edits are
  limited to `instrumentation.allowed`, and a question with nobody there is
  refused rather than waited on (`--permission-prompts none`);
- the request the agent is handed (`request`): the report's question,
  warnings and rows (`pages/report_page.request_text`), what the scenario's
  last comparison found worse (`worse`), and what a run with nobody at the
  keyboard has to know;
- which `echolot` the agent runs (`runs_own`): the page's own, first in its
  `PATH`, and asked of its shell the way the client asks it;
- what a run looks like now (`view`): the investigation's own record round by
  round, and between rounds the phase, from what the agent is running (a
  build, a recording, the phone) and not from anything it printed; how long
  nothing has moved; and once it ended, how, with what it left in the code.

And for every client: which ones the page offers, in which order (`offer`),
what it says of the ones it cannot start (`reasons`, `aside`), what all of
them need alike, a folder in `instrumentation.allowed` (`unplaced`), and
everything after a press that is the same for all of them.

Nothing is written to Claude Code's own settings, and nothing to `echolot.yml`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from . import check as check_mod
from . import files as files_mod
from . import hunt as hunt_mod
from . import job, state
from . import report as report_mod
from . import when as when_mod
from .check import Refusal, _stops_recording
from .config import DEFAULT_PREFIX, Config, ConfigError
from .mark import allowed_entry
from .pages import report_page

JOB = "dig"
STEP = "agent"
PROGRAM = "claude"
CLIENT = "Claude Code"

# The first Claude Code that can be told nobody answers its prompts
# (`--permission-prompts none`); before it, `-p` could start in auto mode.
MIN_VERSION = (2, 1, 259)
# Where Claude Code stops by itself, by its own estimate of what it spent.
BUDGET_USD = 10
# How long nothing may move before the page says so, beside Stop. A real hunt
# was quiet for four and five minutes at a time (an edit, a build, an install,
# the answer being written), and the page must not cry wolf over that.
QUIET_S = 600.0
# The one file outside the allowed paths the agent writes: its verdict.
VERDICT = ".echolot/verdict.json"
# How the words the page closes an investigation with end (`closing_words`):
# the investigation's page heads with them, and the panel reads them to know
# that the page closed it, not the agent.
CLOSED_HERE = "Closed from echolot ui."
# What the agent is told it may run, as the rules hold it and as a person reads it.
COMMANDS = (("echolot", "Bash(echolot *)", "any echolot command"),
            ("./gradlew", "Bash(./gradlew *)", "any Gradle task of the project"),
            ("adb", "Bash(adb *)", "any command on the phone"))
# Names a nested Claude Code would read as "you are inside another session".
UNSET = ("CLAUDECODE",)
# Where Claude Code is told about the layer it needs: the command that hunts,
# and the agent it hands the loop to, as `echolot init` installs them.
LAYER = (Path(".claude") / "commands" / "echolot-hunt.md", Path(".claude") / "agents" / "perf-hunter.md")

# How the page finds the client and asks it the two things it needs. A test
# hands in its own: no test ever runs the real one.
_which: Callable[[str], str | None] = shutil.which


def _quiet_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in UNSET}


def _ask(argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
                              env=_quiet_env(), errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


_run: Callable[[list[str]], subprocess.CompletedProcess | None] = _ask


# ---------------------------------------------------------------------------
# the client: can it be started from here


@dataclass
class Client:
    """A client as the page finds it on this computer: Claude Code unless
    `key` names another of the table. `why` is why it cannot be started from
    here, in a sentence; None when it can. `how` is how it is signed in, for
    a client that names no account (Codex: "with ChatGPT")."""
    program: str | None = None
    version: str | None = None
    account: str | None = None
    why: str | None = None
    key: str = PROGRAM
    how: str | None = None

    @property
    def can(self) -> bool:
        return self.why is None and self.program is not None


def _version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in m.groups()) if m else None


def find() -> Client:
    """Claude Code on this computer: where it is, which version, whose
    account. Asked anew every time, in a third of a second: the server keeps
    nothing between two requests."""
    program = _which(PROGRAM)
    if not program:
        return Client(why=f"{CLIENT} is not installed on this computer: there is no `{PROGRAM}` command here.")
    said = _run([program, "--version"])
    found = _version(said.stdout if said else "")
    if found is None:
        return Client(program=program, why=f"{CLIENT} did not say which version it is (`{PROGRAM} --version`), "
                                           "so the page cannot tell whether it can be started without a window.")
    version = ".".join(str(part) for part in found)
    if found < MIN_VERSION:
        least = ".".join(str(part) for part in MIN_VERSION)
        return Client(program=program, version=version,
                      why=f"{CLIENT} {version} is older than {least}, the first that can be told that nobody is "
                          f"there to answer it. Update it, then come back.")
    status = _run([program, "auth", "status"])
    try:
        known = json.loads(status.stdout) if status and status.stdout.strip() else None
    except ValueError:
        known = None
    if not isinstance(known, dict):
        return Client(program=program, version=version,
                      why=f"{CLIENT} did not say whether it is signed in (`{PROGRAM} auth status`).")
    if known.get("loggedIn") is not True:
        return Client(program=program, version=version,
                      why=f"{CLIENT} is installed and not signed in. Sign in once in a terminal "
                          f"(`{PROGRAM} auth login`), then come back.")
    account = next((str(known[key]) for key in ("email", "account", "orgName")
                    if isinstance(known.get(key), str) and known[key].strip()), None)
    return Client(program=program, version=version, account=account)


# ---------------------------------------------------------------------------
# which echolot the agent runs (#704)
#
# The agent runs `echolot` through its shell, and which one that is depends
# on the person's shell, not on the page. Codex runs every command in a login
# shell (`zsh -lc`), and Claude Code from a snapshot of one that read the rc
# file too, with `PATH` as that shell left it. A field run whose page was
# served by 0.17.0 started Claude Code on a 0.14.1 from pipx, first in `PATH`,
# which stopped after about $0.10 with no answer. So the page puts its own
# folder first in the agent's `PATH`, and before Start asks the client's
# shell, with that same environment, which `echolot` it finds: a profile that
# puts another folder first again is a reason the client cannot start.

# What the shell is asked: which `echolot` it runs, after a mark no profile prints.
_AT = "echolot-at:"
LOOK = f'printf "%s\\n" "{_AT}$(command -v echolot)"'
# How long a login shell may take to answer. One that takes longer holds
# every command the agent runs as long.
SHELL_S = 20


def own_bin() -> Path:
    """The folder of the page's own `echolot`: the one the Python that serves
    the page is in, as it was started and not where a link leads, since a
    virtual environment's `python` is a link to the one it was made from."""
    return Path(sys.executable).absolute().parent


def agent_env(unset: Sequence[str]) -> dict[str, str]:
    """The environment an agent started from the page has: the page's own,
    without the names its adapter leaves out, and with the page's own folder
    first in `PATH`, as the job puts it (`job.path_first`)."""
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env["PATH"] = job.path_first([os.fspath(own_bin())], env.get("PATH"))
    return env


def _in_shell(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=SHELL_S,
                              stdin=subprocess.DEVNULL, errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


# How the page asks the agent's shell, and the `echolot` it finds, a question.
# A test hands in its own: no test reads the shell of the computer it runs on.
_shell: Callable[[list[str], Path, dict[str, str]], subprocess.CompletedProcess | None] = _in_shell


def claude_shell(script: str) -> list[str]:
    """A command line as Claude Code's shell runs it: the shell it uses
    (`$SHELL` where that is zsh or bash, else the first of those it finds),
    as a login shell, with the rc file sourced first, as its snapshot of
    the shell is made."""
    shell = os.environ.get("SHELL") or ""
    if not any(name in Path(shell).name for name in ("zsh", "bash")):
        shell = shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
    name = Path(shell).name
    rc = Path.home() / (".zshrc" if "zsh" in name else ".bashrc" if "bash" in name else ".profile")
    first = f"source {shlex.quote(os.fspath(rc))} < /dev/null; " if rc.is_file() else ""
    return [shell, "-c", "-l", first + script]


def login_shell() -> str:
    """The person's login shell, as the system has it; `$SHELL` where it
    says none."""
    with contextlib.suppress(ImportError, KeyError, OSError):
        import pwd
        found = pwd.getpwuid(os.getuid()).pw_shell
        if found:
            return found
    return os.environ.get("SHELL") or "/bin/sh"


def _whole(path: Path) -> str:
    """A path the person can put into a profile: whole, or from home."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return os.fspath(path)


def _same_file(a: str, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def runs_own(client: Client, project: Path) -> str | None:
    """Why the agent would run another `echolot` than the page's own, in a
    sentence; None when it runs this one. Asked of its shell the way the
    client runs a command (the adapter's `shell`), in the project's folder,
    with the environment the agent gets (`agent_env`). Another `echolot` of
    this same version is this one, as far as the investigation can tell."""
    if os.name != "posix":
        return None
    agent = adapter(client.key)
    env = agent_env(agent.unset)
    argv = agent.shell(LOOK)
    said = _shell(argv, project, env)
    own = own_bin() / "echolot"
    asked = f"`{Path(argv[0]).name} {agent.login} 'command -v echolot'`"
    first = f"Put `{_whole(own_bin())}` first in your shell's PATH"
    marked = [line.strip()[len(_AT):].strip() for line in (said.stdout if said else "").splitlines()
              if line.strip().startswith(_AT)]
    if not marked:
        how = f"did not answer within {SHELL_S} s" if said is None else "said nothing the page could read"
        return (f"The page could not tell which `echolot` {agent.name} would run: the login shell it runs commands "
                f"in {how}. Check that {asked} answers in a terminal, by itself, then come back.")
    found = marked[-1]
    if not found:
        return (f"Not yet: {agent.name} runs commands in your login shell, and that shell has no `echolot` command "
                f"({asked} finds none), so the agent could not run it. {first}, then come back.")
    if found.startswith("/") and _same_file(found, own):
        return None
    ours = _version(__version__)
    theirs = None
    if found.startswith("/"):
        told = _shell([found, "--version"], project, env)
        theirs = _version(told.stdout if told else "")
        if theirs is not None and theirs == ours:
            return None
    if not found.startswith("/"):
        what = f"`{found}`, an alias or a function of your shell"
    elif theirs is None:
        what = f"`{_whole(Path(found))}`, an echolot that does not say its version (`echolot --version`)"
    else:
        what = f"`{_whole(Path(found))}`, echolot {'.'.join(str(part) for part in theirs)}"
    return (f"Not with this echolot: {agent.name} runs commands in your login shell, and there `echolot` is {what}, "
            f"while this page is echolot {__version__} (`{_whole(own)}`). The investigation would run on "
            f"another echolot than the page that reads it. {first}, or remove the other echolot, then come back.")


# ---------------------------------------------------------------------------
# what a press asks for: ticked checks of the report on disk

_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
PICK = "pick."


def asked(form: dict[str, str]) -> list[str]:
    """The checks a press ticked, by their keys: a field `pick.<key>` each.
    Nothing in them is a path or a command: a key is matched against the
    report, and a key that is no such name is no press."""
    keys = []
    for name, value in form.items():
        if not name.startswith(PICK):
            continue
        key = name[len(PICK):]
        if not _KEY.fullmatch(key) or value not in ("1", "on"):
            raise Refusal("This press cannot be read", "The page sent something echolot does not know. "
                          "Go back to the report and tick the checks there.", 400)
        keys.append(key)
    if not keys:
        raise Refusal("Nothing is ticked", "Tick at least one check that found something, then press Dig deeper.", 400)
    return keys


def picked(report: dict[str, Any], keys: Sequence[str]) -> list[dict[str, Any]]:
    """The ticked checks, as the report on disk has them now and in its own
    order. A key it does not have, or a check that found nothing, is refused:
    the report was written anew since the page was drawn."""
    checks = report_page.checks(report)
    by_key = {c["key"]: c for c in checks}
    for key in keys:
        if key not in by_key or not by_key[key]["hot"]:
            raise Refusal("The report changed", "It is not the one the page was drawn from: a check that was ticked "
                          "has nothing to show in it now. Go back to the report: it shows the new one.")
    wanted = set(keys)
    return [c for c in checks if c["key"] in wanted]


# ---------------------------------------------------------------------------
# the grant and the command line, built here and nowhere else

_SEGMENT = re.compile(r"[A-Za-z0-9_.@+*-]+")


def allowed(cfg: Config) -> list[str]:
    """`instrumentation.allowed` as paths an edit rule can name: relative,
    in forward slashes, with no `..`. `""` is the whole project (`.`).
    Refused with the entry named when one is none of those: the page puts
    nothing it cannot read into a rule. An empty list allows every place to
    `echolot mark`, as it does to every reader of `mark.Markers`, and the
    page does not hand that to an agent."""
    raw = cfg.allowed
    if not raw:
        raise Refusal("Where may the agent put its markers?",
                      "`instrumentation.allowed` in `echolot.yml` names no folder. An agent started here puts "
                      "its markers only into the folders that list names, so the page starts none until it names "
                      "one. Add the folders of your app's own code to `echolot.yml` under `instrumentation:`, "
                      "for example `allowed: [\"app/src/main\", \"feature/*/src/main\"]`, then come back.")
    out = []
    for entry in raw:
        # Read the way `mark` reads it (`mark.allowed_entry`): `./app/src/main`
        # and `app/src/main/` are `app/src/main`, and `.` is the whole project.
        text = str(entry).strip().replace("\\", "/")
        plain = allowed_entry(text)
        if not plain:
            out.append("")
            continue
        parts = plain.split("/")
        if text.startswith("/") or ".." in parts \
                or not all(_SEGMENT.fullmatch(p) for p in parts):
            raise Refusal("A folder the page cannot hand on",
                          f"`instrumentation.allowed` holds `{entry}`, which is not a folder inside the project "
                          "written plainly. Write it relative to the project, with forward slashes, and "
                          "without `..`, spaces or quotes; then come back.")
        out.append(plain)
    return list(dict.fromkeys(out))


def rules(paths: Sequence[str]) -> list[str]:
    """What the agent may do without asking, as Claude Code's rules: the
    three commands, and edits under each allowed path and of the verdict
    file. A rule from the command line is anchored at the project's folder
    (`/path`). Reading the project and the commands that only read need no
    rule; everything else is refused."""
    out = [rule for _, rule, _ in COMMANDS]
    out += [f"Edit(/{path}/**)" if path else "Edit(/**)" for path in paths]
    out.append(f"Edit(/{VERDICT})")
    return out


def command(program: str, request: str, grant: Sequence[str]) -> list[str]:
    """Claude Code, once, without a window: the request, one JSON object at
    the end, and the grant. The prompt comes before the list of rules, which
    takes every word after it."""
    return [program, "-p", request, "--output-format", "json",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--setting-sources", "project", "--max-budget-usd", str(BUDGET_USD),
            "--allowedTools", *grant]


def shown(grant: Sequence[str]) -> str:
    """The command line as a person would type it, with the request named
    and not written out: it stands in full beside it."""
    return shlex.join(command(PROGRAM, "<the request>", grant))


# ---------------------------------------------------------------------------
# the question and the request


def hunt_question(report: dict[str, Any]) -> str:
    """The investigation's question as its record keeps it, and as its page
    says it in its headline: the request's question, asked in one line."""
    data = report_page._read(report)
    window = (data.get("window") or {}).get("duration_ms")
    name = data.get("scenario") or "the window"
    budget = (data.get("verdict") or {}).get("budget_ms")
    missed = report_page.not_found(data, name)
    if missed is not None:
        # What the screen's question says, and no time: the report judges
        # none where the window is not the scenario's in every recording (#861).
        return f"What is behind what the report found in {name}? echolot did not find {missed[0]}."
    if not isinstance(window, (int, float)) or isinstance(window, bool) or window <= 0:
        return f"What is behind what the report found in {name}?"
    took = f"{window:,.0f} ms"
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0:
        if window > budget:
            return f"What makes {name} take {took} against a time limit of {budget:,.0f} ms?"
        return f"What is behind the {took} {name} takes, within its time limit of {budget:,.0f} ms?"
    return f"What is behind the {took} {name} takes?"


def _listed(items: Sequence[str]) -> str:
    items = [f"`{i}`" for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def facts(n: int, aside: tuple[str, int] | None) -> list[str]:
    """The three facts of the hunt as every client started from the page is
    told them: the investigation open for it and how to file a round in it,
    its first evidence, and after which change, with no reference.

    The evidence is round 1, and every later round records anew: told only
    not to record the recordings set aside again, a live Codex recorded
    nothing at all, and closed the investigation on round 1 alone."""
    if aside:
        folder, count = aside
        one = count == 1
        # One `analyze` for the set: "naming each trace file" was read as one
        # `analyze` per file, three rounds of one launch where there is one of three.
        filing = (f"file it with `echolot analyze --hunt {n}` and the trace file there" if one else
                  f"file them as one round, with a single `echolot analyze --hunt {n}` that names all {count} trace "
                  "files there")
        evidence = (f"- Round 1's evidence is the {'recording' if one else f'{count} recordings'} behind the report, "
                    f"moved aside when it opened, into `{folder}/`: {filing} (it takes files, not a folder), and do "
                    f"not record {'it' if one else 'them'} again. Every later round records anew, once markers are "
                    f"in: build and install the app, then `echolot collect --hunt {n}` and "
                    f"`echolot analyze --hunt {n}`.")
    else:
        evidence = (f"- No recordings were set aside when it opened: round 1 records them, with "
                    f"`echolot collect --hunt {n}` and then `echolot analyze --hunt {n}`, and every later round "
                    "records anew, once markers are in: build and install the app, then the same two.")
    return [
        f"- The investigation is open: #{n}, opened for this question by the page. Carry on in it, do not open "
        f"another, and close it at the end. Run every `collect` and `analyze` in it with `--hunt {n}`: without "
        "it nothing is filed under it.",
        evidence,
        # The page opens it with `--since unknown` and has no earlier build to
        # name, so the hunt is one build's: its answer is where that build
        # spends its time, and its verdict names no reference (#538).
        "- After which change: unknown. Reference: none — no earlier build was recorded to set this one against, "
        "so the answer is where this build spends its time, not what changed.",
    ]


def unattended(n: int, aside: tuple[str, int] | None, paths: Sequence[str]) -> str:
    """What an agent started from the page is told beyond the request a
    person would copy: that nobody can answer it, which investigation it
    works in and on which recordings, what it may do, and how it ends."""
    where = _listed([p or "." for p in paths])
    return "\n".join([
        "This run was started from echolot ui, and nobody is at the keyboard. Nothing you ask can be answered: "
        "a question, or anything that needs a person's approval, is refused and the run goes on. So do not ask. "
        "Decide with what you have, and say in your answer what you would have asked.",
        # The facts come from one place for every client: Codex's request once
        # kept a copy of its own, and it missed `--hunt` when that came in here.
        *facts(n, aside),
        f"- You may run `echolot`, `./gradlew` and `adb` without asking, and edit files under {where}, and "
        f"`{VERDICT}`. Everything else is refused: git, any other program, any other file. Do not commit "
        "anything.",
        # The rule for the verdict is an edit's: a real run's first try was
        # `cat > … <<'EOF'`, refused, and a turn lost (#821).
        f"- Write `{VERDICT}` with your Write or Edit tool. A shell command that writes it, `cat >` or a heredoc, "
        "is refused like any other program.",
        # Both refusals of a real run were habitual compound commands, each
        # a turn lost.
        "- Run one command per call: a loop, or a chain with another program, is refused, and each refusal costs "
        "a turn.",
        f"- Close the investigation with your verdict, as /echolot-hunt says: "
        f"`echolot hunt --done --verdict {VERDICT}`.",
    ])


# ---------------------------------------------------------------------------
# what the scenario's last comparison found worse (#933)
#
# The request carries the report's rows, and the report says nothing of what
# changed since the one before it. In a field run the clearest evidence, a new
# 558 ms wait of the main thread, was the tenth of sixteen rows and "Worth a
# look", while the comparison against the report before had it new in 5 of 5
# runs. So where `echolot compare` left a comparison of this very report
# beside it, the request carries its rows that are new in every run or grew
# by more than the runs disagree, first and marked as such.

# The most rows of the comparison the request carries: comparison.md has all.
COMPARED_ROWS = 8
COMPARED_HEAD = ("What got worse since the report before it, as `echolot compare` found it ({source}): rows new in "
                 "every run, and rows that grew by more than the runs disagree. These come first:")
# A comparison's warnings under which a row it calls new says nothing: the
# bar moved, and a row can cross it with nothing in the app changed; or both
# sides are the same recordings.
_NEW_SAYS_NOTHING = ("thresholds", "defaults", "same-traces")


def _rel(path: Path, project: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _share_of(face: dict[str, Any] | None, runs: int) -> tuple[int, int]:
    """In how many of how many runs a side found a row: its `runs`, or every
    run of the side where it carries none."""
    try:
        k, n = (int(x) for x in str((face or {}).get("runs")).split("/"))
    except ValueError:
        return runs, runs
    return k, n


def _figure(value: Any) -> str:
    return report_page._number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


def _worse_line(row: dict[str, Any], runs: int, new_counts: bool, prefix: str) -> str | None:
    """One row of the comparison as the request gives it, marked, or None
    for a row that is not new in every run and did not grow for sure."""
    metric = str(row.get("metric") or "")
    after, before = row.get("after") or {}, row.get("before") or {}
    bits = [str(row.get("location")), str(row.get("detector") or "")]
    if row.get("change") == "appeared":
        k, n = _share_of(after, runs)
        if not new_counts or k < n or str(row.get("location")).startswith(prefix):
            return None
        mark = "new in the one run" if n == 1 else f"new in {k} of {n} runs"
        bits.append(f"{_figure(after.get(metric))} ms")
    elif row.get("change") == "grew" and row.get("holds") is True:
        shift = row.get("shift") or {}
        mark = "grew for sure"
        least = shift.get("low_ms")
        bits.append(f"{_figure(before.get(metric))} → {_figure(after.get(metric))} ms "
                    f"({row.get('delta_ms'):+g} ms" + (f", at least {least:+g} ms)" if isinstance(least, (int, float))
                                                      else ")"))
    else:
        return None
    if row.get("detail"):
        bits.append(report_mod.clip(str(row["detail"]), report_page.REQUEST_DETAIL))
    return f"- {mark}: " + " · ".join(b for b in bits if b)


def worse(project: Path, report: dict[str, Any], where: str, prefix: str | None = None) -> str | None:
    """The block of the request with what the scenario's last comparison
    found worse: its rows new in every run after, and those that grew and
    hold, in its own order, at most `COMPARED_ROWS`. None where there is no
    comparison beside the report, where it does not read, where its newer
    side is not this report (`compare` run on other files, or a new report
    analysed since), where it says the two cannot be compared, or where no
    row is either. A row new because the hunt planted a marker under
    `prefix` is no new work, and is left out."""
    base = Path(where)
    base = base if base.is_absolute() else project / base
    path = base.parent / "comparison.json"
    found = files_mod.read_comparison(path)
    if isinstance(found, files_mod.Refusal):
        return None
    stamp = report.get("generated_at")
    if not stamp or (found.get("after") or {}).get("generated_at") != stamp or found.get("comparable") is False:
        return None
    said = {w.get("id") for w in found.get("warnings") or []}
    if "same-traces" in said:
        return None
    runs = (found.get("after") or {}).get("runs") or 1
    new_counts = not said & set(_NEW_SAYS_NOTHING)
    lines = [line for line in (_worse_line(r, runs, new_counts, prefix or DEFAULT_PREFIX)
                               for r in found.get("rows") or []) if line]
    if not lines:
        return None
    table = _rel(path.with_name("comparison.md"), project)
    before = (found.get("before") or {}).get("path")
    source = f"`{table}`" + (f", against `{_rel(Path(before), project)}`" if before else "")
    more = len(lines) - COMPARED_ROWS
    return "\n".join([COMPARED_HEAD.format(source=source), *lines[:COMPARED_ROWS],
                      *([f"- and {more} more in `{table}`"] if more > 0 else [])])


def request_text(project: Path, report: dict[str, Any], picks: Sequence[dict[str, Any]], where: str,
                 prefix: str | None = None) -> str:
    """The request a person copies from the screen before the start, and the
    first part of the one the agent is handed: the report's
    (`report_page.request_text`), with what the last comparison found worse
    (`worse`) after the question and the report's warnings, before its rows."""
    text = report_page.request_text(report, picks, where, last=True)
    block = worse(project, report, where, prefix)
    if not block:
        return text
    head = "\n\n" + report_page.ROWS_HEAD
    at = text.find(head)
    return text[:at] + "\n\n" + block + text[at:] if at >= 0 else text + "\n\n" + block


def request(report: dict[str, Any], picks: Sequence[dict[str, Any]], where: str, n: int,
            aside: tuple[str, int] | None, paths: Sequence[str], project: Path | None = None,
            prefix: str | None = None) -> str:
    """The request the started agent is handed: the one a person would copy,
    with what the last comparison found worse where `project` is named,
    then what a run with nobody there has to know."""
    copied = (request_text(project, report, picks, where, prefix) if project is not None
              else report_page.request_text(report, picks, where, last=True))
    return copied + "\n\n" + unattended(n, aside, paths)


# ---------------------------------------------------------------------------
# whether it can start, and what it would do: one reading for the page
# before the press and for the press itself


def layer_missing(project: Path) -> list[str]:
    return [p.as_posix() for p in LAYER if not (project / p).is_file()]


def changed_files(project: Path) -> int | None:
    """How many files of the working tree are changed and not committed, as
    `git status` counts them; None where that cannot be told."""
    try:
        said = subprocess.run(["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True,
                              timeout=15, stdin=subprocess.DEVNULL, errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if said.returncode != 0:
        return None
    return sum(1 for line in said.stdout.splitlines() if line.strip())


def own_allow(project: Path) -> list[str]:
    """What the project's own Claude Code settings allow (`.claude/settings.json`,
    read with `--setting-sources project`) beyond the page's rules: Claude
    Code adds those to the grant, so the page says them."""
    try:
        data = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    allow = ((data.get("permissions") or {}).get("allow") if isinstance(data, dict) else None) or []
    ours = {rule for _, rule, _ in COMMANDS} | {"Bash(echolot:*)"}
    return [str(rule) for rule in allow if isinstance(rule, str) and rule not in ours]


def unplaced(st: dict[str, Any]) -> str | None:
    """Why an agent started from the page would have no folder it may put
    markers in (`allowed`), with where to name one; None when it has one,
    and None when the config does not load, which the start page says."""
    known = st.get("config") or {}
    try:
        allowed(Config.load(known["path"], scenario=known.get("scenario")))
    except Refusal as e:
        return e.text
    except (ConfigError, KeyError, OSError, TypeError):
        return None
    return None


def why_not(project: Path, st: dict[str, Any], lines: list[dict[str, Any]], client: Client, *,
            shell: bool = False) -> str | None:
    """Why a press would start nothing now, in a sentence; None when it would
    start. The same reading before the press, for the page, and at the
    press, against the project as it is then. With `shell`, last of all,
    which `echolot` the agent's shell would run (`runs_own`): the screen
    before the start asks it, and the press, and not the report's dialog,
    which asks no phone either."""
    current = job.read(project, JOB)
    if current is not None and current["status"] == job.RUNNING:
        return "Not now: an agent is already digging into this app. Its page shows how far it is."
    other = job.read(project, check_mod.JOB)
    if other is not None and other["status"] == job.RUNNING:
        return ("Not now: a check is running on the phone. One thing at a time: when it is done, press Dig "
                "deeper again.")
    kind = state.next_kind(st)
    if kind in ("upgrade", "init", "doctor", "setup", "fix-config"):
        return check_mod._stops_all(st)
    if not client.can:
        return client.why
    # What the project needs for this client: Claude Code's commands, or
    # Codex's rules.
    unready = adapter(client.key).ready(project)
    if unready:
        return unready
    # What it needs for every client: a folder to put the markers in. Read
    # here and not only at the press, so that no screen offers a client the
    # press then refuses, or one client in place of another refused alike
    # (#813, #863).
    nowhere = unplaced(st)
    if nowhere:
        return nowhere
    hunt = st.get("hunt") or {}
    if hunt and (hunt.get("status") or "open") == "open":
        asked_ = f" (\"{hunt['question']}\")" if hunt.get("question") else ""
        return (f"Not now: an investigation is open{asked_}. One investigation at a time: its answer, or closing "
                "it on its page, comes first.")
    collect = st.get("collect") or {}
    if collect.get("status") == "running":
        return "Not now: a recording is running, started from a terminal or by an agent. Wait for it to end."
    stops = _stops_recording(lines)
    if stops or not shell:
        return stops
    return runs_own(client, project)


# ---------------------------------------------------------------------------
# a press


def _aside(project: Path, scenario: str | None, since: float) -> tuple[str, int] | None:
    """The folder `echolot hunt` moved the scenario's recordings into, and
    how many there are: the newest `<scenario>-…` folder made since the press."""
    if not scenario:
        return None
    root = project / ".echolot" / "traces"
    found = []
    with contextlib.suppress(OSError):
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith(f"{scenario}-"):
                with contextlib.suppress(OSError):
                    if d.stat().st_mtime + 2 >= since:
                        found.append((d.stat().st_mtime, d))
    if not found:
        return None
    folder = max(found)[1]
    count = sum(1 for p in folder.iterdir() if p.suffix in (".perfetto-trace", ".pftrace"))
    return folder.relative_to(project).as_posix(), count


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def start(project: Path, st: dict[str, Any], lines: list[dict[str, Any]], report: dict[str, Any],
          picks: Sequence[dict[str, Any]], where: str, client: Client, *,
          run: Callable[..., Any] = subprocess.run,
          spawn: Callable[[Path, str, dict[str, Any]], Any] = job._spawn) -> dict[str, Any]:
    """Open the investigation and start the agent in it, or raise `Refusal`
    with the reason. Held against the project as it is now.

    `run` runs a command of echolot's and waits for it, `spawn` starts the
    job's process: both are a test's to replace.
    """
    why = why_not(project, st, lines, client, shell=True)
    if why:
        raise Refusal("The agent cannot start", why)
    agent = adapter(client.key)
    known = st.get("config") or {}
    try:
        cfg = Config.load(known["path"], scenario=known.get("scenario"))
    except (ConfigError, KeyError, OSError) as e:
        raise Refusal("The agent cannot start", f"`echolot.yml` does not load: {e}") from None
    paths = allowed(cfg)
    name = report.get("scenario") or cfg.scenario_name
    several = len(cfg.scenarios) > 1
    question = hunt_question(report)
    echolot = [sys.executable, "-m", "echolot"]
    opening = [*echolot, "hunt", question, "--since", "unknown", "-c", str(known["path"]),
               *(["--scenario", name] if several else [])]
    began = time.time()
    try:
        done = run(opening, cwd=str(project), capture_output=True, text=True, timeout=120,
                   stdin=subprocess.DEVNULL, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        raise Refusal("The investigation could not be opened", f"`echolot hunt` did not run: {e}.", 500) from None
    opened = state.hunt_now(project)
    if done.returncode != 0 or not opened or opened.get("question") != question or opened.get("status") != "open":
        said = _last_line(done.stderr) or _last_line(done.stdout) or "it said nothing"
        raise Refusal("The investigation could not be opened", f"`echolot hunt` said: {said}", 500)
    n = int(opened["n"])
    aside = _aside(project, name, began)
    text = request_text(project, report, picks, where, cfg.temp_prefix) + "\n\n" + agent.unattended(n, aside, paths)
    # The page's own folder first in its PATH: the `echolot` it runs is the
    # page's, as far as its shell keeps that order (`runs_own`).
    step = {"id": STEP, "argv": agent.argv(str(client.program), text, paths, project),
            "shown": agent.shown(paths, project), "tree": True, "unset": list(agent.unset),
            "path": [os.fspath(own_bin())]}
    idle = agent.idle(n)
    if idle:
        step["idle"] = idle
    what = {"client": agent.key, "hunt": n, "question": question, "scenario": name,
            "picks": [c["key"] for c in picks], "report": where, **agent.what()}
    # What the project holds before it starts, for a client whose edits its
    # grant cannot keep to the allowed folders.
    agent.before(project, n, paths)
    try:
        return job.start(project, JOB, [step], what, spawn=spawn)
    except job.Refused as e:
        # Nobody will work in the investigation just opened: it is closed,
        # with that said, so that it holds nothing back.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run([*echolot, "hunt", "--done", f"Not started: echolot ui could not start {agent.name} ({e}).",
                 "-c", str(known["path"])], cwd=str(project), capture_output=True, text=True, timeout=120,
                stdin=subprocess.DEVNULL, errors="replace")
        raise Refusal(f"{agent.name} did not start", f"echolot could not start it: {e}. The investigation it was "
                      "to work in was closed again.", 500) from None


def stop(project: Path) -> bool:
    """Ask the running agent to stop. False when none is running."""
    return job.stop(project, JOB)


# ---------------------------------------------------------------------------
# a run, read back from its files


def _echolot(args: str) -> str | None:
    """Which echolot command a command line runs, or None for none."""
    words = args.split()
    for i, word in enumerate(words):
        if os.path.basename(word) == "echolot" or (word == "-m" and i + 1 < len(words) and words[i + 1] == "echolot"):
            rest = words[i + 2:] if word == "-m" else words[i + 1:]
            return next((w for w in rest if not w.startswith("-")), "")
    return None


def _named(args: str, *names: str) -> bool:
    return any(os.path.basename(word) in names for word in args.split())


def _gradle(args: str) -> bool:
    return _named(args, "gradlew", "gradle") or "GradleWrapperMain" in args or "org.gradle.launcher" in args


# What the agent is doing, from what it runs: the first of these that a
# process under it is, in this order, since one runs inside another (a
# recording runs adb). Each says it in a few words.
_PHASES: tuple[tuple[str, str, Callable[[str], bool]], ...] = (
    ("record", "Recording the app on the phone", lambda a: _echolot(a) == "collect"),
    ("install", "Building the app and putting it on the phone", lambda a: _gradle(a) and "install" in a.lower()),
    ("build", "Building the app", _gradle),
    ("put", "Putting the app on the phone", lambda a: _named(a, "adb") and " install" in a),
    ("read", "Reading the recordings", lambda a: _echolot(a) == "analyze"),
    ("compare", "Comparing with the round before", lambda a: _echolot(a) == "compare"),
    ("unmark", "Taking the markers out of the code", lambda a: _echolot(a) == "mark" and "--remove" in a),
    ("mark", "Putting markers into the code", lambda a: _echolot(a) == "mark"),
    # Only closing it, or checking the verdict first, writes the answer down;
    # any other `hunt` looks at the investigation.
    ("answer", "Writing down the answer",
     lambda a: _echolot(a) == "hunt" and any(w in ("--done", "--verdict") or w.startswith(("--done=", "--verdict="))
                                             for w in a.split())),
    ("look", "Looking at the investigation", lambda a: _echolot(a) == "hunt"),
    ("phone", "Working with the phone", lambda a: _named(a, "adb")),
    ("echolot", "Running echolot", lambda a: _echolot(a) is not None),
)
THINKING = ("think", "Reading the report and the code, and deciding what to do next")


def phase(pid: int | None, table: list[dict[str, Any]]) -> dict[str, Any]:
    """What the agent is doing now, from the processes under it: the key,
    the words, and how long that has run, in seconds, where it is known."""
    under = job.descendants(pid, table) if isinstance(pid, int) else []
    for key, words, test in _PHASES:
        doing = [p for p in under if test(p.get("args") or "")]
        if doing:
            ages = [p["age"] for p in doing if isinstance(p.get("age"), (int, float))]
            return {"key": key, "words": words, "for": max(ages) if ages else None}
    return {"key": THINKING[0], "words": THINKING[1], "for": None}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def moved(project: Path, run: dict[str, Any], table: list[dict[str, Any]], now: float) -> float:
    """When anything last moved: the run or its step started, the
    investigation's record or one of its reports was written, the agent
    printed, or a process under it started. Read from files and from the
    list of processes, never from what the agent said."""
    step = next(iter(run.get("steps") or []), {})
    stamps = [run.get("started") or 0, step.get("started") or 0]
    n = (run.get("what") or {}).get("hunt")
    stamps.append(_mtime(hunt_mod.path(project)) or 0)
    if isinstance(n, int):
        reports = project / hunt_mod.ARCHIVE_DIR / str(n) / "reports"
        with contextlib.suppress(OSError):
            stamps += [_mtime(p) or 0 for p in reports.iterdir()]
    stamps.append(_mtime(job.log_path(project, JOB, STEP)) or 0)
    pid = step.get("pid")
    for p in job.descendants(pid, table) if isinstance(pid, int) else []:
        if isinstance(p.get("age"), (int, float)):
            stamps.append(now - p["age"])
    return max(stamps)


def result(project: Path) -> dict[str, Any] | None:
    """The one JSON object Claude Code prints at the end of a run: its last
    message, whether it ended in an error, what it spent, what it was
    refused. None when it printed none (it was stopped, or never got there)."""
    path = job.log_path(project, JOB, STEP)
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            log.seek(max(0, size - 4 * 1024 * 1024))
            text = log.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict) and ("result" in found or "is_error" in found):
            return found
    return None


def cost(found: dict[str, Any] | None) -> str | None:
    value = (found or {}).get("total_cost_usd")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    return f"${value:,.2f}"


# How Claude Code says a run ended at `--max-budget-usd` (the Agent SDK's
# reference: the result's `subtype`, and its `terminal_reason`). Its own
# estimate, which it says can pass the cap.
CAPPED = ("error_max_budget_usd", "budget_exhausted")


def capped(found: dict[str, Any] | None) -> bool:
    """Whether the run ended because it reached the spending cap."""
    return bool(found) and (found.get("subtype") in CAPPED or found.get("terminal_reason") in CAPPED)


def last_words(found: dict[str, Any] | None) -> str:
    """What it said at the end: its last message, or, for a run that ended in
    an error, the errors it lists in place of one."""
    words = (found or {}).get("result")
    if isinstance(words, str) and words.strip():
        return words.strip()
    errors = (found or {}).get("errors")
    return "\n".join(str(e) for e in errors if str(e).strip()) if isinstance(errors, list) else ""


def denied(found: dict[str, Any] | None) -> list[str]:
    """What the agent tried and was refused, as it tried it: a command, or
    the file it meant to write."""
    out = []
    for item in (found or {}).get("permission_denials") or []:
        if not isinstance(item, dict):
            continue
        given = item.get("tool_input") if isinstance(item.get("tool_input"), dict) else {}
        said = given.get("command") or given.get("file_path") or given.get("path") or ""
        tool = str(item.get("tool_name") or "a tool")
        out.append(f"{tool}: {said}" if said else tool)
    return out


def _closed_after(hunt: dict[str, Any] | None, run: dict[str, Any]) -> str | None:
    """How the investigation came to be closed once the agent had ended:
    "page" when the page closed it (`close`, with words that end in the
    page's own), "later" when it was closed after the run finished some
    other way, as `echolot hunt --done` typed by a person does. None while it
    is open, and when the agent closed it itself, which it can only do while
    it runs. `closed_at` is kept to the second: a close in the second the
    run finished reads as the agent's, unless the page's words say whose.

    The panel took every closed investigation for the agent's, and said
    "It closed the investigation with a few words" of one the page had
    closed, under a headline that said so (#864)."""
    if not hunt or (hunt.get("status") or "open") == "open":
        return None
    words = hunt.get("conclusion")
    if isinstance(words, str) and words.rstrip().endswith(CLOSED_HERE) and not isinstance(hunt.get("verdict"), dict):
        return "page"
    closed, ended = when_mod.iso_epoch(hunt.get("closed_at")), run.get("finished")
    if closed is not None and isinstance(ended, (int, float)) and not isinstance(ended, bool) and closed >= ended:
        return "later"
    return None


def _left(project: Path, st: dict[str, Any] | None) -> dict[str, Any]:
    return state.leftovers(project, st)


def view(project: Path, st: dict[str, Any] | None = None, run: dict[str, Any] | None = None, *,
         procs: Callable[[], list[dict[str, Any]]] = job.processes, now: float | None = None,
         count: bool = True) -> dict[str, Any] | None:
    """The last run as a page shows it, or None when none was started here.

    While it runs: the phase, how long it has run, how long nothing has
    moved, and whether it was asked to stop. Once it ended: a sentence of
    the page's on how, the agent's last words and what it spent, and what is
    left in the code, with the presses that clear it up. `count` off leaves
    out the count of markers in the sources, which walks every source file,
    and the files a client changed outside the allowed folders, which walks
    the project: a page that only says which one is running needs neither.
    """
    run = run if run is not None else job.read(project, JOB)
    if run is None:
        return None
    now = time.time() if now is None else now
    what = run.get("what") or {}
    agent = adapter(what.get("client"))
    who = agent.name
    n = what.get("hunt") if isinstance(what.get("hunt"), int) else None
    found = state.investigation(project, n) if n else None
    hunt = found["hunt"] if found else None
    step = next(iter(run.get("steps") or []), {})
    status = run["status"]
    # Ended by the job because nothing had moved for as long as the client's
    # adapter allows, or being ended so right now.
    idle = run.get("why") == job.IDLE
    out: dict[str, Any] = {
        "status": status, "hunt": n, "question": what.get("question"), "scenario": what.get("scenario"),
        "hunt_status": (hunt or {}).get("status") or ("open" if hunt else None),
        "verdict": isinstance((hunt or {}).get("verdict"), dict), "rounds": len(found["reports"]) if found else 0,
        # How long it ran: to its end, or to now while it runs. One cut short
        # has no end on record, and is not said to have run until now.
        "ran": (max(0.0, (run.get("finished") or now) - (run.get("started") or now))
                if run.get("finished") or status == job.RUNNING else None),
        "shown": step.get("shown") or "", "log": step.get("log") or "",
        "phase": None, "quiet": None, "stuck": False, "stopping": False, "title": "", "text": "", "said": None,
        "cost": None, "cached": None, "denied": [], "left": None, "can_close": False, "can_unmark": False,
        "capped": False, "answered": False,
        "budget": what.get("budget_usd") if isinstance(what.get("budget_usd"), (int, float)) else BUDGET_USD,
        "who": who, "client": agent.key, "idle": idle, "ends_idle": agent.idle_s, "outside": None,
    }
    if status == job.RUNNING:
        table = procs()
        out["phase"] = phase(step.get("pid"), table)
        out["quiet"] = max(0.0, now - moved(project, run, table, now))
        out["stuck"] = out["quiet"] >= QUIET_S
        with contextlib.suppress(OSError):
            out["stopping"] = job.stop_path(project, JOB).read_text(encoding="utf-8").strip() == str(run.get("pid"))
        out["stopping"] = out["stopping"] or idle
        return out

    found = agent.result(project)
    out["cost"], out["denied"], out["capped"] = agent.cost(found), agent.denied(found), agent.capped(found)
    out["cached"] = agent.cached(found)
    # Closed by the agent, or once it had ended: from this page, or by hand.
    later = _closed_after(hunt, run)
    answered = out["hunt_status"] == "concluded" and later is None
    out["answered"] = bool(out["verdict"]) or answered
    by = " from this page" if later == "page" else ""
    words = agent.last_words(found)
    if status == job.DONE and out["verdict"]:
        out["title"] = f"{who} finished."
        out["text"] = "Its answer is on this page."
    elif out["capped"] and status in (job.DONE, job.FAILED):
        # Whatever its exit code: the cap is the reason, and the page says so.
        out["title"] = f"It reached the spending cap of ${out['budget']:g}."
        out["text"] = (f"{who} stopped by itself when its own estimate of what it had spent reached the cap the "
                       "page sets, before it had an answer"
                       + (". It closed the investigation before that." if answered else
                          f". The investigation was closed{by} after that." if later else
                          ". The investigation is still open, with the rounds it has."))
    elif status == job.DONE and answered:
        out["title"] = f"{who} finished."
        out["text"] = ("It closed the investigation with a few words and no table of causes: they are this page's "
                       "headline.")
    elif status == job.DONE:
        out["title"] = f"{who} finished, and wrote down no answer."
        out["text"] = (f"It left the investigation open, and it was closed{by} after that. Its last words are below."
                       if later else
                       "The investigation is still open, with the rounds it has. Its last words are below.")
    elif status == job.FAILED:
        out["title"] = f"{who} stopped with an error."
        out["text"] = ("It ended with an error after the investigation was closed. What it said is below."
                       if answered else
                       "It ended before it had an answer"
                       + (f", and the investigation was closed{by} after that" if later else "") + ". What it said "
                       f"is below: a sign-in that ran out, or a limit of your plan, is put right in {who} itself.")
    elif status == job.STOPPED and idle:
        out["title"] = f"Stopped: nothing had moved for {_minutes(agent.idle_s or QUIET_S)}."
        out["text"] = (f"{who} had run nothing, written nothing and recorded nothing for that long. That is how it "
                       "waits for an answer to a question, and nobody is here to give one, so the page ended it and "
                       "everything it had started.")
    elif status == job.STOPPED:
        out["title"] = "Stopped."
        out["text"] = f"{who} and everything it had started were ended."
    else:
        out["title"] = "This run was cut short."
        out["text"] = (f"The process that ran {who} is gone: `echolot ui` was stopped, the terminal was closed, "
                       "or the computer went to sleep.")
    if not (status == job.DONE and out["verdict"]):
        lines = [line for line in words.splitlines() if line.strip()][-12:] if words else agent.tail(project)
        # Ended from outside, it printed nothing: nothing to show of it.
        if lines or status not in (job.STOPPED, job.INTERRUPTED):
            out["said"] = {"who": who, "lines": lines, "log": out["log"]}
    if count:
        # Whatever way it ended: what it changed where its grant did not keep it.
        out["outside"] = agent.outside(project, run)
    if not out["verdict"]:
        out["can_close"] = out["hunt_status"] == "open"
        if count:
            out["left"] = _left(project, st)
            out["can_unmark"] = bool(out["left"]["removable"] or out["left"]["lines"])
    return out


def _minutes(seconds: float) -> str:
    whole = int(seconds // 60)
    return "a minute" if whole == 1 else f"{whole} minutes"


def closing_words(seen: dict[str, Any]) -> str:
    """What the investigation is closed with when the page closes it: what
    came of it, in a sentence, since no answer did."""
    who = seen.get("who") or CLIENT
    rounds = seen.get("rounds") or 0
    after = f"after {rounds} round{'s' if rounds != 1 else ''}" if rounds else "before its first round"
    if seen.get("capped"):
        return (f"{who} stopped at its spending cap of ${seen.get('budget') or BUDGET_USD:g}, {after}, with no "
                f"answer. {CLOSED_HERE}")
    if seen.get("idle") and seen.get("status") == job.STOPPED:
        return (f"Stopped from echolot ui once {who} had done nothing for "
                f"{_minutes(seen.get('ends_idle') or QUIET_S)}, {after}, with no answer. {CLOSED_HERE}")
    how = {job.STOPPED: "Stopped from echolot ui",
           job.FAILED: f"{who} ended with an error",
           job.INTERRUPTED: f"Cut short, the process that ran {who} gone",
           job.DONE: f"{who} finished and wrote down nothing"}.get(seen.get("status"), "Left")
    return f"{how}, {after}, with no answer. {CLOSED_HERE}"


def close(project: Path, config: str, st: dict[str, Any] | None, *,
          run: Callable[..., Any] = subprocess.run) -> None:
    """Close the investigation an agent of the page left open, with what came
    of it: `echolot hunt --done "<sentence>"`, as a person would. Refused
    while the agent runs, and for an investigation it did not work in."""
    seen = view(project, st, procs=lambda: [], count=False)
    if seen is None or seen["status"] == job.RUNNING or not seen["can_close"]:
        raise Refusal("Nothing to close", "No investigation of an agent the page started is open and idle. "
                      "Its page shows where it stands.")
    current = (st.get("hunt") if st is not None else state.hunt_now(project)) or {}
    if current.get("n") != seen["hunt"] or (current.get("status") or "open") != "open":
        raise Refusal("Nothing to close", "The investigation open now is not the one the agent worked in.")
    done = run([sys.executable, "-m", "echolot", "hunt", "--done", closing_words(seen), "-c", config],
               cwd=str(project), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
               errors="replace")
    if done.returncode != 0:
        raise Refusal("The investigation was not closed",
                      f"`echolot hunt --done` said: {_last_line(done.stderr) or _last_line(done.stdout)}", 500)


def unmark(project: Path, config: str, *, run: Callable[..., Any] = subprocess.run) -> None:
    """Take the temporary markers out: `echolot mark --remove`, as a person
    would. Refused while the agent runs: it may be putting them in."""
    current = job.read(project, JOB)
    if current is not None and current["status"] == job.RUNNING:
        raise Refusal("Not while the agent works", "It may be putting markers in right now. Stop it first, or "
                      "wait for it to end.")
    done = run([sys.executable, "-m", "echolot", "mark", "--remove", "-c", config], cwd=str(project),
               capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL, errors="replace")
    if done.returncode != 0:
        raise Refusal("The markers were not taken out",
                      f"`echolot mark --remove` said: {_last_line(done.stderr) or _last_line(done.stdout)}", 500)


def progress(project: Path, procs: Callable[[], list[dict[str, Any]]] = job.processes) -> dict[str, Any] | None:
    """The run alone, for a page that is already drawn and asks how far it
    is: no count of markers, which is drawn with the page."""
    return view(project, None, procs=procs, count=False)


# ---------------------------------------------------------------------------
# the clients the page starts: one adapter each, and the order it offers them


class ClaudeCode:
    """Claude Code (#449), whose parts are the functions above. Its grant is
    its command line: what it may run, and edits limited to the allowed
    folders by its own rules. It refuses a question rather than waits for
    one, stops at a spending cap, and prints one JSON object at the end."""

    key, name, program = PROGRAM, CLIENT, PROGRAM
    unset = UNSET
    # It refuses a question nobody can answer and goes on: nothing waits, and
    # the job ends nothing for having been quiet.
    idle_s: float | None = None
    # How a person asks a shell what it asks (`runs_own`): its snapshot reads
    # the rc file, as an interactive shell does.
    login = "-lic"

    def find(self) -> Client:
        return find()

    def shell(self, script: str) -> list[str]:
        return claude_shell(script)

    def ready(self, project: Path) -> str | None:
        """Why this project cannot have it now: the command that hunts and the
        agent it hands the loop to, which `echolot init` installs."""
        gone = layer_missing(project)
        if gone:
            return (f"Not yet: this project does not have echolot's commands for {CLIENT} ({_listed(gone)}). "
                    "`echolot init` in the project's folder installs them.")
        return None

    def argv(self, program: str, text: str, paths: Sequence[str], project: Path) -> list[str]:
        return command(program, text, rules(paths))

    def shown(self, paths: Sequence[str], project: Path) -> str:
        return shown(rules(paths))

    def unattended(self, n: int, aside: tuple[str, int] | None, paths: Sequence[str]) -> str:
        return unattended(n, aside, paths)

    def idle(self, n: int) -> dict[str, Any] | None:
        return None

    def what(self) -> dict[str, Any]:
        return {"budget_usd": BUDGET_USD}

    def before(self, project: Path, n: int, paths: Sequence[str]) -> None:
        """Nothing to note first: its own rules keep its edits to the allowed folders."""

    def terms(self, project: Path) -> dict[str, Any]:
        """What the screen before the start says of it besides the rest."""
        return {"own_allow": own_allow(project), "budget": BUDGET_USD}

    def result(self, project: Path) -> dict[str, Any] | None:
        return result(project)

    def cost(self, found: dict[str, Any] | None) -> str | None:
        return cost(found)

    def cached(self, found: dict[str, Any] | None) -> str | None:
        """Its cost is in dollars, and says nothing of a cache."""
        return None

    def denied(self, found: dict[str, Any] | None) -> list[str]:
        return denied(found)

    def capped(self, found: dict[str, Any] | None) -> bool:
        return capped(found)

    def last_words(self, found: dict[str, Any] | None) -> str:
        return last_words(found)

    def tail(self, project: Path) -> list[str]:
        return job.said(project, JOB, STEP)

    def outside(self, project: Path, run: dict[str, Any]) -> dict[str, Any] | None:
        return None


CLAUDE = ClaudeCode()


def clients() -> tuple[Any, ...]:
    """The table: every client the page can start, in the order it offers
    them when nothing says otherwise. Codex's adapter is a module of its own,
    read when the table is."""
    from . import dig_codex
    return (CLAUDE, dig_codex.CODEX)


def adapter(key: str | None) -> Any:
    """A client's adapter by its key. A run that names none, or none of the
    table, is Claude Code's: the runs #449 started name it."""
    return next((a for a in clients() if a.key == key), CLAUDE)


def find_all() -> list[Client]:
    """Every client of the table as the page finds it on this computer."""
    return [a.find() for a in clients()]


def offer(project: Path, found: Sequence[Client], st: dict[str, Any],
          lines: list[dict[str, Any]], *, shell: bool = False) -> list[tuple[Client, str | None]]:
    """The clients found, each with why a press would start nothing with it
    now (None when it would), in the order the page offers them: the one the
    last run started from here used first, since that is the person's own
    choice, then the table's order, which puts the one whose edits its grant
    keeps to the allowed folders first. `shell` asks each one's shell which
    `echolot` it runs (`why_not`)."""
    last = ((job.read(project, JOB) or {}).get("what") or {}).get("client")
    order = sorted(found, key=lambda c: c.key != last)
    return [(c, why_not(project, st, lines, c, shell=shell)) for c in order]


def reasons(offered: Sequence[tuple[Client, str | None]], key: str | None = None) -> list[str]:
    """Why none of the clients offered can start, each reason once, in their
    order: those of the clients on this computer and of the one `key` names,
    and every one where none of them is. That a client is not installed is
    no reason the page cannot start one that is: beside a folder still to
    name, it would read as a second thing to do (#863)."""
    said = [why for c, why in offered if why and (c.program or c.key == key)] \
        or [why for _, why in offered if why]
    return list(dict.fromkeys(said))


def aside(offered: Sequence[tuple[Client, str | None]], key: str | None = None) -> list[str]:
    """Where a client can start: why each other client on this computer
    cannot, besides the one `key` names, whose screen says it. The reasons a
    client was not offered were read and never said, so a person who set
    up one client did not learn what the other needs (#819). A client that
    is not installed is left out: nothing says it is wanted."""
    if not any(why is None for _, why in offered):
        return []
    return list(dict.fromkeys(why for c, why in offered if why and c.program and c.key != key))


def pick(offered: Sequence[tuple[Client, str | None]], key: str | None = None) -> tuple[Client, str | None] | None:
    """The client a press is about: the one its address names, or else the
    first that can start, or else the first. None when the address names a
    client the table does not have."""
    if key is not None:
        return next(((c, why) for c, why in offered if c.key == key), None)
    return next(((c, why) for c, why in offered if why is None), offered[0] if offered else None)
