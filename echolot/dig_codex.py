"""Dig deeper with Codex (#454): the second client of the launcher in
`echolot/dig.py`, whose table holds the one adapter defined here.

A press runs what a person would run by hand, as for Claude Code:

    echolot hunt "<the question>" --since unknown       the investigation
    codex exec --json -s workspace-write -C <project> "<the request>"

Codex differs from Claude Code in four things, and each is decided here:

- **The grant is not on the command line.** Under `exec` Codex asks nothing
  (its approval policy is `never`): a command runs in its sandbox, which
  writes only inside the project and has no network, and a command a rule
  lets out runs outside it. The rules are `.codex/rules/echolot.rules`, which
  `echolot init --for codex` writes and owns (`echolot/codex.py`): `echolot`,
  `./gradlew` and `adb`. Codex reads them only in a project it trusts, and
  `exec` marks the project trusted in the person's own `~/.codex/config.toml`
  by itself; the screen before the start says so. The page starts it only in
  a git repository, which Codex asks for unless told otherwise, and never in
  a project the person marked untrusted.
- **Its edits cannot be kept to `instrumentation.allowed`.** So the page notes
  the project's files when it starts (`before`, a size and a time each) and
  lists afterwards the ones changed outside the allowed folders (`outside`),
  whichever way the run ended.
- **It waits for an answer nobody gives.** Asked a question with nobody
  there, it accepts it and then sleeps for hours. The request says nobody can
  answer, and the job ends a run under which nothing has moved for
  `IDLE_S` (`idle`): no event printed, no round filed, no command running.
- **What it says comes as events**, one JSON object a line (`result`): its
  last message, the tokens it used (no money figure exists), and what its
  sandbox refused, which shows only as a failed command, since the exit code
  is 0 even then.

Stop is the launcher's own: Ctrl-C to everything under the client, the
deepest first, then to the client, and what is left is ended. Codex starts
each command in a process group of its own, which a signal to the client's
group never reaches; the job finds them by their parent instead
(`job.descendants`), and so reaches them.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any, Sequence

from . import codex as codex_mod
from . import dig, hosts, job, sandbox
from . import hunt as hunt_mod
from .dig import Client
from .mark import under_allowed

KEY = PROGRAM = "codex"
NAME = "Codex"
# The oldest Codex whose `exec --json` events this module was made against.
MIN_VERSION = (0, 159, 0)
# How long nothing may move under it before the job ends it: three times the
# page's "nothing has moved" (`dig.QUIET_S`), longer than any silence of a
# real hunt (four and five minutes: an edit, a build, an install), and far
# shorter than the twelve hours Codex sleeps on a question.
IDLE_S = 1800.0
# Its own helper processes, which run beside it the whole time and say nothing.
HELPERS = ("codex",)
# Names a Codex started from the page must not find: a Claude Code terminal's,
# and the marks of a Codex sandbox the page's server may have been started in.
UNSET = ("CLAUDECODE", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")

# The project's files as they were when it started, for the check afterwards.
SNAPSHOT = job.LOG_DIR / "dig.files.json"
# Folders the check does not look into: echolot's own and git's, and what a
# build or the IDE writes there while it works.
SKIP_DIRS = frozenset({".git", ".gradle", ".idea", ".kotlin", ".cxx", ".externalNativeBuild", "build", "captures",
                       "node_modules"})
SKIP_FILES = frozenset({".DS_Store"})
# The most files the check notes; a project with more outside the allowed
# folders is said to be too large to check.
FILES_MAX = 100_000
# A file written up to this long after the run ended is still its: the end is
# recorded once the process is gone, and a file system keeps times coarsely.
SLACK_S = 2.0

# What a refusal by its sandbox looks like in what a command printed: the
# system's words for a write or a port it refused, or echolot's own for its
# port on localhost.
REFUSED = ("Operation not permitted", "Read-only file system", "Codex's sandbox")
# The events the page reads, found before a line is parsed: most of the log is
# what commands printed, megabytes of a build.
_WANTED = (b"turn.completed", b"turn.failed", b'"error"', b"agent_message", b'"failed"', b'"declined"')
# What it says on stderr every time, before anything else.
_NOISE = ("Reading additional input from stdin",)
_SHELLS = ("sh", "bash", "zsh", "dash")


# ---------------------------------------------------------------------------
# whether it can be started from here


def find() -> Client:
    """Codex on this computer: where it is, which version, and how it is
    signed in (`codex login status`, which names no account). Asked anew every
    time; a test hands in its own `dig._which` and `dig._run`."""
    program = dig._which(PROGRAM)
    if not program:
        return Client(key=KEY, why=f"{NAME} is not installed on this computer: there is no `{PROGRAM}` command here.")
    said = dig._run([program, "--version"])
    found = dig._version(said.stdout if said else "")
    if found is None:
        return Client(key=KEY, program=program, why=f"{NAME} did not say which version it is (`{PROGRAM} --version`), "
                                                    "so the page cannot tell whether it can read what it prints.")
    version = ".".join(str(part) for part in found)
    if found < MIN_VERSION:
        least = ".".join(str(part) for part in MIN_VERSION)
        return Client(key=KEY, program=program, version=version,
                      why=f"{NAME} {version} is older than {least}, the oldest whose report of a run the page knows "
                          "how to read. Update it, then come back.")
    status = dig._run([program, "login", "status"])
    if status is None:
        return Client(key=KEY, program=program, version=version,
                      why=f"{NAME} did not say whether it is signed in (`{PROGRAM} login status`).")
    # It says it on stderr.
    text = f"{status.stdout or ''}\n{status.stderr or ''}"
    if status.returncode != 0 or "Logged in" not in text:
        return Client(key=KEY, program=program, version=version,
                      why=f"{NAME} is installed and not signed in. Sign in once in a terminal (`{PROGRAM} login`), "
                          "then come back.")
    how = "with ChatGPT" if "ChatGPT" in text else "with an API key" if "API key" in text else None
    return Client(key=KEY, program=program, version=version, how=how)


def ready(project: Path) -> str | None:
    """Why this project cannot have Codex now: not a git repository, marked
    untrusted in the person's own settings, or rules that do not let what an
    investigation runs out of the sandbox. None when it can."""
    if codex_mod._git_root(project.absolute()) is None:
        return (f"Not here: {NAME} starts only in a git repository, so that what it changes can be told and "
                "undone, and this project is not one. Make it one (`git init`, then a first commit), then come back.")
    if codex_mod.trust(project) == "untrusted":
        return (f"Not here: your {NAME} settings ({sandbox.shown(codex_mod.config_file())}) mark this project "
                f"untrusted, and {NAME} reads no rules in a project it does not trust: nothing would let echolot, "
                "Gradle and adb out of its sandbox. Change that in Codex first, then come back.")
    missing = [word for word in codex_mod.COMMANDS if word not in codex_mod.project_lets_out(project)]
    if not missing:
        return None
    cmd = codex_mod.init_command(hosts.keys(project), project)
    state = codex_mod.rule_state(project)
    if state == "missing":
        return (f"Not yet: this project has no `{codex_mod.RULE_SHOWN}`, the rules that let echolot, Gradle and adb "
                f"out of {NAME}'s sandbox. `{cmd}`, in a terminal in the project's folder, writes it.")
    if state == "stale":
        return (f"Not yet: this project's `{codex_mod.RULE_SHOWN}` is from an older echolot. It lets `echolot` out of "
                f"{NAME}'s sandbox, and not `./gradlew` or `adb`, which an investigation runs too. `{cmd}` brings it "
                "up to date.")
    return (f"Not yet: `{codex_mod.RULE_SHOWN}` was edited here, and does not let {dig._listed(missing)} out of "
            f"{NAME}'s sandbox. Delete it, and `{cmd}` writes echolot's again.")


# ---------------------------------------------------------------------------
# the command line and the request


def command(program: str, request: str, project: Path) -> list[str]:
    """Codex, once, without a window: its events as JSON Lines, commands in
    its sandbox with writes inside the project, the project as its working
    root, and the request last. No flag bypasses its approvals or its sandbox."""
    return [program, "exec", "--json", "-s", "workspace-write", "-C", os.fspath(project.absolute()), request]


def shown(project: Path) -> str:
    """The command line as a person would type it, the request named."""
    return shlex.join(command(PROGRAM, "<the request>", project))


def unattended(n: int, aside: tuple[str, int] | None, paths: Sequence[str]) -> str:
    """What a Codex started from the page is told beyond the request a person
    would copy: that nobody can answer it and a run that waits is ended, how
    the hunt goes here, which investigation and recordings, what the rules
    let out of the sandbox and what it may edit, and how it ends."""
    where = dig._listed([p or "." for p in paths])
    if aside:
        folder, count = aside
        evidence = (f"- Its first evidence is the {count} recording{'s' if count != 1 else ''} behind the report, "
                    f"moved aside when it opened, into `{folder}/`. Do not record {'them' if count != 1 else 'it'} "
                    "again.")
    else:
        evidence = "- No recordings were set aside when it opened: record them in its first round."
    return "\n".join([
        "This run was started from echolot ui, and nobody is at the keyboard. Nothing you ask can be answered, and a "
        "run that waits for an answer is ended. So do not ask, and do not wait. Decide with what you have, and say in "
        "your answer what you would have asked.",
        "- /echolot-hunt is Claude Code's command; here `echolot guide hunt` prints the same steps. Run it first, and "
        "follow it.",
        f"- The investigation is open: #{n}, opened for this question by the page. Carry on in it, do not open "
        "another, and close it at the end.",
        evidence,
        "- After which change: unknown.",
        f"- The rules in `{codex_mod.RULE_SHOWN}` let `echolot`, `./gradlew` and `adb` out of your sandbox; anything "
        f"else runs inside it, with no network. Edit files under {where}, and `{dig.VERDICT}`, and nothing else: the "
        "page lists every other file you change. Do not commit anything.",
        "- Run each command on its own: a rule lets a command line out only when it is that command alone, with no "
        "pipe, `&&`, redirect, glob or loop.",
        f"- Close the investigation with your verdict, as `echolot guide hunt` says: "
        f"`echolot hunt --done --verdict {dig.VERDICT}`.",
    ])


def idle(n: int) -> dict[str, Any]:
    """The job's watchdog for it: what says it moved, and for how long nothing
    may. The events it prints, the investigation's record and its reports."""
    here = Path(".")
    return {"seconds": IDLE_S, "helpers": list(HELPERS),
            "watch": [job.log_path(here, dig.JOB, dig.STEP).as_posix(), hunt_mod.HUNT_FILE.as_posix(),
                      (hunt_mod.ARCHIVE_DIR / str(n) / "reports").as_posix()]}


