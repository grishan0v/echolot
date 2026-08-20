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

LOG_DIR = Path(".echolot") / "log"
LOG_FILE = LOG_DIR / "runs.jsonl"

_facts: dict[str, Any] = {}


def note(**facts: Any) -> None:
    """Attach facts to the current run. Called from inside a command."""
    _facts.update(facts)


class isolated:
    """A scope whose notes do not reach the run being recorded.

    The self-check calls commands (`init` into a temp dir, for one), and
    those note facts of their own; without this, `doctor`'s line in the log
    carried `written: 2, overwritten: 1` from a temp directory that no
    longer existed.
    """

    def __enter__(self):
        self._saved = dict(_facts)
        _facts.clear()
        return self

    def __exit__(self, *exc):
        _facts.clear()
        _facts.update(self._saved)
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
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    finally:
        _facts.clear()


def read(path: Path | None = None) -> list[dict[str, Any]]:
    """All recorded runs, oldest first. Missing file → empty list."""
    p = path or LOG_FILE
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
