"""A launch that ended on another activity than the one started (#909).

Wikipedia opens the page read last by itself when that was less than a day
ago. Two of thirteen cold starts on an SM-F711B ended on `PageActivity`, 497
and 572 ms against 273–317 for the feed, with WebView's start inside, and
`collect` recorded both as an ordinary `COLD`: `am start -W` printed
`Activity: …PageActivity` and the runner read past it. A set holding both
said `Repeat windows diverged 2.1x` without saying which repeats or why; a
set with one said nothing.

`collect` keeps where each launch ended, says it, and counts the set;
`analyze` leaves out the repeats that ended where fewer did, by `collect`'s
record or, without one, by the trace's last `performCreate:`.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, recording, runner  # noqa: E402
from echolot import render as render_mod  # noqa: E402
from echolot.main import _one_journey, main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402
    ACTIVITY, AM_START, CONFIG, PACKAGE, FakeAdb, am_start)

MAIN, PAGE = f"{PACKAGE}.MainActivity", f"{PACKAGE}.page.PageActivity"


def ended_on(cls: str, total: int = 523) -> str:
    """`am start -W` for a cold launch of `.MainActivity` that ended on `cls`,
    named the way a phone prints it: short where the class is in the package."""
    shown = f"{PACKAGE}/.{cls[len(PACKAGE) + 1:]}" if cls.startswith(PACKAGE + ".") \
        else f"{PACKAGE}/{cls}"
    return AM_START.format(activity=ACTIVITY, total=total, wait=total + 20).replace(
        f"Activity: {ACTIVITY}", f"Activity: {shown}")


# --- one component, written two ways ---------------------------------------------

@pytest.mark.parametrize("given, full", [
    ("com.example.app/.MainActivity", "com.example.app/com.example.app.MainActivity"),
    ("com.example.app/com.example.app.MainActivity", "com.example.app/com.example.app.MainActivity"),
    ("org.wikipedia.alpha/org.wikipedia.page.PageActivity",
     "org.wikipedia.alpha/org.wikipedia.page.PageActivity"),
    ("", None), (None, None), ("no slash", None),
])
def test_a_component_reads_the_same_in_its_short_form(given, full):
    """`am start -W` prints the short form (an SM-F711B:
    `com.sec.android.app.clockpackage/.ClockPackage`); a config may hold
    either."""
    check(f"{given!r} is {full!r}", runner.component(given) == full)


# --- collect -----------------------------------------------------------------------

def test_a_launch_that_ended_where_it_started_says_nothing(tmp_path, monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    said: list[str] = []
    made = runner.collect(PACKAGE, tmp_path / "t", 2, section={}, name="startup",
                          log=said.append)
    kept = [runner.launch_record(r["path"]) for r in made]
    check("the short form and the resolved one are one activity: nothing said",
          not any("ended on" in s for s in said), said)
    check("and each record keeps where it ended and what was started",
          all(k["activity"] == k["asked"] == f"{PACKAGE}/{MAIN}" for k in kept), kept)
    check("nothing at the end of the set", runner.activities_said(made) is None)


def test_collect_says_which_repeat_ended_on_another_activity(tmp_path, monkeypatch, capsys):
    fake = FakeAdb(script={"start": [ended_on(PAGE, 572), ended_on(MAIN, 290)]})
    monkeypatch.setattr(runner, "_run", fake)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect"])
    out, err = capsys.readouterr()
    lines = err.splitlines()
    first = next(i for i, s in enumerate(lines) if "[1/3]" in s)
    check("exit 0, and all three repeats are the set's", code == 0
          and len([s for s in out.splitlines() if s.endswith(".perfetto-trace")]) == 3, err)
    check("a line under repeat 1 names both activities",
          lines[first + 1].strip() == f"the launch ended on {PAGE}, not on the {MAIN} it "
          f"started: the app went on to another activity by itself", lines[first:first + 3])
    check("and only there", err.count("the launch ended on") == 1, err)
    check("the end of the set counts them, by repeat",
          f"[!] The launches did not end on one activity: {PAGE} in repeat 1, {MAIN} in "
          f"the other 2." in err and "analyze reads the activity most repeats ended on"
          in err, err)
    rec = runner.launch_record(tmp_path / ".echolot" / "traces" / "startup_iter000.perfetto-trace")
    check("the repeat's record keeps it, the line with it",
          rec["activity"] == f"{PACKAGE}/{PAGE}" and rec["asked"] == f"{PACKAGE}/{MAIN}"
          and rec["notes"] == [lines[first + 1].strip()], rec)


def test_a_splash_that_hands_over_every_time_is_said_once(tmp_path, monkeypatch):
    """A launcher activity that starts the main one and finishes ends every
    launch elsewhere: that is the app's start, and one line says so."""
    splash = f"{PACKAGE}/.SplashActivity"
    fake = FakeAdb(script={"start": [ended_on(MAIN)]})
    monkeypatch.setattr(runner, "_run", fake)
    said: list[str] = []
    made = runner.collect(PACKAGE, tmp_path / "t", 3, section={"activity": splash},
                          name="startup", log=said.append)
    under = [s for s in said if "the launch ended on" in s]
    check("one line, under repeat 1", len(under) == 1 and said.index(under[0]) - 1
          == next(i for i, s in enumerate(said) if "[1/3]" in s), said)
    end = runner.activities_said(made) or ""
    check("and the end says every launch ended there", end.startswith(
        f"every launch of the 3 ended on {MAIN}, not on the {PACKAGE}.SplashActivity "
        f"it started"), end)


