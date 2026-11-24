"""The open investigation: which question the traces on disk are about.

`.echolot/traces/` and `.echolot/out/report.json` have always meant "the latest
set" and nothing more. The tool had no way to say which question that set was
recorded for, so `/echolot` a week later found history, attached to it and
carried on — whether or not the human had come back for the same thing. Old
cold-start traces answered a question about scrolling; thresholds calibrated on
one scenario gated another; and markers left in the tree by an investigation
that ran out of context became the starting conditions of the next one.

This file is the missing label. One open investigation at a time: the question
in the human's words, when it opened, what it has been through, and the config
it was opened against. `next_kind` reads it to tell "carry on where we stopped"
apart from "this is something else", and where it cannot tell, the answer is
`resume-or-new` — the CLI states the facts and the agent asks the human.

The loop is untouched, by construction rather than by a special case.
`perf-hunter` never calls `status`: it is handed an investigation in its prompt
and works inside it. The question here is *which investigation to work in*, so
once one is open the question cannot arise. `collect` setting traces aside
between rounds and this module setting them aside between investigations are
two different boundaries, and they use the same primitive without meeting.

Everything is read from and written to `.echolot/`, which is in .gitignore: an
investigation is the state of a machine, while `echolot.yml` describes the
project and is committed.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from . import atomic, files
from . import verdict as verdict_mod
from .when import ago as _ago
from .when import iso_epoch as _epoch

HUNT_FILE = Path(".echolot") / "hunt.json"
ARCHIVE_DIR = Path(".echolot") / "hunts"
LOCK_FILE = Path(".echolot") / "hunt.lock"

# Below this, a return is the same sitting rather than a new visit, and asking
# would be noise. Above it, the human has been away long enough that "carry on
# with what exactly?" is a fair question.
FRESH_MINUTES = 30

# Long enough that the previous question is no longer on anyone's mind.
STALE_DAYS = 7


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].strip("-") or "hunt"


def next_n(project: Path) -> int:
    """The next investigation's number.

    A person needs something short to name one by — `hunt --show 2` rather
    than a timestamp. Derived from what is on disk rather than kept in a
    counter file: one fewer thing to go stale, and a hand-deleted archive
    just frees its number.

    The numbered directories count as well. An open investigation's reports
    are filed in `.echolot/hunts/<n>/` before its record is archived, and
    with `hunt.json` deleted or unreadable the next one took the same number
    and inherited the reports. A number is free once its directory is gone.
    """
    root = project / ARCHIVE_DIR
    dirs = [int(d.name) for d in root.iterdir()
            if d.is_dir() and d.name.isdigit()] if root.is_dir() else []
    return max([*(int(h.get("n") or 0) for h in history(project)), *dirs],
               default=0) + 1


def find(project: Path, ident: str) -> dict[str, Any] | None:
    """An investigation by number, or by a piece of its question."""
    past = history(project)
    if ident.isdigit():
        n = int(ident)
        return next((h for h in past if int(h.get("n") or 0) == n), None)
    needle = ident.lower()
    return next((h for h in past
                 if needle in (h.get("question") or "").lower()), None)


def path(project: Path) -> Path:
    return project / HUNT_FILE


def load(project: Path) -> dict[str, Any] | None:
    """The last investigation, open or concluded, or None. A corrupt file reads as None.

    Whether it is open is its `status`: a concluded one stays in place,
    readable, until the next question opens another.

    A hunt file is a convenience, never a precondition: if it cannot be read,
    the tool must behave exactly as it did before this module existed. It
    is not silent about it, though: `unreadable` says why, and `status`,
    `hunt` and the start page say that line where the investigation would be.

    Each field is read by its type (`files.HUNT`): one that holds what
    echolot never writes there reads as absent, and the rest of the record
    is read.
    """
    return _read(path(project))[0]


def unreadable(project: Path) -> str | None:
    """Why `.echolot/hunt.json` is there and does not load as a record; None
    when it loads or is not there at all.

    `load` reads such a file as no investigation, and nothing said so: an
    open investigation vanished from `status`, `hunt` and the start page,
    and the next `hunt "<question>"` wrote over the file (#580).
    """
    return _read(path(project))[1]


def unreadable_line(project: Path, then: bool = True) -> str | None:
    """`unreadable` as the one line said where the investigation would be,
    with what the next `hunt "<question>"` does about it unless `then` is
    false."""
    why = unreadable(project)
    if why is None:
        return None
    line = f"`{HUNT_FILE.as_posix()}` does not load: {why} — the open investigation is unknown"
    if then:
        line += (f'. The next `echolot hunt "<question>"` opens a new one beside it and sets this '
                 f"file aside in `{ARCHIVE_DIR.as_posix()}/`")
    return line


def _read(p: Path) -> tuple[dict[str, Any] | None, str | None]:
    """A record file as (record, None), (None, why it does not load), or
    (None, None) when there is no file: `files.read_hunt`, the one reader
    of a record (#833)."""
    got = files.read_hunt(p)
    if isinstance(got, files.Refusal):
        return None, None if got.absent else got.why
    return dict(got), None


def set_aside_unreadable(project: Path) -> Path | None:
    """Move a `hunt.json` that does not load out of the way, under a name of
    its own in `.echolot/hunts/`, and say where; None when there was none.

    `open_new` used to write over it: the question and the counts of the
    investigation it held were gone, though its reports stayed in
    `hunts/<n>/`. Kept as it is, whatever can still be read off it by hand
    is there. The name ends in no `.json`, so `history` does not read it as
    a record.
    """
    p = path(project)
    if unreadable(project) is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = project / ARCHIVE_DIR / f"hunt.json.unreadable-{stamp}"
    n = 1
    while dest.exists():
        n += 1
        dest = dest.with_name(f"hunt.json.unreadable-{stamp}-{n}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dest)
    except OSError:
        return None
    return dest


def save(project: Path, hunt: dict[str, Any]) -> None:
    p = path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write(p, hunt)


def _write(p: Path, hunt: dict[str, Any]) -> None:
    """Written whole or not at all: to a file beside it, then moved into place.

    Written in place, a write cut short by Ctrl-C or a killed harness left
    half a file, which `load` reads as no investigation: its question,
    rounds and conclusion were lost, and the next one took its number. The
    file beside it has a name of its own writer's: under one shared name,
    one writer moved another's half-written file into place, or found it
    gone (#724).
    """
    atomic.write_text(p, json.dumps(hunt, ensure_ascii=False, indent=2) + "\n")


# Held by this process: the lock of each project, how deep, and the file
# that holds it against other processes.
_held: dict[str, list[Any]] = {}
_held_guard = threading.Lock()


@contextlib.contextmanager
def lock(project: Path, create: bool = False) -> Iterator[None]:
    """One writer of a project's investigations at a time.

    Every change to `hunt.json` reads it, changes it and writes it back,
    and the writes are atomic while the three steps together were not: two
    questions asked at once read the same record, took the same number, and
    the second write replaced the first, so a question that said `opened
    #N` left nothing behind (#724). A `collect` filing its round could
    likewise write back the record a new question had just closed.

    Held across processes by a lock on `.echolot/hunt.lock`, and within this
    one by a lock that the same thread can take again, so a function that
    holds it can call another that takes it. Where there is no `.echolot/`
    there is nothing to write over, and none is made unless `create` says
    the caller is about to write one; where the file cannot be opened or
    locked, the change goes ahead as it did before there was a lock.
    """
    key = os.path.abspath(project / LOCK_FILE)
    with _held_guard:
        entry = _held.setdefault(key, [threading.RLock(), 0, None])
    with entry[0]:
        if entry[1] == 0:
            entry[2] = _take(Path(key), create)
        entry[1] += 1
        try:
            yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                _give_back(entry[2])
                entry[2] = None


def _take(path: Path, create: bool) -> Any:
    """The lock file, open and locked; None when there is none to take."""
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.parent.is_dir():
            return None
        held = open(path, "a+b")  # noqa: SIM115 - held until _give_back
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            held.seek(0)
            while True:
                try:
                    msvcrt.locking(held.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ten seconds
                    continue
        else:
            import fcntl
            fcntl.flock(held, fcntl.LOCK_EX)
    except OSError:
        held.close()
        return None
    return held


def _give_back(held: Any) -> None:
    if held is None:
        return
    with contextlib.suppress(OSError):
        if os.name == "nt":
            import msvcrt
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(held, fcntl.LOCK_UN)
    held.close()


_F = TypeVar("_F", bound=Callable[..., Any])


def _one_writer(create: bool = False) -> Callable[[_F], _F]:
    """Run the function under `lock` of the project it is given first."""
    def wrap(f: _F) -> _F:
        @functools.wraps(f)
        def inner(project: Path, *args: Any, **kwargs: Any) -> Any:
            with lock(project, create=create):
                return f(project, *args, **kwargs)
        return inner  # type: ignore[return-value]
    return wrap


def home(project: Path, hunt: dict[str, Any] | None) -> Path | None:
    """`.echolot/hunts/<n>/` — where an investigation keeps its own copies.

    The working paths do not move: `collect` still writes into
    `.echolot/traces/` and `analyze` still leaves the latest report in
    `.echolot/out/`, which is what every example, every CI job and the agent
    read. What changes is that each artefact is also filed under the
    investigation it belongs to — reports copied in, trace directories
    recorded by path, because traces run to gigabytes and copying them would
    be a way to fill a disk.
    """
    n = (hunt or {}).get("n")
    return project / ARCHIVE_DIR / str(n) if n else None


# The investigation as a page for a person, in its own directory beside its
# record and its reports (`pages/answer_page.py`).
ANSWER_PAGE = "answer.html"


def page_path(project: Path, hunt: dict[str, Any] | None) -> Path | None:
    """Where an investigation's page stands, or would. None for a record with
    no number: it has no directory of its own to put one in."""
    own = home(project, hunt)
    return own / ANSWER_PAGE if own is not None else None


def last(project: Path) -> dict[str, Any] | None:
    """The newest investigation, open or not; None when there has been none."""
    past = history(project)
    return past[0] if past else None


def _rel(project: Path, p: Path) -> str:
    """Stored relative to the project, so a record survives a move or a clone."""
    try:
        return str(p.resolve().relative_to(project.resolve()))
    except ValueError:
        return str(p)


class NotOpen(Exception):
    """`--hunt N` names no open investigation. Said, and nothing runs."""


def inside(project: Path, asked: int | None) -> dict[str, Any] | None:
    """The investigation a `collect` or an `analyze` works inside: the open
    one, when `--hunt N` names it. None without the flag: the run is outside
    any.

    Every run used to be filed under whatever was open. A cold-start
    baseline recorded two months after a question about scrolling went into
    that question's record as its eighth report, renewed it as worked on
    that day, and in a config with several scenarios would have been
    measured as the scroll (#498). The run is the investigation's when it
    says so: the hunt and its loop pass `--hunt N`, a plain command does not.

    By number, so that a run meant for one investigation is never filed under
    another that opened since. Raises NotOpen, saying what to do instead,
    when N is not the open one: before anything is recorded or read, rather
    than after a set nobody can file.
    """
    if asked is None:
        return None
    damaged = unreadable_line(project, then=False)
    if damaged:
        raise NotOpen(f"--hunt {asked}: {damaged}")
    hunt = load(project)
    if hunt and hunt.get("status") == "open":
        if hunt.get("n") == asked:
            return hunt
        n = hunt.get("n") or "?"
        raise NotOpen(f'--hunt {asked}: the open investigation is #{n}, '
                      f'"{hunt.get("question") or "—"}". `--hunt {n}` works inside it; '
                      f"without `--hunt` this runs outside any")
    was = find(project, str(asked))
    said = f"#{asked} is {was.get('status') or 'closed'}" if was else f"there is no #{asked}"
    raise NotOpen(f"--hunt {asked}: no investigation is open ({said}). "
                  f'`echolot hunt "<question>"` opens one; without `--hunt` this '
                  f"runs outside any")


def _still(hunt: dict[str, Any] | None, n: int | None) -> bool:
    """Open, and the one numbered `n` when a number is given.

    A gradle `collect` runs for up to an hour, and an investigation can be
    closed and another opened while it does: what it was started inside is
    what it is filed under, or nothing.
    """
    return bool(hunt) and hunt.get("status") == "open" and (n is None or hunt.get("n") == n)


@_one_writer()
def record_traces(project: Path, directory: Path, n: int | None = None) -> None:
    """Note that this directory of traces belongs to the open investigation,
    the one numbered `n` when it is given.

    Called by `collect`, which pushes the previous round's set aside; the set
    pushed aside when a new investigation opens is recorded by `open_new`
    itself, as `traces_aside`. Until this existed only
    the closing set was recorded, so a hunt that ran four rounds remembered
    the last one and lost the three it reasoned from.
    """
    hunt = load(project)
    if not _still(hunt, n):
        return
    rel = _rel(project, directory)
    kept = hunt.setdefault("traces", [])
    if rel not in kept:
        kept.append(rel)
        with contextlib.suppress(OSError):
            save(project, hunt)


@_one_writer()
def record_report(project: Path, out_dir: Path, n: int | None = None) -> Path | None:
    """Copy the report just written into the open investigation's own history,
    the one numbered `n` when it is given.

    `.echolot/out/report.json` is always the latest one and every analyze
    overwrites it — including an analyze belonging to a different question.
    A copy per run costs tens of kilobytes and is the only way to see what an
    investigation concluded at each step rather than only at the end.
    """
    hunt = load(project)
    if not _still(hunt, n):
        return None
    dest = home(project, hunt)
    if dest is None:
        return None
    dest = dest / "reports"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # One past the highest number already there, not one past how many
        # there are. Deleting a report from the middle — which a person may
        # well do, they are just files in a directory — made the count line
        # up with a name that was taken, and the next `analyze` wrote over
        # the round before it. Silently, and these copies exist because the
        # working report is overwritten every run.
        seq = max((int(p.stem) for p in dest.glob("*.json")
                   if p.stem.isdigit()), default=0) + 1
        for ext in ("json", "md"):
            src_file = out_dir / f"report.{ext}"
            if src_file.exists():
                shutil.copy2(src_file, dest / f"{seq:03d}.{ext}")
    except OSError:
        return None
    hunt["reports"] = int(hunt.get("reports") or 0) + 1
    with contextlib.suppress(OSError):
        save(project, hunt)
    return dest / f"{seq:03d}.json"


def archive(project: Path, hunt: dict[str, Any]) -> Path | None:
    """Move a finished investigation into `.echolot/hunts/`.

    Never deleted. The question someone was chasing three weeks ago costs a
    kilobyte to keep and cannot be reconstructed from the traces.
    """
    if not hunt:
        return None
    # A numbered investigation already has a directory holding its reports;
    # its record goes in beside them. The flat, timestamped filename is what
    # 0.2.0 wrote, and unnumbered records from it keep using that shape.
    own = home(project, hunt)
    if own is not None:
        own.mkdir(parents=True, exist_ok=True)
        dest = own / "hunt.json"
    else:
        opened = (hunt.get("opened_at") or _now())[:19].replace(":", "").replace("-", "")
        dest = project / ARCHIVE_DIR / f"{opened}-{_slug(hunt.get('question', ''))}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dest.exists():
            n += 1
            dest = dest.with_name(f"{dest.stem}-{n}.json")
    _write(dest, hunt)
    return dest


@_one_writer(create=True)
def open_new(project: Path, question: str, since: str | None = None,
             scenario: str | None = None, config_sha: str | None = None,
             status: str = "abandoned",
             traces_aside: Path | None = None,
             confirmed: dict[str, Any] | None = None,
             last_collect: dict[str, Any] | None = None) -> dict[str, Any]:
    """Close whatever was open, archive it, and start a new investigation.

    `status` is what the previous one is recorded as — `abandoned` when the
    human simply moved on, which is the common case and the honest word for it.

    `confirmed` is every value in the config a person confirmed, as it stood
    when the investigation opened: what `confirmed_changed` holds the config
    to while it is open.

    `traces_aside` is where the loose set of traces was moved to make room.
    That set is the *previous* investigation's evidence, so it is recorded on
    the record being closed. Without it the archive remembers the question and
    forgets what was measured, which makes it decoration. Only when it was
    recorded for that one (`_its_evidence`): `last_collect` is what
    `.echolot/log/collect.json` says of the run that left it.
    """
    set_aside_unreadable(project)
    previous = load(project)
    if previous:
        previous.setdefault("status", "open")
        ours = traces_aside is not None and _its_evidence(project, previous, traces_aside,
                                                         last_collect)
        if previous["status"] == "open":
            previous["status"] = status
            previous["closed_at"] = _now()
        if ours:
            previous.setdefault("traces", []).append(str(traces_aside))
        archive(project, previous)

    hunt = {
        "schema": files.HUNT_SCHEMA,
        "n": next_n(project),
        "question": question,
        "since": since,
        "scenario": scenario,
        "config_sha": config_sha,
        "confirmed": confirmed or {},
        "opened_at": _now(),
        "touched_at": _now(),
        "status": "open",
        "collects": 0,
        "analyzes": 0,
        "traces": [],
        "conclusion": None,
    }
    save(project, hunt)
    return hunt


def _its_evidence(project: Path, hunt: dict[str, Any], aside: Path,
                  last_collect: dict[str, Any] | None) -> bool:
    """Whether the set just moved aside was recorded for `hunt`, the
    investigation about to be closed.

    Not when the collect that left it says it ran outside `hunt`, and not
    when it was written after `hunt` was concluded. Filed all the same, a
    baseline recorded months after the question was answered read in
    `hunt --show` as the evidence for that answer (#718), the outcome #498
    kept `collect` and `analyze` from. A collect that does not say where it
    ran, from before it wrote that, is taken as the investigation's, as it
    always was.
    """
    if recorded_outside(hunt, last_collect):
        return False
    closed = hunt.get("closed_at") if hunt.get("status", "open") != "open" else None
    if not closed:
        return True
    try:
        closed_at = _epoch(closed)
        folder = aside if aside.is_absolute() else project / aside
        newest = max((f.stat().st_mtime for f in folder.iterdir() if f.is_file()), default=None)
    except (OSError, TypeError, ValueError):
        return True
    # `closed_at` is kept to the second: a trace written within that second
    # was written before the close as likely as after it.
    return closed_at is None or newest is None or newest < closed_at + 1


@_one_writer()
def touch(project: Path, *, collect: bool = False, analyze: bool = False,
          n: int | None = None) -> None:
    """Record that the open investigation is still being worked on, the one
    numbered `n` when it is given.

    Called from `hunt --resume`, and from a `collect` or an `analyze` run
    inside the investigation (`--hunt N`), which is what makes `touched_at`
    honest: the freshness rule is about its work, not about when someone last
    typed `echolot` — nor about a run outside it, which renewed a two-month-old
    investigation as worked on that day and took away the question it was
    owed (#498). Silent when nothing is open — an ad-hoc `analyze` on a
    trace from somewhere else must not invent an investigation.
    """
    hunt = load(project)
    if not _still(hunt, n):
        return
    hunt["touched_at"] = _now()
    if collect:
        hunt["collects"] = int(hunt.get("collects") or 0) + 1
    if analyze:
        hunt["analyzes"] = int(hunt.get("analyzes") or 0) + 1
    try:
        save(project, hunt)
    except OSError:
        pass


def archived_reports(project: Path, hunt: dict[str, Any] | None) -> verdict_mod.Reports:
    """The reports an investigation filed, by the number they are filed under.

    `reports/003.json` is report 3 — the number `record_report` gave it, which
    is what a verdict cites. One that is there and does not read as a report
    is None, so that a citation of it says so rather than that it is missing.
    """
    own = home(project, hunt)
    out: verdict_mod.Reports = {}
    if own is None:
        return out
    for p in sorted((own / "reports").glob("*.json")):
        if not (p.stem.isascii() and p.stem.isdigit()):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        out[int(p.stem)] = data if isinstance(data, dict) else None
    return out


@_one_writer()
def conclude(project: Path, conclusion: str, verdict: dict[str, Any] | None = None,
             reports: verdict_mod.Reports | None = None,
             left: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Mark the open investigation answered. It stays in place, readable.

    None when none is open: a concluded one keeps the conclusion it has, and
    a second one written over it was lost without a word.

    With a `verdict`, already checked by the caller, it is kept as it came
    under `verdict`, and what echolot found under `verdict_check`: the
    reports it was held against and the markers still in the sources, which
    `left` is the count of. `conclusion` stays a string either way, because
    `--list`, `status` and the recap read that one.
    """
    hunt = load(project)
    if not hunt or hunt.get("status", "open") != "open":
        return None
    hunt["status"] = "concluded"
    hunt["conclusion"] = conclusion
    hunt["closed_at"] = _now()
    if verdict is not None:
        hunt["verdict"] = verdict
        hunt["verdict_check"] = verdict_mod.record(verdict, reports or {}, left or {},
                                                   hunt["closed_at"])
    save(project, hunt)
    return hunt


def history(project: Path) -> list[dict[str, Any]]:
    """Every investigation this project has had, newest first, open one included.

    Archived records are `<n>/hunt.json`, sorted by their number, with
    `opened_at` for the 0.2.0 records that have none; the sort is over the
    fields rather than the path, so a hand-edited archive still lands in the
    right place.
    """
    out: list[dict[str, Any]] = []
    current = load(project)
    if current:
        out.append(dict(current, current=True))
    d = project / ARCHIVE_DIR
    if d.is_dir():
        # <n>/hunt.json is the shape from here on; *.json at the top level is
        # what 0.2.0 wrote and is still read.
        for f in list(d.glob("*/hunt.json")) + list(d.glob("*.json")):
            h = _read(f)[0]
            if h is not None:
                out.append(dict(h, current=False))
    # By number first: several investigations can open within one second —
    # a person trying a few questions in a row — and a sort on the timestamp
    # alone then puts them in whatever order the directory listing gave.
    # Records written before numbering exist fall back to the timestamp.
    return sorted(out, key=lambda h: (int(h.get("n") or 0), h.get("opened_at") or ""),
                  reverse=True)


# --- deciding whether to ask ------------------------------------------------

def is_fresh(hunt: dict[str, Any] | None, minutes: int = FRESH_MINUTES) -> bool:
    """Worked on within the last `minutes` — the same sitting, do not ask."""
    t = _epoch((hunt or {}).get("touched_at"))
    return t is not None and (time.time() - t) < minutes * 60


def has_history(st: dict[str, Any]) -> bool:
    """Is there anything a new investigation would inherit by accident?"""
    if (st.get("traces") or {}).get("count"):
        return True
    rep = st.get("report")
    return bool(rep and not rep.get("error"))


def needs_choice(hunt: dict[str, Any] | None, st: dict[str, Any]) -> bool:
    """Should the human be asked "continue this, or start something new"?

    Only at the door, and only when the answer is genuinely open: there is an
    investigation, it left something behind, and enough time has passed that
    the human may well have come back for a different reason.
    """
    if not hunt or hunt.get("status") != "open":
        return False
    if not has_history(st):
        return False
    # A collect in flight inside the investigation is the investigation
    # working. A gradle run takes up to an hour and touches the investigation
    # only when it ends, and the agent calls `echolot` while it runs: past
    # thirty minutes it was told to ask the human whether to carry on with the
    # work it was doing. Any collect used to count, whoever started it, and a
    # baseline recording outside a two-month-old investigation attached the
    # next `/echolot` to it without the question (#498).
    if running_inside(hunt, st.get("collect")):
        return False
    return not is_fresh(hunt)


def running_inside(hunt: dict[str, Any] | None, run: dict[str, Any] | None) -> bool:
    """A collect in flight that was started inside this investigation (`--hunt N`)."""
    run = run or {}
    return (run.get("status") == "running" and bool(hunt)
            and hunt.get("n") is not None and run.get("hunt") == hunt.get("n"))


def recorded_outside(hunt: dict[str, Any] | None, run: dict[str, Any] | None) -> bool:
    """The last collect ran outside this investigation.

    Only a collect that says which investigation it ran in can say so: one
    from before `collect` wrote it is not taken for either.
    """
    run = run or {}
    return bool(hunt) and "hunt" in run and run.get("hunt") != hunt.get("n")


def confirmed_changed(hunt: dict[str, Any] | None,
                      now: dict[str, Any]) -> list[dict[str, Any]]:
    """The values a person confirmed that are no longer what they confirmed.

    Held against what the investigation recorded when it opened, so it names
    the field and both values where a changed hash could only say that the
    file changed. A live hunt rewrote the end of the scenario, which a person
    had confirmed as the moment the app is ready, and every report after the
    edit measured a window nobody had agreed to (#196). A field that is gone
    counts as changed; one confirmed since the investigation opened is not in
    the record, and is not held to anything.
    """
    was = (hunt or {}).get("confirmed") or {}
    return [{"field": key, "was": value, "now": now.get(key)}
            for key, value in was.items() if now.get(key) != value]


def drift(hunt: dict[str, Any] | None, st: dict[str, Any]) -> list[str]:
    """Signs the open investigation is no longer the one being asked about.

    Shown with the question so the human decides on facts, and so the agent
    can offer "start a new one" as the likely answer instead of a coin toss.
    """
    if not hunt:
        return []
    out: list[str] = []
    cfg = st.get("config") or {}
    if not cfg.get("error"):
        scenario = hunt.get("scenario")
        names = cfg.get("scenarios") or ([cfg["scenario"]] if cfg.get("scenario") else [])
        changed = confirmed_changed(hunt, cfg.get("confirmed") or {})
        if scenario and names and scenario not in names and len(names) == 1:
            out.append(f"the config's scenario changed: {scenario} → {names[0]}")
        elif scenario and names and scenario not in names:
            out.append(f"the config no longer has this investigation's scenario, "
                       f"{scenario}: it has {', '.join(names)}")
        elif changed:
            out.append("a value a person confirmed changed since this investigation "
                       "opened: " + "; ".join(f"{c['field']} {c['was']!r} → {c['now']!r}"
                                              for c in changed))
        elif hunt.get("config_sha") and cfg.get("sha") and hunt["config_sha"] != cfg["sha"]:
            out.append("echolot.yml changed since this investigation opened")
    t = _epoch(hunt.get("touched_at"))
    if t is not None:
        days = (time.time() - t) / 86400
        if days >= STALE_DAYS:
            out.append(f"untouched for {days:.0f} days")
    return out


# --- what the previous investigation left in the tree -----------------------

# The lines `mark --remove` leaves that a list names one by one; the rest are
# counted.
KEPT_SHOWN = 12


def leftovers(root: Path, prefix: str | None = None) -> dict[str, Any]:
    """Temporary markers still in the sources, and whether `mark` can remove them.

    An investigation that ran out of context leaves its instrumentation behind,
    and the next one then measures a tree nobody meant to ship or profile.

    Counted by the prefix, which appears once per instrumentation point — on
    the `beginSection`, while its paired `endSection` carries only the tag. Of
    those points, the ones `mark --apply` wrote are a line of their own and
    `mark --remove` takes them out; ones the agent added by hand are not, and
    have to go by hand. The same predicate decides both here and there, so the
    number is what `--remove` will actually do. One number for both would send
    the human away believing the tree was clean.

    `lines` counts every line `--remove` would delete, prefix or not. A tagged
    `endSection()` left after its `beginSection` went by hand has no prefix to
    be found by, and ends whatever section the app has open on that thread
    (#274); its file is listed with the rest.

    `kept` is every line `--remove` leaves where it is and names, as
    `file:line`: the tag in another shape than the one `--apply` writes, with
    something besides the marker on the line, which is why it goes by hand.
    An agent's one-line wrap is one:

        try { work() } finally { android.os.Trace.endSection() } // echolot:mark

    Its `beginSection` was a line of its own and went at the press; the wrap
    has no prefix and is not a line `--remove` deletes, so it was counted nowhere,
    and every reader said the code was clean over an `endSection()` that
    ends whatever section the app has open (#703). Its places are its own
    list: `files` holds the markers and the lines `--remove` deletes, which
    the sentences about those count files by.
    """
    from . import mark as mark_mod

    prefix = prefix or mark_mod.DEFAULT_PREFIX
    files: list[str] = []
    kept: list[str] = []
    total = tagged = lines = 0
    # The walk `mark --remove` takes and the text it reads, so the count is
    # of what it removes and the lines are the ones it leaves.
    try:
        sources = mark_mod.domains_source_files(root)
    except OSError:
        return {"files": [], "markers": 0, "removable": 0, "lines": 0, "kept": [],
                "prefix": prefix}
    for p in sources:
        try:
            text = mark_mod.read_source(p, strict=True)
        except (OSError, UnicodeDecodeError):
            continue
        if prefix not in text and mark_mod.TAG not in text:
            continue
        rows = text.split("\n")
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        kept += [f"{rel}:{n}" for n, ln in enumerate(rows, 1) if mark_mod.is_kept_line(ln)]
        hits = [ln for ln in rows if prefix in ln]
        applied = sum(1 for ln in rows if mark_mod.is_applied_line(ln))
        if not hits and not applied:
            continue
        files.append(rel)
        total += len(hits)
        tagged += sum(1 for ln in hits if mark_mod.is_applied_line(ln))
        lines += applied
    return {"files": files, "markers": total, "removable": tagged, "lines": lines,
            "kept": kept, "prefix": prefix}


# --- rendering --------------------------------------------------------------

def summary_line(hunt: dict[str, Any] | None, st: dict[str, Any]) -> str:
    """The one line `status` prints among layer / config / traces / report.

    With none open it says what opens one. `hunt "<q>"` refuses while
    echolot.yml is there and does not load, so then the promise waits on
    the config, and the config's own line above this one says why it does
    not load. With no config here or above, `hunt "<q>"` refuses too: an
    investigation belongs to a project (#721).
    """
    if not hunt:
        if st.get("hunt_unreadable"):
            return st["hunt_unreadable"]
        if not st.get("config"):
            return "none open — a hunt opens one once the project has an echolot.yml"
        if (st.get("config") or {}).get("error"):
            return "none open — a hunt opens one once echolot.yml loads"
        return "none open — the next hunt opens one"
    q = hunt.get("question") or "(no question recorded)"
    bits = [f'"{q}"']
    if hunt.get("scenario") and len((st.get("config") or {}).get("scenarios") or []) > 1:
        bits.append(f"scenario {hunt['scenario']}")
    state = hunt.get("status") or "open"
    if state != "open":
        bits.append(state)
    counts = []
    if hunt.get("collects"):
        counts.append(f"{hunt['collects']} collect(s)")
    if hunt.get("analyzes"):
        counts.append(f"{hunt['analyzes']} analyze(s)")
    if counts:
        bits.append(", ".join(counts))
    return " · ".join(bits)


def recap(hunt: dict[str, Any] | None, st: dict[str, Any],
          root: Path | None = None, quoted: bool = True) -> list[str]:
    """Everything the human needs to answer "continue, or start new?".

    A week later nobody remembers what they were chasing, so the question has
    to carry the answer's context with it: the question itself, what came of
    it, what drifted, and what is still sitting in the source tree.
    """
    if not hunt:
        return ["No investigation is open."]
    q = hunt.get("question") or "(no question recorded)"
    state = hunt.get("status") or "open"
    if state == "open":
        # `quoted` False under `status`, whose hunt line has just said it.
        out = [f'Open investigation: "{q}"' if quoted else
               f"Open investigation #{hunt.get('n') or '?'}, the question on the hunt line:"]
    else:
        # A closed record stays at .echolot/hunt.json until the next one
        # opens, and bare `echolot hunt` reads it from there. Headed "Open
        # investigation", it told an agent deciding whether to open one that
        # one was open — and inside a closed one nothing is filed, so the
        # loop's `compare` had nothing to compare.
        out = [f'No investigation is open. The last one, #{hunt.get("n") or "?"}, '
               f'{state}: "{q}"']
        if hunt.get("conclusion"):
            out.append(f"  → {hunt['conclusion']}")
    if hunt.get("since"):
        out.append(f"  after: {hunt['since']}")
    facts = []
    if hunt.get("opened_at"):
        facts.append(f"opened {_ago(_epoch(hunt['opened_at']))}")
    if hunt.get("touched_at"):
        facts.append(f"last worked on {_ago(_epoch(hunt['touched_at']))}")
    if hunt.get("collects"):
        facts.append(f"{hunt['collects']} collect(s)")
    if hunt.get("analyzes"):
        facts.append(f"{hunt['analyzes']} analyze(s)")
    if facts:
        out.append("  " + " · ".join(facts))

    tr = st.get("traces") or {}
    last_run = st.get("collect") or {}
    if tr.get("count") and recorded_outside(hunt, last_run):
        # Offered for reuse, a baseline recorded outside the investigation
        # read as its own evidence: "traces it would reuse: 5" were cold
        # starts a question about scrolling never asked for (#498).
        out.append(f"  traces in .echolot/traces: {tr['count']}, not this investigation's "
                   f"— the last collect ({last_run.get('scenario') or 'a scenario'}, "
                   f"{_ago(last_run.get('started'))}) ran outside it")
    elif tr.get("count"):
        out.append(f"  traces it would reuse: {tr['count']} in .echolot/traces")
    rep = st.get("report")
    if rep and not rep.get("error") and rep.get("run") is not None:
        out.append(f"  last report: {rep['fired']} of {rep['run']} detectors fired")

    for d in drift(hunt, st):
        out.append(f"  ! {d}")

    if root is not None:
        for note in leftover_lines(leftovers(root, (st.get("config") or {}).get("temp_prefix"))):
            out.append(f"  ! {note}")
    return out


def leftover_lines(left: dict[str, Any]) -> list[str]:
    """What `leftovers` found, a sentence for each kind; none when the
    sources are clean.

    The lines `--remove` leaves are a sentence of their own, with where each
    one is: after the press they are all that is left, and they go by hand.
    """
    out = []
    if left["markers"]:
        hand = left["markers"] - left["removable"]
        how = f"`echolot mark --remove` takes out {left['removable']}"
        if hand:
            how += f", {hand} were added by hand and need removing by hand"
        out.append(f"{left['markers']} {left['prefix']} marker(s) still in "
                   f"{len(left['files'])} file(s) — {how}")
    elif left["lines"]:
        out.append(f"{left['lines']} line(s) tagged `// echolot:mark` still in "
                   f"{len(left['files'])} file(s), with no {left['prefix']} marker "
                   f"beside them — `echolot mark --remove` takes them out")
    kept = left.get("kept") or []
    if kept:
        out.append(f"{len(kept)} line(s) tagged `// echolot:mark` carry something besides the "
                   f"marker, so `echolot mark --remove` leaves them — take the marker out by "
                   f"hand, keeping the code it wraps: {verdict_mod.places(kept)}")
    return out


def _when(ts: str | None) -> str:
    """In local time, the clock the set-aside trace directories are named by
    a few lines below. The stored UTC with its zone cut off read as local and
    was off by the offset."""
    try:
        return datetime.fromisoformat(ts or "").astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return (ts or "")[:16].replace("T", " ") or "—"


def _ran_for(hunt: dict[str, Any]) -> str | None:
    """How long an investigation was open, when both ends are known."""
    a, b = _epoch(hunt.get("opened_at")), _epoch(hunt.get("closed_at") or hunt.get("touched_at"))
    if a is None or b is None or b <= a:
        return None
    mins = (b - a) / 60
    if mins < 90:
        return f"{mins:.0f}m"
    if mins < 2880:
        return f"{mins / 60:.0f}h"
    return f"{mins / 1440:.0f}d"


def _did(hunt: dict[str, Any]) -> list[str]:
    """What an investigation actually did — the facts already in the record.

    These were being written and never shown, so a list of past hunts read as
    a list of questions with no answers and no work behind them.
    """
    bits = []
    if hunt.get("collects"):
        bits.append(f"{hunt['collects']} collect(s)")
    if hunt.get("analyzes"):
        bits.append(f"{hunt['analyzes']} analyze(s)")
    ran = _ran_for(hunt)
    if ran:
        bits.append(f"open for {ran}")
    traces = hunt.get("traces") or []
    if traces:
        bits.append(f"{len(traces)} trace set(s)")
    if hunt.get("reports"):
        bits.append(f"{hunt['reports']} report(s) kept")
    verdict = hunt.get("verdict")
    if isinstance(verdict, dict):
        causes = [c for c in verdict.get("causes") or [] if isinstance(c, dict)] \
            if isinstance(verdict.get("causes"), list) else []
        bits.append(f"verdict: {len(causes)} cause(s)")
    return bits


def list_rows(project: Path) -> list[str]:
    """`hunt --list`: every investigation, newest first, with what it came to."""
    past = history(project)
    if not past:
        return ['no investigations yet — `echolot hunt "<what regressed>"` opens one']
    width = max(len(str(h.get("n") or "?")) for h in past)
    out = []
    for h in past:
        mark = "→" if h.get("current") else " "
        n = str(h.get("n") or "?").rjust(width)
        out.append(f'{mark} {n}  {_when(h.get("opened_at"))}  '
                   f'{(h.get("status") or "open"):<10} "{h.get("question") or "—"}"')
        pad = " " * (width + 22)
        did = _did(h)
        if did:
            out.append(f"{pad}{' · '.join(did)}")
        if h.get("conclusion"):
            out.append(f"{pad}→ {h['conclusion']}")
    return out


def detail(hunt: dict[str, Any], project: Path) -> list[str]:
    """`hunt --show`: one investigation in full, evidence included."""
    out = [f'#{hunt.get("n") or "?"}  "{hunt.get("question") or "—"}"']
    if hunt.get("since"):
        out.append(f"  after: {hunt['since']}")
    verdict = hunt.get("verdict")
    conclusion = hunt.get("conclusion")
    # A verdict's summary is what the conclusion line holds, and the verdict
    # below says it in full: once is enough.
    if isinstance(verdict, dict) and conclusion == verdict.get("summary"):
        conclusion = None
    out.append(f"  status: {hunt.get('status') or 'open'}"
               + (f" · {conclusion}" if conclusion else ""))
    if isinstance(verdict, dict):
        out.extend(verdict_mod.lines(verdict, hunt.get("verdict_check")))
    out.append(f"  opened: {_when(hunt.get('opened_at'))}"
               + (f" · closed {_when(hunt['closed_at'])}" if hunt.get("closed_at")
                  else f" · last worked on {_ago(_epoch(hunt.get('touched_at')))}"))
    if hunt.get("scenario"):
        out.append(f"  scenario: {hunt['scenario']}")
    did = _did(hunt)
    if did:
        out.append("  did: " + " · ".join(did))
    traces = hunt.get("traces") or []
    if traces:
        out.append("  traces:")
        for t in traces:
            d = Path(t)
            if not d.is_absolute():
                d = project / t
            n = len(list(d.glob("*.perfetto-trace"))) + len(list(d.glob("*.pftrace"))) \
                if d.is_dir() else 0
            gone = "" if d.is_dir() else "  (gone)"
            out.append(f"    {t}  {n} trace(s){gone}")
    else:
        out.append("  traces: none recorded")

    own = home(project, hunt)
    reports = sorted((own / "reports").glob("*.json")) if own else []
    if reports:
        out.append(f"  reports ({len(reports)}, oldest first):")
        for r in reports:
            out.append(f"    {r.relative_to(project) if r.is_relative_to(project) else r}")
    return out
