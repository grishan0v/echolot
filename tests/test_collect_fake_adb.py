#!/usr/bin/env python3
"""`collect` against an adb that answers from a script.

The runner is the one part of echolot that talks to the outside world, and
the part no test had driven: there is no phone in CI, so every call to adb
went unexercised, and so did the order they come in. An audit run with a
fake adb found five things the runner said and did not do — a typo in
`reset_policy` announced as force-stop and run warm, a `TotalTime: 0` that
crashed `collect` after the traces were pulled, the previous set moved out
of the way by a collect that then failed on no device, a device chosen with
`--device` that never reached the scenario command, and a Linux `no
permissions` read as a device in state `no`.

`runner._run` is the single door to adb, so a fake in its place sees every
question in order. Command and gradle modes run real shell commands — the
scenario and the wrapper are processes either way — and gradle mode never
asks adb anything, which the fake holds it to.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder, runner, scan  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

SERIAL = "R58M123ABC"
PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/.MainActivity"

ONE_DEVICE = f"List of devices attached\n{SERIAL}\tdevice\n\n"
TWO_DEVICES = f"List of devices attached\n{SERIAL}\tdevice\nemulator-5554\tdevice\n\n"
NO_DEVICES = "List of devices attached\n\n"
UNAUTHORIZED = f"List of devices attached\n{SERIAL}\tunauthorized\n\n"
OFFLINE = f"List of devices attached\n{SERIAL}\toffline\n\n"
NO_PERMISSIONS = (
    f"List of devices attached\n{SERIAL}\tno permissions (missing udev rules? "
    f"user is in the plugdev group); see "
    f"[http://developer.android.com/tools/device.html]\n\n")
# What adb prints on Windows, and on any host once the daemon had to start.
CRLF = ("* daemon not running; starting now at tcp:5037\r\n"
        "* daemon started successfully\r\n"
        f"List of devices attached\r\n{SERIAL}\tdevice\r\n\r\n")

RESOLVED = ("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 "
            f"isDefault=true\n{ACTIVITY}\n")
AM_START = ("Starting: Intent {{ cmp={activity} }}\nStatus: ok\n"
            "LaunchState: COLD\nActivity: {activity}\nTotalTime: {total}\n"
            "WaitTime: {wait}\nComplete\n")


def kind(args: list[str]) -> str:
    """What one adb call was for, in a word."""
    text = " ".join(args)
    for name, needle in (("devices", "adb devices"), ("resolve", "resolve-activity"),
                         ("force-stop", "am force-stop"), ("rm", "rm -f"),
                         ("perfetto", "perfetto -c"), ("start", "am start -W"),
                         ("wait", "pidof perfetto"), ("pull", " pull ")):
        if needle in text:
            return name
    return text


class FakeAdb:
    """Answers `runner._run` from a script, and keeps every question in order.

    `fail` names a kind of call that fails the way adb does, with an error
    of its own; the pull writes a small file where the trace would land.
    """

    def __init__(self, listing: str = ONE_DEVICE, totals=(523,), fail: str | None = None):
        self.listing = listing
        self.totals = list(totals)
        self.fail = fail
        self.calls: list[list[str]] = []
        self.stdin: list[str | None] = []

    def __call__(self, args, stdin=None, timeout=120) -> str:
        args = list(args)
        self.calls.append(args)
        self.stdin.append(stdin)
        what = kind(args)
        if what == self.fail:
            raise runner.RunnerError(f"{' '.join(args)}\nerror: {what} refused")
        if what == "devices":
            return self.listing
        if what == "resolve":
            return RESOLVED
        if what == "start":
            total = self.totals.pop(0) if len(self.totals) > 1 else self.totals[0]
            return AM_START.format(activity=ACTIVITY, total=total, wait=total + 20)
        if what == "pull":
            Path(args[-1]).write_bytes(b"not a real trace, but not empty either")
            return "1 file pulled"
        return ""

    def kinds(self) -> list[str]:
        return [kind(c) for c in self.calls]


@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


def old_set(out: Path, name: str = "startup", n: int = 3) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    names = [f"{name}_iter{i:03d}.perfetto-trace" for i in range(n)]
    for f in names:
        (out / f).write_bytes(b"the baseline")
    return names


# --- launch mode: the calls, in order -----------------------------------------

def test_launch_mode_asks_once_then_runs_six_steps_per_iteration(tmp_path, adb):
    said: list[str] = []
    results = runner.collect(PACKAGE, tmp_path / "traces", 3, section={},
                             name="startup", log=said.append)
    kinds = adb.kinds()
    check("the device, then the activity, once each and first",
          kinds[:2] == ["devices", "resolve"], kinds)
    check("then per iteration: stop, clear, record, launch, wait, pull",
          kinds[2:] == ["force-stop", "rm", "perfetto", "start", "wait", "pull"] * 3,
          kinds)
    check("every call after `adb devices` names the device it picked",
          all(c[:3] == ["adb", "-s", SERIAL] for c in adb.calls[1:]), adb.calls)
    recording = adb.stdin[kinds.index("perfetto")] or ""
    check("perfetto is handed the config on stdin, with the package in it",
          f'atrace_apps: "{PACKAGE}"' in recording, recording[:200])
    check("three traces, named by iteration",
          sorted(p.name for p in (tmp_path / "traces").iterdir())
          == [f"startup_iter{i:03d}.perfetto-trace" for i in range(3)])
    check("am start -W read per iteration",
          [r["total_time_ms"] for r in results] == [523] * 3, results)
    check("the log names the device and the activity",
          any(SERIAL in line and ACTIVITY in line for line in said), said)


# --- reset_policy: what is announced is what runs -----------------------------

ABSENT = object()


@pytest.mark.parametrize("value, stops, warned", [
    (ABSENT, 3, False),
    ("force-stop", 3, False),
    ("none", 0, False),
    ("force_stop", 3, True),    # the typo that used to run warm under a cold name
    (None, 3, True),            # `reset_policy:` with nothing after it
    ("pm clear", 3, True),      # deliberately unsupported
], ids=["default", "force-stop", "none", "typo", "empty", "pm-clear"])
def test_reset_policy_runs_what_it_announces(tmp_path, adb, value, stops, warned):
    section = {} if value is ABSENT else {"reset_policy": value}
    said: list[str] = []
    runner.collect(PACKAGE, tmp_path / "traces", 3, section=section,
                   name="startup", log=said.append)
    check(f"{stops} force-stops for {value!r}",
          adb.kinds().count("force-stop") == stops, adb.kinds())
    notes = [line for line in said if "reset_policy" in line]
    check("warned exactly when the value was not one of the two", bool(notes) == warned, said)
    if warned:
        check("and the warning says what runs", "Using force-stop" in notes[0], notes)


# --- sampling: sent to the device, and said ------------------------------------

@pytest.mark.parametrize("value, hz", [(ABSENT, None), (False, None),
                                       (True, 100), (150, 150)],
                         ids=["default", "false", "true", "rate"])
def test_sampling_reaches_the_device_and_is_said(tmp_path, adb, value, hz):
    """`runner.sampling` is the one knob that makes the app slower on purpose.

    So it is said when the recording starts, as well as sent to the device —
    the set it records compares only with another sampled one.
    """
    section = {} if value is ABSENT else {"sampling": value}
    said: list[str] = []
    runner.collect(PACKAGE, tmp_path / "traces", 1, section=section,
                   name="startup", log=said.append)
    recording = adb.stdin[adb.kinds().index("perfetto")] or ""
    if hz is None:
        check("no sampler unless asked for", "linux.perf" not in recording, recording)
        check("and nothing said about one", not [s for s in said if "sampling" in s], said)
        return
    check("the sampler is in the device's config, at the rate asked",
          "linux.perf" in recording and f"frequency: {hz} " in recording, recording)
    check("the log says it samples, and at what rate",
          any(f"sampling callstacks at {hz} Hz" in line for line in said), said)


# --- the previous set moves when a new trace takes its place, not before ------

@pytest.mark.parametrize("listing, section, fail", [
    (NO_DEVICES, {}, None),
    (TWO_DEVICES, {}, None),
    (UNAUTHORIZED, {}, None),
    (ONE_DEVICE, {"mode": "lanuch"}, None),
    (ONE_DEVICE, {"mode": "command"}, None),
    (ONE_DEVICE, {"duration_ms": "12s"}, None),
    (ONE_DEVICE, {"sampling": "fast"}, None),
    (ONE_DEVICE, {}, "perfetto"),
    (ONE_DEVICE, {}, "wait"),
], ids=["no-device", "several", "unauthorized", "mode-typo", "no-command",
        "duration-unit", "sampling-word", "perfetto-refused", "wait-failed"])
def test_a_collect_that_fails_before_its_first_trace_leaves_the_set_alone(
        tmp_path, adb, listing, section, fail):
    """The baseline is the first thing a re-record loses, and it was lost for nothing.

    `set_aside` ran first thing, so a collect that stopped at once — no phone
    attached, a typo in the mode — still emptied .echolot/traces of the set
    the next report was to be compared against.
    """
    adb.listing, adb.fail = listing, fail
    out = tmp_path / "traces"
    before = old_set(out)
    moved: list[Path] = []
    with pytest.raises(runner.RunnerError):
        runner.collect(PACKAGE, out, 3, section=section, name="startup",
                       log=lambda m: None, on_set_aside=moved.append)
    check("the set is where it was",
          sorted(p.name for p in out.glob("*.perfetto-trace")) == before)
    check("with nothing set aside", not moved and not [p for p in out.iterdir() if p.is_dir()],
          list(out.iterdir()))


def test_the_set_moves_after_the_device_check_and_before_the_first_pull(tmp_path, adb):
    out = tmp_path / "traces"
    before = old_set(out)
    moved: list[Path] = []

    def aside(d: Path) -> None:
        moved.append(d)
        adb.calls.append(["<set aside>"])

    runner.collect(PACKAGE, out, 2, section={}, name="startup",
                   log=lambda m: None, on_set_aside=aside)
    kinds = adb.kinds()
    check("once", kinds.count("<set aside>") == 1 and len(moved) == 1, kinds)
    where = kinds.index("<set aside>")
    check("after the device check and the first recording, right before its pull",
          kinds[:where] == ["devices", "resolve", "force-stop", "rm", "perfetto",
                            "start", "wait"] and kinds[where + 1] == "pull", kinds)
    check("the old set is in the directory it went to",
          sorted(p.name for p in moved[0].iterdir()) == before)


def test_a_scenario_whose_name_begins_with_this_ones_is_left_alone(tmp_path, adb):
    """`startup_*` used to take `startup_warm_*` along with it."""
    out = tmp_path / "traces"
    old_set(out)
    warm = old_set(out, "startup_warm", 2)
    (out / "startup_probe_2026-09-05.perfetto-trace").write_bytes(b"probe")
    moved: list[Path] = []
    runner.collect(PACKAGE, out, 1, section={}, name="startup",
                   log=lambda m: None, on_set_aside=moved.append)
    check("the warm set stayed",
          sorted(p.name for p in out.glob("startup_warm_*")) == warm)
    check("this scenario's probe went with its set",
          (moved[0] / "startup_probe_2026-09-05.perfetto-trace").exists(),
          list(moved[0].iterdir()))


# --- command mode: the scenario runs against the device being recorded -------

def test_the_scenario_command_gets_the_device_as_android_serial(tmp_path, adb):
    """`adb shell …` inside the scenario, with two devices attached.

    It used to inherit no serial, so with a second device plugged in it
    failed with "more than one device" — `--device` or not.
    """
    adb.listing = TWO_DEVICES
    seen = tmp_path / "serials.txt"
    runner.collect(PACKAGE, tmp_path / "traces", 2, device="emulator-5554",
                   section={"mode": "command", "reset_policy": "none",
                            "command": f'echo "$ANDROID_SERIAL" >> "{seen}"'},
                   name="scroll", log=lambda m: None)
    check("each iteration's scenario saw the device it was recorded on",
          seen.read_text(encoding="utf-8").split() == ["emulator-5554"] * 2,
          seen.read_text(encoding="utf-8"))
    kinds = adb.kinds()
    check("and nothing but record, wait and pull around it",
          kinds[1:] == ["rm", "perfetto", "wait", "pull"] * 2, kinds)


def test_without_device_the_one_attached_is_the_serial(tmp_path, adb):
    seen = tmp_path / "serial.txt"
    runner.collect(PACKAGE, tmp_path / "traces", 1,
                   section={"mode": "command", "command": f'echo "$ANDROID_SERIAL" > "{seen}"'},
                   name="scroll", log=lambda m: None)
    check("the one device", seen.read_text(encoding="utf-8").strip() == SERIAL,
          seen.read_text(encoding="utf-8"))


# --- the devices table --------------------------------------------------------

@pytest.mark.parametrize("listing, wanted", [
    (TWO_DEVICES, ["several devices connected", "--device", "runner.device"]),
    (UNAUTHORIZED, ["Allow USB debugging"]),
    (OFFLINE, ["offline", "adb kill-server"]),
    (NO_PERMISSIONS, ["may not open this device", "plugdev", "udev rules"]),
    (NO_DEVICES, ["no devices at all"]),
], ids=["several", "unauthorized", "offline", "no-permissions", "none"])
def test_what_adb_lists_is_the_sentence_collect_says(adb, listing, wanted):
    adb.listing = listing
    with pytest.raises(runner.RunnerError) as e:
        runner.pick_device()
    for words in wanted:
        check(f"says {words!r}", words in str(e.value), str(e.value))
    check("never a state called `no`", "state is no," not in str(e.value), str(e.value))


def test_a_named_device_in_a_bad_state_says_which_state(adb):
    adb.listing = NO_PERMISSIONS
    with pytest.raises(runner.RunnerError) as e:
        runner.pick_device(SERIAL)
    check("the udev hint for the device named", "udev rules" in str(e.value), str(e.value))
    adb.listing = ONE_DEVICE
    with pytest.raises(runner.RunnerError) as e:
        runner.pick_device("emulator-5556")
    check("a serial that is not there lists what is",
          "not among the connected ones" in str(e.value) and SERIAL in str(e.value),
          str(e.value))


def test_crlf_and_the_daemon_notice_are_not_devices(adb):
    adb.listing = CRLF
    check("the one device, without a carriage return", runner.pick_device() == SERIAL,
          runner.parse_devices(CRLF))
    check("found by name as well", runner.pick_device(SERIAL) == SERIAL, "")
    check("and nothing else was read as one", runner.devices() == [(SERIAL, "device")],
          runner.devices())


def test_scan_reads_the_same_states(monkeypatch):
    listing = (
        "List of devices attached\r\n"
        f"{SERIAL}               no permissions (missing udev rules? user is in the "
        "plugdev group); see [http://developer.android.com/tools/device.html] "
        "usb:1-4 transport_id:2\r\n"
        "emulator-5554          device product:sdk_gphone64 model:sdk_gphone64 "
        "device:emu64a transport_id:1\r\n\r\n")
    monkeypatch.setattr(runner, "_run", lambda args, stdin=None, timeout=120: listing)
    found = {d["serial"]: d for d in scan.devices_attached()}
    check("both devices", set(found) == {SERIAL, "emulator-5554"}, found)
    check("the Linux state whole", found[SERIAL]["state"] == "no permissions", found[SERIAL])
    check("the emulator's model", found["emulator-5554"]["model"] == "sdk_gphone64"
          and found["emulator-5554"]["emulator"], found["emulator-5554"])


@pytest.mark.parametrize("failure, note", [
    (runner.AdbNotFound("adb not found"), "adb is not on PATH"),
    (runner.RunnerError("no answer within 30s from: adb devices -l"),
     "did not list the devices: no answer within 30s"),
], ids=["missing", "timeout"])
def test_scan_says_why_no_device_was_listed(tmp_path, monkeypatch, failure, note):
    """Every failure of adb's used to read "adb is not on PATH"."""
    def fail(args, stdin=None, timeout=120):
        raise failure
    monkeypatch.setattr(runner, "_run", fail)
    facts = scan.describe(tmp_path, devices=True)
    check("no devices", facts.devices is None, facts.devices)
    check(f"and the note says {note!r}", any(note in n for n in facts.notes), facts.notes)


