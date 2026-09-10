#!/usr/bin/env python3
"""What a `collect` leaves behind while it runs and when it fails.

A macrobenchmark round is minutes of silence, and the agent that started it
waits on a background task and asks `echolot` where things stand. The
answer used to be the trace count from the run before; a failure left
`exit: 2` in the log and nothing else, and the harness's "moved to the
background" notice as the only output anyone kept.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, runner, state  # noqa: E402
from tests.support import check  # noqa: E402

SDK_OUT = """\
> Task :benchmark:connectedBetaBenchmarkAndroidTest
Starting 1 tests on SM-A515F - 13
com.example.benchmark.StartupBenchmark > startup[SM-A515F - 13] FAILED
        java.lang.IllegalStateException: Issue while enabling Perfetto SDK tracing in com.example.app: binary verification error
        at androidx.benchmark.perfetto.PerfettoCapture.enableAndroidxTracingPerfetto(PerfettoCapture.kt:118)
Tests on SM-A515F - 13 failed: There was 1 failure(s).
"""
SDK_ERR = """\
FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':benchmark:connectedBetaBenchmarkAndroidTest'.
> There were failing tests. See the report at: file:///build/reports/index.html

* Try:
> Run with --stacktrace option to get the stack trace.
BUILD FAILED in 2m 9s
"""


# --- the failure, read off both streams ---------------------------------------

def test_the_instrumentation_line_on_stdout_is_kept_and_the_boilerplate_is_not():
    lines = runner.failure_lines(SDK_OUT, SDK_ERR)
    text = "\n".join(lines)
    check("the test that failed", "startup[SM-A515F - 13] FAILED" in text, lines)
    check("and the exception that failed it", "binary verification error" in text, lines)
    check("gradle's what-went-wrong too", "Execution failed for task" in text, lines)
    check("but not its advice", "--stacktrace" not in text and "BUILD FAILED" not in text, lines)


def test_a_failure_that_says_nothing_recognisable_still_shows_its_tail():
    lines = runner.failure_lines("all quiet\nlast line\n", "")
    check("the tail", lines == ["all quiet", "last line"], lines)


def test_each_known_failure_gets_its_one_hint():
    sdk = runner.hints("Issue while enabling Perfetto SDK tracing: binary verification error")
    check("the SDK half, with the flag that turns it off",
          len(sdk) == 1 and "perfettoSdkTracing.enable=false" in sdk[0], sdk)
    check("stale code_cache is named as the usual cause", "code_cache" in sdk[0], sdk)
    sup = runner.hints("ERRORS (not suppressed): EMULATOR, LOW-BATTERY")
    check("the benchmark's refusal, with suppressErrors", len(sup) == 1 and "suppressErrors" in sup[0], sup)
    dev = runner.hints("com.android.builder.testing.api.DeviceException: No online devices found.")
    check("no device, with runner.device", len(dev) == 1 and "runner.device" in dev[0], dev)
    check("nothing for an unknown failure", runner.hints("something else entirely") == [], "")


def test_the_message_carries_the_command_the_lines_and_the_hint():
    msg = runner.failure_message("./gradlew :b:connectedAndroidTest", 1, SDK_OUT, SDK_ERR)
    check("the command", "./gradlew :b:connectedAndroidTest" in msg, msg)
    check("the line", "binary verification error" in msg, msg)
    check("the hint, marked as one", "\n→ " in msg and "perfettoSdkTracing" in msg, msg)


def test_run_command_raises_the_readable_message():
    cmd = "echo 'ERRORS (not suppressed): EMULATOR'; echo 'FAILURE: Build failed' >&2; exit 1"
    try:
        runner.run_command(cmd, timeout=10)
    except runner.RunnerError as e:
        text = str(e)
        check("returned 1", "returned 1" in text, text)
        check("the stdout line", "ERRORS (not suppressed)" in text, text)
        check("the hint", "suppressErrors" in text, text)
    else:
        raise AssertionError("a failing command must raise")


# --- the reason, in the log ---------------------------------------------------

def test_a_clean_failure_leaves_its_sentence_in_the_log(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)

    class Args:
        cmd = "collect"
        config = None

    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.failed("collection error: the scenario command returned 1")
        recorder.record(Args(), ["collect"], time.time(), exit_code=2)
        runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("one line", len(runs) == 1, runs)
    check("with the sentence as its error",
          runs[0]["error"] == "collection error: the scenario command returned 1", runs[0])

    with recorder.isolated():
        recorder.at(tmp_path)
        recorder.failed("would be wrong here")
        recorder.record(Args(), ["collect"], time.time(), exit_code=0)
        runs = recorder.read(tmp_path / recorder.LOG_FILE)
    check("a run that exited 0 carries no reason", "error" not in runs[1], runs[1])


# --- where it stands, for status ----------------------------------------------

def write_progress(project: Path, **fields) -> None:
    p = runner.Progress(project / runner.PROGRESS_FILE)
    p.update(**fields)


def test_a_run_in_flight_says_how_far_it_got(tmp_path):
    write_progress(tmp_path, scenario="coldStart", mode="launch", started=time.time() - 90,
                   pid=os.getpid(), iterations=15, done=7)
    st = {"collect": state.collect_state(tmp_path), "traces": {"newest": None}}
    check("running, since this process is alive", st["collect"]["status"] == "running", st)
    line = state.collect_line(st)
    check("the line counts iterations", "running: coldStart, 7/15 iterations" in line, line)
    write_progress(tmp_path, scenario="coldStart", mode="gradle", started=time.time() - 90,
                   pid=os.getpid(), iterations=None, done=0)
    line = state.collect_line({"collect": state.collect_state(tmp_path), "traces": {}})
    check("gradle mode says who drives", "macrobenchmark drives" in line, line)


def test_a_run_whose_process_is_gone_reads_as_interrupted(tmp_path):
    write_progress(tmp_path, scenario="s", mode="launch", started=time.time() - 3600,
                   pid=2 ** 22 - 1, iterations=5, done=2)
    st = {"collect": state.collect_state(tmp_path), "traces": {"newest": None}}
    check("interrupted", st["collect"]["status"] == "interrupted", st)
    check("at the iteration it reached", "interrupted at 2/5" in state.collect_line(st), state.collect_line(st))


def test_a_failure_is_the_last_word_until_newer_traces_arrive(tmp_path):
    now = time.time()
    write_progress(tmp_path, scenario="s", mode="gradle", started=now - 200, pid=1,
                   iterations=None, done=0, finished=now - 60, exit=2,
                   error="collection error: the scenario command returned 1")
    st = {"collect": state.collect_state(tmp_path), "traces": {"newest": now - 3600}}
    line = state.collect_line(st)
    check("failed, with the reason", line and line.startswith("failed") and "returned 1" in line, line)
    st["traces"]["newest"] = now
    check("newer traces make it history", state.collect_line(st) is None, "")
    write_progress(tmp_path, scenario="s", mode="gradle", started=now - 200, pid=1,
                   iterations=None, done=0, finished=now - 60, exit=0, traces=5)
    check("a run that ended well says nothing here",
          state.collect_line({"collect": state.collect_state(tmp_path), "traces": {}}) is None, "")


def test_a_broken_or_missing_progress_file_is_nothing(tmp_path):
    check("missing", state.collect_state(tmp_path) is None, "")
    path = tmp_path / runner.PROGRESS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    check("unreadable", state.collect_state(tmp_path) is None, "")
    path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    check("without a start it is not a run", state.collect_state(tmp_path) is None, "")