def terms(project: Path) -> dict[str, Any]:
    """What the screen before the start says of it besides the rest: whether
    its settings trust the project already, where those settings are, and
    the other rules files it reads, whose rules apply too."""
    ours = (project / codex_mod.RULE_PATH).resolve()
    others = []
    with contextlib.suppress(OSError):
        others += [p.relative_to(project).as_posix() for p in sorted((project / ".codex" / "rules").glob("*.rules"))
                   if p.resolve() != ours]
    with contextlib.suppress(OSError):
        others += [sandbox.shown(p) for p in sorted((sandbox.codex_home(os.environ) / "rules").glob("*.rules"))]
    return {"trust": codex_mod.trust(project), "config": sandbox.shown(codex_mod.config_file()),
            "rules_file": codex_mod.RULE_SHOWN, "other_rules": others, "idle": IDLE_S}


# ---------------------------------------------------------------------------
# what it changed outside the allowed folders


def _inside(rel: str, paths: Sequence[str]) -> bool:
    """Whether a path is under one of the allowed folders, as `echolot mark`
    reads them (globs, segment by segment); `""` is the whole project."""
    return "" in paths or under_allowed(rel, [p for p in paths if p])


def walk(project: Path, paths: Sequence[str]) -> tuple[dict[str, list[int]], bool]:
    """The project's files the check looks at, by path: each one's size and
    when it was last written, in nanoseconds. Not under the allowed folders,
    not in `.echolot/` or a folder of `SKIP_DIRS`. False beside them when
    there were more than `FILES_MAX` and the walk stopped."""
    files: dict[str, list[int]] = {}
    if "" in paths:
        return files, True                      # it may edit everything: nothing is outside
    stack = [project]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for entry in entries:
            rel = Path(entry.path).relative_to(project).as_posix()
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS and rel != ".echolot" and not _inside(rel, paths):
                        stack.append(Path(entry.path))
                    continue
                if entry.name in SKIP_FILES or _inside(rel, paths):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            files[rel] = [st.st_size, st.st_mtime_ns]
            if len(files) > FILES_MAX:
                return files, False
    return files, True


