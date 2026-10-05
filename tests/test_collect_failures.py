"""`collect` at the edges of the runner: a failure's hint, a launcher the device
cannot name, an interrupt, arguments with a `$` or a space in them, and a
trace that goes away before it is copied.
"""

from __future__ import annotations

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
from tests.test_collect_fake_adb import GRADLE_CONFIG, no_adb, wrapper  # noqa: E402,F401

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