# --- the CLI: TotalTime 0, and gradle mode from another directory -------------

CONFIG = f"""\
project:
  package: {PACKAGE}
scenario:
  name: startup
runner:
  iterations: 3
"""


def test_a_launch_nobody_timed_is_a_note_not_a_crash(tmp_path, monkeypatch, capsys):
    """`TotalTime: 0` went into the spread as a divisor, after the pull.

    The ZeroDivisionError came out of `collect` with three traces on disk
    and none of their paths printed.
    """
    fake = FakeAdb(totals=(0, 480, 0))
    monkeypatch.setattr(runner, "_run", fake)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with recorder.isolated():
        code = main(["collect"])
    out, err = capsys.readouterr()
    check("exit 0", code == 0, err[-600:])
    printed = [line for line in out.splitlines() if line.endswith(".perfetto-trace")]
    check("the three paths are printed", len(printed) == 3, out)
    check("the untimed launches are named", "2 of 3 launches report TotalTime: 0" in err, err)
    check("and no spread is made of one number", "spread" not in err, err)


def wrapper(app: Path, body: str) -> None:
    app.mkdir(parents=True, exist_ok=True)
    script = app / "fake-gradlew"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)


GRADLE_CONFIG = f"""\
project:
  package: {PACKAGE}
scenario:
  name: startup
runner:
  mode: gradle
  gradle: ./fake-gradlew
  gradle_task: ":benchmark:connectedBenchmarkAndroidTest"
  project_root: app
  device: {SERIAL}
"""

