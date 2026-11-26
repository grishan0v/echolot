"""Which two reports a bare `compare` takes inside an open investigation.

Every `analyze` inside an open investigation files a report, a look at part of
a round included. Taken as the last two, round 2 was held against a single-run
look at one of its traces: no spread, an empty Holds column, and a header that
read like two rounds (#613). The rule that left looks out went by the traces'
paths and their number, and `collect --hunt` records every round under the same
loose names: a third round read the second one's paths and was left out as a
look, so the newest round was never compared; and a round of ten after one of
eleven was a look as well, so the `--set` look after it became a side (#908).

`analyze` now says what it filed, round or look, by the recordings' bytes and
the thresholds, and `compare` reads that. A report filed before it did is told
from the report itself, and `compare` says it went by that.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from echolot import hunt as hunt_mod
from echolot import main as main_mod
from echolot import recorder
from tests.support import check
from tests.test_compare import FIXTURE_CONFIG, det, report, row

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")
LOOSE = ".echolot/traces"


def _run(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main_mod.main(list(argv))
    return code, err.getvalue()


def _compared(root: Path) -> tuple[str | None, str | None, str]:
    """The two reports a bare `compare` took, by file name, and what it said."""
    code, err = _run("compare")
    if code != 0:
        return None, None, err
    body = json.loads((root / ".echolot" / "out" / "comparison.json").read_text())
    return Path(body["before"]["path"]).name, Path(body["after"]["path"]).name, err


# --- reports `analyze` files, and marks --------------------------------------

@pytest.fixture
def project(tmp_path: Path, monkeypatch, marker_report) -> Path:
    """Investigation #1 open, and an `analyze` that needs no trace_processor:
    every recording reads as the fixture's report."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    hunt_mod.open_new(tmp_path, "the start got slower", scenario="coldStart")

    def analyzed(trace, *a, **kw):
        rep = copy.deepcopy(marker_report)
        rep["trace"] = str(trace)
        return rep
    monkeypatch.setattr("echolot.analyze.analyze_trace", analyzed)
    (tmp_path / LOOSE).mkdir(parents=True)
    return tmp_path


def _record(root: Path, tag: str, n: int, where: str = LOOSE) -> list[str]:
    """A round as `collect --hunt` leaves it: `n` new recordings under the
    loose names, `coldStart_iter000…`, over whatever had those names."""
    (root / where).mkdir(parents=True, exist_ok=True)
    paths = [f"{where}/coldStart_iter{i:03d}.perfetto-trace" for i in range(n)]
    for i, p in enumerate(paths):
        (root / p).write_bytes(f"{tag} recording {i}".encode())
    return paths


def _analyze(paths: list[str], *flags: str) -> str:
    code, err = _run("analyze", *paths, "--hunt", "1", *flags)
    check("analyze files its report", code == 0, err[-600:])
    return err


def _kinds(root: Path) -> list[str]:
    return [e["kind"] for e in hunt_mod.load(root)["filed"]]


def test_rounds_recorded_under_the_same_names_are_rounds(project: Path) -> None:
    """Investigation 1 of the field test (#908): two rounds read the same loose
    paths, `collect --hunt` having moved the round before aside."""
    _analyze(_record(project, "baseline", 5, ".echolot/traces/aside"))
    _analyze(_record(project, "baseline", 5, ".echolot/traces/aside"),
             "--set", "main_thread_block.min_slice_ms=4")
    _analyze(_record(project, "second", 5))
    said = _analyze(_record(project, "third", 5))
    check("analyze says what it filed", "[i] filed as round 3" in said
          and "holds it against round 2 (003.json)" in said, said[-600:])
    check("the record says it", _kinds(project) == ["round", "look", "round", "round"],
          hunt_mod.load(project)["filed"])
    before, after, err = _compared(project)
    check("round 2 against round 3", (before, after) == ("003.json", "004.json"), err[-600:])
    check("and the line says which and why",
          "reports/003.json against" in err and "reports/004.json, rounds 2 and 3" in err
          and "no mark" not in err, err[-600:])


