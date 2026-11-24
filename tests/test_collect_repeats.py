#!/usr/bin/env python3
"""The repeats `collect` keeps, sets aside, and offers to `analyze`.

A round that re-records warm repeats (#523), stops on a first repeat that
never reached its screen (#536), or runs with nothing stopping the app between
repeats (#593) ends with traces of three kinds in .echolot/traces: the set's,
the ones set aside, and the set from before. These are the places where what
`collect` said of them and what it, `status` and the `next` line did with them
came apart (#856, #857, #858, #860), driven through the fake adb of
test_collect_fake_adb.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, runner, state  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_failures import _CtrlC  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402
    AM_START_IN_FRONT,
    CONFIG,
    PACKAGE,
    FakeAdb,
    am_start,
    old_set,
)


def _collect(tmp_path: Path, monkeypatch, capsys, fake: FakeAdb, *args: str,
             config: str = CONFIG) -> tuple[int, str, str]:
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    (tmp_path / "echolot.yml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect", *args])
    out, err = capsys.readouterr()
    return code, out, err


def _status(project: Path) -> dict:
    return state.project_state(project)


# --- #858: past the re-recordings, the count moves on -----------------------------

def test_once_the_re_recordings_are_spent_the_next_recording_is_the_next_repeats(
        tmp_path, monkeypatch, capsys):
    """WARM four times, then COLD: three re-recordings of repeat 1, the fourth
    warm one leaves the set one cold start short, and the next two are repeats
    2 and 3. Repeat 1 used to be recorded a fifth time, and `[3/3]` never came."""
    fake = FakeAdb(script={"start": [am_start("WARM", 500)] * 4 + [am_start("COLD", 500)]})
    code, out, err = _collect(tmp_path, monkeypatch, capsys, fake, "-n", "3")
    check("exit 0 with the two cold starts printed", code == 0
          and [Path(s).name for s in out.splitlines()]
          == ["startup_iter000.perfetto-trace", "startup_iter001.perfetto-trace"], (out, err))
    shown = re.findall(r"^\s*\[(\d+/\d+)\] (\S+)", err, re.MULTILINE)
    check("repeat 1 four times, then 2 and 3", shown == [
        ("1/3", "startup_iter000-warm.perfetto-trace"),
        ("1/3", "startup_iter000-warm-2.perfetto-trace"),
        ("1/3", "startup_iter000-warm-3.perfetto-trace"),
        ("1/3", "startup_iter000-warm-4.perfetto-trace"),
        ("2/3", "startup_iter000.perfetto-trace"),
        ("3/3", "startup_iter001.perfetto-trace")], err)
    check("after the line that says they are spent, no re-recording of repeat 1",
          "repeat 1 is recorded again" not in err.split("are spent")[1], err)
    traces = tmp_path / ".echolot" / "traces"
    kept = [runner.launch_record(traces / f"startup_iter{i:03d}.perfetto-trace")
            for i in range(2)]
    check("each kept trace's account names the repeat it is, first recording",
          [(k["repeat"], k["attempt"]) for k in kept] == [(2, 1), (3, 1)], kept)
    last = runner.launch_record(traces / "startup_iter000-warm-4.perfetto-trace")
    check("the one left short is repeat 1's fourth", (last["repeat"], last["attempt"]) == (1, 4),
          last)
    check("the end counts what the set holds",
          "The set holds 2 cold starts of the 3 asked for" in err, err)


# --- #857: the set before moves once the first trace is let in --------------------

def test_a_round_its_first_repeat_stops_leaves_the_set_before_where_it_was(tmp_path, monkeypatch):
    """The first trace is read before the rest are recorded (#536). The set
    before had moved by then, for a trace the stop deleted, and `on_set_aside`
    had filed it as a round under the investigation (#857)."""
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    out = tmp_path / "traces"
    before = old_set(out)
    moved: list[Path] = []
    read: list[Path] = []

    def stop(trace: Path) -> tuple[str, None]:
        read.append(trace)
        return "com.example.app drew no frame in the scenario's window", None
    with pytest.raises(runner.RunnerError) as e:
        runner.collect(PACKAGE, out, 3, section={}, name="startup", log=lambda m: None,
                       on_set_aside=moved.append, check_first=stop)
    check("the round stops on the first repeat", "repeat 1 of 3: com.example.app drew no "
          "frame" in str(e.value) and fake.kinds().count("pull") == 1, str(e.value))
    check("the set is where it was, under its own names, and nothing beside it",
          sorted(p.name for p in out.iterdir()) == before, list(out.iterdir()))
    check("nothing set aside, so nothing filed", not moved, moved)
    check("the trace read was beside the set, under the repeat's name",
          read and read[0].name == "startup_iter000.perfetto-trace"
          and read[0].parent.parent == out, read)


def test_the_set_before_moves_after_the_first_trace_is_read(tmp_path, monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    out = tmp_path / "traces"
    before = old_set(out)
    moved: list[Path] = []

    def look(trace: Path) -> tuple[None, str]:
        fake.calls.append(["<read>"])
        check("while it is read, the set before is still there",
              sorted(p.name for p in out.glob("*.perfetto-trace")) == before)
        check("and its account is beside it", runner.launch_record(trace) is not None)
        return None, "the end anchor `x` did not occur in repeat 1"

    def aside(d: Path) -> None:
        moved.append(d)
        fake.calls.append(["<set aside>"])
    results = runner.collect(PACKAGE, out, 2, section={}, name="startup",
                             log=lambda m: None, on_set_aside=aside, check_first=look)
    kinds = fake.kinds()
    check("pulled, read, then the set moves, then the second repeat",
          kinds.index("pull") < kinds.index("<read>") < kinds.index("<set aside>")
          < kinds.index("force-stop", kinds.index("<set aside>")), kinds)
    check("the old set is in the directory it went to, whole",
          len(moved) == 1 and sorted(p.name for p in moved[0].iterdir()) == before, moved)
    names = [r["path"] for r in results]
    check("the first trace took the set's first name, with its account and the note",
          [p.name for p in names] == ["startup_iter000.perfetto-trace",
                                      "startup_iter001.perfetto-trace"]
          and all(p.parent == out and p.exists() for p in names)
          and "[!] the end anchor `x` did not occur in repeat 1"
          in runner.launch_record(names[0])["notes"], results)
    check("and nothing is left beside the set", sorted(p.name for p in out.iterdir())
          == sorted([moved[0].name, *(n.name for n in names),
                     *(f"{n.name}.recording.json" for n in names)]), list(out.iterdir()))


def test_one_repeat_that_never_reached_its_screen_is_kept_to_look_at(tmp_path, monkeypatch):
    """With `-n 1` the one trace there is stays, marked: it is let in."""
    monkeypatch.setattr(runner, "_run", FakeAdb())
    out = tmp_path / "traces"
    old_set(out)
    moved: list[Path] = []
    results = runner.collect(PACKAGE, out, 1, section={}, name="startup", log=lambda m: None,
                             on_set_aside=moved.append,
                             check_first=lambda t: ("the app drew no frame", None))
    check("kept under its name, the set before moved for it",
          results[0]["path"] == out / "startup_iter000.perfetto-trace"
          and results[0]["path"].exists() and len(moved) == 1, (results, moved))


# --- #856: a repeat that started nothing is left out of the step ------------------

def test_the_repeats_that_started_nothing_are_left_out_of_the_next_step(
        tmp_path, monkeypatch, capsys):
    """`collect` said to leave them out of `analyze`, and `next` named all
    three: an agent that followed it read two traces with no launch into the
    medians."""
    fake = FakeAdb(script={"start": [am_start("COLD", 500), AM_START_IN_FRONT]})
    code, out, err = _collect(tmp_path, monkeypatch, capsys, fake,
                              config=CONFIG + "  reset_policy: none\n")
    check("exit 0, the three paths printed", code == 0
          and len([s for s in out.splitlines() if s.endswith(".perfetto-trace")]) == 3, err)
    check("the end line says `next` leaves them out",
          "Leave them out of analyze, as the `next` line of `echolot status` does, or record "
          "cold starts with reset_policy: force-stop." in err, err)
    traces = tmp_path / ".echolot" / "traces"
    check("each one's account says it started nothing",
          [bool((runner.launch_record(traces / f"startup_iter{i:03d}.perfetto-trace") or {})
                .get("no_launch")) for i in range(3)] == [False, True, True])
    st = _status(tmp_path)
    step, line = state.by_hand(st)
    check("the step by hand names the one that recorded a launch", step == "analyze"
          and "startup_iter000.perfetto-trace" in line
          and "iter001" not in line and "iter002" not in line, line)
    check("and so does next", "iter001" not in state.next_step(st), state.next_step(st))
    check("the starts line names them, and says they are left out",
          state.starts_line(st) == "startup: 2 UNKNOWN (0), 1 COLD · started nothing, left "
          "out of `next`: startup_iter001.perfetto-trace (UNKNOWN (0)), "
          "startup_iter002.perfetto-trace (UNKNOWN (0))", state.starts_line(st))


def test_a_set_that_is_all_repeats_that_started_nothing_is_offered_as_it_is(
        tmp_path, monkeypatch, capsys):
    """The app in front before the first repeat too: there is no launch to
    read instead, and the `starts` line says what the repeats hold."""
    fake = FakeAdb(script={"start": [AM_START_IN_FRONT]})
    code, _, err = _collect(tmp_path, monkeypatch, capsys, fake, "-n", "2",
                            config=CONFIG + "  reset_policy: none\n")
    check("the end line does not promise a step that leaves them out",
          code == 0 and "2 of 2 repeats started nothing" in err
          and "as the `next` line" not in err, err)
    st = _status(tmp_path)
    _, line = state.by_hand(st)
    check("both offered", "iter000" in line and "iter001" in line, line)
    check("and named as having started nothing, not left out",
          "· started nothing: startup_iter000" in (state.starts_line(st) or ""),
          state.starts_line(st))


# --- #860: no repeat kept ------------------------------------------------------------

def test_a_round_that_kept_no_repeat_says_so_on_the_starts_line(tmp_path, monkeypatch, capsys):
    fake = FakeAdb(script={"start": [am_start("WARM", 500)]})
    code, _, err = _collect(tmp_path, monkeypatch, capsys, fake, "-n", "1")
    check("collect fails: no cold start", code == 2
          and "none of the 2 recordings started cold" in err, err)
    said = state.starts_line(_status(tmp_path))
    check("a count, not an empty one",
          said == "startup: no repeat kept · set aside, not cold: "
          "startup_iter000-warm-2.perfetto-trace (WARM), "
          "startup_iter000-warm.perfetto-trace (WARM)", said)


def test_an_interrupt_while_repeat_1_is_recorded_again_names_the_one_set_aside(
        tmp_path, monkeypatch, capsys):
    """The first repeat started warm, and Ctrl-C came while it was recorded
    again: the line said no trace was kept, with the warm one in
    .echolot/traces."""
    old = old_set(tmp_path / ".echolot" / "traces")
    fake = _CtrlC("start", 2)
    fake.script["start"] = [am_start("WARM", 500)]
    code, _, err = _collect(tmp_path, monkeypatch, capsys, fake)
    check("exit 130", code == 130, err[-600:])
    traces = tmp_path / ".echolot" / "traces"
    check("the warm one is there",
          sorted(p.name for p in traces.glob("*.perfetto-trace"))
          == ["startup_iter000-warm.perfetto-trace"], list(traces.iterdir()))
    aside = [d for d in traces.iterdir() if d.is_dir()]
    check("the set before made room for it",
          len(aside) == 1 and sorted(p.name for p in aside[0].iterdir()) == old, aside)
    check("the line names it, and where the set before went",
          "collect interrupted before the first of 3 repeats was kept: the recording set "
          "aside as not cold is kept in .echolot/traces: startup_iter000-warm.perfetto-trace; "
          f"the set that was there before is in .echolot/traces/{aside[0].name}" in err, err)
    check("and does not say no trace was kept", "no trace was kept" not in err, err)


def test_an_interrupt_after_kept_repeats_names_the_ones_set_aside_too(
        tmp_path, monkeypatch, capsys):
    fake = _CtrlC("start", 3)
    fake.script["start"] = [am_start("COLD", 500), am_start("WARM", 500)]
    code, _, err = _collect(tmp_path, monkeypatch, capsys, fake)
    check("how far it got, and the recording set aside beside it", code == 130
          and "collect interrupted after 1 of 3 repeats: its trace is kept in .echolot/traces; "
          "the recording set aside as not cold is kept there too: "
          "startup_iter001-warm.perfetto-trace" in err, err)
