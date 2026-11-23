"""A round in which the scenario never reached its screen (#536).

Without a network a list screen showed its full-screen error state, the
swipes scrolled nothing, and all five recordings were accepted: no frame drawn
in any of them. `collect` said nothing, and `analyze` led with "the app on the
phone was built before" the markers, of a build `collect` had just found to
carry every one of them.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import main as main_mod  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot import recording, runner  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _drawn_info, _markers_against_code  # noqa: E402
from echolot.main import _scenario_ran as real_scenario_ran  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_build_before_record import _project, _report  # noqa: E402
from tests.test_collect_fake_adb import ACTIVITY, PACKAGE, FakeAdb  # noqa: E402
from tests.test_markers_in_build import answer, planted  # noqa: E402


@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


# --- collect: one look at the first repeat ---------------------------------------

def test_a_first_repeat_that_drew_nothing_stops_the_round(tmp_path: Path, adb) -> None:
    looked: list[Path] = []

    def look(trace: Path):
        looked.append(trace)
        return "com.example.app drew no frame in the scenario's window", None

    out = tmp_path / "traces"
    with pytest.raises(runner.RunnerError) as e:
        runner.collect(PACKAGE, out, 5, section={"activity": ACTIVITY}, name="startup",
                       log=lambda m: None, check_first=look)
    check("the first trace was looked at, once", looked == [out / "startup_iter000.perfetto-trace"],
          looked)
    check("the round stops, saying why and to look at the phone",
          str(e.value).startswith("repeat 1 of 5: com.example.app drew no frame in the "
                                  "scenario's window. The scenario did not get where it was "
                                  "going") and "Look at the phone" in str(e.value), str(e.value))
    check("nothing more was recorded", adb.kinds().count("perfetto") == 1, adb.kinds())
    check("and nothing of it is left to be analyzed", list(out.iterdir()) == [],
          list(out.iterdir()))


def test_a_single_repeat_keeps_its_trace_and_says_it(tmp_path: Path, adb) -> None:
    said: list[str] = []
    got = runner.collect(PACKAGE, tmp_path / "traces", 1, section={"activity": ACTIVITY},
                         name="startup", log=said.append,
                         check_first=lambda t: ("the end anchor `screen_shown` opened and "
                                                "never closed in its recording", None))
    check("kept", len(got) == 1 and got[0]["path"].exists(), got)
    check("and marked", any(s.startswith("      [!] the end anchor `screen_shown` opened and "
                                         "never closed") for s in said), said)


def test_a_note_is_said_and_the_round_goes_on(tmp_path: Path, adb) -> None:
    said: list[str] = []
    got = runner.collect(PACKAGE, tmp_path / "traces", 3, section={"activity": ACTIVITY},
                         name="startup", log=said.append,
                         check_first=lambda t: (None, "the end anchor did not occur"))
    check("three repeats", len(got) == 3, got)
    check("the note under the first", said.count("      [!] the end anchor did not occur") == 1,
          said)


class _Window:
    """Stands in for trace_processor and the window analyze would cut."""

    def __init__(self, monkeypatch, window: dict):
        @contextlib.contextmanager
        def session(trace, binary=None, **kw):
            yield object()
        monkeypatch.setattr(main_mod, "TraceSession", session)
        monkeypatch.setattr(main_mod, "_resolve_process",
                            lambda tp, glob, trace=None, **kw: [{"upid": 1}])
        monkeypatch.setattr(main_mod, "_setup_context", lambda *a, **kw: {})
        monkeypatch.setattr(main_mod, "_window_info", lambda *a, **kw: window)


@pytest.mark.parametrize("window, stop, note", [
    ({"process": "com.example.app", "drawn": {"count": 0}}, "drew no frame", None),
    ({"process": "p", "drawn": {"count": 30},
      "end_anchor": {"glob": "screen_shown", "matches": 1, "unfinished": True}},
     "opened and never closed", None),
    ({"process": "p", "drawn": {"count": 30},
      "end_anchor": {"glob": "screen_shown", "matches": 0}}, None, "did not occur"),
    ({"process": "p", "drawn": None, "end_anchor": {"glob": "x", "matches": 2}}, None, None),
], ids=["no-frame", "never-closed", "no-end", "fine"])
def test_what_the_first_look_reads(tmp_path, monkeypatch, window, stop, note) -> None:
    _Window(monkeypatch, window)
    got = real_scenario_ran(Config({"project": {"package": PACKAGE}}), tmp_path / "startup_iter000.perfetto-trace")
    check("stop", (stop is None) == (got[0] is None) and (stop is None or stop in got[0]), got)
    check("note", (note is None) == (got[1] is None) and (note is None or note in got[1]), got)


def test_a_trace_the_look_cannot_read_says_nothing(tmp_path) -> None:
    trace = tmp_path / "startup_iter000.perfetto-trace"
    trace.write_bytes(b"not a trace")
    check("nothing to say", real_scenario_ran(Config({"project": {"package": PACKAGE}}), trace) == (None, None))


class _Tp:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        return self.rows


def test_frames_are_counted_where_anything_drew_one() -> None:
    window = {"ts_start": 0, "ts_end": 10}
    check("none drawn by the app", _drawn_info(_Tp([{"seen": 412, "n": 0}]), window)
          == {"count": 0})
    check("nothing anywhere: the categories were not recorded",
          _drawn_info(_Tp([{"seen": 0, "n": 0}]), window) is None)
    check("no window, no count", _drawn_info(_Tp([{"seen": 1, "n": 1}]), {}) is None)


# --- analyze: the first thing it says ----------------------------------------------

def _drawn(count: int, in_trace: list[str] | None = None) -> dict:
    rep = _report(in_trace)
    rep["window"]["drawn"] = {"count": count}
    return rep


def test_a_set_that_drew_no_frame_says_so_first() -> None:
    text = report_mod.to_markdown(_drawn(0))
    said = ("> ⚠️ The app drew no frame in its recording: the scenario did not reach its "
            "screen there. Look at what the phone showed")
    check("said", said in text, text)
    check("right under the process, before anything else is said",
          text.index(said) == text.index("Process: `p`") + len("Process: `p` (pid None)\n"), text)
    merged = report_mod.aggregate([_drawn(0), _drawn(41), _drawn(0)])
    check("merged: in how many repeats",
          merged["window"]["drawn"] == {"count": 0, "none": 2, "runs": 3}
          and "drew no frame in 2 of 3 repeats" in report_mod.to_markdown(merged),
          merged["window"]["drawn"])
    check("a set that drew is not warned", "drew no frame" not in report_mod.to_markdown(
        report_mod.aggregate([_drawn(30), _drawn(41)])))


def test_markers_missing_from_a_set_that_drew_nothing_are_not_put_on_the_build(
        tmp_path: Path) -> None:
    cfg = _project(tmp_path, names=("AGENTTMP_a", "AGENTTMP_b"))
    rep = _drawn(0, [])
    _markers_against_code(rep, cfg)
    text = report_mod.to_markdown(rep)
    check("the scenario, not the build",
          "> ⚠️ 2 temporary markers are in the code and none is in these traces. The app "
          "drew no frame in these recordings, so the scenario most likely did not run the "
          "code they wrap" in text and "built before them" not in text, text)
    check("and after the warning it points to",
          text.index("drew no frame in its recording") < text.index("temporary markers"), text)


def test_markers_collect_found_in_the_build_are_not_put_on_the_build(tmp_path: Path) -> None:
    cfg = _project(tmp_path, names=("AGENTTMP_a",))
    traces = tmp_path / ".echolot" / "traces"
    traces.mkdir(parents=True)
    names = [f"listScroll_iter{i:03d}.perfetto-trace" for i in range(2)]
    for n in names:
        recording.write(traces / n, {"runner": "command", "reset_policy": "none",
                                     "markers_in_build": ["AGENTTMP_a"]})
    reps = []
    for n in names:
        rep = _report([])
        rep["trace"] = str(traces / n)
        reps.append(rep)
    rep = report_mod.aggregate(reps)
    _markers_against_code(rep, cfg)
    check("known to be in the build", rep["markers"]["unbuilt"].get("in_build") is True,
          rep["markers"])
    text = report_mod.to_markdown(rep)
    check("so the scenario did not run their code",
          "> ⚠️ The temporary marker `AGENTTMP_a` is in the code and not in these traces, "
          "though `collect` found it in the build on the phone before it recorded: the "
          "scenario did not run the code it wraps." in text
          and "built before" not in text, text)

    other = _report([])
    other["trace"] = str(tmp_path / "elsewhere_iter000.perfetto-trace")
    _markers_against_code(other, cfg)
    check("a trace collect kept no record of is the old build, as before",
          "in_build" not in other["markers"]["unbuilt"], other["markers"])


def test_collect_keeps_what_it_found_in_the_build(tmp_path: Path, adb) -> None:
    adb.script["build"] = [answer("AGENTTMP_a", "AGENTTMP_b")]
    out = tmp_path / "traces"
    runner.collect(PACKAGE, out, 2, section={"activity": ACTIVITY}, name="startup",
                   log=lambda m: None, planted=planted("AGENTTMP_a", "AGENTTMP_b"))
    check("beside every trace of the set",
          [runner.markers_in_build(out / f"startup_iter{i:03d}.perfetto-trace") for i in range(2)]
          == [["AGENTTMP_a", "AGENTTMP_b"]] * 2)
    runner.collect(PACKAGE, out, 1, section={"activity": ACTIVITY}, name="startup",
                   log=lambda m: None)
    check("and a set with no markers to look for has none",
          runner.markers_in_build(out / "startup_iter000.perfetto-trace") is None)