WRITES_TWO = """\
printf %s "$ANDROID_SERIAL" > serial.txt
d="benchmark/build/outputs/connected_android_test_additional_output/benchmark/connected/Pixel"
mkdir -p "$d"
printf one > "$d/StartupBenchmark_startup_iter000_2026-09-28.perfetto-trace"
printf two > "$d/StartupBenchmark_startup_iter001_2026-09-28.perfetto-trace"
"""


@pytest.fixture
def no_adb(monkeypatch):
    """Gradle mode picks its device through gradle; adb is not ours to ask."""
    def refuse(args, stdin=None, timeout=120):
        raise AssertionError(f"gradle mode called adb: {args}")
    monkeypatch.setattr(runner, "_run", refuse)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)


def test_gradle_mode_follows_the_config_not_the_shell(tmp_path, monkeypatch, capsys, no_adb):
    """`-c` into another directory: project_root, -o and the device all follow it.

    `runner.project_root` resolved against wherever `echolot` was started,
    while -o and the investigation followed the config — so the same config
    ran gradle in two different places depending on the shell. `runner.device`
    never reached gradle at all, and `-n` was dropped without a word.
    """
    project = tmp_path / "project"
    wrapper(project / "app", WRITES_TWO)
    (project / "echolot.yml").write_text(GRADLE_CONFIG, encoding="utf-8")
    baseline = old_set(project / ".echolot" / "traces")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with recorder.isolated():
        code = main(["collect", "-c", str(project / "echolot.yml"), "-n", "1"])
    out, err = capsys.readouterr()
    check("exit 0", code == 0, err[-800:])
    traces = project / ".echolot" / "traces"
    check("both traces landed beside the config",
          sorted(p.name for p in traces.glob("*.perfetto-trace"))
          == ["startup_iter000.perfetto-trace", "startup_iter001.perfetto-trace"],
          list(traces.iterdir()))
    check("and the set before them was set aside, whole",
          [sorted(p.name for p in d.iterdir()) for d in traces.iterdir() if d.is_dir()]
          == [baseline], list(traces.iterdir()))
    check("nothing landed where echolot was started", list(elsewhere.iterdir()) == [],
          list(elsewhere.iterdir()))
    check("gradle ran in project_root, taken from the config's directory",
          f"in {(project / 'app').resolve()}" in err, err)
    check("with the device as ANDROID_SERIAL",
          (project / "app" / "serial.txt").read_text(encoding="utf-8") == SERIAL)
    check("and -n said not to reach the benchmark",
          "-n does not reach the macrobenchmark" in err, err)
    check("the paths are printed", out.count(".perfetto-trace") == 2, out)


