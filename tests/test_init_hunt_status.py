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


def test_files_equal_to_the_template_are_current_without_skill_md(tmp_path: Path) -> None:
    _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input", "--no-doctor")
    (tmp_path / ".claude" / "skills" / "echolot" / "SKILL.md").unlink()
    _, out, _ = _run("init", "--into", str(tmp_path), "--for", "claude", "--no-input",
                     "--no-doctor")
    check("none called differs", "differs" not in out and "init --all" not in out, out)
