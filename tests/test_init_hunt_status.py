"""`init`, `hunt` and `status` in the cases they got wrong: `-c` from a
subdirectory, `init` for the plugin and Codex, the self-check `init` just
ran, the plugin's door, a second conclusion, the binary local.yml names, a
report that does not read, a progress file `collect` did not write, and a
layer whose SKILL.md is gone.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import hunt as hunt_mod  # noqa: E402
from echolot import main as main_mod  # noqa: E402
from echolot import ready, recorder, runner, state  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("ECHOLOT_NO_RECORD", "1")


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    traces = root / ".echolot" / "traces"
    traces.mkdir(parents=True)
    for i in range(2):
        (traces / f"coldStart_iter00{i}.perfetto-trace").write_bytes(b"x")
    return root


def test_hunt_c_from_a_subdirectory_is_the_project_s(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path / "p")
    monkeypatch.chdir(project)
    _run("hunt", "cold start got slower")
    (project / "bench").mkdir()
    monkeypatch.chdir(project / "bench")
    code, _, _ = _run("hunt", "-c", "../echolot.yml", "scroll got slow")
    h = hunt_mod.load(project)
    check("the project's investigation is the new one",
          code == 0 and h["question"] == "scroll got slow" and h["n"] == 2, h)
    check("none opened in the subdirectory", not (project / "bench" / ".echolot").exists())
    check("and the traces were set aside",
          not list((project / ".echolot" / "traces").glob("*.perfetto-trace")))


def test_hunt_from_a_subdirectory_without_c_finds_the_project_above(tmp_path: Path,
                                                                    monkeypatch) -> None:
    project = _project(tmp_path / "p")
    monkeypatch.chdir(project)
    _run("hunt", "cold start got slower")
    (project / ".echolot" / "traces" / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
    (project / "app" / "src").mkdir(parents=True)
    monkeypatch.chdir(project / "app" / "src")
    code, _, err = _run("hunt", "startup regressed after merge")
    h = hunt_mod.load(project)
    check("the project's investigation is the new one", code == 0
          and h["question"] == "startup regressed after merge" and h["n"] == 2, err)
    check("nothing written under the subdirectory", not (project / "app" / ".echolot").exists()
          and not (project / "app" / "src" / ".echolot").exists())
    check("the project's traces were set aside",
          not list((project / ".echolot" / "traces").glob("*.perfetto-trace")))
    _, out, _ = _run("status")
    check("and status reads the same project", "startup regressed after merge" in out
          and str(project) in out, out)


def test_hunt_with_no_config_here_or_above_opens_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, _, err = _run("hunt", "cold start got slower")
    check("refused, saying what to run", code == 2 and "no echolot.yml" in err
          and "echolot init" in err and "-c" in err, err)
    check("nothing written", not (tmp_path / ".echolot").exists())
    code, _, err = _run("hunt", "-c", "perf.yml", "cold start got slower")
    check("a config named and not there is said as such", code == 2
          and "config not found" in err and not (tmp_path / ".echolot").exists(), err)


def test_status_c_reads_the_project_it_names(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path / "p")
    monkeypatch.chdir(project)
    _run("hunt", "cold start got slower")
    (project / ".echolot" / "traces" / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
    build = project / "bench" / "build"
    build.mkdir(parents=True)
    monkeypatch.chdir(build)
    _, out, _ = _run("status", "-c", "../../echolot.yml")
    check("its investigation and its traces",
          "cold start got slower" in out and "1 in .echolot/traces" in out
          and str(project) in out, out)


def test_init_for_the_plugin_checks_and_says_what_next(tmp_path: Path, monkeypatch) -> None:
    ran = []

    def fake(args, info, **kw):
        ran.append(kw.get("project"))
        recorder.note(checks=3, failed=[])
        return 0, True
    monkeypatch.setattr(main_mod, "_doctor_quiet", fake)
    code, out, _ = _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input")
    check("the self-check ran", code == 0 and ran == [tmp_path], ran)
    check("and the next line is there", "\nnext  " in out, out[-400:])
    ran.clear()
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    check("which --no-doctor skips", ran == [])


def test_init_s_own_self_check_decides_the_next_line(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"ts": "2026-10-01T10:00:00+00:00", "cmd": "doctor", "exit": 1,
                               "facts": {"checks": 0, "failed": ["the self-check did not run"]}})
                   + "\n", encoding="utf-8")

    def fake(args, info, **kw):
        recorder.note(checks=5, failed=[])
        return 0, True
    monkeypatch.setattr(main_mod, "_doctor_quiet", fake)
    _, out, _ = _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input")
    nxt = out.rsplit("next  ", 1)[-1]
    check("not back to doctor after a pass", not nxt.startswith("echolot doctor"), nxt)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-01T11:00:00+00:00", "cmd": "init", "exit": 0,
                            "facts": {"checks": 5, "failed": []}}) + "\n")
    check("and init's line counts as the last self-check",
          state.project_state(tmp_path)["last_doctor"]["cmd"] == "init")


def test_hunt_names_the_plugin_s_door(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    monkeypatch.chdir(tmp_path)
    _, _, err = _run("hunt", "cold start got slower")
    check("the plugin's skill", "the plugin's echolot skill" in err, err[-400:])


def test_a_second_conclusion_is_refused(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _run("hunt", "cold start slow")
    _run("hunt", "--done", "first answer")
    code, _, err = _run("hunt", "--done", "second answer")
    check("refused", code == 1 and "already concluded" in err, err)
    check("and the first answer kept", hunt_mod.load(tmp_path)["conclusion"] == "first answer")
    touched = []
    monkeypatch.setattr(hunt_mod, "touch", lambda project: touched.append(project))
    _run("hunt", "--resume")
    check("a concluded one is not resumed", touched == [])


def test_status_names_the_binary_local_yml_names(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    fake = tmp_path / "tp"
    fake.write_text("#!/bin/sh\necho 'Perfetto v99.0-custom'\n", encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "local.yml").write_text(f"toolchain:\n  tp_binary: {fake}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("the custom binary", "Perfetto v99.0-custom" in out.splitlines()[0], out[:200])


def test_a_report_that_does_not_read_is_said(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    out_dir = tmp_path / ".echolot" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "report.json").write_text("{ truncated", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    check("not none yet", "does not read" in out and "none yet" not in out, out)


def _report_reads_as(tmp_path: Path, monkeypatch, text: str) -> str:
    """`status` over a project whose report.json holds `text`: its exit code
    checked, its output returned."""
    _project(tmp_path)
    out_dir = tmp_path / ".echolot" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "report.json").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("status")
    check("status ends as it should", code == 0, out)
    return out


def _does_not_read(tmp_path: Path, monkeypatch, text: str) -> None:
    """What `echolot`, `status --next` and the start page of `echolot ui`
    say of a report.json that holds `text`: that it does not read, and the
    rest of what each of them says."""
    out = _report_reads_as(tmp_path, monkeypatch, text)
    check("said in the report's line", "report  .echolot/out/report.json does not read" in out, out)
    check("and the rest of the status is printed", "\ntraces  2 in .echolot/traces" in out and "\nnext  " in out, out)
    code, out, _ = _run("status", "--next")
    check("the next step is still one word", code == 0 and out.strip() in state.NEXT_KINDS, out)
    st = state.project_state(tmp_path)
    check("the state names it as one that does not read",
          st["report"] == {"path": tmp_path / ".echolot" / "out" / "report.json", "error": "unreadable"},
          st["report"])
    listed = ready.latest(st)
    check("and the start page lists it as one", "does not read" in listed[0]["plain"], listed)


@pytest.mark.parametrize("text", ["[1, 2]", "[]", "5", '"a report"', "null", "true"])
def test_a_report_that_is_json_and_no_object_does_not_read(tmp_path: Path, monkeypatch, text: str) -> None:
    """#460: such a file parsed, and the first key read off it was a traceback."""
    _does_not_read(tmp_path, monkeypatch, text)


