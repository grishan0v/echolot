"""`echolot`'s summary says what is true on each line (#501).

The summary is what an agent decides on. It showed a report over the whole
trace as healthy, said "none" with eleven earlier sets beside it, printed the
open investigation's question three times, offered a second `collect` while
one ran, sent a directory holding only a copy of the config to `init`, named
the wrong scenario's runner, and lost a report when the config moved to
`scenarios:`.
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

from echolot import hunt as hunt_mod
from echolot import recorder, runner, state
from echolot.main import main
from tests.support import check

ONE = "project:\n  package: com.example.app\nscenario:\n  name: coldStart\n"
TWO = ("project:\n  package: com.example.app\nrunner:\n  mode: gradle\n"
       "  gradle_task: :app:connectedCheck\n"
       "scenarios:\n  coldStart: {}\n"
       "  listScroll:\n    runner:\n      mode: command\n      command: ./scroll.sh\n")


def status(project: Path) -> tuple[dict[str, str], str]:
    """`echolot` in `project`: its lines by label, and the whole output."""
    out = io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with recorder.isolated(), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            assert main([]) == 0
    finally:
        os.chdir(here)
    text = out.getvalue()
    lines = {}
    for ln in text.splitlines()[1:]:
        if not ln.strip():
            break
        key, _, rest = ln.partition("  ")
        lines.setdefault(key.strip(), rest.strip())
    return lines, text


def _report(where: Path, window: dict | None = None, scenario: str = "coldStart") -> None:
    where.mkdir(parents=True, exist_ok=True)
    (where / "report.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenario": scenario, "traces": ["a", "b", "c", "d", "e"],
        "summary": {"detectors_fired": 13, "detectors_run": 16},
        "config": {}, "window": window or {}}), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "echolot.yml").write_text(ONE, encoding="utf-8")
    (tmp_path / "settings.gradle.kts").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("window, said", [
    ({"end_anchor": {"glob": "list_shown", "matches": 0, "missed": "5/5"}},
     "! end anchor not found in 5/5 runs: their window is everything the process did"),
    ({"start_anchor": {"glob": "bindApplication", "matches": 0}},
     "! start anchor not found: the window is everything the process did"),
    ({"end_anchor": {"glob": "x", "matches": 1, "unfinished": True}},
     "! end anchor never closed"),
    ({"duration_ms_min": 300, "duration_ms_max": 1200}, "! repeat windows diverged 4.0x"),
    ({"end_anchor": {"glob": "x", "matches": "garbled"}, "duration_ms_min": "a"}, None),
])
def test_the_report_line_carries_the_window_warning(project: Path, window, said) -> None:
    _report(project / ".echolot/out", window)
    lines, _ = status(project)
    if said:
        check("the warning report.md opens with", said in lines["report"], lines["report"])
    else:
        check("a field that reads as nothing written warns of nothing",
              "!" not in lines["report"], lines["report"])


def test_a_healthy_report_has_no_warning(project: Path) -> None:
    _report(project / ".echolot/out", {"end_anchor": {"glob": "x", "matches": 5}})
    lines, _ = status(project)
    check("no !", "!" not in lines["report"] and "13 of 16 detectors fired" in lines["report"],
          lines["report"])


def test_sets_moved_aside_are_counted(project: Path) -> None:
    traces = project / ".echolot/traces"
    for i, name in enumerate(("coldStart-20261101-1000", "coldStart-20261102-1000")):
        (traces / name).mkdir(parents=True)
        (traces / name / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
        os.utime(traces / name / "coldStart_iter000.perfetto-trace", (1e9 + i, 1e9 + i))
    (traces / "empty").mkdir()
    lines, _ = status(project)
    check("none loose, two sets aside, the last named",
          lines["traces"] == "none in .echolot/traces · 2 earlier set(s) in subdirectories, "
          "last coldStart-20261102-1000", lines["traces"])


def test_the_question_is_said_once(project: Path) -> None:
    _report(project / ".echolot/out")
    hunt_mod.open_new(project, "the list stutters when scrolling", since="unknown")
    h = hunt_mod.load(project)
    h["touched_at"] = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    hunt_mod.save(project, h)
    # No checkout here, so `next` goes past `init` to the question.
    (project / "settings.gradle.kts").unlink()
    st = state.project_state(project)
    check("the question is open", state.next_kind(st) == "resume-or-new", state.next_kind(st))
    _, text = status(project)
    check("once, on the hunt line", text.count("the list stutters when scrolling") == 1
          and '"the list stutters when scrolling"' in status(project)[0]["hunt"], text)
    check("the recap names it by number",
          "Open investigation #1, the question on the hunt line:" in text, text)
    check("and so does next", "carry on with investigation #1 or start a new one" in text, text)


def test_while_a_collect_runs_next_says_wait(project: Path) -> None:
    (project / "settings.gradle.kts").unlink()  # past `init`, to the hunt
    progress = project / runner.PROGRESS_FILE
    progress.parent.mkdir(parents=True)
    progress.write_text(json.dumps({"started": time.time(), "pid": os.getpid(), "done": 1,
                                    "iterations": 5, "scenario": "coldStart"}),
                        encoding="utf-8")
    st = state.project_state(project)
    check("not a second collect", "echolot collect" not in state.next_step(st)
          and "wait for the running collect" in state.next_step(st), state.next_step(st))
    progress.write_text(json.dumps({"started": time.time(), "finished": time.time(),
                                    "exit": 0, "done": 5}), encoding="utf-8")
    check("once it is done, the step by hand is back",
          "echolot collect" in state.next_step(state.project_state(project)))


def test_a_copy_of_the_config_is_not_sent_to_init(tmp_path: Path) -> None:
    (tmp_path / "echolot.yml").write_text(ONE, encoding="utf-8")
    st = state.project_state(tmp_path)
    check("only a config here: no init", st["layer_verdict"] == "absent"
          and state.next_kind(st) != "init", state.next_kind(st))
    for marker in ("settings.gradle.kts", ".git", "gradlew"):
        (tmp_path / marker).write_text("", encoding="utf-8")
        check(f"with {marker}, a project: init",
              state.next_kind(state.project_state(tmp_path)) == "init", marker)
        (tmp_path / marker).unlink()


def test_the_config_line_names_the_runner_of_its_scenario(project: Path) -> None:
    (project / "echolot.yml").write_text(TWO, encoding="utf-8")
    hunt_mod.open_new(project, "the list stutters", scenario="listScroll")
    lines, _ = status(project)
    check("the investigation's scenario runs a command", "runner command" in lines["config"],
          lines["config"])
    run = hunt_mod.load(project)
    run["status"] = "done"
    hunt_mod.save(project, run)
    lines, _ = status(project)
    check("and the first scenario gradle", "runner gradle" in lines["config"], lines["config"])


def test_a_report_from_before_scenarios_is_named_where_it_belongs(project: Path) -> None:
    _report(project / ".echolot/out", scenario="coldStart")
    (project / "echolot.yml").write_text(TWO, encoding="utf-8")
    _, text = status(project)
    row = next(ln for ln in text.splitlines() if ln.startswith("scenario") and "coldStart" in ln)
    check("its scenario's line says where it is and where it belongs",
          ".echolot/out/report.json is this scenario's, from before the config named "
          "several: it belongs in .echolot/out/coldStart/" in row, row)
    other = next(ln for ln in text.splitlines() if "listScroll" in ln)
    check("the other scenario's line does not", "belongs" not in other, other)
    _report(project / ".echolot/out/coldStart")
    _, text = status(project)
    check("once it has a report of its own, nothing to say", "belongs" not in text, text)


def _repeat(traces: Path, name: str, launch: dict | None) -> None:
    from echolot import recording
    traces.mkdir(parents=True, exist_ok=True)
    (traces / name).write_bytes(b"x")
    if launch is not None:
        recording.write(traces / name, {"runner": "launch", "reset_policy": "force-stop",
                                        "launch": launch})


def test_status_says_how_each_repeat_started_without_a_device(project: Path, monkeypatch) -> None:
    """The lines under each repeat were in `collect`'s output alone, and an
    agent that had cut it with `tail` recorded the round again to read them
    (#619). `status` reads them off what `collect` kept beside each trace."""
    def no_adb(*args, **kw):
        raise AssertionError(f"status asked the device: {args}")
    monkeypatch.setattr(runner, "_run", no_adb)
    traces = project / ".echolot/traces"
    cold = {"state": "COLD", "cold": True, "notes": []}
    for i in range(3):
        _repeat(traces, f"coldStart_iter{i:03d}.perfetto-trace", {**cold, "repeat": i + 1})
    _repeat(traces, "coldStart_iter001-warm.perfetto-trace",
            {"repeat": 2, "state": "WARM", "cold": False, "notes": ["[!] not a cold start"]})
    # Another scenario's, with a name that begins with this one's.
    _repeat(traces, "coldStart_warm_iter000-hot.perfetto-trace",
            {"repeat": 1, "state": "HOT", "cold": False, "notes": []})
    lines, _ = status(project)
    check("the set's states counted, the recording set aside named",
          lines.get("starts") == "coldStart: 3 COLD · set aside, not cold: "
          "coldStart_iter001-warm.perfetto-trace (WARM)", lines)
    step, line = state.by_hand(state.project_state(project))
    check("and the line to analyze them names the set alone", step == "analyze"
          and "coldStart_iter002.perfetto-trace" in line and "-warm" not in line, line)


def test_a_set_collect_kept_no_starts_for_has_no_starts_line(project: Path) -> None:
    traces = project / ".echolot/traces"
    for i in range(2):
        _repeat(traces, f"coldStart_iter{i:03d}.perfetto-trace", None)
    lines, _ = status(project)
    check("nothing to say", "starts" not in lines, lines)
    _repeat(traces, "coldStart_iter002.perfetto-trace",
            {"repeat": 3, "state": "COLD", "cold": True, "notes": []})
    lines, _ = status(project)
    check("one that has it, and the two that do not, counted",
          lines.get("starts") == "coldStart: 1 COLD, 2 recorded before collect kept it", lines)
