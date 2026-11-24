"""`echolot reflect --hunt N`: the session that worked inside investigation N (#826).

After an investigation the page started, the newest session that used echolot
is most often the person's own — the one that started `echolot ui`, or tried
a command since — and `--last` read that one. The run log ties the agent's:
a `collect` or `analyze` run with `--hunt N` notes `inside: N`, and the line
carries the id of the session whose command it was.

Each case is a project with two Claude Code transcripts, the agent's older
than the person's, and the run log both left.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.pages import answer_page  # noqa: E402
from echolot.reflect.render import to_markdown  # noqa: E402
from tests.support import check  # noqa: E402

T0 = datetime(2026, 11, 20, 9, 0, 0, tzinfo=timezone.utc)
AGENT = "a6e47000-0000-4000-8000-000000000001"
PERSON = "9e550000-0000-4000-8000-000000000002"


def _ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _transcript(pdir: Path, sid: str, cwd: Path, at: float, commands: list[str]) -> None:
    rows: list[dict] = [{"type": "user", "timestamp": _ts(at), "sessionId": sid, "cwd": str(cwd),
                         "message": {"role": "user", "content": "/echolot"}}]
    for i, command in enumerate(commands):
        t = at + 60 * (i + 1)
        rows.append({"type": "assistant", "timestamp": _ts(t), "sessionId": sid, "cwd": str(cwd),
                     "message": {"id": f"{sid}-{i}", "role": "assistant", "content": [
                         {"type": "tool_use", "id": f"u{i}", "name": "Bash",
                          "input": {"command": command}}]}})
        rows.append({"type": "user", "timestamp": _ts(t + 1), "sessionId": sid, "cwd": str(cwd),
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": f"u{i}", "content": "ok"}]}})
    path = pdir / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    stamp = (T0 + timedelta(seconds=at + 3600)).timestamp()
    os.utime(path, (stamp, stamp))


def _line(sid: str | None, t: float, cmd: str, **facts) -> dict:
    row = {"ts": (T0 + timedelta(seconds=t)).isoformat(timespec="seconds"), "cmd": cmd,
           "argv": [cmd], "exit": 0, "ms": 900, "version": "0.19.0"}
    if sid:
        row.update(session=sid, agent="claude-code")
    if facts:
        row["facts"] = facts
    return row


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    """Investigation 3, worked in by the agent; 2, by nobody; the person after both."""
    app, pdir = tmp_path / "app", tmp_path / "transcripts"
    (app / ".echolot" / "hunts" / "2").mkdir(parents=True)
    pdir.mkdir()
    (app / "echolot.yml").write_text("project:\n  process: com.example.app\n", encoding="utf-8")
    (app / ".echolot" / "hunt.json").write_text(json.dumps(
        {"schema": 1, "n": 3, "question": "why is cold start slow", "status": "concluded"}),
        encoding="utf-8")
    (app / ".echolot" / "hunts" / "2" / "hunt.json").write_text(json.dumps(
        {"schema": 1, "n": 2, "question": "scrolling", "status": "concluded"}), encoding="utf-8")
    _transcript(pdir, AGENT, app, 0, ["echolot collect -n 3 --hunt 3",
                                      "echolot analyze .echolot/traces/a.perfetto-trace --hunt 3"])
    _transcript(pdir, PERSON, app, 7200, ["echolot status",
                                          "echolot analyze .echolot/traces/a.perfetto-trace"])
    _log(app, [_line(AGENT, 60, "collect", inside=3), _line(AGENT, 120, "analyze", inside=3),
               _line(PERSON, 7260, "status"), _line(PERSON, 7320, "analyze")])
    return app, pdir


def _log(app: Path, rows: list[dict]) -> None:
    log = app / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _reflect(app: Path, pdir: Path, *how: str) -> tuple[int, list[dict], str]:
    out = app / ".echolot" / "reflect"
    for old in out.glob("*.json") if out.is_dir() else ():
        old.unlink()
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = main(["reflect", *how, "--transcripts", str(pdir), "--project", str(app)])
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json"))
               if p.name != "summary.json"] if out.is_dir() else []
    return code, reports, err.getvalue()


def test_the_newest_session_is_the_person_s_and_the_hunt_s_is_the_agent_s(project):
    app, pdir = project
    code, reports, _ = _reflect(app, pdir, "--last")
    check("--last reads the newest session that used echolot: the person's",
          code == 0 and [r["source"]["session"] for r in reports] == [PERSON],
          [r["source"]["session"] for r in reports])
    code, reports, err = _reflect(app, pdir, "--hunt", "3")
    check("--hunt 3 reads the session that ran inside it", code == 0
          and [r["source"]["session"] for r in reports] == [AGENT], (code, err))
    inv = reports[0]["context"]["investigation"]
    check("and the report says which investigation, and how the session was told",
          inv == {"n": 3, "question": "why is cold start slow", "status": "concluded",
                  "runs": 2, "by": "session"}, inv)
    md = to_markdown(reports[0])
    check("in its header too",
          'Investigation #3 "why is cold start slow" (concluded): 2 run(s) of this session '
          "logged inside it, by the id they carry" in md, md[:600])


def test_an_investigation_nothing_ran_inside_and_one_that_is_not_there(project):
    app, pdir = project
    code, reports, err = _reflect(app, pdir, "--hunt", "2")
    check("#2 is there and nothing ran inside it: nothing to reflect on, exit 1",
          code == 1 and not reports and "ran inside investigation #2" in err
          and "--hunt 2" in err, (code, err))
    code, reports, err = _reflect(app, pdir, "--hunt", "9")
    check("#9 is not there: an error that names where the numbers are, exit 2",
          code == 2 and not reports and "no investigation #9" in err
          and "echolot hunt --list" in err, (code, err))
    for word in ("0", "three"):
        with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stop:
            main(["reflect", "--hunt", word, "--project", str(app)])
        check(f"--hunt {word} is refused as no investigation's number", stop.value.code == 2)
    with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stop:
        main(["reflect", "--hunt", "3", "--last", "--project", str(app)])
    check("and it is one way of picking, like --last and --session", stop.value.code == 2)


def test_a_session_whose_transcript_is_gone_is_read_from_the_log(project):
    app, pdir = project
    (pdir / f"{AGENT}.jsonl").unlink()
    code, reports, err = _reflect(app, pdir, "--hunt", "3")
    check("the agent's runs, read from the log under its id, and said to be",
          code == 0 and [(r["source"]["session"], r["source"]["agent"]) for r in reports]
          == [(AGENT, "recorder")] and f"no transcript here for {AGENT[:8]}" in err,
          (code, err, [r["source"] for r in reports]))


def test_runs_that_carry_no_session_id_are_read_as_the_sitting_they_are_in(project):
    app, pdir = project
    _log(app, [_line(None, 60, "collect", inside=3), _line(None, 120, "analyze", inside=3),
               _line(None, 9000, "status")])
    code, reports, err = _reflect(app, pdir, "--hunt", "3")
    check("the sitting that holds them, said to be one", code == 0 and len(reports) == 1
          and "carry no session id" in err
          and reports[0]["context"]["investigation"]["by"] == "sitting"
          and reports[0]["context"]["investigation"]["runs"] == 2, (code, err))
    check("not the later one", [r["context"]["started"][:19] for r in reports]
          == [(T0 + timedelta(seconds=60)).isoformat(timespec="seconds")[:19]],
          [r["source"].get("started") for r in reports])


def test_the_answer_page_names_the_command():
    page = answer_page.render({"n": 3, "question": "why is cold start slow", "status": "concluded",
                               "conclusion": "A sync read on the main thread."}, {})
    check("the Files row names reflect --hunt beside hunt --show",
          "<code>echolot hunt --show 3</code>" in page
          and "<code>echolot reflect --hunt 3</code>" in page, page[-1500:])


def test_two_sessions_that_worked_inside_it_are_both_read(project):
    """An investigation resumed the next day by another session: both are its."""
    app, pdir = project
    _log(app, [_line(AGENT, 60, "collect", inside=3), _line(AGENT, 120, "analyze", inside=3),
               _line(PERSON, 7260, "status"), _line(PERSON, 7320, "analyze", inside=3)])
    code, reports, _ = _reflect(app, pdir, "--hunt", "3")
    check("both sessions, newest first, each with its own count",
          code == 0 and [(r["source"]["session"], r["context"]["investigation"]["runs"])
                         for r in reports] == sorted([(AGENT, 2), (PERSON, 1)]),
          [(r["source"]["session"], r["context"].get("investigation")) for r in reports])
    check("and the summary beside them, as for any run over several",
          (app / ".echolot" / "reflect" / "summary.md").is_file())