@pytest.mark.parametrize("text", [
    '{"summary": [1]}', '{"summary": "x"}', '{"summary": 5}', '{"summary": true}',
    '{"config": 5}', '{"config": "abc"}', '{"config": [1]}', '{"config": true}',
    '{"traces": 5}', '{"traces": true}', '{"traces": 1.5}',
    '{"generated_at": 5}', '{"generated_at": [1]}', '{"generated_at": {"a": 1}}', '{"generated_at": true}',
    # A file that ended nothing and said what is not there: two runs.
    '{"traces": "ab"}',
    # With the section every report has, the start page reads the counts as numbers.
    '{"detectors": [], "summary": {"detectors_fired": "x", "detectors_run": 3}}',
    '{"detectors": [], "summary": {"detectors_fired": [1], "detectors_run": 3}}',
    '{"detectors": [], "summary": {"detectors_fired": true, "detectors_run": 3}}',
    '{"detectors": [], "summary": {"detectors_fired": 1, "detectors_run": "3"}}',
    '{"detectors": [], "config": {"sha": 5}}', '{"detectors": [], "config": {"defaults": "no"}}',
])
def test_a_report_with_a_field_of_the_wrong_type_does_not_read(tmp_path: Path, monkeypatch, text: str) -> None:
    """#470: an object, and a field read off it as what `analyze` writes there
    was a traceback in `echolot`, in `status --next` or on the start page."""
    _does_not_read(tmp_path, monkeypatch, text)


