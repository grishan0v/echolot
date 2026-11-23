"""Work started outside the open investigation leaves it as it was (#498).

A cold-start baseline recorded by hand, in a project whose investigation about
list scrolling had been untouched for two months, was filed into it: its
report became the investigation's eighth, `touched_at` became today, so the
next `/echolot` carried on without asking, and the recap offered the
baseline's five cold starts as the traces it would reuse. With several
scenarios in the config it would have been recorded as the scroll.

`collect --hunt` and `analyze --hunt` work inside the investigation; without
the flag, they leave its record alone and say so.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from echolot import demo, recorder, runner, state
from echolot import hunt as hunt_mod
from echolot.main import main
from tests.support import check

TWO = ("project:\n  package: com.example.app\n  process: com.example.app\n"
       "scenarios:\n  coldStart:\n    start: {name: bindApplication}\n"
       "    end: {name: collection_load}\n"
       "  listScroll:\n    start: {name: bindApplication}\n"
       "    end: {name: collection_load}\n")


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _age(project: Path, days: int) -> None:
    """Make the open investigation look untouched for `days`."""
    h = hunt_mod.load(project)
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    h["touched_at"] = h["opened_at"] = when
    hunt_mod.save(project, h)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """The demo project with a stale investigation open, its traces outside
    `.echolot/traces` so opening it moved nothing."""
    traces = demo.write(tmp_path / "rec")
    for p in (tmp_path / "rec").iterdir():
        if p.is_file() and not p.name.endswith(".perfetto-trace"):
            os.replace(p, tmp_path / p.name)
    monkeypatch.chdir(tmp_path)
    code, _, err = run("hunt", "the list stutters when scrolling", "--since", "unknown")
    assert code == 0, err
    _age(tmp_path, 60)
    tmp_path.joinpath("traces.txt").write_text("\n".join(map(str, traces)), encoding="utf-8")
    return tmp_path


def _traces(project: Path) -> list[str]:
    return project.joinpath("traces.txt").read_text(encoding="utf-8").split("\n")


def test_analyze_without_hunt_files_nothing_and_says_so(project: Path) -> None:
    before = hunt_mod.load(project)
    code, _, err = run("analyze", *_traces(project))
    assert code == 0, err
    after = hunt_mod.load(project)
    check("no report filed", not (project / ".echolot/hunts/1/reports").exists()
          and not after.get("reports"), after)
    check("and the investigation is as stale as it was",
          after["touched_at"] == before["touched_at"] and not after.get("analyzes"), after)
    check("stderr says it was left alone, and how to file it",
          "not filed under the open investigation #1: run inside it with --hunt" in err, err)
    st = state.project_state(project)
    check("the next visit still asks", hunt_mod.needs_choice(st["hunt"], st))


def test_analyze_with_hunt_files_as_it_did(project: Path) -> None:
    code, _, err = run("analyze", *_traces(project), "--hunt")
    assert code == 0, err
    after = hunt_mod.load(project)
    check("filed", (project / ".echolot/hunts/1/reports/001.json").is_file()
          and after["reports"] == 1 and after["analyzes"] == 1, after)
    check("and fresh again", hunt_mod.is_fresh(after), after)
    check("the copy is named", "(this investigation's copy)" in err, err)
    check("its number does as well", run("analyze", *_traces(project), "--hunt", "1")[0] == 0)


def test_hunt_names_only_what_is_open(project: Path) -> None:
    code, _, err = run("analyze", *_traces(project), "--hunt", "7")
    check("another number is refused before any work", code == 2
          and "--hunt 7: the open investigation is #1" in err, err)
    run("hunt", "--done", "nothing")
    code, _, err = run("analyze", *_traces(project), "--hunt")
    check("and with none open there is nothing to file into", code == 2
          and "no investigation is open" in err, err)
    code, _, err = run("analyze", *_traces(project))
    check("a plain analyze then runs as ever, and says nothing of one", code == 0
          and "not filed" not in err, err)


def test_outside_the_investigation_the_first_scenario_is_measured(project: Path) -> None:
    (project / "echolot.yml").write_text(TWO, encoding="utf-8")
    h = hunt_mod.load(project)
    h["scenario"] = "listScroll"
    hunt_mod.save(project, h)
    code, _, err = run("analyze", *_traces(project))
    assert code == 0, err
    check("without --hunt: the first, coldStart",
          "scenario coldStart, the first of 2" in err
          and (project / ".echolot/out/coldStart/report.json").is_file(), err)
    code, _, err = run("analyze", *_traces(project), "--hunt")
    assert code == 0, err
    check("with it: the investigation's",
          "scenario listScroll, the open investigation's" in err
          and (project / ".echolot/out/listScroll/report.json").is_file(), err)


def _fake_collect(project: Path, seen: dict):
    """`runner.collect` without a device: writes its progress the way the real
    one does, asks whether the door would ask while it runs, and leaves `n`
    traces in `.echolot/traces`, setting the loose ones aside first."""
    def collect(*, out_dir, name, progress, on_set_aside, iterations, **_):
        n = iterations
        progress(scenario=name, mode="launch", started=time.time(),
                 pid=os.getpid(), iterations=n, done=0)
        st = state.project_state(project)
        seen["asks"] = hunt_mod.needs_choice(st["hunt"], st)
        out_dir.mkdir(parents=True, exist_ok=True)
        loose = list(out_dir.glob("*.perfetto-trace"))
        if loose:
            aside = out_dir / f"{name}-{len(list(out_dir.iterdir()))}"
            aside.mkdir()
            for p in loose:
                os.replace(p, aside / p.name)
            on_set_aside(aside)
        paths = []
        for i in range(n):
            p = out_dir / f"{name}_iter{i:03d}.perfetto-trace"
            p.write_bytes(b"x")
            paths.append({"path": str(p)})
        return paths
    return collect


def test_a_collect_outside_it_neither_holds_the_question_back_nor_files(
        project: Path, monkeypatch) -> None:
    # A round of its own, analysed, two months ago: history to ask about.
    assert run("analyze", *_traces(project), "--hunt")[0] == 0
    _age(project, 60)
    seen: dict = {}
    monkeypatch.setattr(runner, "collect", _fake_collect(project, seen))
    code, _, err = run("collect", "-n", "2")
    assert code == 0, err
    check("while it ran, the door still asked", seen["asks"] is True, seen)
    h = hunt_mod.load(project)
    check("nothing counted, nothing touched", not h.get("collects")
          and not hunt_mod.is_fresh(h) and h.get("round") == [], h)
    check("stderr says so", "not filed under the open investigation #1" in err, err)
    st = state.project_state(project)
    lines = hunt_mod.recap(h, st, project)
    check("the recap does not offer its traces as the investigation's",
          "  traces it would reuse: none of its own — the 2 in .echolot/traces were "
          "recorded outside this investigation" in lines, lines)

    code, _, err = run("collect", "-n", "2")
    assert code == 0, err
    check("its set moved aside is not recorded under the investigation either",
          not hunt_mod.load(project)["traces"], hunt_mod.load(project))


def test_the_investigation_s_own_collect_holds_the_question_and_files(
        project: Path, monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(runner, "collect", _fake_collect(project, seen))
    code, _, err = run("collect", "-n", "2", "--hunt")
    assert code == 0, err
    check("while it ran, the door did not ask", seen["asks"] is False, seen)
    progress = json.loads((project / runner.PROGRESS_FILE).read_text(encoding="utf-8"))
    check("the progress file says whose it is", progress.get("hunt") == 1, progress)
    h = hunt_mod.load(project)
    check("counted and fresh", h["collects"] == 1 and hunt_mod.is_fresh(h), h)
    check("its round is its own", h["round"] == [
        ".echolot/traces/coldStart_iter000.perfetto-trace",
        ".echolot/traces/coldStart_iter001.perfetto-trace"], h)
    lines = hunt_mod.recap(h, state.project_state(project), project)
    check("and offered for reuse", "  traces it would reuse: 2 in .echolot/traces" in lines, lines)

    # Work outside it moves that round aside: where it went is still
    # recorded, since it is the investigation's evidence, and from then on
    # none of the loose traces are its own.
    _age(project, 60)
    code, _, err = run("collect", "-n", "3")
    assert code == 0, err
    h = hunt_mod.load(project)
    check("its round, moved aside, is recorded where it went",
          len(h["traces"]) == 1 and h["round"] == [], h)
    check("the outside collect is still not counted", h["collects"] == 1, h)
    lines = hunt_mod.recap(h, state.project_state(project), project)
    check("the three loose ones are not offered as its own",
          any("none of its own — the 3 in .echolot/traces" in line for line in lines), lines)


def test_a_record_from_before_rounds_reads_every_loose_trace_as_its_own() -> None:
    hunt = {"n": 1, "status": "open", "question": "q"}
    lines = hunt_mod.recap(hunt, {"traces": {"count": 4, "names": ["a", "b", "c", "d"]}})
    check("as it always did", "  traces it would reuse: 4 in .echolot/traces" in lines, lines)
    hunt["round"] = ["a", "b"]
    lines = hunt_mod.recap(hunt, {"traces": {"count": 4, "names": ["a", "b", "c", "d"]}})
    check("with a round: its own, and the rest said apart",
          lines[-2:] == ["  traces it would reuse: 2 in .echolot/traces",
                         "  2 more there were recorded outside this investigation"], lines)
