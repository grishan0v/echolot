#!/usr/bin/env python3
"""Self-check for the open investigation — `.echolot/hunt.json`.

The question this feature answers is "carry on with what we were chasing, or
start something new", and the expensive way to get it wrong is to ask inside
the loop: `perf-hunter` re-records traces and adds markers on purpose, and a
prompt in the middle of that would break it. So the checks below pin both
halves — when the choice is offered, and when it must not be.

    python tests/check_hunt.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot.main import next_kind, project_state  # noqa: E402

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: coldStart
runner:
  mode: launch
"""

RESULTS: list[tuple[str, str | None]] = []


def check(name: str, ok: bool, why: str = "") -> None:
    RESULTS.append((name, None if ok else (why or "failed")))


def setup(tmp: Path, *, config: str = CONFIG, traces: int = 0,
          report: bool = False) -> Path:
    """A project with as much history as the case under test needs."""
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "echolot.yml").write_text(config, encoding="utf-8")
    if traces:
        d = tmp / ".echolot" / "traces"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(traces):
            (d / f"coldStart_iter{i}.perfetto-trace").write_bytes(b"not a real trace")
    if report:
        d = tmp / ".echolot" / "out"
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": {"detectors_fired": 3, "detectors_run": 6},
            "traces": ["a", "b"],
        }), encoding="utf-8")
    return tmp


def backdate(project: Path, **delta) -> None:
    """Age the open investigation, so freshness can be tested without waiting."""
    h = hunt_mod.load(project)
    when = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds")
    h["touched_at"] = when
    h.setdefault("opened_at", when)
    hunt_mod.save(project, h)


def kind(project: Path) -> str:
    """`next` for these cases, with the layer check taken out of the way.

    `next_kind` looks at the `.claude/` layer before anything else, and none
    of these cases are about the layer.
    """
    st = project_state(project, "echolot.yml")
    st["layer_verdict"] = "current"
    return next_kind(st)


# --- the choice, and when it is not offered ---------------------------------

def case_no_hunt(tmp: Path) -> None:
    """No investigation ever opened: nothing to carry on with, do not ask."""
    p = setup(tmp, traces=5, report=True)
    check("no investigation → hunt", kind(p) == "hunt", kind(p))


def case_fresh(tmp: Path) -> None:
    """Worked on minutes ago: the same sitting, asking would be noise."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    check("worked on just now → hunt", kind(p) == "hunt", kind(p))
    check("fresh is fresh", hunt_mod.is_fresh(hunt_mod.load(p)))


def case_stale_with_history(tmp: Path) -> None:
    """Away for days with traces still on disk: this is the case to ask about."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    check("away, history present → resume-or-new",
          kind(p) == "resume-or-new", kind(p))


def case_stale_no_history(tmp: Path) -> None:
    """Away for days but nothing on disk: a new hunt inherits nothing. Do not ask."""
    p = setup(tmp)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    check("away, nothing to inherit → hunt", kind(p) == "hunt", kind(p))


def case_concluded(tmp: Path) -> None:
    """An answered investigation is not something to carry on with."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    hunt_mod.conclude(p, "TextLayout:initLayout on the main thread")
    check("concluded → hunt", kind(p) == "hunt", kind(p))
    check("conclusion kept",
          hunt_mod.load(p)["conclusion"].startswith("TextLayout"))


def case_loop_is_never_asked(tmp: Path) -> None:
    """The loop's own work keeps the investigation fresh, so it never sees a prompt.

    This is the constraint the whole design turns on. `perf-hunter` re-records
    and re-analyses for several rounds; each of those touches the open
    investigation, which keeps it inside the freshness window. Even starting
    from an old one, the loop's first `analyze` settles the question.
    """
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    check("stale before the loop starts", kind(p) == "resume-or-new", kind(p))
    for _ in range(3):                       # three rounds of the loop
        hunt_mod.touch(p, collect=True)
        hunt_mod.touch(p, analyze=True)
        check("inside the loop → never asked", kind(p) == "hunt", kind(p))
    h = hunt_mod.load(p)
    check("rounds counted", h["collects"] == 3 and h["analyzes"] == 3,
          f"collects={h['collects']} analyzes={h['analyzes']}")


def case_touch_follows_the_config(tmp: Path) -> None:
    """`analyze` runs from wherever the traces are, not from the project root.

    The agent calls it inside a macrobenchmark's output directory. If the touch
    followed the working directory instead of the config, a loop's own work
    would never reach the investigation, and the freshness rule would then
    offer to abandon a hunt that was running at that very moment.
    """
    from echolot.config import Config
    from echolot.main import _project_root

    p = setup(tmp, traces=5)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    elsewhere = tmp / "build" / "outputs"
    elsewhere.mkdir(parents=True)
    here = Path.cwd()
    try:
        os.chdir(elsewhere)
        cfg = Config.load(p / "echolot.yml")
        check("the config names the project root", _project_root(cfg) == p.resolve(),
              str(_project_root(cfg)))
        hunt_mod.touch(_project_root(cfg), analyze=True)
    finally:
        os.chdir(here)
    check("work from elsewhere still reaches the investigation",
          hunt_mod.load(p)["analyzes"] == 1 and kind(p) == "hunt")


def case_touch_without_hunt(tmp: Path) -> None:
    """An ad-hoc analyze must not invent an investigation out of nothing."""
    p = setup(tmp)
    hunt_mod.touch(p, analyze=True)
    check("touch with nothing open writes nothing",
          hunt_mod.load(p) is None and not hunt_mod.path(p).exists())


# --- drift, archiving, leftovers --------------------------------------------

def case_drift_scenario(tmp: Path) -> None:
    """The config's scenario changed: almost certainly a different question."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    backdate(p, days=6)
    backdate(p, days=9)
    (p / "echolot.yml").write_text(CONFIG.replace("coldStart", "listScroll"),
                                   encoding="utf-8")
    reasons = hunt_mod.drift(hunt_mod.load(p), project_state(p, "echolot.yml"))
    check("scenario change is reported",
          any("scenario changed" in r for r in reasons), str(reasons))
    check("age is reported", any("untouched for" in r for r in reasons), str(reasons))


