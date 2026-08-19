#!/usr/bin/env python3
"""`echolot reflect` with no agent transcript — the recorder log on its own.

`init` points five clients at this tool and `reflect` read one of them. What
makes the fallback worth having is not that it produces a report; it is that
the report says what it could not look at. A check that returns "clean" over
evidence it never had is worse than one that does not run, and
`trace_opened_directly` — the one rule of the whole design — is exactly that
shape: no evidence found, so everything is fine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.reflect import from_log  # noqa: E402
from echolot.reflect.model import TOOLS  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""


def line(ts: str, cmd: str, argv: list[str], exit_code: int = 0,
         ms: int = 1000, **extra) -> dict:
    return {"ts": ts, "cmd": cmd, "argv": argv, "cwd": "/p", "exit": exit_code,
            "ms": ms, "version": "0.4.0", **extra}


def write_log(project: Path, rows: list[dict]) -> None:
    log = project / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --- sittings ---------------------------------------------------------------

def test_a_gap_cuts_one_sitting_from_the_next():
    """The log is a stream. What stands in for a session id is the gap."""
    rows = [line("2026-08-19T10:00:00+00:00", "doctor", ["doctor"]),
            line("2026-08-19T10:02:00+00:00", "analyze", ["analyze", "a"]),
            # four hours later: another day's work, not this one
            line("2026-08-19T14:00:00+00:00", "analyze", ["analyze", "b"])]
    got = from_log.sittings(rows)
    check("two sittings out of three runs", len(got) == 2, [len(x) for x in got])
    check("the pair that belongs together stayed together",
          len(got[0]) == 2 and len(got[1]) == 1, [len(x) for x in got])


def test_a_run_that_took_a_while_does_not_start_a_new_sitting():
    """The gap is measured from the end of the last run, not its start.

    `collect -n 5` on a real device is minutes. Measuring from the start would
    cut a sitting in half every time the tool did its slowest job.
    """
    rows = [line("2026-08-19T10:00:00+00:00", "collect", ["collect"],
                 ms=25 * 60 * 1000),
            line("2026-08-19T10:35:00+00:00", "analyze", ["analyze", "a"])]
    check("one sitting", len(from_log.sittings(rows)) == 1)


def test_runs_arrive_in_time_order_however_the_log_was_written():
    rows = [line("2026-08-19T10:05:00+00:00", "analyze", ["analyze"]),
            line("2026-08-19T10:00:00+00:00", "doctor", ["doctor"])]
    got = from_log.sittings(rows)[0]
    check("sorted by time", [r["cmd"] for r in got] == ["doctor", "analyze"],
          [r["cmd"] for r in got])


def test_the_id_is_stable_and_does_not_collide_within_a_day(tmp_path):
    write_log(tmp_path, [
        line("2026-08-19T10:00:00+00:00", "doctor", ["doctor"]),
        line("2026-08-19T18:00:00+00:00", "doctor", ["doctor"]),
    ])
    first = [r.id for r in from_log.list_sessions(tmp_path)]
    second = [r.id for r in from_log.list_sessions(tmp_path)]
    check("stable across reads", first == second, (first, second))
    check("two sittings, two ids", len(set(first)) == 2, first)
    check("and short enough for a filename stem",
          all(len(i) == 12 for i in first), first)


# --- the session it produces ------------------------------------------------

def test_the_session_carries_echolot_calls_and_says_it_carries_nothing_else(tmp_path):
    write_log(tmp_path, [
        line("2026-08-19T10:00:00+00:00", "doctor", ["doctor", "-q"]),
        line("2026-08-19T10:01:00+00:00", "analyze",
             ["analyze", "t.perfetto-trace", "-c", "echolot.yml"], ms=4200),
    ])
    ref = from_log.list_sessions(tmp_path)[0]
    session = from_log.read_session(ref)

    check("both runs became calls", len(session.calls) == 2, session.calls)
    check("as shell commands the fact extraction already reads",
          session.calls[0].command == "echolot doctor -q",
          session.calls[0].command)
    check("with the duration the recorder measured",
          session.calls[1].duration_s == 4.2, session.calls[1].duration_s)
    check("this source does not carry the agent's own tools",
          not session.shows(TOOLS), session.carries)
    check("and says so in as many words", session.notes, session.notes)


def test_a_non_zero_exit_survives_into_the_session(tmp_path):
    """The reason the recorder exists: a transcript loses this behind `| tail`."""
    write_log(tmp_path, [line("2026-08-19T10:00:00+00:00", "analyze",
                              ["analyze", "gone.perfetto-trace"], exit_code=2)])
    session = from_log.read_session(from_log.list_sessions(tmp_path)[0])
    check("the call is marked failed", session.calls[0].is_error, session.calls[0])
    check("with the exit code where the fact extraction looks for it",
          "Exit code 2" in session.calls[0].output_head,
          session.calls[0].output_head)


def test_a_sitting_of_nothing_but_reflect_is_not_worth_a_report(tmp_path):
    write_log(tmp_path, [line("2026-08-19T10:00:00+00:00", "reflect", ["reflect"])])
    session = from_log.read_session(from_log.list_sessions(tmp_path)[0])
    check("skipped", not from_log.involves_echolot(session))


# --- the part that matters: what it refuses to claim -------------------------

@pytest.fixture
def report_without_a_transcript(tmp_path):
    """A real `reflect` run in a project that has a log and no transcript."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    write_log(tmp_path, [
        line("2026-08-19T10:00:00+00:00", "doctor", ["doctor", "-q"]),
        line("2026-08-19T10:01:00+00:00", "analyze",
             ["analyze", "a", "-c", "echolot.yml"]),
        line("2026-08-19T10:02:00+00:00", "analyze",
             ["analyze", "b", "-c", "echolot.yml"], exit_code=1),
    ])
    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "reflect", "--last",
         "--project", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path,
        env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("reflect exits 0 with no transcript anywhere",
          done.returncode == 0, done.stderr[-400:])
    check("and says which source it fell back to",
          "runs.jsonl" in done.stderr, done.stderr[-200:])
    written = list((tmp_path / ".echolot" / "reflect").glob("*.json"))
    check("one report", len(written) == 1, written)
    return json.loads(written[0].read_text(encoding="utf-8")), done.stdout