# A macrobenchmark that could not set up the SDK half of its tracing, as
# gradle prints it: the exception on stdout, the frame around it on stderr.
SDK_FAILURE = """\
echo "> Task :benchmark:connectedBenchmarkAndroidTest"
echo "Starting 1 tests on Pixel 6 - 14"
echo "com.example.benchmark.StartupBenchmark > startup[Pixel 6 - 14] FAILED"
echo "        java.lang.IllegalStateException: Issue while enabling Perfetto SDK tracing in com.example.app: binary verification error"
echo "        at androidx.benchmark.perfetto.PerfettoCapture.enableAndroidxTracingPerfetto(PerfettoCapture.kt:118)"
echo "Tests on Pixel 6 - 14 failed: There was 1 failure(s)."
echo "FAILURE: Build failed with an exception." >&2
echo "* What went wrong:" >&2
echo "Execution failed for task ':benchmark:connectedBenchmarkAndroidTest'." >&2
echo "> There were failing tests. See the report at: file:///work/app/benchmark/build/reports/androidTests/connected/benchmark/index.html" >&2
exit 1
"""

# The arguments a real config carries, which make the command line long.
GRADLE_ARGS = """\
  gradle_args:
    - -Pandroid.testInstrumentationRunnerArguments.class=com.example.benchmark.StartupBenchmark
    - -Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED
    - -Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.fullTracing.enable=true
"""


