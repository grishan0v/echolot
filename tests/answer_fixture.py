"""An investigation to draw a page from: its record, and the reports it filed.

Built from the package's demo app (`echolot/demo.py`, `com.example.app`) and
nothing else. The first round is the demo's report as `analyze` leaves it. The
second is the same report with the temporary markers an agent would have put
in, added by hand: the demo's traces carry none. The verdict cites rows both
reports really hold, so `echolot hunt --verdict` accepts it.

The cases of `tests/test_answer_page.py` read it, and so does whoever wants to
look at the page: `python -m tests.answer_fixture <dir>` writes every state of
it as a file.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

N = 3
OPENED = "2026-03-02T09:00:00+00:00"
CLOSED = "2026-03-02T09:18:35+00:00"
QUESTION = "The first screen takes over a second to appear: what holds it back?"

# The agent's markers of round 2, as the Markers section of a report holds them.
MARKERS = [
    {"location": "AGENTTMP_bind_item_cards", "runs": "5/5", "count": 15, "self_ms": 38.4, "total_ms": 236.9,
     "max_ms": 88.1, "detail": "com.example.app",
     "spread": {"total_ms": {"min": 229.5, "max": 251.3, "values": [229.5, 233.0, 236.9, 240.2, 251.3]}}},
    {"location": "AGENTTMP_find_item", "runs": "5/5", "count": 15, "self_ms": 4.1, "total_ms": 66.2,
     "max_ms": 61.9, "detail": "com.example.app",
     "spread": {"total_ms": {"min": 63.0, "max": 70.4, "values": [63.0, 64.8, 66.2, 68.1, 70.4]}}},
]

VERDICT: dict[str, Any] = {
    "summary": "Most of the launch is one screen being built: collection_load takes 0.9 s of the 1.1 s launch. "
               "Three things inside it cost the most.",
    "inside": {"report": 1, "location": "collection_load"},
    "causes": [
        {
            "title": "Every item card is inflated on the main thread, one after another",
            "where": ["feature/collection/src/main/java/com/example/app/ui/CollectionAdapter.kt:41-58"],
            "mechanism": "The list builds all 15 cards before the first frame, and each `inflate` runs on the "
                         "main thread, which also has to draw the screen (inferred from the marker around the bind).",
            "evidence": [
                {"report": 1, "detector": "main_thread_block", "location": "inflate"},
                {"report": 2, "location": "AGENTTMP_bind_item_cards"},
            ],
            "cost_ms": 223,
            "cost_of": "of the main thread's own work, as `inflate` measured it",
            "fix": "Build only the cards that fit the first screen, and reuse one inflated layout for the rest.",
            "expected_ms": [120, 160],
            "confidence": "high",
            "confidence_why": "five launches in each of two rounds, within 10% of each other",
        },
        {
            "title": "The main thread waits for a lock the store holds",
            "where": ["feature/collection/src/main/java/com/example/app/data/StoreRepository.kt:30",
                      "feature/collection/src/main/java/com/example/app/data/StoreRepository.kt:61"],
            "mechanism": "`find` is called from the main thread while `update` holds the same lock on a "
                         "background thread.",
            "evidence": [
                {"report": 1, "detector": "monitor_contention", "location": "main"},
                {"report": 2, "location": "AGENTTMP_find_item"},
            ],
            "cost_ms": [62, 66],
            "cost_of": "the main thread spent waiting",
            "fix": "Read the items from a snapshot, so that `find` takes no lock.",
            "expected_ms": 60,
            "confidence": "medium",
            "confidence_why": "the wait is measured; that the snapshot removes all of it is not",
        },
        {
            "title": "Text is laid out again for every card",
            "where": ["feature/collection/src/main/java/com/example/app/ui/ItemCard.kt:22"],
            "evidence": [{"report": 1, "detector": "main_thread_block", "location": "TextLayout:initLayout"}],
        },
    ],
    "suggestions": [
        {"fix": "Show the first screen from the list kept from last time, and load the collection after it.",
         "expected_ms": [600, 800]},
    ],
    "ruled_out": [
        "Garbage collection: no pause above 8 ms in any launch",
        "Waiting for the disk: the main thread did not sleep on it",
    ],
    "also_measured": [
        "AGENTTMP_bind_item_cards: 237 ms",
        "AGENTTMP_find_item: 66 ms",
    ],
    "cleanup": "removed",
}

CLEAN = {"files": [], "markers": 0, "removable": 0, "lines": 0, "prefix": "AGENTTMP_"}


def rounds(demo_report: dict[str, Any]) -> dict[int, dict[str, Any] | None]:
    """The reports of the investigation, by the number each is filed under."""
    first = copy.deepcopy(demo_report)
    first["generated_at"] = "2026-03-02T09:01:17+00:00"
    second = copy.deepcopy(demo_report)
    second["generated_at"] = "2026-03-02T09:07:37+00:00"
    second["markers"]["rows"] = copy.deepcopy(MARKERS) + second["markers"]["rows"]
    second["markers"]["in_trace"] = [m["location"] for m in MARKERS]
    return {1: first, 2: second}


def check_of(verdict: dict[str, Any], reports: dict[int, Any], left: dict[str, Any] | None = None) -> dict[str, Any]:
    """`verdict_check`, as closing an investigation records it."""
    from echolot import verdict as verdict_mod
    return verdict_mod.record(verdict, reports, left or CLEAN, CLOSED)


def record(verdict: dict[str, Any] | None = None, status: str = "concluded", reports: int = 2,
           **over: Any) -> dict[str, Any]:
    """The investigation's record, as `hunt.json` holds it."""
    hunt: dict[str, Any] = {
        "n": N, "question": QUESTION, "since": "the 2.4 release", "scenario": "coldStart",
        "config_sha": "3d78738defec", "confirmed": {}, "opened_at": OPENED,
        "touched_at": "2026-03-02T09:16:02+00:00", "status": status, "collects": reports,
        "analyzes": reports, "traces": [".echolot/traces"], "conclusion": None, "reports": reports,
    }
    if status != "open":
        hunt["closed_at"] = CLOSED
    if verdict is not None:
        hunt["verdict"] = verdict
        hunt["conclusion"] = verdict.get("summary")
    hunt.update(over)
    return hunt


