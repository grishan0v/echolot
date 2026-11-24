"""`collect` at the edges of the runner: a failure's hint, a launcher the device
cannot name, an interrupt, arguments with a `$` or a space in them, and a
trace that goes away before it is copied.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, runner  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402,F401
    CONFIG,
    GRADLE_CONFIG,
    FakeAdb,
    kind,
    no_adb,
    old_set,
    wrapper,
)

ROOT = Path(__file__).resolve().parent.parent


def test_a_failed_install_is_not_called_no_device() -> None:
    gist = runner.failure_gist(
        1, "", "> com.android.builder.testing.api.DeviceException: "
               "com.android.ddmlib.InstallException: INSTALL_FAILED_UPDATE_INCOMPATIBLE: "
               "Package com.example.app signatures do not match newer version; ignoring!\n")
    check("the install hint, and no device hint before it",
          "the APK did not install" in gist and "no device it could use" not in gist, gist)
    gist = runner.failure_gist(
        1, "", "com.android.builder.testing.api.DeviceException: No online devices found.\n")
    check("a device gradle could not find still is", "no device it could use" in gist, gist)


def test_the_suppress_hint_names_what_the_benchmark_refused() -> None:
    gist = runner.failure_gist(
        1, "java.lang.AssertionError: ERRORS (not suppressed): DEBUGGABLE\n", "")
    check("suppressErrors=DEBUGGABLE", "suppressErrors=DEBUGGABLE to runner.gradle_args" in gist,
          gist)


def test_whitespace_on_stderr_does_not_hide_stdout() -> None:
    check("stdout's line is the cause", runner.failure_lines("hello\n", "\n") == ["hello"])
    check("and the command did print something",
          "printed nothing" not in runner.failure_gist(1, "hello\n", "\n"))


def test_the_system_chooser_is_not_the_launcher(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_run", lambda args, **kw: (
        "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n"
        "android/com.android.internal.app.ResolverActivity\n"))
    with pytest.raises(runner.RunnerError, match="more than one launcher activity"):
        runner.resolve_activity("SERIAL", "com.example.app")
    monkeypatch.setattr(runner, "_run", lambda args, **kw: "com.example.app/.MainActivity\n")
    check("the app's own is taken",
          runner.resolve_activity("SERIAL", "com.example.app") == "com.example.app/.MainActivity")


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
@pytest.mark.parametrize("how", ["SIGINT to the group", "SIGTERM to echolot"])
def test_an_interrupted_collect_takes_its_command_with_it(tmp_path: Path, how: str) -> None:
    """Ctrl-C reaches the foreground group, which holds echolot alone; an
    agent's harness sends SIGTERM. The command used to run on either way."""
    marker = tmp_path / "survived.txt"
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from echolot import runner\n"
            "runner.run_command('sleep 3; echo survived > %s', timeout=60)\n"
            % (str(ROOT), marker))
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    time.sleep(1.0)
    if how.startswith("SIGINT"):
        os.killpg(proc.pid, signal.SIGINT)
    else:
        proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    time.sleep(3.0)
    check("the command did not finish after echolot did", not marker.exists())


@pytest.mark.usefixtures("no_adb")
def test_gradle_args_reach_gradle_word_for_word(tmp_path, monkeypatch, capsys) -> None:
    project = tmp_path / "project"
    wrapper(project / "app", 'for a in "$@"; do printf "[%s]\\n" "$a"; done > args.txt\n')
    (project / "echolot.yml").write_text(GRADLE_CONFIG + (
        "  gradle_args:\n"
        "    - -Pandroid.testInstrumentationRunnerArguments.class=com.example.Benchmarks$Startup\n"
        "    - -Dorg.gradle.jvmargs=-Xmx4g -XX:+UseParallelGC\n"), encoding="utf-8")
    with recorder.isolated():
        main(["collect", "-c", str(project / "echolot.yml"), "-n", "1"])
    got = (project / "app" / "args.txt").read_text(encoding="utf-8").splitlines()
    check("each element one argument, with nothing expanded", got == [
        "[:benchmark:connectedBenchmarkAndroidTest]",
        "[-Pandroid.testInstrumentationRunnerArguments.class=com.example.Benchmarks$Startup]",
        "[-Dorg.gradle.jvmargs=-Xmx4g -XX:+UseParallelGC]"], got)
    err = capsys.readouterr().err
    check("and the line logged is the line run", "Benchmarks$Startup'" in err, err)