def test_a_launch_with_no_activity_line_records_none(tmp_path, monkeypatch):
    fake = FakeAdb(script={"start": [am_start().replace(f"Activity: {ACTIVITY}\n", "")]})
    monkeypatch.setattr(runner, "_run", fake)
    made = runner.collect(PACKAGE, tmp_path / "t", 1, section={}, name="startup",
                          log=lambda m: None)
    rec = runner.launch_record(made[0]["path"])
    check("nothing to compare: nothing said, and the record says null",
          rec["activity"] is None and not rec["notes"]
          and runner.activities_said(made) is None, rec)


# --- analyze -----------------------------------------------------------------------

LAUNCH_CONFIG = f"""\
project:
  package: {PACKAGE}
  process: {PACKAGE}
scenario:
  name: startup
  window: launch
"""


def _trace(root: Path, name: str, activities: tuple[str, ...],
           launch: dict | None = None, **build) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fixture.build(activities=activities, **build))
    if launch is not None:
        recording.write(path, {"runner": "launch", "reset_policy": "force-stop",
                               "launch": launch})
    return str(path)


def _analyze(tmp_path: Path, traces: list[str]) -> tuple[int, dict, str, str]:
    (tmp_path / "echolot.yml").write_text(LAUNCH_CONFIG, encoding="utf-8")
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(["analyze", "-c", str(tmp_path / "echolot.yml"),
                     "-o", str(tmp_path / "out"), *traces])
    rep = json.loads((tmp_path / "out" / "report.json").read_text()) if code == 0 else {}
    md = (tmp_path / "out" / "report.md").read_text() if code == 0 else ""
    return code, rep, md, err.getvalue()


def test_analyze_leaves_out_the_launch_that_ended_elsewhere_by_its_trace(tmp_path):
    """A set recorded before `collect` kept where each launch ended: the
    trace's last `performCreate:` says it."""
    a = _trace(tmp_path, "t/s_iter000.perfetto-trace", (MAIN, PAGE))
    b = _trace(tmp_path, "t/s_iter001.perfetto-trace", (MAIN,))
    c = _trace(tmp_path, "t/s_iter002.perfetto-trace", (MAIN,), frames=False)
    code, rep, md, err = _analyze(tmp_path, [a, b, c])
    assert code == 0, err[-800:]
    check("the medians are over the two that ended on the feed",
          rep["traces"] == [b, c], rep["traces"])
    check("report.json keeps which was left out, where it ended, and its repeat",
          rep["ended_elsewhere"] == [{"trace": a, "activity": PAGE, "repeat": 1,
                                      "others": MAIN}], rep.get("ended_elsewhere"))
    said = (f"Left out: `s_iter000.perfetto-trace` (repeat 1, ended on `{PAGE}`); the "
            f"other 2 ended on `{MAIN}`. Those are two journeys")
    check("report.md says it under the runs", said in md
          and md.index("Runs: **2**") < md.index(said), md[:1200])
    check("and so does the run, and the overview", said in err
          and said in render_mod.overview(rep), err)
    check("the window line says where the launches ended",
          f"The launch ended on `{MAIN}`." in md, md[:1600])


