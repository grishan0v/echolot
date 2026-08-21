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

# Relative on purpose: every reader spells it `project / LOG_FILE`, and the
# project is not always the working directory. `at()` below is what tells the
# writer which one it is.
LOG_DIR = Path(".echolot") / "log"
LOG_FILE = LOG_DIR / "runs.jsonl"

_facts: dict[str, Any] = {}
_root: Path | None = None


def note(**facts: Any) -> None:
    """Attach facts to the current run. Called from inside a command."""
    _facts.update(facts)


def at(root: Path | str | None) -> None:
    """Which project this invocation is about — where `.echolot/` belongs.

    `analyze` is run from wherever the traces are: the agent calls it inside a
    macrobenchmark's output directory, which is named after a build variant
    and a device model. Everything it leaves behind already follows the config
    that named the project rather than the working directory — the report
    through `_out_dir`, the investigation through `_project_root`.

    This file did not, and it was the only one. `LOG_DIR` is a relative path
    and it was resolved against whatever directory the command happened to run
    in, while every reader looks under the project: `status` then said "doctor:
    never run here" on a project where doctor had just passed, `next` could not
    see a failed self-check, and `reflect` read whichever half of the session
    had been run from the right place.
    """
    global _root
    _root = Path(root) if root is not None else None


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
        self._saved = dict(_facts)
        self._saved_root = _root
        _facts.clear()
        return self

    def __exit__(self, *exc):
        _facts.clear()
        _facts.update(self._saved)
        at(self._saved_root)
        return False


def _config_stamp(path: str | None) -> dict[str, Any] | None:
    """Path plus a short content hash: enough to tell two configs apart."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "sha": None}
    digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
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


def record(args: Any, argv: list[str] | None, started: float,
           exit_code: int | None, error: BaseException | None = None) -> None:
    if os.environ.get("ECHOLOT_NO_RECORD"):
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
        stamp = _config_stamp(getattr(args, "config", None))
        if stamp:
            entry["config"] = stamp
        if _facts:
            entry["facts"] = dict(_facts)
        if error is not None:
            tb = "".join(traceback.format_exception(
                type(error), error, error.__traceback__))
            # The tail is what matters; the head is argparse and main().
            entry["error"] = tb[-2000:]
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


def read(path: Path | None = None) -> list[dict[str, Any]]:
    """All recorded runs, oldest first. Missing file → empty list."""
    p = path or log_file()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
