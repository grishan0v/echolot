"""A line of `.echolot/log/runs.jsonl` that is not a run, among lines that are.

`record` writes one object per line. A line that is anything else comes from
a hand edit, a merge of two clones' logs or another tool appending to the
file, and it ended `echolot`, `status`, `status --next`, `hunt` and
`reflect --from-log` in a traceback, and every page of `echolot ui` in a 500
(#555). Such a line is skipped, as one that is no JSON always was, and a
field that holds what `record` never writes there is ignored.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import main as main_mod  # noqa: E402
from echolot import files, recorder, state  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_ui import Project, adb, get, quiet, site_of  # noqa: E402,F401

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")

# Each a line no `record` writes; the rows of the issue's table and the ones
# found beside them.
NOT_A_RUN = [
    b"[1,2]", b"null", b'"text"', b"42", b"true", b"[]",
    b"\xff\xfe garbage \x80",   # no UTF-8 text
    b"garbage line",            # no JSON: skipped since the first release
]
# Objects with a field of the wrong type: the line is a run, the field is not.
WRONG_FIELD = [
    b'{"cmd": "doctor", "facts": [1]}', b'{"cmd": "doctor", "facts": "x"}',
    b'{"cmd": "analyze", "facts": "x"}', b'{"cmd": "init", "facts": 5}',
    b'{"cmd": "doctor", "ts": 5}', b'{"cmd": "doctor", "ts": ["2026-11-22"]}',
    b'{"cmd": ["doctor"]}', b'{"cmd": "analyze", "ms": "x"}', b'{"cmd": "analyze", "ms": NaN}',
    b'{"cmd": "analyze", "argv": "analyze"}', b'{"cmd": "analyze", "exit": "1"}',
    # The facts `status` and the start page read off a self-check.
    b'{"cmd": "doctor", "facts": {"checks": 3, "failed": 5}}',
    b'{"cmd": "doctor", "facts": {"checks": 0, "failed": ["x"], "sandbox": ["codex"]}}',
    b'{"cmd": "doctor", "facts": {"checks": 0, "failed": ["x"], "sandbox": {"a": 1}}}',
]
LINES = NOT_A_RUN + WRONG_FIELD


def _id(line: bytes) -> str:
    return line.decode("utf-8", "backslashreplace")


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _good(cmd: str, hours_ago: float, **facts) -> bytes:
    run = {"ts": _ts(hours_ago), "cmd": cmd, "argv": [cmd], "cwd": "/p", "exit": 0,
           "ms": 1000, "version": "0.17.0"}
    if facts:
        run["facts"] = facts
    return json.dumps(run).encode()


def _log(root: Path, *lines: bytes) -> None:
    log = root / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"".join(line + b"\n" for line in lines))


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _project(root: Path) -> Path:
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    traces = root / ".echolot" / "traces"
    traces.mkdir(parents=True)
    (traces / "coldStart_iter000.perfetto-trace").write_bytes(b"x")
    return root


# --- the reading layer --------------------------------------------------------

def test_the_log_keeps_the_runs_and_skips_every_line_that_is_none(tmp_path: Path) -> None:
    first, last = _good("doctor", 3, checks=178, failed=[]), _good("analyze", 1)
    _log(tmp_path, first, *NOT_A_RUN, b"", b"   ", last)
    runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("the two runs, in order", runs == [json.loads(first), json.loads(last)], runs)


@pytest.mark.parametrize("line", WRONG_FIELD, ids=_id)
def test_a_field_of_the_wrong_type_is_dropped_and_its_line_kept(tmp_path: Path, line: bytes) -> None:
    _log(tmp_path, line)
    runs = recorder.read(tmp_path / recorder.LOG_FILE)
    written = json.loads(line)
    check("the line is a run", len(runs) == 1, runs)
    kept, dropped = runs[0], set(written) - set(runs[0])
    check("what it keeps is what was written there, a self-check's facts that do not hold left out of them",
          all(written[k] == kept[k] or (k == "facts" and kept[k].items() <= written[k].items()) for k in kept), kept)
    check("and each field it drops held what `record` never writes there",
          all(files.RUN.shape.fields[k].shape.fault(written[k], k)[1] for k in dropped), dropped)


def test_a_line_as_record_writes_it_reads_as_it_was(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.note(checks=3, failed=["a check"])
        recorder.record(type("A", (), {"cmd": "doctor", "config": None})(), ["doctor"],
                        1763719200.5, exit_code=1)
    written = json.loads((tmp_path / recorder.LOG_FILE).read_text(encoding="utf-8"))
    check("every field kept", recorder.read(tmp_path / recorder.LOG_FILE) == [written], written)


def test_a_line_split_on_no_newline_of_its_own(tmp_path: Path) -> None:
    """`record` keeps text as it is: a U+2028 inside an error is part of the
    line, and the line reads whole."""
    run = {"ts": _ts(1), "cmd": "analyze", "error": "one two"}
    _log(tmp_path, json.dumps(run, ensure_ascii=False).encode())
    check("one run, its error whole", recorder.read(tmp_path / recorder.LOG_FILE) == [run])


def test_the_last_self_check_is_the_last_one_that_reads(tmp_path: Path) -> None:
    failed = _good("doctor", 3, checks=178, failed=["a check"])
    _log(tmp_path, failed, *NOT_A_RUN, _good("analyze", 1))
    st = state.project_state(tmp_path)
    check("the self-check before them", st["last_doctor"] == json.loads(failed), st["last_doctor"])
    check("and the analysis after them", st["last_analyze"]["cmd"] == "analyze", st["last_analyze"])
    st["layer_verdict"] = "current"
    check("the next step still reads it", state.next_kind(st) == "doctor", state.next_kind(st))


def test_a_self_check_s_facts_of_the_wrong_type_are_dropped(tmp_path: Path) -> None:
    _log(tmp_path, b'{"cmd": "doctor", "facts": {"checks": true, "failed": 5, "sandbox": ["codex"], "x": 1}}')
    st = state.project_state(tmp_path)
    check("what is left reads as doctor writes it", st["last_doctor"]["facts"] == {"x": 1},
          st["last_doctor"])


# --- every command that reads it ---------------------------------------------

@pytest.mark.parametrize("line", LINES, ids=_id)
@pytest.mark.parametrize("argv", [(), ("status",), ("status", "--next"), ("hunt",)], ids=" ".join)
def test_the_commands_that_read_the_log_go_on(tmp_path: Path, monkeypatch, line: bytes,
                                              argv: tuple[str, ...]) -> None:
    _project(tmp_path)
    _log(tmp_path, _good("doctor", 3, checks=178, failed=[]), line, _good("analyze", 1))
    monkeypatch.chdir(tmp_path)
    code, out, err = _run(*argv)
    check("ends as it does over a clean log", code == 0 and "Traceback" not in err, (code, out, err))
    if argv == ("status", "--next"):
        check("one word", out.strip() in state.NEXT_KINDS, out)
    elif argv == ("status",):
        check("the whole status", "\ntraces  " in out and "\nnext  " in out, out)


@pytest.mark.parametrize("line", LINES, ids=_id)
def test_reflect_from_the_log_reports_on_the_runs(tmp_path: Path, line: bytes) -> None:
    _project(tmp_path)
    _log(tmp_path, _good("doctor", 0.2, checks=178, failed=[]), line, _good("analyze", 0.1))
    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "reflect", "--from-log", "--last",
         "--project", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("exits 0", done.returncode == 0 and "Traceback" not in done.stderr, done.stderr[-600:])
    written = list((tmp_path / ".echolot" / "reflect").glob("*.json"))
    check("one report", len(written) == 1, written)
    report = json.loads(written[0].read_text(encoding="utf-8"))
    commands = [r.get("cmd") for r in report.get("runs_recorded") or []]
    check("over the runs", {"doctor", "analyze"} <= set(commands), report.get("runs_recorded"))


@pytest.mark.usefixtures("adb")
@pytest.mark.parametrize("line", LINES, ids=_id)
def test_every_page_of_echolot_ui_is_drawn(tmp_path: Path, monkeypatch, line: bytes) -> None:
    monkeypatch.chdir(tmp_path)
    project = Project(tmp_path).ready().traces().report().hunt(1, "why is it slow", reports=2)
    log = tmp_path / recorder.LOG_FILE
    log.write_bytes(log.read_bytes() + line + b"\n" + _good("analyze", 1) + b"\n")
    site = site_of(project)
    for name, path in (("the start page", ""), ("the last investigation", "answer"),
                       ("an investigation's report", "hunt/1/report/1"), ("the report", "report")):
        reply = get(site, path)
        check(f"{name} is drawn", reply.status == 200, (reply.status, reply.body[:300]))