def test_a_round_one_recording_short_is_a_round(project: Path) -> None:
    """Investigation 2 of the field test (#908): ten recordings after eleven,
    a `--set` look at them, ten more."""
    _analyze(_record(project, "baseline", 11, ".echolot/traces/final"))
    _analyze(_record(project, "second", 10))
    said = _analyze(_record(project, "second", 10), "--set", "main_thread_block.min_slice_ms=2")
    check("analyze says the look is one, and why",
          "[i] filed as a look, not a round: it ran with --set "
          "main_thread_block.min_slice_ms=2" in said, said[-600:])
    _analyze(_record(project, "third", 10))
    before, after, err = _compared(project)
    check("round 2 against round 3, past the look", (before, after) == ("002.json", "004.json"),
          err[-600:])
    check("the look is named with why",
          "Left out as looks rather than rounds: 003.json (it ran with --set "
          "main_thread_block.min_slice_ms=2)" in err, err[-600:])


def test_a_look_at_one_run_is_not_the_after_side(project: Path) -> None:
    """#613: a look at one recording of round 2, not its first."""
    _analyze(_record(project, "baseline", 5, ".echolot/traces/aside"))
    second = _record(project, "second", 5)
    _analyze(second)
    said = _analyze(second[3:4])
    check("analyze says it reads round 2 again",
          "filed as a look, not a round: it reads again what round 2 (002.json) read"
          in said, said[-600:])
    before, after, err = _compared(project)
    check("round 1 against round 2", (before, after) == ("001.json", "002.json"), err[-600:])
    check("with the look left out", "003.json (it reads again" in err, err[-600:])


def test_the_same_recordings_elsewhere_are_a_look(project: Path) -> None:
    """A copy is the same recordings under other paths; part of them among new
    ones is not a new round either."""
    _analyze(_record(project, "baseline", 5, ".echolot/traces/aside"))
    _analyze(_record(project, "second", 5))
    _analyze(_record(project, "second", 5, ".echolot/traces/copy"))
    pooled = _record(project, "second", 5, ".echolot/traces/pool")[:2] \
        + _record(project, "other", 3, ".echolot/traces/more")
    said = _analyze(pooled)
    check("part of round 2 among new recordings is a look",
          "2 of its 5 recordings were read by round 2 (002.json) already" in said, said[-600:])
    check("so are both", _kinds(project) == ["round", "round", "look", "look"],
          hunt_mod.load(project)["filed"])
    before, after, _ = _compared(project)
    check("round 1 against round 2", (before, after) == ("001.json", "002.json"))


def test_a_look_first_does_not_take_a_new_round(project: Path) -> None:
    """New recordings looked at with `--set` first are still a round when they
    are read with the config's thresholds."""
    _analyze(_record(project, "baseline", 5, ".echolot/traces/aside"))
    fresh = _record(project, "second", 5)
    _analyze(fresh, "--defaults")
    _analyze(fresh)
    check("look, then round", _kinds(project) == ["round", "look", "round"],
          hunt_mod.load(project)["filed"])
    before, after, _ = _compared(project)
    check("round 1 against round 2", (before, after) == ("001.json", "003.json"))


def test_with_one_round_the_last_two_reports_are_said_for_what_they_are(project: Path) -> None:
    _analyze(_record(project, "baseline", 5))
    _analyze(_record(project, "baseline", 5)[:1])
    before, after, err = _compared(project)
    check("the last two reports", (before, after) == ("001.json", "002.json"), err[-600:])
    check("said with what each is", "[!] compare:" in err and "one round so far" in err
          and "round 1 and a look (it reads again what round 1 (001.json) read)" in err,
          err[-600:])


# --- reports filed before `analyze` marked them -------------------------------

def _investigation(root: Path) -> Path:
    (root / "echolot.yml").write_text(FIXTURE_CONFIG, encoding="utf-8")
    out = root / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    hunt_mod.open_new(root, "scroll janks", scenario="fixture")
    return out