def before(project: Path, n: int, paths: Sequence[str]) -> None:
    """Note the project's files as they are now, for the check afterwards.
    Nothing is noted where that cannot be written: the check then says so."""
    files, complete = walk(project, paths)
    data = {"hunt": n, "taken": time.time(), "allowed": list(paths), "complete": complete,
            "files": files if complete else {}}
    with contextlib.suppress(OSError):
        path = project / SNAPSHOT
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def outside(project: Path, run: dict[str, Any]) -> dict[str, Any]:
    """The files of the project it changed outside the allowed folders,
    whatever way it ended: written, new or gone, against what `before` noted.
    A file written again after the run ended reads as the person's, and is
    not listed; one gone is listed, since nothing tells when it went.

    `checked` is False with `why` in a sentence where the page cannot tell."""
    what = run.get("what") or {}
    try:
        noted = json.loads((project / SNAPSHOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        noted = None
    if not isinstance(noted, dict) or noted.get("hunt") != what.get("hunt") or not isinstance(noted.get("files"), dict) \
            or not isinstance(noted.get("allowed"), list):
        return {"checked": False, "allowed": [],
                "why": "The page did not note the project's files when it started, so it cannot tell what changed."}
    paths = [str(p) for p in noted["allowed"]]
    big = (f"This project has more than {FILES_MAX:,} files outside the folders it may edit, more than the page "
           "notes, so it cannot tell what changed.")
    if not noted.get("complete"):
        return {"checked": False, "allowed": paths, "why": big}
    now, complete = walk(project, paths)
    if not complete:
        return {"checked": False, "allowed": paths, "why": big}
    ended = run.get("finished")
    until = int((ended + SLACK_S) * 1e9) if isinstance(ended, (int, float)) else None
    was = noted["files"]
    files = []
    for rel in sorted(set(now) | set(was)):
        if rel not in now:
            files.append({"path": rel, "how": "removed"})
        elif until is not None and now[rel][1] > until:
            continue
        elif rel not in was:
            files.append({"path": rel, "how": "new"})
        elif list(now[rel]) != list(was[rel]):
            files.append({"path": rel, "how": "changed"})
    return {"checked": True, "allowed": paths, "files": files}


# ---------------------------------------------------------------------------
# what it printed, read back


def _command(text: Any) -> str:
    """A command as it was run, without the shell Codex wraps each one in
    (`/bin/zsh -lc '…'`)."""
    words = [str(w) for w in text] if isinstance(text, list) else None
    if words is None:
        try:
            words = shlex.split(str(text or ""))
        except ValueError:
            return str(text or "")
    if len(words) == 3 and os.path.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
        return words[2]
    return shlex.join(words) if isinstance(text, list) else str(text or "")


def result(project: Path) -> dict[str, Any] | None:
    """What a run printed, read from its events: the tokens of its last
    turn, its last message, the errors it ended with, and what its sandbox
    refused or Codex declined. None when it printed no event (stopped before
    it started, or it never got there)."""
    path = job.log_path(project, dig.JOB, dig.STEP)
    found: dict[str, Any] = {"events": 0, "usage": None, "last": "", "errors": [], "denied": [], "completed": False}
    try:
        with open(path, "rb") as log:
            for raw in log:
                line = raw.strip()
                if not line.startswith(b"{"):
                    continue
                found["events"] += 1
                if not any(word in line for word in _WANTED):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    _read(event, found)
    except OSError:
        return None
    return found if found["events"] else None


def _read(event: dict[str, Any], found: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "turn.completed":
        found["completed"] = True
        if isinstance(event.get("usage"), dict):
            found["usage"] = event["usage"]
    elif kind == "turn.failed":
        message = (event.get("error") or {}).get("message") if isinstance(event.get("error"), dict) else None
        if isinstance(message, str) and message.strip():
            found["errors"].append(message.strip())
    elif kind == "error":
        if isinstance(event.get("message"), str) and event["message"].strip():
            found["errors"].append(event["message"].strip())
    elif kind == "item.completed" and isinstance(event.get("item"), dict):
        item = event["item"]
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str) and item["text"].strip():
            found["last"] = item["text"].strip()
        elif item.get("type") == "command_execution":
            said = str(item.get("aggregated_output") or "")
            if item.get("status") == "declined" or (item.get("status") == "failed"
                                                     and any(word in said for word in REFUSED)):
                found["denied"].append(_command(item.get("command")))
        elif item.get("type") == "file_change" and item.get("status") == "failed":
            changes = [str(c.get("path")) for c in item.get("changes") or [] if isinstance(c, dict) and c.get("path")]
            found["denied"].append("a change to " + ", ".join(changes) if changes else "a change to a file")


def cost(found: dict[str, Any] | None) -> str | None:
    """The tokens it used, as its last turn counts them: read and written,
    the cached among the read."""
    usage = (found or {}).get("usage") or {}
    parts = [usage.get("input_tokens"), usage.get("output_tokens")]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in parts):
        return None
    return f"{sum(parts):,} tokens"


def last_words(found: dict[str, Any] | None) -> str:
    """What it said at the end: its last message, or the errors a run that
    did not finish ended with."""
    if not found:
        return ""
    if found["errors"] and not found["completed"]:
        return "\n".join(found["errors"])
    return found["last"] or "\n".join(found["errors"])


def tail(project: Path) -> list[str]:
    """What it printed besides its events, the last lines of it: where a
    run that could not begin says why."""
    lines = job.said(project, dig.JOB, dig.STEP, lines=200)
    return [line for line in lines if not line.lstrip().startswith("{")
            and not any(line.lstrip().startswith(noise) for noise in _NOISE)][-12:]


# ---------------------------------------------------------------------------
# the adapter


class Codex:
    """Codex as `dig`'s table holds it: the parts above, by the names the
    launcher asks for."""

    key, name, program = KEY, NAME, PROGRAM
    unset = UNSET
    idle_s: float | None = IDLE_S

    def find(self) -> Client:
        return find()

    def ready(self, project: Path) -> str | None:
        return ready(project)

    def argv(self, program: str, text: str, paths: Sequence[str], project: Path) -> list[str]:
        return command(program, text, project)

    def shown(self, paths: Sequence[str], project: Path) -> str:
        return shown(project)

    def unattended(self, n: int, aside: tuple[str, int] | None, paths: Sequence[str]) -> str:
        return unattended(n, aside, paths)

    def idle(self, n: int) -> dict[str, Any] | None:
        return idle(n)

    def what(self) -> dict[str, Any]:
        return {}

    def before(self, project: Path, n: int, paths: Sequence[str]) -> None:
        before(project, n, paths)

    def terms(self, project: Path) -> dict[str, Any]:
        return terms(project)

    def result(self, project: Path) -> dict[str, Any] | None:
        return result(project)

    def cost(self, found: dict[str, Any] | None) -> str | None:
        return cost(found)

    def denied(self, found: dict[str, Any] | None) -> list[str]:
        return list((found or {}).get("denied") or [])

    def capped(self, found: dict[str, Any] | None) -> bool:
        return False                            # there is no spending cap to reach

    def last_words(self, found: dict[str, Any] | None) -> str:
        return last_words(found)

    def tail(self, project: Path) -> list[str]:
        return tail(project)

    def outside(self, project: Path, run: dict[str, Any]) -> dict[str, Any] | None:
        return outside(project, run)


CODEX = Codex()
