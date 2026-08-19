"""The reader that needs no transcript: `.echolot/log/runs.jsonl`.

`echolot init` points five clients at this tool, and `reflect` read one of
them. Everyone else got an error naming a directory they will never have.

The recorder is the way out, and it was always meant to be. It is written by
every command from every caller — an agent, a human, CI — and it does not
depend on which agent was driving: transcripts differ between clients and lose
the exit code the moment a call is wrapped in `2>&1 | tail`; this file does
not. What it holds is exactly one thing, and it holds it for everybody: which
echolot commands ran, when, for how long, with what exit code and what facts
each attached.

So this reader produces a Session whose only tool calls are echolot's own.
Every check that reads `f.echolot_calls` then works unchanged — doctor before
analyze, failures, retries, help lookups. Every check that needs the agent's
other tool calls has nothing to work from, and **that is the dangerous part**:
`trace_opened_directly` finding no evidence returns "the trace was never
opened directly", which on this source would be a green tick over a question
nobody asked. `Session.carries` is what stops it — a reader says what it can
show, and a check needing more is reported as not checked.

## A sitting, not a session

The log is a stream, not a set of files, so there is no session id in it. What
there is, is gaps: a run half a second after the last one belongs with it, and
one four hours later does not. Sittings are cut at `GAP_MINUTES`, the same
notion `hunt` already uses to decide whether it is looking at the same sitting
before asking a question.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import recorder
from .model import MAIN, Call, Session, epoch_to_ts, ts_to_epoch

AGENT_NAME = "recorder"

# What separates one sitting from the next. Half an hour: long enough that a
# gradle build or a device swap stays inside one, short enough that yesterday
# does not join today.
GAP_MINUTES = 30


@dataclass
class SessionRef:
    id: str
    path: Path
    mtime: float
    size: int
    entries: list[dict[str, Any]]


def log_path(project: Path) -> Path:
    return project / recorder.LOG_FILE


def sittings(runs: list[dict[str, Any]],
             gap_minutes: int = GAP_MINUTES) -> list[list[dict[str, Any]]]:
    """Consecutive runs, cut wherever the tool was left alone for a while."""
    ordered = sorted((r for r in runs if r.get("ts")),
                     key=lambda r: ts_to_epoch(r["ts"]))
    out: list[list[dict[str, Any]]] = []
    gap = gap_minutes * 60
    for run in ordered:
        if out and ts_to_epoch(run["ts"]) - _end_epoch(out[-1][-1]) <= gap:
            out[-1].append(run)
        else:
            out.append([run])
    return out


def _end_epoch(run: dict[str, Any]) -> float:
    return ts_to_epoch(run.get("ts")) + (run.get("ms") or 0) / 1000.0


def list_sessions(project: Path) -> list[SessionRef]:
    """Every sitting in this project's log, newest first."""
    path = log_path(project)
    runs = recorder.read(path)
    refs = []
    for entries in sittings(runs):
        first = entries[0]["ts"]
        # Stable across runs and unique per sitting: the filename it becomes
        # must not change when the log grows, and must not collide with the
        # sitting before it on the same day.
        ident = hashlib.sha256(
            f"{project}\n{first}".encode()).hexdigest()[:12]
        refs.append(SessionRef(ident, path, _end_epoch(entries[-1]),
                               len(entries), entries))
    refs.sort(key=lambda r: r.mtime, reverse=True)
    return refs


def echolot_subcommands(session: Session) -> list[str]:
    """Every subcommand in the sitting, in order. Here they are all of it."""
    return [c.input["sub"] for c in session.calls if "sub" in (c.input or {})]


def involves_echolot(session: Session) -> bool:
    """Anything but reflect on its own.

    A sitting whose only entry is the `reflect` that produced this report is
    not worth a report, and it is always the newest candidate.
    """
    return any(sub != "reflect" for sub in echolot_subcommands(session))


def read_session(ref: SessionRef) -> Session:
    """One sitting as the normalised session every signal reads."""
    entries = ref.entries
    session = Session(
        id=ref.id,
        agent=AGENT_NAME,
        cwd=entries[-1].get("cwd"),
        agent_version=next((e.get("version") for e in reversed(entries)
                            if e.get("version")), None),
        started=entries[0].get("ts"),
        ended=epoch_to_ts(_end_epoch(entries[-1])),
        # Nothing but echolot's own calls. Named rather than left empty, so a
        # check that needs more is skipped instead of reading as clean.
        carries=[],
        notes=[
            "read from .echolot/log/runs.jsonl — no agent transcript was used",
            "the agent's own tools, its turns, its questions to the human and "
            "its token usage are not in this source and are not reported",
        ],
    )
    for i, entry in enumerate(entries):
        argv = entry.get("argv") or []
        command = " ".join(["echolot", *(str(a) for a in argv)]).strip()
        exit_code = entry.get("exit")
        session.calls.append(Call(
            id=f"{ref.id}-{i}",
            ts=entry.get("ts") or "",
            tool="Bash",
            input={"command": command, "sub": entry.get("cmd") or ""},
            agent=MAIN,
            is_error=exit_code not in (0, None),
            # The recorder keeps the exit code the transcript would have lost
            # behind a `| tail`. Spelled the way a shell reports it, because
            # that is the shape the fact extraction already reads.
            output_head="" if exit_code in (0, None) else f"Exit code {exit_code}",
            duration_s=(entry.get("ms") or 0) / 1000.0,
            command=command,
        ))
    return session