def _file(root: Path, out: Path, value: float, traces: list[str], first: int,
          **config) -> None:
    """One analyze as an echolot before the marks filed it: the latest report,
    and its copy under the investigation, with nothing said of what it is.
    `first` is when its first recording's window opened."""
    body = report([det("d", [row("A", value)])], runs=len(traces),
                  window={"process": "com.example.app", "duration_ms": 1000.0,
                          "pid": 4242, "ts_start": first},
                  config={"sha": "aaaa", "defaults": False, **config})
    body["traces"] = traces if len(traces) > 1 else None
    body["trace"] = traces[0]
    (out / "report.json").write_text(json.dumps(body), encoding="utf-8")
    (out / "report.md").write_text("#", encoding="utf-8")
    hunt_mod.record_report(root, out)


def _loose(n: int) -> list[str]:
    return [f"{LOOSE}/fixture_iter{i:03d}.perfetto-trace" for i in range(n)]


def test_unmarked_rounds_under_the_same_names(tmp_path: Path, monkeypatch) -> None:
    """The field test's investigation 1 as 0.18 filed it: 001 → 003, the
    newest round taken for a look. By their windows, 003 → 004."""
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, [f"traces/aside/{i}" for i in range(5)], first=1000)
    _file(tmp_path, out, 100.0, [f"traces/aside/{i}" for i in range(5)], first=1000,
          set={"main_thread_block": {"min_slice_ms": 4}})
    _file(tmp_path, out, 400.0, _loose(5), first=2000)
    _file(tmp_path, out, 150.0, _loose(5), first=3000)
    monkeypatch.chdir(tmp_path)
    before, after, err = _compared(tmp_path)
    check("round 2 against round 3", (before, after) == ("003.json", "004.json"), err[-800:])
    check("and it says it went by the reports",
          "These reports carry no mark of round or look" in err
          and "passes for a round" in err, err[-800:])


def test_unmarked_round_one_recording_short(tmp_path: Path, monkeypatch) -> None:
    """The field test's investigation 2 as 0.18 filed it: the `--set` look
    against round 3. By their windows and thresholds, 002 → 004."""
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, [f"traces/final/{i}" for i in range(11)], first=1000)
    _file(tmp_path, out, 400.0, _loose(10), first=2000)
    _file(tmp_path, out, 900.0, _loose(10), first=2000,
          set={"main_thread_block": {"min_slice_ms": 2}})
    _file(tmp_path, out, 150.0, _loose(10), first=3000)
    monkeypatch.chdir(tmp_path)
    before, after, err = _compared(tmp_path)
    check("round 2 against round 3", (before, after) == ("002.json", "004.json"), err[-800:])
    check("the --set look named",
          "003.json (it ran with --set main_thread_block.min_slice_ms=2)" in err, err[-800:])


def test_unmarked_look_at_a_rounds_first_recording(tmp_path: Path, monkeypatch) -> None:
    """#613 as it was filed: one recording of round 2, its first."""
    out = _investigation(tmp_path)
    _file(tmp_path, out, 100.0, _loose(5), first=1000)
    _file(tmp_path, out, 400.0, _loose(5), first=2000)
    _file(tmp_path, out, 900.0, _loose(1), first=2000)
    monkeypatch.chdir(tmp_path)
    before, after, err = _compared(tmp_path)
    check("round 1 against round 2", (before, after) == ("001.json", "002.json"), err[-800:])
    check("the look named", "003.json (its first recording is the one round 2 (002.json) read first" in err,
          err[-800:])


def test_a_mark_that_does_not_read_is_told_from_the_report(project: Path) -> None:
    _analyze(_record(project, "baseline", 5))
    _analyze(_record(project, "second", 5))
    h = hunt_mod.load(project)
    h["filed"][1] = {"report": "two", "kind": "maybe"}
    hunt_mod.save(project, h)
    before, after, err = _compared(project)
    check("still compared", (before, after) == ("001.json", "002.json"), err[-800:])
    check("and 002 said to have gone by the report",
          "002.json carries no mark of round or look" in err, err[-800:])