def test_the_one_rule_is_unchecked_not_kept(report_without_a_transcript):
    """The failure this whole mechanism exists to prevent.

    `trace_opened_directly` returns "the trace was never opened directly" when
    it finds no evidence. On a source that cannot hold that evidence, that is a
    green tick over a question nobody asked — about the one rule the design
    rests on.
    """
    report, text = report_without_a_transcript
    skipped = report["summary"]["skipped_ids"]
    check("it is listed as not checked", "trace_opened_directly" in skipped, skipped)

    passed = [x["id"] for x in report["signals"] if x["severity"] == "ok"]
    check("and never as a check that passed",
          "trace_opened_directly" not in passed, passed)
    check("the markdown says the same",
          "## Not checked" in text and "`trace_opened_directly`" in text,
          text[:400])


def test_the_checks_that_need_only_echolot_calls_still_run(report_without_a_transcript):
    report, _ = report_without_a_transcript
    ran = {x["id"] for x in report["signals"] if x["severity"] != "skip"}
    check("doctor before analyze was checked", "doctor_first" in ran, ran)
    check("and the failed run was found",
          "echolot_failures" in report["summary"]["warn_ids"],
          report["summary"])


def test_the_report_leads_with_what_it_could_not_see(report_without_a_transcript):
    """Read the other way round, a short report looks like a clean one."""
    _, text = report_without_a_transcript
    check("the caveat is above the findings",
          text.index("no agent transcript was used") < text.index("## Signals"),
          text[:600])


def test_from_log_can_be_asked_for_where_a_transcript_exists(tmp_path):
    """`--from-log` is how the two reports get compared against each other."""
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    write_log(tmp_path, [
        line("2026-08-19T10:00:00+00:00", "analyze", ["analyze", "a"])])
    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "reflect", "--last", "--from-log",
         "--project", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path,
        env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("exits 0", done.returncode == 0, done.stderr[-300:])
    check("and does not announce a fallback it was asked for",
          "no agent transcript for this project" not in done.stderr, done.stderr)


def test_a_project_with_neither_source_says_so(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "echolot.main", "reflect", "--last",
         "--project", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path,
        env=dict(os.environ, ECHOLOT_NO_RECORD="1"))
    check("refused", done.returncode == 2, f"exit {done.returncode}")
    named_both = ("no Claude Code transcripts" in done.stderr
                  and "no run log" in done.stderr)
    check("naming both places it looked", named_both, done.stderr[-300:])
