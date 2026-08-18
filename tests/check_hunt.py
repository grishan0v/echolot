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
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib  # noqa: E402
import io  # noqa: E402

from echolot import hunt as hunt_mod  # noqa: E402
# `main` under another name: this file defines its own runner below.
from echolot.main import main as cli  # noqa: E402
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


# --- through the CLI, the way anyone actually reaches this ------------------

def run(*argv) -> tuple[int, str]:
    """`echolot …` for real: argv in, exit code and everything printed out."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(list(argv))
    return code, out.getvalue() + err.getvalue()


def case_cli_round_trip(tmp: Path) -> None:
    """Open, report, conclude, list — the whole verb, from the command line."""
    p = setup(tmp, traces=3)
    here = Path.cwd()
    try:
        os.chdir(p)
        code, text = run("hunt")
        check("bare hunt with nothing open says so",
              code == 0 and "no investigation is open" in text, text)

        code, text = run("hunt", "cold start 3s → 7s", "--since", "tab redesign")
        check("hunt opens one", code == 0 and "cold start" in text, text)
        check("and moves the previous traces aside", "set aside: 3 trace(s)" in text, text)
        # The half a shell cannot do has to be said, or the person is left
        # with an open investigation and no idea what comes next.
        check("and names the half it cannot do", "/echolot" in text, text)
        check("traces no longer loose",
              not list((p / ".echolot" / "traces").glob("*.perfetto-trace")))

        code, text = run("hunt")
        check("bare hunt now recaps", "cold start 3s → 7s" in text, text)

        code, text = run("hunt", "--done", "TextLayout on main")
        check("--done closes it", code == 0 and "concluded" in text, text)

        code, text = run("hunt", "--list")
        check("--list shows it with its conclusion",
              "cold start 3s → 7s" in text and "TextLayout on main" in text, text)

        code, text = run("hunt", "--resume")
        check("--resume on a concluded hunt still reports it",
              code == 0 and "cold start" in text, text)
    finally:
        os.chdir(here)


def case_cli_status_is_read_only(tmp: Path) -> None:
    """`status` must not change what it reports on: it runs in loops."""
    p = setup(tmp, traces=3)
    here = Path.cwd()
    try:
        os.chdir(p)
        run("hunt", "the list stutters")
        before = hunt_mod.path(p).read_text()
        for _ in range(3):
            run("status")
            run("status", "--next")
        check("status left the investigation untouched",
              hunt_mod.path(p).read_text() == before)
        code, text = run("status")
        check("status still names the open one", "the list stutters" in text, text)
    finally:
        os.chdir(here)


def case_cli_numbering_and_evidence(tmp: Path) -> None:
    """Numbers a person can type, and where each question's traces went.

    The archive used to remember the question and forget what was measured:
    `set_aside` returns the directory it created and the return value was
    dropped. A set of traces belongs to the investigation that was open when
    it was pushed aside, so it is recorded on the one being closed.
    """
    p = setup(tmp)
    here = Path.cwd()
    try:
        os.chdir(p)

        def traces(n=4):
            d = p / ".echolot" / "traces"
            d.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (d / f"coldStart_iter{i}.perfetto-trace").write_bytes(b"x")

        traces()
        _, text = run("hunt", "cold start 3s → 7s")
        check("first is #1", "opened #1:" in text, text)
        run("hunt", "--done", "TextLayout on main")
        traces()
        _, text = run("hunt", "the list stutters")
        check("second is #2", "opened #2:" in text, text)

        # The set aside when #2 opened was #1's evidence, not #2's.
        first = hunt_mod.find(p, "1")
        check("#1 kept its traces", len(first.get("traces") or []) == 1,
              str(first.get("traces")))
        check("#2 starts with none", not (hunt_mod.load(p).get("traces") or []))
        recorded = Path(first["traces"][0])
        check("the path is relative to the project", not recorded.is_absolute(),
              str(recorded))
        check("and it is really there",
              len(list((p / recorded).glob("*.perfetto-trace"))) == 4)

        code, text = run("hunt", "--show", "1")
        check("--show by number", code == 0 and "cold start 3s → 7s" in text, text)
        check("--show counts the traces", "4 trace(s)" in text, text)
        code, text = run("hunt", "--show", "stutters")
        check("--show by words", code == 0 and "the list stutters" in text, text)
        code, text = run("hunt", "--show", "99")
        check("--show on a miss exits 1", code == 1, f"exit {code}")
    finally:
        os.chdir(here)


def case_cli_list_order(tmp: Path) -> None:
    """Newest first, even when several open within the same second.

    Sorting on the timestamp alone left three same-second investigations in
    whatever order the directory listing produced.
    """
    p = setup(tmp)
    here = Path.cwd()
    try:
        os.chdir(p)
        for q in ("first thing", "second thing", "third thing"):
            run("hunt", q)
        _, text = run("hunt", "--list")
        order = re.findall(r"^[→ ] *(\d+)  \d{4}-", text, re.M)
        check("newest first", order == ["3", "2", "1"], f"{order}\n{text}")
    finally:
        os.chdir(here)


def case_record_from_0_2_0(tmp: Path) -> None:
    """A hunt.json written before numbering existed must still work.

    Anyone upgrading from 0.2.0 has one on disk with no `n` and no `traces`.
    """
    p = setup(tmp, traces=3)
    hunt_mod.path(p).parent.mkdir(parents=True, exist_ok=True)
    hunt_mod.path(p).write_text(json.dumps({
        "question": "cold start 3s → 7s", "since": None, "scenario": "coldStart",
        "config_sha": "abc", "opened_at": "2026-08-17T10:00:00+00:00",
        "touched_at": "2026-08-17T10:00:00+00:00", "status": "open",
        "collects": 2, "analyzes": 5, "conclusion": None,
    }), encoding="utf-8")
    here = Path.cwd()
    try:
        os.chdir(p)
        code, text = run("hunt", "--list")
        check("an old record still lists", code == 0 and "cold start" in text, text)
        check("and shows what it did", "2 collect(s)" in text, text)
        code, text = run("hunt", "--show", "cold start")
        check("and is reachable by words", code == 0, text)
        check("its missing traces are stated", "none recorded" in text, text)
        # The next investigation numbers itself around the gap.
        _, text = run("hunt", "something else")
        check("numbering starts at 1 beside an unnumbered record",
              "opened #1:" in text, text)
    finally:
        os.chdir(here)


def case_rounds_and_reports_accumulate(tmp: Path) -> None:
    """A multi-round hunt keeps every round and every report it produced.

    Before this, `.echolot/out/report.json` was overwritten by each analyze —
    including one belonging to a different question — and the directories
    `collect` pushed aside between rounds were recorded nowhere. An
    investigation remembered its last set of traces and nothing it reasoned
    from on the way there.
    """
    p = setup(tmp)
    out = p / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")

    for r in (1, 2, 3):
        out.joinpath("report.json").write_text(f'{{"round": {r}}}', encoding="utf-8")
        out.joinpath("report.md").write_text(f"# round {r}", encoding="utf-8")
        hunt_mod.record_report(p, out)
        d = p / ".echolot" / "traces" / f"coldStart-round{r}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "coldStart_iter0.perfetto-trace").write_bytes(b"x")
        hunt_mod.record_traces(p, d)

    h = hunt_mod.load(p)
    check("every round recorded", len(h["traces"]) == 3, str(h["traces"]))
    check("every report counted", h["reports"] == 3, str(h.get("reports")))
    kept = sorted((p / hunt_mod.ARCHIVE_DIR / "1" / "reports").glob("*.json"))
    check("and kept on disk, oldest first", [f.name for f in kept]
          == ["001.json", "002.json", "003.json"], str([f.name for f in kept]))
    check("each holds that round's report",
          json.loads(kept[0].read_text())["round"] == 1
          and json.loads(kept[2].read_text())["round"] == 3)
    check("the markdown came along",
          (p / hunt_mod.ARCHIVE_DIR / "1" / "reports" / "003.md").exists())
    check("recording the same directory twice does not double it",
          (hunt_mod.record_traces(p, p / ".echolot" / "traces" / "coldStart-round3")
           or len(hunt_mod.load(p)["traces"])) == 3)


def case_collect_reports_the_round_it_set_aside(tmp: Path) -> None:
    """`collect` hands back the directory it pushed the previous round into.

    It used to call `set_aside` and drop the return value — the same shape of
    defect as `cmd_hunt` dropping it, and the reason rounds went unrecorded.
    Checked against the real function: the callback has to fire before any
    device work, or a hunt on a machine with no phone attached would lose the
    round anyway.
    """
    from echolot import runner

    p = setup(tmp, traces=3)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    seen: list[Path] = []
    try:
        # gradle with no task fails immediately after the set-aside, which is
        # exactly the window under test — no adb, no device, no waiting.
        runner.collect(package="com.example.app",
                       out_dir=p / ".echolot" / "traces",
                       iterations=1, section={"mode": "gradle"},
                       name="coldStart", log=lambda m: None,
                       on_set_aside=lambda d: (seen.append(d),
                                               hunt_mod.record_traces(p, d)))
    except runner.RunnerError:
        pass
    check("collect reported the set-aside directory", len(seen) == 1, str(seen))
    if seen:
        check("with the traces really in it",
              len(list(seen[0].glob("*.perfetto-trace"))) == 3)
        check("and the investigation recorded it",
              len(hunt_mod.load(p).get("traces") or []) == 1)


def case_reports_do_not_cross_investigations(tmp: Path) -> None:
    """The whole point: one question's evidence never lands under another's."""
    p = setup(tmp)
    out = p / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)

    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    out.joinpath("report.json").write_text('{"belongs_to": 1}', encoding="utf-8")
    hunt_mod.record_report(p, out)

    hunt_mod.open_new(p, "the list stutters", scenario="coldStart")
    out.joinpath("report.json").write_text('{"belongs_to": 2}', encoding="utf-8")
    hunt_mod.record_report(p, out)

    for n in (1, 2):
        kept = sorted((p / hunt_mod.ARCHIVE_DIR / str(n) / "reports").glob("*.json"))
        check(f"#{n} kept exactly its own", len(kept) == 1, str(kept))
        if kept:
            check(f"#{n}'s report is the right one",
                  json.loads(kept[0].read_text())["belongs_to"] == n)


def case_nothing_open_files_nothing(tmp: Path) -> None:
    """An ad-hoc analyze on someone else's trace has no investigation to file under."""
    p = setup(tmp)
    out = p / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    out.joinpath("report.json").write_text("{}", encoding="utf-8")
    check("record_report is a no-op", hunt_mod.record_report(p, out) is None)
    hunt_mod.record_traces(p, p / ".echolot" / "traces")
    check("and nothing was created",
          not (p / hunt_mod.ARCHIVE_DIR).exists() and not hunt_mod.path(p).exists())


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
    """Starting a new investigation never destroys the previous question.

    A numbered investigation is archived into the directory that already holds
    its reports, so everything it produced sits together.
    """
    p = setup(tmp, traces=5, report=True)
    hunt_mod.open_new(p, "cold start 3s → 7s", scenario="coldStart")
    hunt_mod.open_new(p, "the list stutters", scenario="listScroll")
    record = p / hunt_mod.ARCHIVE_DIR / "1" / "hunt.json"
    check("archived into its own home", record.exists(), str(record))
    if record.exists():
        old = json.loads(record.read_text())
        check("archived with its question", old["question"] == "cold start 3s → 7s")
        check("archived as abandoned", old["status"] == "abandoned", old["status"])
    check("the new one is open", hunt_mod.load(p)["question"] == "the list stutters")
    check("both are in the history", len(hunt_mod.history(p)) == 2,
          str(len(hunt_mod.history(p))))


def case_archive_from_0_2_0_is_still_read(tmp: Path) -> None:
    """0.2.0 archived to a flat, timestamped filename. Those must keep showing.

    Anyone upgrading has some. Reading only the new shape would make their
    past investigations vanish from `hunt --list` — the tool losing history
    it had explicitly promised not to delete.
    """
    p = setup(tmp)
    flat = p / hunt_mod.ARCHIVE_DIR / "20260817T100000-an-older-question.json"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(json.dumps({
        "question": "an older question", "scenario": "coldStart",
        "opened_at": "2026-08-17T10:00:00+00:00",
        "touched_at": "2026-08-17T10:30:00+00:00",
        "status": "concluded", "collects": 1, "analyzes": 2,
        "conclusion": "it was the images",
    }), encoding="utf-8")
    here = Path.cwd()
    try:
        os.chdir(p)
        code, text = run("hunt", "--list")
        check("a 0.2.0 archive still lists", code == 0 and "an older question" in text, text)
        check("with its conclusion", "it was the images" in text, text)
        code, text = run("hunt", "--show", "older")
        check("and opens by words", code == 0 and "an older question" in text, text)
    finally:
        os.chdir(here)


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