def test_a_failed_gradle_run_keeps_its_cause_and_its_hint(tmp_path, monkeypatch, capsys, no_adb):
    """What `echolot` and the run log say after gradle fails.

    The progress file kept the first line of the error — "the scenario
    command returned 1:", after every gradle failure — so the `collect` line
    of `echolot` named no cause. The run log cut the whole message at 800
    characters, which on a failure like this one fell before the hint.
    """
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    project = tmp_path / "project"
    wrapper(project / "app", SDK_FAILURE)
    (project / "echolot.yml").write_text(GRADLE_CONFIG + GRADLE_ARGS, encoding="utf-8")
    baseline = old_set(project / ".echolot" / "traces")
    monkeypatch.chdir(tmp_path)

    with recorder.isolated():
        code = main(["collect", "-c", str(project / "echolot.yml")])
    _, err = capsys.readouterr()
    check("exit 2", code == 2, err[-600:])
    printed = err[err.find("collection error:"):]
    check("the case is the real one: the hint comes later than the log's 800",
          printed.find("perfettoSdkTracing.enable=false") > 800, printed)
    check("the set before it is where it was",
          sorted(p.name for p in (project / ".echolot" / "traces").iterdir()) == baseline)

    progress = json.loads((project / runner.PROGRESS_FILE).read_text(encoding="utf-8"))
    check("the progress file names the cause",
          "binary verification error" in progress["error"], progress)
    check("and the hint", "perfettoSdkTracing.enable=false" in progress["error"], progress)
    check("not the line that says nothing",
          not progress["error"].startswith("the scenario command returned"), progress)

    runs = recorder.read(project / recorder.LOG_FILE)
    logged = runs[-1].get("error") or ""
    check("the run log leads with the cause", logged.startswith(
        "collection error: java.lang.IllegalStateException"), logged[:200])
    check("and keeps the hint inside what it keeps",
          "perfettoSdkTracing.enable=false" in logged, logged)