def case_archive(tmp: Path) -> None:
    """Starting a new investigation never destroys the previous question."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    hunt_mod.open_new(p, "the list stutters", scenario="listScroll")
    archived = sorted((p / hunt_mod.ARCHIVE_DIR).glob("*.json"))
    check("previous investigation archived", len(archived) == 1,
          f"{len(archived)} file(s)")
    if archived:
        old = json.loads(archived[0].read_text())
        check("archived with its question", old["question"] == "cold start 3s → 7s")
        check("archived as abandoned", old["status"] == "abandoned", old["status"])
    check("the new one is open", hunt_mod.load(p)["question"] == "the list stutters")


def case_leftover_markers(tmp: Path) -> None:
    """Markers a dead investigation left behind, and who can remove them.

    `mark --apply` tags the lines it writes and `mark --remove` takes exactly
    those out; anything the agent added by hand carries only the prefix and
    has to go by hand. Reporting one count for both would send the human away
    believing the tree was clean.
    """
    p = setup(tmp)
    src = p / "app" / "src" / "main" / "kotlin"
    src.mkdir(parents=True)
    (src / "App.kt").write_text(
        'fun a() {\n'
        '  android.os.Trace.beginSection("AGENTTMP_onCreate") // echolot:mark\n'
        '  android.os.Trace.endSection() // echolot:mark\n'
        '}\n', encoding="utf-8")
    (src / "Feed.kt").write_text(
        'fun b() {\n'
        '  android.os.Trace.beginSection("AGENTTMP_byHand")\n'
        '}\n', encoding="utf-8")
    left = hunt_mod.leftovers(p)
    check("every instrumentation point found", left["markers"] == 2,
          str(left["markers"]))
    check("only tagged ones are removable", left["removable"] == 1,
          str(left["removable"]))
    check("both files named", len(left["files"]) == 2, str(left["files"]))

    clean = hunt_mod.leftovers(setup(tmp / "clean"))
    check("a clean tree reports nothing", clean["markers"] == 0)


def case_recap(tmp: Path) -> None:
    """The recap has to answer "carry on with what?" without a second call."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", since="the tab redesign",
                      scenario="coldStart")
    backdate(p, days=9)
    text = "\n".join(hunt_mod.recap(hunt_mod.load(p), project_state(p, "echolot.yml"),
                                    root=p))
    for want in ("cold start 3s → 7s", "the tab redesign", "5 in .echolot/traces",
                 "3 of 6 detectors fired", "untouched for"):
        check(f"recap says: {want}", want in text, text)


def case_corrupt_file(tmp: Path) -> None:
    """A broken hunt file degrades to the behaviour from before it existed."""
    p = setup(tmp, traces=5, report=True)
    hunt_mod.path(p).parent.mkdir(parents=True, exist_ok=True)
    hunt_mod.path(p).write_text("{not json", encoding="utf-8")
    check("unreadable investigation reads as none", hunt_mod.load(p) is None)
    check("and the tool carries on", kind(p) == "hunt", kind(p))


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main() -> int:
    os.environ["ECHOLOT_NO_RECORD"] = "1"
    for case in CASES:
        with tempfile.TemporaryDirectory() as d:
            here = Path.cwd()
            try:
                case(Path(d))
            except Exception as e:                      # noqa: BLE001
                check(f"{case.__name__} raised", False, repr(e))
            finally:
                os.chdir(here)

    failed = [(n, w) for n, w in RESULTS if w]
    for name, why in RESULTS:
        print(f"  ok    {name}" if not why else f"  FAILS {name}\n          {why}")
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
