"""The flight recorder: one line per CLI invocation.

`.echolot/log/runs.jsonl` is the tool's own view of what happened to it. It is
written by every command, from every caller — an agent, a human, CI — and it is
the only source of facts that does not depend on which agent was driving:
transcripts differ between agents and lose the exit code the moment a call is
wrapped in `2>&1 | tail`; this file does not.

`echolot reflect` reads it alongside the agent's transcript. Each command may
attach a few facts of its own with `note()` — how many detectors fired, whether
the anchor matched — so that a run can be judged without re-reading the
report.

The recorder must never break the command it records: every failure inside it
is swallowed. Set ECHOLOT_NO_RECORD=1 to switch it off entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import files

# Relative on purpose: every reader spells it `project / LOG_FILE`, and the
# project is not always the working directory. `at()` below is what tells the
# writer which one it is.
LOG_DIR = Path(".echolot") / "log"
LOG_FILE = LOG_DIR / "runs.jsonl"

_facts: dict[str, Any] = {}
_root: Path | None = None
_reason: str | None = None
_skipped = False


def note(**facts: Any) -> None:
    """Attach facts to the current run. Called from inside a command."""
    _facts.update(facts)


def reason() -> str | None:
    """The reason the current run gave for failing, if it gave one."""
    return _reason


def facts() -> dict[str, Any]:
    """The facts attached to the current run so far, as a copy."""
    return dict(_facts)


def failed(reason: str) -> None:
    """Why the command is about to exit non-zero, in the words it printed.

    A clean failure — a device that is not there, a gradle task that did
    not run — used to leave `exit: 2` in the log and nothing else: `error`
    was written only for a traceback. On a real project a `collect` that
    fell over after two minutes of gradle left a line nobody could read a
    cause off, and the transcript's copy of the output was a notice from the
    agent's own harness. The sentence the command printed is the record.
    """
    global _reason
    _reason = reason.strip()[:800] or None


def at(root: Path | str | None) -> None:
    """Which project this invocation is about — where `.echolot/` belongs.

    `analyze` is run from wherever the traces are: the agent calls it inside a
    macrobenchmark's output directory, which is named after a build variant
    and a device model. Everything it leaves behind already follows the config
    that named the project rather than the working directory — the report
    through `_out_dir`, the investigation through `project.resolve`.

    This file did not, and it was the only one. `LOG_DIR` is a relative path
    and it was resolved against whatever directory the command happened to run
    in, while every reader looks under the project: `status` then said "doctor:
    never run here" on a project where doctor had just passed, `next` could not
    see a failed self-check, and `reflect` read whichever half of the session
    had been run from the right place.
    """
    global _root
    _root = Path(root) if root is not None else None


def skip() -> None:
    """This run leaves no line.

    `uninit` alone calls it, once it has taken `.echolot/` out of the
    project: the line `main` writes after every command would create the
    folder again the moment it went, with a log of one run in it (#562).
    """
    global _skipped
    _skipped = True


def log_file(root: Path | None = None) -> Path:
    """Where this invocation's line goes. `at()` decides; cwd is the fallback."""
    return (root or _root or Path.cwd()) / LOG_FILE


class isolated:
    """A scope whose notes do not reach the run being recorded.

    The self-check calls commands (`init` into a temp dir, for one), and
    those note facts of their own; without this, `doctor`'s line in the log
    carried `written: 2, overwritten: 1` from a temp directory that no
    longer existed.

    The project is saved and restored for the same reason: a command run
    inside a check must not move where the run hosting it is writing.
    """

    def __enter__(self):
        global _reason, _skipped
        self._saved = dict(_facts)
        self._saved_root = _root
        self._saved_reason = _reason
        self._saved_skipped = _skipped
        _facts.clear()
        _reason = None
        _skipped = False
        return self

    def __exit__(self, *exc):
        global _reason, _skipped
        _facts.clear()
        _facts.update(self._saved)
        _reason = self._saved_reason
        _skipped = self._saved_skipped
        at(self._saved_root)
        return False


def _config_stamp(path: str | None) -> dict[str, Any] | None:
    """Path plus a short content hash: enough to tell two configs apart."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "sha": None}
    # A directory, or a file it may not read: the stamp goes without its
    # hash. Raised, it took the whole line with it, past the `except` that
    # keeps recording from ever failing a command.
    try:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return {"path": str(p), "sha": None}
    return {"path": str(p), "sha": digest}


def version() -> str:
    """The version of the code that is running.

    Read from the package rather than from installed metadata. An editable
    install keeps the dist-info it was created with, so importlib.metadata
    happily answers 0.1.0 for a checkout that says 0.4.0 — and that number is
    stamped into every line of this log, into the layer manifest, and into the
    first line `doctor` prints.
    """
    try:
        from . import __version__
        return __version__
    except Exception:
        return "unknown"


_version = version   # the name the rest of this module uses


# Where an agent host puts the id of the session whose command this is, and
# the host's name as `reflect`'s readers spell it. Claude Code exports
# `CLAUDE_CODE_SESSION_ID`, the name of the session's transcript file; Codex
# `CODEX_THREAD_ID`, the thread's — a subagent's commands carry the
# subagent's own thread. `ECHOLOT_SESSION` comes first, for any other host, a
# hook or CI to name its own; Cursor gives its commands no id yet. Without
# one the line has no `session`, the same as a line written before there was
# such a field, and `reflect` matches it by time as it always has (#543).
_SESSION_ENV: tuple[tuple[str, str | None], ...] = (
    ("ECHOLOT_SESSION", None),
    ("CLAUDE_CODE_SESSION_ID", "claude-code"),
    ("CODEX_THREAD_ID", "codex"),
)
# An id is an opaque token. Anything else in one of those variables — a path,
# a sentence, a value with a slash or a space in it — is not written: this
# file must never carry a home directory or whatever else a host happened to
# put there. `reflect --from-log` names a report file after the id, so it is
# also a name every filesystem takes.
# The reader of the log holds a `session` to the same token (`files.RUN`).
_SESSION_ID = files.SESSION_ID


def session(env: Any = None) -> tuple[str | None, str | None]:
    """`(id, host)` of the agent session running this command; `(None,
    None)` when the environment names none that reads as an id."""
    env = os.environ if env is None else env
    for var, host in _SESSION_ENV:
        value = str(env.get(var) or "").strip()
        if _SESSION_ID.fullmatch(value):
            return value, host
    return None, None


def record(args: Any, argv: list[str] | None, started: float,
           exit_code: int | None, error: BaseException | None = None) -> None:
    global _reason, _skipped
    if os.environ.get("ECHOLOT_NO_RECORD"):
        _skipped = False
        return
    if _skipped:
        _facts.clear()
        _reason = None
        _skipped = False
        return
    try:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(started, timezone.utc)
                          .isoformat(timespec="seconds"),
            "cmd": getattr(args, "cmd", None),
            "argv": list(argv if argv is not None else sys.argv[1:]),
            "cwd": os.getcwd(),
            "exit": exit_code,
            "ms": int((time.time() - started) * 1000),
            "version": _version(),
        }
        # Which agent session asked. Three sessions working in one checkout
        # write to one log at the same seconds, and time alone cannot tell
        # their lines apart (#543).
        sid, host = session()
        if sid:
            entry["session"] = sid
            if host:
                entry["agent"] = host
        try:
            stamp = _config_stamp(getattr(args, "config", None))
        except Exception:
            stamp = None
        if stamp:
            entry["config"] = stamp
        if _facts:
            entry["facts"] = dict(_facts)
        if isinstance(error, KeyboardInterrupt):
            # Ctrl-C is an interruption, not a crash: the shell reports 130,
            # and a traceback without the word in its last 160 characters
            # read as echolot failing.
            entry["exit"] = 130
            entry["error"] = "interrupted"
        elif error is not None:
            tb = "".join(traceback.format_exception(
                type(error), error, error.__traceback__))
            # The tail is what matters; the head is argparse and main().
            entry["error"] = tb[-2000:]
        elif exit_code not in (0, None) and _reason:
            entry["error"] = _reason
        # `cwd` above and the file below are two different questions, and
        # they used to have one answer. Where the command ran is a fact worth
        # keeping; where the log lives is the project.
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    finally:
        _facts.clear()
        _reason = None
        _skipped = False


def read(path: Path | None = None) -> list[dict[str, Any]]:
    """All recorded runs, oldest first. Missing file → empty list.

    `files.read_runs`, the one reader of the log: a line that is no run is
    skipped, and a field that holds what `record` never writes there is
    dropped with its line kept (#555).
    """
    return list(files.read_runs(path or log_file()))