def test_a_trace_that_goes_away_is_skipped(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "out"
    src.mkdir()
    for i in range(3):
        (src / f"Bench_iter{i:03d}.perfetto-trace").write_bytes(b"t%d" % i)
    gone = src / "Bench_iter001.perfetto-trace"
    read = Path.read_bytes

    def flaky(self):
        if self == gone:
            raise FileNotFoundError(self)
        return read(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    got = runner.harvest(src, 0, tmp_path / "traces", "startup")
    check("the two left are copied, numbered without a hole",
          [p["path"].name for p in got]
          == ["startup_iter000.perfetto-trace", "startup_iter001.perfetto-trace"], got)


# A build that printed a D8 warning long before its test failed, as an SM-A515F
# round did (#521): the warning holds `error: Could`, and came first.
D8_THEN_FAILED = """\
> Task :app:mergeExtDexBenchmark
WARNING: /home/u/.gradle/caches/isoparser-1.9.41.jar: D8: Invalid stack map table at instruction 12, error: Could not assign java.lang.Object to int
w: file:///work/app/src/main/java/Foo.kt:12:5 'bar' is deprecated. Error: none
> Task :benchmark:connectedBenchmarkAndroidTest
com.example.benchmark.StartupBenchmark > startup[SM-A515F - 13] FAILED
        com.example.benchmark.SetupHelper$SetupException: setup failed: HTTP 400
        at com.example.benchmark.SetupHelper.run(SetupHelper.kt:40)
Tests on SM-A515F - 13 failed: There was 1 failure(s).
"""


def test_a_build_warning_is_not_the_cause_of_a_failed_test() -> None:
    gist = runner.failure_gist(1, D8_THEN_FAILED,
                               "FAILURE: Build failed with an exception.\n"
                               "* What went wrong:\n"
                               "Execution failed for task ':benchmark:connectedBenchmarkAndroidTest'.\n")
    check("the gist is what the test threw",
          gist.startswith("com.example.benchmark.SetupHelper$SetupException: setup failed: "
                          "HTTP 400"), gist)
    lines = runner.failure_lines(D8_THEN_FAILED, "")
    check("and the warnings are not among the lines shown",
          not any(ln.startswith(("WARNING", "w: ")) for ln in lines), lines)


def test_a_lowercase_error_still_speaks_when_nothing_was_thrown() -> None:
    gist = runner.failure_gist(1, "", "adb: error: failed to get feature set: no devices\n")
    check("adb's own line", gist.startswith("adb: error: failed to get feature set"), gist)


class _CtrlC(FakeAdb):
    """An adb that is interrupted at the `when`-th call of one kind."""

    def __init__(self, at: str, when: int):
        super().__init__()
        self.at, self.when, self.seen = at, when, 0

    def __call__(self, args, stdin=None, timeout=120) -> str:
        if kind(list(args)) == self.at:
            self.seen += 1
            if self.seen == self.when:
                if self.at == "pull":
                    Path(args[-1]).write_bytes(b"half a trace")
                raise KeyboardInterrupt
        return super().__call__(args, stdin=stdin, timeout=timeout)


def _interrupted(tmp_path: Path, monkeypatch, capsys, at: str, when: int):
    monkeypatch.setattr(runner, "_run", _CtrlC(at, when))
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect"])
    return code, capsys.readouterr().err


def test_an_interrupted_collect_says_how_far_it_got(tmp_path, monkeypatch, capsys) -> None:
    """Ctrl-C during the third repeat ended in thirty lines of traceback (#484)."""
    code, err = _interrupted(tmp_path, monkeypatch, capsys, "start", 3)
    check("exit 130, as a shell says for Ctrl-C", code == 130, err[-600:])
    check("no traceback", "Traceback" not in err and "KeyboardInterrupt" not in err, err)
    check("one line with the count and where the traces are",
          "collect interrupted after 2 of 3 repeats: their 2 traces are kept in "
          ".echolot/traces" in err, err)
    traces = sorted(p.name for p in (tmp_path / ".echolot" / "traces").glob("*.perfetto-trace"))
    check("the two finished traces are kept",
          traces == ["startup_iter000.perfetto-trace", "startup_iter001.perfetto-trace"],
          traces)
    progress = json.loads((tmp_path / runner.PROGRESS_FILE).read_text(encoding="utf-8"))
    check("the progress file is left unfinished, for status to read as interrupted",
          progress.get("finished") is None and progress.get("done") == 2, progress)


def test_half_a_pulled_trace_is_not_kept(tmp_path, monkeypatch, capsys) -> None:
    old = old_set(tmp_path / ".echolot" / "traces")
    code, err = _interrupted(tmp_path, monkeypatch, capsys, "pull", 2)
    check("exit 130", code == 130, err[-600:])
    traces = tmp_path / ".echolot" / "traces"
    check("no trace under the repeat's name, the one before it kept",
          sorted(p.name for p in traces.glob("*.perfetto-trace"))
          == ["startup_iter000.perfetto-trace"], list(traces.iterdir()))
    aside = [d for d in traces.iterdir() if d.is_dir()]
    check("the set before it was moved aside, whole",
          len(aside) == 1 and sorted(p.name for p in aside[0].iterdir()) == old, aside)
    check("and the line says how far it got",
          "collect interrupted after 1 of 3 repeats: its trace is kept in "
          ".echolot/traces" in err, err)


def test_an_interrupt_before_the_first_trace_is_in_leaves_the_set_where_it_was(
        tmp_path, monkeypatch, capsys) -> None:
    """The first trace waits for its check beside the set, and the set before
    moves once the check lets it in (#857): cut short in its copy, it leaves
    .echolot/traces as the round found it."""
    old = old_set(tmp_path / ".echolot" / "traces")
    code, err = _interrupted(tmp_path, monkeypatch, capsys, "pull", 1)
    check("exit 130", code == 130, err[-600:])
    traces = tmp_path / ".echolot" / "traces"
    check("the set is where it was, and nothing beside it",
          sorted(p.name for p in traces.iterdir()) == old, list(traces.iterdir()))
    check("and the line says nothing was kept, and moved nothing",
          "before the first of 3 repeats was recorded: no trace was kept" in err
          and "the set that was there before" not in err, err)