@pytest.mark.parametrize("text, said", [
    ("{}", "never · 1 run(s)"),
    ('{"generated_at": "yesterday"}', "never · 1 run(s)"),
    ('{"detectors": [], "generated_at": null, "traces": ["a", "b"], "summary": {"detectors_run": 3, '
     '"detectors_fired": 0}, "config": {"sha": null, "defaults": false}}', "never · 2 run(s) · 0 of 3 detectors fired"),
    ('{"config": {"sha": "0123456789ab", "defaults": true}}', "made with --defaults"),
])
def test_a_report_as_analyze_writes_it_still_reads(tmp_path: Path, monkeypatch, text: str, said: str) -> None:
    """The other side of #470: a field that is not there, is null, or holds
    what `analyze` writes there is read as it was."""
    out = _report_reads_as(tmp_path, monkeypatch, text)
    line = next((ln for ln in out.splitlines() if ln.startswith("report  ")), "")
    check("read", line.startswith("report  .echolot/out/report.json, ") and said in line, out)


def _running() -> dict:
    """The progress file of a run in flight in this process, as `collect`
    writes it."""
    return {"scenario": "coldStart", "mode": "launch", "started": time.time() - 900,
            "pid": os.getpid(), "iterations": 5, "done": 2,
            "cooling": "battery 41.0 °C, waiting for 40"}


def _failed() -> dict:
    """The same run once it failed."""
    return {**_running(), "cooling": None, "finished": time.time() - 600, "exit": 2,
            "error": "collection error: no device"}


PROGRESS = {"running": _running, "failed": _failed,
            "started alone": lambda: {"started": time.time() - 900}}


def _status_over_progress(tmp_path: Path, monkeypatch, text: str) -> str:
    """`status` over a project whose progress file holds `text`, with traces
    an hour older than the run in it: its exit code checked, its output
    returned."""
    _project(tmp_path)
    hour_ago = time.time() - 3600
    for trace in (tmp_path / ".echolot" / "traces").iterdir():
        os.utime(trace, (hour_ago, hour_ago))
    path = tmp_path / runner.PROGRESS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("status")
    check("status ends as it should", code == 0, out)
    return out


def _said(out: str, key: str) -> str | None:
    """What a line of `status` says after its key, or None without one."""
    found = re.search(rf"^{key} +(.*)$", out, re.M)
    return found.group(1) if found else None


def _no_scan(root: Path):
    raise OSError("no repository to scan here")


