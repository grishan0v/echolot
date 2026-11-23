"""Work inside the open investigation, and work outside it (#498).

With an investigation about scrolling open and untouched for two months, a
cold-start baseline — `collect`, then `analyze`, nothing to do with it — was
filed into it: the report as its eighth, the round it pushed aside as its
traces, `touched_at` renewed to that day. The next `/echolot` carried on with
the scroll question on cold-start traces without asking, and the recap had
lost its "untouched for 68 days" line. A run is the investigation's now when
it says so, `--hunt N`; a plain one is outside it.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from echolot import hunt as hunt_mod
from echolot import main as main_mod
from echolot import recorder, runner, state
from tests.support import check
from tests.test_collect_fake_adb import FakeAdb

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\nrunner:\n  iterations: 1\n")
TRACE = ".echolot/traces/coldStart_iter000.perfetto-trace"
TWO_MONTHS = (datetime.now(timezone.utc) - timedelta(days=61)).isoformat(timespec="seconds")


def run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main_mod.main(list(argv))
        except SystemExit as e:     # argparse's refusal
            code = e.code
    return code, err.getvalue()


@pytest.fixture
def project(tmp_path: Path, monkeypatch, marker_report) -> Path:
    """A project with investigation #1 open, about another question, untouched
    for two months, with a round of it on disk, and an `analyze` that needs
    no trace_processor."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    hunt_mod.open_new(tmp_path, "the list stutters when scrolling")
    h = hunt_mod.load(tmp_path)
    h["touched_at"] = TWO_MONTHS
    hunt_mod.save(tmp_path, h)
    monkeypatch.setattr(main_mod, "analyze_trace",
                        lambda *a, **kw: copy.deepcopy(marker_report))
    (tmp_path / ".echolot/traces").mkdir(parents=True)
    (tmp_path / TRACE).write_bytes(b"a trace")
    return tmp_path


def _stale(project: Path) -> bool:
    return hunt_mod.needs_choice(hunt_mod.load(project), state.project_state(project))


def test_an_analyze_outside_files_nothing_and_renews_nothing(project: Path) -> None:
    before = hunt_mod.load(project)
    check("the door would ask", _stale(project))
    code, err = run("analyze", TRACE, "-c", "echolot.yml")
    check("the report is written", code == 0
          and (project / ".echolot/out/report.json").is_file(), err[-400:])
    check("and the run says it is outside the investigation",
          '[i] outside investigation #1, "the list stutters when scrolling"' in err
          and "`--hunt 1` runs inside it" in err and "this investigation's copy" not in err,
          err[-400:])
    check("nothing filed", not (project / ".echolot/hunts/1/reports").exists())
    check("the record as it was", hunt_mod.load(project) == before, hunt_mod.load(project))
    check("and the door still asks", _stale(project))
    recap = "\n".join(hunt_mod.recap(hunt_mod.load(project), state.project_state(project)))
    check("the recap still says how long it has been untouched", "untouched for 61 days" in recap,
          recap)


def test_an_analyze_inside_is_filed_and_counted(project: Path) -> None:
    code, err = run("analyze", TRACE, "-c", "echolot.yml", "--hunt", "1")
    h = hunt_mod.load(project)
    check("filed", code == 0 and (project / ".echolot/hunts/1/reports/001.json").is_file()
          and "(this investigation's copy)" in err and "outside investigation" not in err,
          err[-400:])
    check("counted as worked on", h["analyzes"] == 1 and h["touched_at"] != TWO_MONTHS, h)
    check("so the door does not ask", not _stale(project))


@pytest.mark.parametrize("command", ["analyze", "collect"])
def test_a_number_that_is_not_the_open_one_refuses_before_anything_runs(
        project: Path, monkeypatch, command: str) -> None:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    argv = (["analyze", TRACE] if command == "analyze" else ["collect"]) + [
        "-c", "echolot.yml"]
    code, err = run(*argv, "--hunt", "2")
    check("exit 2, naming the open one", code == 2
          and 'error: --hunt 2: the open investigation is #1, "the list stutters when scrolling"'
          in err, err[-400:])
    check("and nothing ran", not (project / ".echolot/out").exists() and not fake.calls
          and not (project / runner.PROGRESS_FILE).exists(), fake.calls)
    hunt_mod.conclude(project, "a lock in the list's adapter")
    code, err = run(*argv, "--hunt", "1")
    check("a closed one is not open", code == 2
          and "no investigation is open (#1 is concluded)" in err, err[-400:])
    code, err = run(*argv, "--hunt", "x")
    check("a word is no number", code == 2 and "is not an investigation's number" in err,
          err[-400:])


def test_a_collect_outside_leaves_the_investigation_and_its_rounds_where_they_are(
        project: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_run", FakeAdb())
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    code, err = run("collect", "--hunt", "1")
    check("a round inside it", code == 0, err[-400:])
    h = hunt_mod.load(project)
    check("counted", h["collects"] == 1, h)

    code, err = run("collect")
    check("then a baseline outside it", code == 0 and "[i] outside investigation #1" in err,
          err[-400:])
    progress = json.loads((project / runner.PROGRESS_FILE).read_text(encoding="utf-8"))
    check("its progress file says so", "hunt" in progress and progress["hunt"] is None, progress)
    after = hunt_mod.load(project)
    check("not counted, not renewed", after["collects"] == 1
          and after["touched_at"] == h["touched_at"], after)
    check("the round it pushed aside was the investigation's, and is recorded on it",
          after["traces"][:-1] == h["traces"]
          and after["traces"][-1].startswith(".echolot/traces/coldStart-"),
          (h["traces"], after["traces"]))
    recap = "\n".join(hunt_mod.recap(after, state.project_state(project)))
    check("the recap does not offer the baseline as its evidence",
          "traces it would reuse" not in recap
          and "not this investigation's — the last collect (coldStart" in recap
          and "ran outside it" in recap, recap)

    code, err = run("collect", "--hunt", "1")
    last = hunt_mod.load(project)
    check("the baseline a round inside pushes aside is not recorded on it",
          code == 0 and last["traces"] == after["traces"] and last["collects"] == 2,
          (last, err[-400:]))
    recap = "\n".join(hunt_mod.recap(last, state.project_state(project)))
    check("and its own round is offered again", "traces it would reuse: 1" in recap, recap)


def test_the_progress_file_holds_the_investigation_to_its_type(tmp_path: Path) -> None:
    p = tmp_path / runner.PROGRESS_FILE
    p.parent.mkdir(parents=True)
    base = {"started": 1.0, "finished": 2.0, "exit": 0, "scenario": "coldStart"}
    for value, ok in ((3, True), (None, True), ("3", False), (True, False)):
        p.write_text(json.dumps(dict(base, hunt=value)), encoding="utf-8")
        check(f"hunt {value!r}", (state.collect_state(tmp_path) is not None) == ok)