def test_collects_record_is_read_before_the_trace(tmp_path):
    """`am start -W`'s word, kept beside the trace, decides; a trace on an older
    phone names no activity at all."""
    rec = {"repeat": 3, "attempt": 1, "state": "COLD", "total_time_ms": 572, "cold": True,
           "activity": f"{PACKAGE}/{PAGE}", "asked": f"{PACKAGE}/{MAIN}", "notes": []}
    main_rec = {**rec, "repeat": 1, "activity": f"{PACKAGE}/{MAIN}"}
    a = _trace(tmp_path, "t/s_iter000.perfetto-trace", (), main_rec)
    b = _trace(tmp_path, "t/s_iter001.perfetto-trace", (), {**main_rec, "repeat": 2})
    c = _trace(tmp_path, "t/s_iter002.perfetto-trace", (), rec)
    reports = [{"trace": t, "window": {}} for t in (a, b, c)]
    read, left, mixed = _one_journey(reports)
    check("repeat 3 is left out by its record", [r["trace"] for r in read] == [a, b]
          and left == [{"trace": c, "activity": PAGE, "repeat": 3, "others": MAIN}]
          and mixed is None, left)


def test_as_many_on_each_reads_the_one_started_or_all(tmp_path):
    started = {"repeat": 1, "attempt": 1, "state": "COLD", "cold": True,
               "asked": f"{PACKAGE}/{MAIN}", "notes": []}
    a = _trace(tmp_path, "r/s_iter000.perfetto-trace", (), {**started, "activity": f"{PACKAGE}/{PAGE}"})
    b = _trace(tmp_path, "r/s_iter001.perfetto-trace", (), {**started, "activity": f"{PACKAGE}/{MAIN}"})
    read, left, mixed = _one_journey([{"trace": t, "window": {}} for t in (a, b)])
    check("one each, and one is the activity started: that one is read",
          [r["trace"] for r in read] == [b] and [e["trace"] for e in left] == [a]
          and mixed is None, (read, left))
    x = _trace(tmp_path, "m/s_iter000.perfetto-trace", (MAIN, PAGE))
    y = _trace(tmp_path, "m/s_iter001.perfetto-trace", (MAIN,), frames=False)
    code, rep, md, err = _analyze(tmp_path, [x, y])
    assert code == 0, err[-800:]
    check("with nothing started to go by, both are read", rep["traces"] == [x, y]
          and "ended_elsewhere" not in rep, rep["traces"])
    check("and the report says the medians mix two journeys",
          rep["window"]["activities"] == {PAGE: 1, MAIN: 1}
          and f"> ⚠️ The launches ended on different activities: `{PAGE}` in 1, `{MAIN}` "
          f"in 1 of 2." in md and "The launch ended on" not in md, md[:1600])


def test_a_set_that_ended_on_one_activity_is_read_whole(tmp_path):
    a = _trace(tmp_path, "t/s_iter000.perfetto-trace", (MAIN,))
    b = _trace(tmp_path, "t/s_iter001.perfetto-trace", ())
    reports = [{"trace": t, "window": {"activity": w}} for t, w in ((a, MAIN), (b, None))]
    check("one activity, and one repeat with nothing to tell it by: both read",
          _one_journey(reports) == (reports, [], None))