@pytest.mark.parametrize("field, value", [
    # Text, a list or an object where `collect` writes a time.
    ("started", '"2026-11-21T10:00:00+00:00"'), ("started", "[1]"), ("started", '{"at": 1}'),
    ("finished", '"2026-11-21T10:05:00+00:00"'), ("finished", "[1]"), ("finished", '{"at": 1}'),
    # What json reads and no clock writes.
    ("started", "Infinity"), ("started", "NaN"),
    pytest.param("started", "1" + "0" * 400, id="started-a-401-digit-number"),
    ("finished", "Infinity"), ("started", "true"), ("finished", "true"), ("started", "null"),
    # No process id: past what `os.kill` takes, its own group, every process.
    ("pid", str(2 ** 40)), ("pid", "0"), ("pid", "-1"), ("pid", '"1"'), ("pid", "true"), ("pid", "1.5"),
    ("done", '"2"'), ("done", "[2]"), ("exit", '"2"'), ("exit", "true"),
    ("scenario", '["coldStart"]'), ("cooling", '{"battery": 41}'), ("error", '["no device"]'),
])
def test_a_progress_file_collect_did_not_write_says_nothing(tmp_path: Path, monkeypatch,
                                                            field: str, value: str) -> None:
    """#474: a field of `.echolot/log/collect.json` holding what `collect`
    never writes there ended `echolot`, `status --next` or the start page
    in a traceback, or printed a run that is not there. Now the file reads
    as one that does not parse: no `collect` line, and the rest as it was."""
    fields = _failed() if field in ("finished", "exit", "error") else _running()
    fields[field] = "<value>"
    out = _status_over_progress(tmp_path, monkeypatch, json.dumps(fields).replace('"<value>"', value))
    check("no collect line", _said(out, "collect") is None, out)
    check("and the rest of the status is printed",
          (_said(out, "traces") or "").startswith("2 in .echolot/traces") and _said(out, "next"), out)
    code, out, _ = _run("status", "--next")
    check("the next step is still one word", code == 0 and out.strip() in state.NEXT_KINDS, out)
    st = state.project_state(tmp_path)
    check("the state has no collect", st["collect"] is None, st["collect"])
    page = ready.read(tmp_path, "echolot.yml", ready.Sources(scan=_no_scan, pick_device=lambda serial: "EXAMPLE0001"))
    check("and the start page is read", page["next"]["kind"] in state.NEXT_KINDS, page["next"])


@pytest.mark.parametrize("base, fields, said", [
    ("running", {}, "running: coldStart, 2/5 iterations, cooling: battery 41.0 °C, waiting for 40, started 15m ago"),
    ("running", {"mode": "gradle", "iterations": None, "done": 0, "cooling": None},
     "running: coldStart, the macrobenchmark drives the iterations, started 15m ago"),
    # Whole seconds are a time as well.
    ("running", {"started": 1763719200}, "running: coldStart, 2/5 iterations, cooling: battery 41.0 °C"),
    # A process that is gone: Linux and macOS hand out no pid this high.
    ("running", {"pid": 2 ** 22}, "interrupted at 2/5, started 15m ago — the process is gone"),
    ("started alone", {}, "interrupted, started 15m ago — the process is gone"),
    ("failed", {}, "failed 10m ago: collection error: no device"),
    # `collect` writes the count it was handed before it checks it, and a
    # count that is none fails at once, saying why.
    ("failed", {"iterations": "five", "done": 0,
                "error": "-n / runner.iterations: 'five' is not a number of repeats. One or more, in digits."},
     "failed 10m ago: -n / runner.iterations: 'five' is not a number of repeats."),
])
def test_a_progress_file_as_collect_writes_it_still_reads(tmp_path: Path, monkeypatch, base: str,
                                                          fields: dict, said: str) -> None:
    """The other side of #474: a field that is not there, is null, or holds
    what `collect` writes there is read as it was."""
    out = _status_over_progress(tmp_path, monkeypatch, json.dumps({**PROGRESS[base](), **fields}))
    check("read", (_said(out, "collect") or "").startswith(said), out)


def _open_hunt(root: Path) -> Path:
    """A project with an investigation open, three rounds filed, as `hunt`,
    `collect` and `analyze` leave it: the path of its hunt.json."""
    _project(root)
    reports = root / ".echolot" / "hunts" / "1" / "reports"
    reports.mkdir(parents=True)
    for k in (1, 2, 3):
        (reports / f"{k:03d}.json").write_text("{}", encoding="utf-8")
    path = root / hunt_mod.HUNT_FILE
    path.write_text(json.dumps({
        "n": 1, "question": "cold start got slower", "since": "the new splash", "scenario": "coldStart",
        "config_sha": "abc123", "confirmed": {}, "opened_at": "2026-11-21T09:00:00+00:00",
        "touched_at": "2026-11-21T09:30:00+00:00", "status": "open", "collects": 3, "analyzes": 3,
        "reports": 3, "traces": [".echolot/traces/coldStart-20261121-090000"], "conclusion": None,
    }, indent=2), encoding="utf-8")
    return path


