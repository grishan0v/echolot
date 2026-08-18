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

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HUNT_FILE = Path(".echolot") / "hunt.json"
ARCHIVE_DIR = Path(".echolot") / "hunts"

# Below this, a return is the same sitting rather than a new visit, and asking
# would be noise. Above it, the human has been away long enough that "carry on
# with what exactly?" is a fair question.
FRESH_MINUTES = 30

# Long enough that the previous question is no longer on anyone's mind.
STALE_DAYS = 7


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].strip("-") or "hunt"


def next_n(project: Path) -> int:
    """The next investigation's number.

    A person needs something short to name one by — `hunt --show 2` rather
    than a timestamp. Derived from what is on disk rather than kept in a
    counter file: one fewer thing to go stale, and a hand-deleted archive
    just frees its number.
    """
    return max((int(h.get("n") or 0) for h in history(project)), default=0) + 1


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
    """The open investigation, or None. A corrupt file reads as None.

    A hunt file is a convenience, never a precondition: if it cannot be read,
    the tool must behave exactly as it did before this module existed.
    """
    p = path(project)
    if not p.exists():
        return None
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return h if isinstance(h, dict) else None


def save(project: Path, hunt: dict[str, Any]) -> None:
    p = path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hunt, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def archive(project: Path, hunt: dict[str, Any]) -> Path | None:
    """Move a finished investigation into `.echolot/hunts/`.

    Never deleted. The question someone was chasing three weeks ago costs a
    kilobyte to keep and cannot be reconstructed from the traces.
    """
    if not hunt:
        return None
    opened = (hunt.get("opened_at") or _now())[:19].replace(":", "").replace("-", "")
    dest = project / ARCHIVE_DIR / f"{opened}-{_slug(hunt.get('question', ''))}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while dest.exists():
        n += 1
        dest = dest.with_name(f"{dest.stem}-{n}.json")
    dest.write_text(json.dumps(hunt, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return dest


def open_new(project: Path, question: str, since: str | None = None,
             scenario: str | None = None, config_sha: str | None = None,
             status: str = "abandoned",
             traces_aside: Path | None = None) -> dict[str, Any]:
    """Close whatever was open, archive it, and start a new investigation.

    `status` is what the previous one is recorded as — `abandoned` when the
    human simply moved on, which is the common case and the honest word for it.

    `traces_aside` is where the loose set of traces was moved to make room.
    That set is the *previous* investigation's evidence, so it is recorded on
    the record being closed. Without it the archive remembers the question and
    forgets what was measured, which makes it decoration.
    """
    previous = load(project)
    if previous:
        previous.setdefault("status", "open")
        if previous["status"] == "open":
            previous["status"] = status
            previous["closed_at"] = _now()
        if traces_aside is not None:
            previous.setdefault("traces", []).append(str(traces_aside))
        archive(project, previous)

    hunt = {
        "n": next_n(project),
        "question": question,
        "since": since,
        "scenario": scenario,
        "config_sha": config_sha,
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


def touch(project: Path, *, collect: bool = False, analyze: bool = False) -> None:
    """Record that the open investigation is still being worked on.

    Called from `collect` and `analyze`, which is what makes `touched_at`
    honest: the freshness rule is about work, not about when someone last
    typed `echolot`. Silent when nothing is open — an ad-hoc `analyze` on a
    trace from somewhere else must not invent an investigation.
    """
    hunt = load(project)
    if not hunt or hunt.get("status") != "open":
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


def conclude(project: Path, conclusion: str) -> dict[str, Any] | None:
    """Mark the open investigation answered. It stays in place, readable."""
    hunt = load(project)
    if not hunt:
        return None
    hunt["status"] = "concluded"
    hunt["conclusion"] = conclusion
    hunt["closed_at"] = _now()
    save(project, hunt)
    return hunt


def history(project: Path) -> list[dict[str, Any]]:
    """Every investigation this project has had, newest first, open one included.

    Archived files are named by when the investigation opened, so the sort is
    over the field rather than the filename: a hand-edited archive still lands
    in the right place.
    """
    out: list[dict[str, Any]] = []
    current = load(project)
    if current:
        out.append(dict(current, current=True))
    d = project / ARCHIVE_DIR
    if d.is_dir():
        for f in d.glob("*.json"):
            try:
                h = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(h, dict):
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
    return not is_fresh(hunt)


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
        if scenario and cfg.get("scenario") and scenario != cfg["scenario"]:
            out.append(f"the config's scenario changed: {scenario} → {cfg['scenario']}")
        elif hunt.get("config_sha") and cfg.get("sha") and hunt["config_sha"] != cfg["sha"]:
            out.append("echolot.yml changed since this investigation opened")
    t = _epoch(hunt.get("touched_at"))
    if t is not None:
        days = (time.time() - t) / 86400
        if days >= STALE_DAYS:
            out.append(f"untouched for {days:.0f} days")
    return out


# --- what the previous investigation left in the tree -----------------------

def leftovers(root: Path, prefix: str | None = None) -> dict[str, Any]:
    """Temporary markers still in the sources, and whether `mark` can remove them.

    An investigation that ran out of context leaves its instrumentation behind,
    and the next one then measures a tree nobody meant to ship or profile.

    Counted by the prefix, which appears once per instrumentation point — on
    the `beginSection`, while its paired `endSection` carries only the tag. Of
    those points, the ones `mark --apply` wrote also carry the tag and
    `mark --remove` takes them out; ones the agent added by hand do not, and
    have to go by hand. One number for both would send the human away
    believing the tree was clean.
    """
    from . import mark as mark_mod

    prefix = prefix or mark_mod.DEFAULT_PREFIX
    files: list[str] = []
    total = tagged = 0
    try:
        sources = mark_mod.source_files(root)
    except OSError:
        return {"files": [], "markers": 0, "removable": 0, "prefix": prefix}
    for p in sources:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if prefix not in text:
            continue
        hits = [ln for ln in text.split("\n") if prefix in ln]
        if not hits:
            continue
        files.append(str(p.relative_to(root)) if p.is_relative_to(root) else str(p))
        total += len(hits)
        tagged += sum(1 for ln in hits if mark_mod.TAG in ln)
    return {"files": files, "markers": total, "removable": tagged, "prefix": prefix}


# --- rendering --------------------------------------------------------------

def summary_line(hunt: dict[str, Any] | None) -> str:
    """The one line `status` prints among layer / config / traces / report."""
    if not hunt:
        return "none open — the next hunt opens one"
    q = hunt.get("question") or "(no question recorded)"
    bits = [f'"{q}"']
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
          root: Path | None = None) -> list[str]:
    """Everything the human needs to answer "continue, or start new?".

    A week later nobody remembers what they were chasing, so the question has
    to carry the answer's context with it: the question itself, what came of
    it, what drifted, and what is still sitting in the source tree.
    """
    if not hunt:
        return ["No investigation is open."]
    out = [f'Open investigation: "{hunt.get("question") or "(no question recorded)"}"']
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
    if tr.get("count"):
        out.append(f"  traces it would reuse: {tr['count']} in .echolot/traces")
    rep = st.get("report")
    if rep and not rep.get("error") and rep.get("run") is not None:
        out.append(f"  last report: {rep['fired']} of {rep['run']} detectors fired")

    for d in drift(hunt, st):
        out.append(f"  ! {d}")

    if root is not None:
        left = leftovers(root)
        if left["markers"]:
            hand = left["markers"] - left["removable"]
            how = f"`echolot mark --remove` takes out {left['removable']}"
            if hand:
                how += f", {hand} were added by hand and need removing by hand"
            out.append(f"  ! {left['markers']} {left['prefix']} marker(s) still in "
                       f"{len(left['files'])} file(s) — {how}")
    return out


def _when(ts: str | None) -> str:
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
        bits.append(f"{len(traces)} trace set(s) kept")
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
    out.append(f"  status: {hunt.get('status') or 'open'}"
               + (f" · {hunt['conclusion']}" if hunt.get("conclusion") else ""))
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
    return out


def _ago(epoch: float | None) -> str:
    if not epoch:
        return "never"
    delta = time.time() - epoch
    if delta < 90:
        return f"{int(delta)}s ago"
    if delta < 5400:
        return f"{int(delta // 60)}m ago"
    if delta < 172800:
        return f"{delta / 3600:.0f}h ago"
    return f"{delta / 86400:.0f}d ago"
