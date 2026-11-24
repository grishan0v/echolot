"""Which agent session ran each command, and `reflect` reading by it (#543).

Three sessions worked in one checkout at once, each running echolot, two of
them with copies of the config in directories of their own. `reflect` read
one session's runs out of the other two's lines by time, gave a call another
session's exit code, and never read the logs the copies' directories held.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.reflect import facts as facts_mod  # noqa: E402
from echolot.reflect.model import MAIN, Call, Session, SubAgent  # noqa: E402
from tests.support import check  # noqa: E402

T0 = datetime(2026, 10, 4, 10, 0, 0, tzinfo=timezone.utc)


def _ts(s: float) -> str:
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _iso(s: float) -> str:
    return (T0 + timedelta(seconds=s)).isoformat(timespec="seconds")


def _bash(at: float, cmd: str) -> Call:
    return Call(id=f"c{at}", ts=_ts(at), tool="Bash", input={"command": cmd},
                agent=MAIN, command=cmd)


def _run(at: float, cmd: str, session: str | None, exit_code: int = 0) -> dict:
    row = {"ts": _iso(at), "cmd": cmd, "argv": [cmd], "cwd": "/w", "exit": exit_code,
           "ms": 1000, "version": "0.17.0"}
    if session:
        row["session"] = session
    return row


def _write(log: Path, rows: list[dict]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --- the line ----------------------------------------------------------------

def test_a_line_carries_the_session_the_host_exported(tmp_path, monkeypatch):
    """Claude Code names the transcript after the id it exports; the line
    carries it as given, with the host's name beside it."""
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "5c1d2e3f-0000-4000-8000-0000000000aa")
    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.record(type("A", (), {"cmd": "status", "config": None})(),
                        ["status"], time.time(), exit_code=0)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "/Users/someone/projects/app")
        recorder.record(type("A", (), {"cmd": "status", "config": None})(),
                        ["status"], time.time(), exit_code=0)
    first, second = recorder.read(tmp_path / recorder.LOG_FILE)
    check("the id and the host", (first.get("session"), first.get("agent"))
          == ("5c1d2e3f-0000-4000-8000-0000000000aa", "claude-code"), first)
    check("a path in the variable is no id, and is not written",
          "session" not in second and "agent" not in second, second)


def test_an_old_line_and_a_line_that_is_no_object_read_as_before(tmp_path):
    """No `session`: a line as an older echolot wrote it, read and matched by
    time. `[1,2]` is still skipped (#555)."""
    log = tmp_path / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(_run(1, "doctor", None)) + "\n[1, 2]\n"
                   + json.dumps({**_run(2, "status", "s"), "session": 7}) + "\n"
                   + json.dumps(_run(3, "status", "../../elsewhere")) + "\n",
                   encoding="utf-8")
    runs = recorder.read(log)
    check("three lines; the list skipped", len(runs) == 3, runs)
    check("a session that is no string, or no token, is dropped, the line kept",
          all("session" not in r for r in runs[1:]) and runs[2]["cmd"] == "status", runs)


# --- matching ----------------------------------------------------------------

def _session(cwd: str, calls: list[Call], sid: str = "s") -> Session:
    s = Session(id=sid, agent="claude-code", cwd=cwd, started=_ts(0), ended=_ts(1000))
    s.calls = calls
    return s


def test_a_line_of_another_session_is_never_matched():
    """The other session's failure is a second nearer than this one's run."""
    s = _session("/w", [_bash(10, "echolot analyze t.perfetto-trace")])
    runs = [_run(10, "analyze", "other", exit_code=3), _run(11, "analyze", "s"),
            _run(12, "collect", "other")]
    f = facts_mod.gather(s, None, runs)
    call = f.echolot_calls[0]
    check("matched to its own line", (call.recorded or {}).get("session") == "s"
          and call.exit == 0, call.recorded)
    check("the other session's lines are not this session's runs",
          [r.get("session") for r in f.runs] == ["s"], f.runs)
    check("and counted as left out", f.runs_by == {"by_session": 1, "by_time": 0,
                                                    "other_sessions": 2}, f.runs_by)


def test_a_codex_subagent_s_own_thread_is_the_session_s():
    s = _session("/w", [_bash(10, "echolot analyze t.perfetto-trace")], sid="main-thread")
    s.subagents = [SubAgent(id="sub-thread")]
    f = facts_mod.gather(s, None, [_run(10, "analyze", "sub-thread")])
    check("a line of the subagent's thread is matched",
          (f.echolot_calls[0].recorded or {}).get("session") == "sub-thread", f.runs)


def test_a_line_without_an_id_is_taken_by_time_after_the_session_s_own():
    s = _session("/w", [_bash(10, "echolot analyze t.perfetto-trace")])
    f = facts_mod.gather(s, None, [_run(10, "analyze", None), _run(12, "analyze", "s")])
    check("its own line first, even two seconds further",
          (f.echolot_calls[0].recorded or {}).get("session") == "s", f.echolot_calls[0])
    check("the line without one is still among the runs, by time",
          f.runs_by == {"by_session": 1, "by_time": 1, "other_sessions": 0}, f.runs_by)


# --- the logs the session wrote ----------------------------------------------

def test_the_logs_of_the_directories_a_session_worked_in_are_read(tmp_path):
    """`-c ../work-a/echolot.yml` and `cd bench &&` logged under those
    directories, where `reflect` never looked."""
    project, copy, bench = tmp_path / "app", tmp_path / "work-a", tmp_path / "app" / "bench"
    _write(project / recorder.LOG_FILE, [_run(1, "doctor", "s")])
    _write(copy / recorder.LOG_FILE, [_run(10, "analyze", "other", exit_code=2),
                                      _run(11, "analyze", "s")])
    _write(bench / recorder.LOG_FILE, [_run(20, "names", None)])
    (copy / "echolot.yml").write_text("project: {}\n", encoding="utf-8")
    s = _session(str(project), [
        _bash(10, "echolot analyze t.perfetto-trace -c ../work-a/echolot.yml"),
        _bash(20, "cd bench && echolot names t.perfetto-trace"),
    ])
    logs = facts_mod.run_logs(s, project)
    check("the copy's log and the subdirectory's, not the project's twice",
          sorted(logs) == sorted([(copy / recorder.LOG_FILE).resolve(),
                                  (bench / recorder.LOG_FILE).resolve()]), logs)
    runs = recorder.read(project / recorder.LOG_FILE) + [
        r for log in logs for r in recorder.read(log)]
    f = facts_mod.gather(s, None, runs, project)
    analyze, names = f.echolot_calls
    check("the call made with the copy is matched to its line there, not the "
          "other session's", (analyze.recorded or {}).get("session") == "s"
          and analyze.exit == 0, analyze.recorded)
    check("the call made after `cd` is matched by time", names.recorded is not None,
          names.recorded)