DAMAGED = {
    "cut in half": lambda text: text[: len(text) // 2],
    "empty": lambda text: "",
    "blank": lambda text: "\n  \n",
    "a list": lambda text: "[1]",
    "null": lambda text: "null",
    "a number": lambda text: "5",
    "text": lambda text: '"cold start got slower"',
    "true": lambda text: "true",
}


@pytest.mark.parametrize("damage", list(DAMAGED))
def test_a_hunt_json_that_does_not_load_is_said(tmp_path: Path, monkeypatch, damage: str) -> None:
    """#580: a hunt.json cut in half, empty, or JSON and no object read as
    no investigation at all: `status` said "none open", `hunt` said none
    is open. Now it is named as a file that does not load, and nothing
    else is refused for it."""
    path = _open_hunt(tmp_path)
    path.write_text(DAMAGED[damage](path.read_text(encoding="utf-8")), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("status")
    said = _said(out, "hunt") or ""
    check("status ends as it should", code == 0, out)
    check("its hunt line names the file", said.startswith("`.echolot/hunt.json` does not load: ")
          and "the open investigation is unknown" in said and "none open" not in said, out)
    check("and the rest of the status is printed",
          (_said(out, "traces") or "").startswith("2 in .echolot/traces") and _said(out, "next"), out)
    code, out, _ = _run("status", "--next")
    check("the next step is still one word", code == 0 and out.strip() in state.NEXT_KINDS, out)
    code, out, err = _run("hunt")
    check("bare hunt names the file", code == 0 and "`.echolot/hunt.json` does not load" in out
          and "no investigation is open" not in out + err, out + err)
    code, _, err = _run("hunt", "--show")
    check("hunt --show names it", code == 1 and "does not load" in err and "no investigations yet" not in err, err)
    code, out, _ = _run("hunt", "--list")
    check("hunt --list names it", code == 0 and "does not load" in out and "no investigations yet" not in out, out)
    for argv in (("hunt", "--resume"), ("hunt", "--done", "it was the lock")):
        code, out, err = _run(*argv)
        check(f"{' '.join(argv)} names it", code != 0 and "does not load" in err
              and "no investigation is open" not in err, err)
    check("and wrote nothing over it", hunt_mod.unreadable(tmp_path) is not None)
    check("the state has no investigation, and says why",
          state.project_state(tmp_path)["hunt"] is None
          and "does not load" in (state.project_state(tmp_path)["hunt_unreadable"] or ""))
    page = ready.read(tmp_path, "echolot.yml", ready.Sources(scan=_no_scan, pick_device=lambda serial: "EXAMPLE0001"))
    check("and the start page has the line", "does not load" in (page["hunt_unreadable"] or ""), page["hunt_unreadable"])


def test_a_hunt_json_that_is_not_utf8_is_said(tmp_path: Path, monkeypatch) -> None:
    path = _open_hunt(tmp_path)
    path.write_bytes(b"\xff\xfe{")
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run("status")
    check("named", code == 0 and "not UTF-8" in (_said(out, "hunt") or ""), out)


def test_a_hunt_on_a_hunt_json_that_does_not_load_sets_it_aside(tmp_path: Path, monkeypatch) -> None:
    """#580: `hunt "<another question>"` wrote over a hunt.json that did not
    load, and the question and counts in it were gone. Now the file is set
    aside under a name of its own, said, and the new one opens beside the
    one whose reports are filed in hunts/1."""
    path = _open_hunt(tmp_path)
    damaged = path.read_text(encoding="utf-8")[:200]
    path.write_text(damaged, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = _run("hunt", "scroll got slow")
    kept = list((tmp_path / ".echolot" / "hunts").glob("hunt.json.unreadable-*"))
    check("it opens", code == 0 and 'opened #2: "scroll got slow"' in out, out + err)
    check("the damaged file is kept as it was", len(kept) == 1 and kept[0].read_text(encoding="utf-8") == damaged, kept)
    check("and where is said", "did not load" in err and kept[0].name in err, err)
    check("history does not read it as a record",
          [h.get("n") for h in hunt_mod.history(tmp_path)] == [2], hunt_mod.history(tmp_path))
    check("the new record is the open one", hunt_mod.unreadable(tmp_path) is None
          and (hunt_mod.load(tmp_path) or {}).get("question") == "scroll got slow")


def test_a_run_that_picks_a_scenario_says_the_hunt_json_does_not_load(tmp_path: Path, monkeypatch, capsys) -> None:
    """With several scenarios `report`, `collect` and `analyze` take the open
    investigation's: one in a hunt.json that does not load fell back to the
    first without a word."""
    path = _open_hunt(tmp_path)
    (tmp_path / "echolot.yml").write_text(
        "project:\n  package: com.example.app\n  process: com.example.app\n"
        "scenarios:\n  coldStart:\n    window: launch\n  feedScroll:\n    window: launch\n", encoding="utf-8")
    path.write_text("[1]", encoding="utf-8")
    cfg = main_mod.Config.load(tmp_path / "echolot.yml")
    with recorder.isolated():
        main_mod._pick_scenario(cfg, None, project=tmp_path)
    err = capsys.readouterr().err
    check("said", "`.echolot/hunt.json` does not load" in err and "first scenario, coldStart" in err, err)


WRONG = [
    # The rows of #580's table.
    ("reports", "abc"), ("n", "x"), ("traces", 7), ("opened_at", 5), ("question", 5),
    # The other fields echolot writes, each with what it never writes there.
    ("n", 0), ("n", -1), ("n", True), ("n", 1.5), ("collects", "3"), ("analyzes", -2), ("reports", [3]),
    ("touched_at", 5), ("touched_at", [1]), ("closed_at", {"at": 1}), ("since", 5), ("scenario", ["coldStart"]),
    ("config_sha", 5), ("status", 1), ("conclusion", ["it is the lock"]), ("confirmed", []),
    ("verdict", "the lock"), ("verdict_check", []), ("traces", "abc"), ("traces", {"a": 1}),
]


@pytest.mark.parametrize("field, value", WRONG)
def test_a_hunt_json_with_a_field_of_the_wrong_type_still_reads(tmp_path: Path, monkeypatch,
                                                                field: str, value) -> None:
    """#580: a field of hunt.json holding what echolot never writes there
    ended `hunt --show`, `hunt --list` and the start page in a traceback,
    or printed a question nobody asked. Now that field reads as unknown and
    the rest of the record is read."""
    path = _open_hunt(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for argv in (("status",), ("status", "--next"), ("hunt",), ("hunt", "--show"), ("hunt", "--list"),
                 ("hunt", "--show", "1")):
        code, out, err = _run(*argv)
        check(f"{' '.join(argv)} ends as it should", code in (0, 1) and "Traceback" not in out + err, out + err)
    h = hunt_mod.load(tmp_path)
    check("the field reads as unknown", h is not None and field not in h, h)
    check("and the rest is read", h["status" if field != "status" else "question"] is not None, h)
    if field != "question":
        check("its question too", "cold start got slower" in (_said(_run("status")[1], "hunt") or ""))
    else:
        check("a question nobody asked is not printed", '"5"' not in (_said(_run("status")[1], "hunt") or ""))
    page = ready.read(tmp_path, "echolot.yml", ready.Sources(scan=_no_scan, pick_device=lambda serial: "EXAMPLE0001"))
    check("the start page is read", page["next"]["kind"] in state.NEXT_KINDS, page["next"])
    check("with no line about a file that does not load", page["hunt_unreadable"] is None)


@pytest.mark.parametrize("field, value", WRONG)
def test_a_record_with_a_field_of_the_wrong_type_takes_the_next_round(tmp_path: Path, field: str, value) -> None:
    """What `collect` and `analyze` write into the open investigation, over
    a field of the wrong type: `"reports": "abc"` ended `analyze` in
    `int()`, and `"traces": 7` ended `collect` setting a set aside."""
    path = _open_hunt(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    out = tmp_path / ".echolot" / "out"
    out.mkdir(parents=True)
    (out / "report.json").write_text("{}", encoding="utf-8")
    hunt_mod.touch(tmp_path, collect=True, analyze=True)
    hunt_mod.record_traces(tmp_path, tmp_path / ".echolot" / "traces" / "aside")
    hunt_mod.record_report(tmp_path, out)
    check("the record still loads", hunt_mod.load(tmp_path) is not None)


def test_an_archived_record_with_a_field_of_the_wrong_type_still_reads(tmp_path: Path, monkeypatch) -> None:
    """The records in `.echolot/hunts/` are read the same way: `"n": "x"` in
    one of them ended `hunt --list`, and the next `hunt` in counting."""
    _open_hunt(tmp_path)
    old = tmp_path / ".echolot" / "hunts" / "7"
    old.mkdir(parents=True)
    (old / "hunt.json").write_text(json.dumps({"n": "x", "question": "older", "opened_at": 5,
                                               "traces": 7, "reports": "abc"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = _run("hunt", "--list")
    check("listed", code == 0 and '"older"' in out and '"cold start got slower"' in out, out + err)
    check("and the next number counts the directory", hunt_mod.next_n(tmp_path) == 8)


def test_files_equal_to_the_template_are_current_without_skill_md(tmp_path: Path) -> None:
    _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input", "--no-doctor")
    (tmp_path / ".claude" / "skills" / "echolot" / "SKILL.md").unlink()
    _, out, _ = _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input",
                     "--no-doctor")
    check("none called differs", "differs" not in out and "init --all" not in out, out)


TWO = ("project:\n  package: com.example.app\n  process: com.example.app\n"
       "scenarios:\n"
       "  cold:\n    start: {name: bindApplication}\n    end: {name: home_shown}\n"
       "  scroll:\n    start: {name: Feed.scroll}\n    end: {name: Feed.idle}\n")


@pytest.mark.parametrize("config, said", [
    ("", "the file is empty"),
    (TWO.replace("    end: {name: Feed.idle}\n", '    end: {name: Feed.idle}\n    budget_ms: "abc"\n'),
     "scenarios.scroll.budget_ms must be a number"),
    (CONFIG + "detectors:\n  main_thread_block:\n    min_slice_ms: abc\n",
     "main_thread_block.min_slice_ms (from the config) must be a number"),
    (TWO.replace("    end: {name: Feed.idle}\n", "    end: {}\n"),
     "scenarios.scroll.end: the block has no `name`"),
])
def test_status_says_what_analyze_would_refuse(tmp_path: Path, monkeypatch, config: str,
                                               said: str) -> None:
    """#583: status called a config sound that analyze then refused."""
    (tmp_path / "echolot.yml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    _, out, _ = _run("status")
    line = next((ln for ln in out.splitlines() if ln.startswith("config")), "")
    check("the config line names it", "does not load" in line and said in line, out)
    nxt = next((ln for ln in out.splitlines() if ln.startswith("next")), "")
    check("and the next step is the config", "fix echolot.yml" in nxt, out)


def test_status_still_calls_a_sound_config_sound(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "echolot.yml").write_text(TWO, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run("status")
    line = next((ln for ln in out.splitlines() if ln.startswith("config")), "")
    check("no error", "does not load" not in line and "2 scenarios" in line, out)


GRADLE = CONFIG + ('runner:\n  mode: gradle\n'
                   '  gradle_task: ":benchmark:connectedBenchmarkAndroidTest"\n')


@pytest.mark.parametrize("config, line", [
    (GRADLE, "echolot collect -c echolot.yml\n"),
    (CONFIG, "echolot collect -c echolot.yml -n 5\n"),
])
def test_the_line_to_collect_by_hand_has_no_count_for_a_macrobenchmark(
        tmp_path: Path, monkeypatch, config: str, line: str) -> None:
    """#520, #551: the benchmark's measureRepeated decides how many times it runs."""
    (tmp_path / "echolot.yml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    st = state.project_state(tmp_path, "echolot.yml")
    check("by hand", state.by_hand(st) == ("collect", line.strip()), state.by_hand(st))
    _run("init", "--into", str(tmp_path), "--for", "plugin", "--no-input", "--no-doctor")
    _, out, _ = _run("status")
    nxt = next((ln for ln in out.splitlines() if ln.startswith("next")), "")
    check("status, its next line", nxt.endswith(line.strip()), out)
    _, _, err = _run("hunt", "cold start got slower")
    by = next((ln for ln in err.splitlines() if ln.startswith("By hand:")), "")
    check("and the line hunt prints, inside the investigation it opened",
          line.strip() + " --hunt 1," in by and by.endswith("--hunt 1")
          and (config is CONFIG or ("-n " not in by and "_iter" not in by)), err[-500:])


@pytest.mark.parametrize("finished", [False, True], ids=["running", "finished"])
def test_hunt_waits_for_a_running_collect(tmp_path: Path, monkeypatch, finished: bool) -> None:
    """#717: a new question while a collect records for the open one."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    _run("hunt", "cold start slow")
    now = time.time()
    progress = {"scenario": "coldStart", "pid": os.getpid(), "iterations": 5, "done": 2,
                "finished": now if finished else None, "hunt": 1, "started": now - 60}
    if finished:
        progress["exit"] = 0
    (project / runner.PROGRESS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (project / runner.PROGRESS_FILE).write_text(json.dumps(progress), encoding="utf-8")
    loose = sorted((project / ".echolot" / "traces").glob("*.perfetto-trace"))
    code, _, err = _run("hunt", "is the list scroll janky")
    h = hunt_mod.load(project)
    if finished:
        check("once it finished, hunt opens as before", code == 0 and h["n"] == 2, err)
        return
    check("refused, saying a collect is running", code == 2
          and "a collect is running: coldStart, 2/5 iterations" in err
          and "for #1" in err and "wait for it to finish" in err, err)
    check("no second collect offered", "echolot collect" not in err, err)
    check("the open investigation stays open", h["n"] == 1 and h["status"] == "open", h)
    check("its repeats stay where the collect writes them",
          sorted((project / ".echolot" / "traces").glob("*.perfetto-trace")) == loose)


@pytest.mark.parametrize("case", ["baseline-after-close", "own-round-before-close",
                                  "outside-while-open", "inside-while-open",
                                  "unsaid-after-close"])
def test_a_new_investigation_files_the_loose_set_only_under_its_own(
        tmp_path: Path, monkeypatch, case: str) -> None:
    """#718: the set moved aside goes on the previous record only when it
    was recorded for it."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(root)
    _run("hunt", "cold start slow")
    if case in ("baseline-after-close", "own-round-before-close", "unsaid-after-close"):
        _run("hunt", "--done", "Firebase init on main")
    traces = root / ".echolot" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    closed = hunt_mod.load(root).get("closed_at")
    stamp = (time.time() + 5 if case.endswith("after-close")
             else (hunt_mod._epoch(closed) - 60 if closed else time.time()))
    for i in range(5):
        trace = traces / f"coldStart_iter00{i}.perfetto-trace"
        trace.write_bytes(b"x")
        os.utime(trace, (stamp, stamp))
    inside = case in ("own-round-before-close", "inside-while-open")
    (root / runner.PROGRESS_FILE).parent.mkdir(parents=True, exist_ok=True)
    progress = {"scenario": "coldStart", "pid": 1, "iterations": 5, "done": 5, "exit": 0,
                "started": stamp - 30, "finished": stamp, "hunt": 1 if inside else None}
    if case == "unsaid-after-close":
        del progress["hunt"]  # a collect from before it wrote where it ran
    (root / runner.PROGRESS_FILE).write_text(json.dumps(progress), encoding="utf-8")
    code, _, err = _run("hunt", "scroll jank in list")
    check("opened #2", code == 0 and hunt_mod.load(root)["n"] == 2, err)
    check("and the set was moved aside all the same",
          not list(traces.glob("*.perfetto-trace")))
    first = hunt_mod.find(root, "1")
    _, out, _ = _run("hunt", "--show", "1")
    if inside:
        check("its own round is filed under it", len(first.get("traces") or []) == 1, first)
    else:
        check("a set recorded outside it is not", not first.get("traces"), first)
        check("and --show says so", "traces: none recorded" in out, out)