# The path a served page has in its top bar: (label, href, is this page).
STEPS = [("Start", "#start", False), ("Report", "#report", False), ("Agent's answer", "#answer", True),
         ("After the fix", "#after", False)]

AGAIN = ("echolot collect && echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace "
         f"&& echolot compare .echolot/hunts/{N}/reports/001.json")


def states(demo_report: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[int, Any]]]:
    """Every state a page can be in, by name: the record and its reports."""
    both = rounds(demo_report)
    one = copy.deepcopy(VERDICT)
    one["causes"] = one["causes"][:1]
    for key in ("suggestions", "ruled_out", "also_measured"):
        del one[key]
    nothing = {"summary": "Nothing stands out: the launch is spread thin over many small pieces of work.",
               "causes": [], "ruled_out": VERDICT["ruled_out"], "also_measured": VERDICT["also_measured"],
               "cleanup": "none_added"}
    long = copy.deepcopy(VERDICT)
    long["causes"][0]["title"] = ("Every item card of the collection screen is inflated on the main thread, one "
                                  "after another, before the first frame of the launch can be drawn at all")
    long["causes"][0]["where"] = [
        "feature/collection/src/main/java/com/example/app/feature/collection/presentation/list/adapter/"
        "CollectionItemCardAdapterWithAVeryLongNameThatHasNoSlashToBreakAt.kt:41-58",
        "feature/collection/src/main/java/com/example/app/ui/CollectionAdapter.kt:41-58"]
    text = ("The first screen waits for the whole collection to be built. Inflating every item card on the main "
            "thread costs about 0.2 s, and a lock in StoreRepository about 60 ms more.\n\n"
            "Build only the cards that fit the screen first.")
    return {
        "many": (record(VERDICT, verdict_check=check_of(VERDICT, both)), both),
        "one": (record(one, verdict_check=check_of(one, both)), both),
        "nothing": (record(nothing, verdict_check=check_of(nothing, both)), both),
        "long": (record(long, verdict_check=check_of(long, both)), both),
        "text": (record(None, conclusion=text), both),
        "open": (record(None, status="open"), both),
        "open-empty": (record(None, status="open", reports=0, collects=0, analyzes=0), {}),
        "left": (record(None, status="abandoned"), {1: both[1]}),
        "gone": (record(VERDICT, verdict_check=check_of(VERDICT, both)), {1: both[1]}),
    }


def write(root: Path, demo_report: dict[str, Any]) -> list[Path]:
    """Every state as a page in `root`, and the investigation itself as files
    under `root/project`, the way a project holds one."""
    from echolot.pages import answer_page
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for name, (hunt, reports) in states(demo_report).items():
        page = root / f"answer-{name}.html"
        page.write_text(answer_page.render(hunt, reports, again=AGAIN), encoding="utf-8")
        out.append(page)
    hunt, reports = states(demo_report)["many"]
    # The same page as a server would hand it out: the steps to the other
    # pages in the top bar, and a button in the place kept for one.
    served = root / "answer-served.html"
    served.write_text(answer_page.render(
        hunt, reports, again=AGAIN, steps=STEPS,
        action_html='<button type="button" class="btn primary">Check again</button>'), encoding="utf-8")
    out.append(served)
    home = root / "project" / ".echolot" / "hunts" / str(N)
    (home / "reports").mkdir(parents=True, exist_ok=True)
    (home / "hunt.json").write_text(json.dumps(hunt, indent=2) + "\n", encoding="utf-8")
    for n, report in reports.items():
        (home / "reports" / f"{n:03d}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    from echolot import demo, recorder
    with recorder.isolated():
        for written in write(Path(sys.argv[1]), demo.report()):
            print(written)
