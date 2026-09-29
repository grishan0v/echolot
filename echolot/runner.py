"""Trace collection: N repeats of one scenario on a device.

A separate module because this is the only part of echolot that touches the
outside world: adb, the device, launching the app. Everything else is a pure
function of a trace file.

Repeats are not decoration. A single run cannot tell a regression from a random
spike: on a live cold start the spread between iterations reaches tens of
percent. Thresholds are calibrated from repeats and the report is aggregated
over repeats; without them both are guessing.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Callable

# Default atrace categories. sched in ftrace_events is mandatory: without it
# there is no thread_state, and both runnable_starvation and uninstrumented_cpu
# go silent.
DEFAULT_CATEGORIES = [
    "am", "wm", "gfx", "view", "dalvik", "binder_driver", "res", "database",
]

# Perfetto reads this as protobuf text format. Braces are doubled because the
# same string goes through str.format; nothing else about it is Python.
#
# `android.surfaceflinger.frametimeline` is SurfaceFlinger's own record of
# every frame — the deadline it was given, what it actually took, and why it
# missed — and it is the only source frame_jank has. Android 12 and up; on
# anything older the data source does not exist, perfetto records the rest
# without complaining, and the detector stays silent.
#
# Comments do not go in the template. Text format spells them `#`, and a
# config that fails to parse on the device turns every capture into a puzzle
# for the sake of a sentence that reads better here.
TRACE_CONFIG = """\
buffers: {{ size_kb: {buffer_kb} fill_policy: DISCARD }}
{perf_buffer}data_sources: {{
  config {{
    name: "linux.ftrace"
    ftrace_config {{
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "sched/sched_process_exit"
      ftrace_events: "sched/sched_process_free"
      ftrace_events: "task/task_newtask"
      ftrace_events: "task/task_rename"
{environment}{categories}
      atrace_apps: "{package}"
    }}
  }}
}}
data_sources: {{
  config {{
    name: "linux.process_stats"
    process_stats_config {{ scan_all_processes_on_start: true }}
  }}
}}
data_sources: {{
  config {{
    name: "android.surfaceflinger.frametimeline"
  }}
}}{sys_stats}{perf}
duration_ms: {duration_ms}
"""

# Platform state: the four events that say what the device was doing to the app
# while we measured it.
#
# The reason they are here is `compare`. The same code on a lower clock takes
# longer, and a comparison that cannot see the clock reports that as a
# regression — a table of grown rows for an app nobody changed. Without these
# events the report has no way to tell one from the other, and neither has
# anybody reading it.
#
# `power/cpu_frequency` fires when the governor changes a frequency, so its
# volume follows the governor rather than the workload. Measured on an
# SM-A515F (Android 13, 8 cores, two cpufreq policies) over three 12-second
# cold starts: 4564–4940 samples per trace, about 400 a second, 1.6% of all
# ftrace events in the same trace, and the buffer never overran. `power/
# cpu_idle` is the expensive one — it fires on every idle-state transition on
# every core — and it is deliberately NOT here: we read the clock only over
# intervals where our own threads were on a CPU, and a CPU running our thread
# is by definition not idle.
#
# `sched/sched_blocked_reason` fires when a task blocks, not while it runs, so
# it is cheap. It carries two things and only one of them survives a
# production device. `io_wait` reaches `thread_state.io_wait` and works: on
# the A51 it was filled in for 6486 of 6683 uninterruptible-sleep intervals.
# `caller` is a kernel address that perfetto turns into
# `thread_state.blocked_function` by reading /proc/kallsyms — which is
# unreadable on a production build, so `blocked_function` came back NULL for
# every row, with and without `symbolize_ksyms`. Anything built on the name of
# the blocking function is for a userdebug kernel; `io_wait` is what the rest
# of us get, and it is enough to tell disk waiting from other blocking.
#
# The thermal pair is polled by the kernel and costs nothing worth measuring.
# It is also the one that earned its place fastest: on the same three runs the
# A51 reached 76 °C and the kernel throttled `thermal-cpufreq-1` during two of
# them, so the numbers in that report are the app on a slowed machine — which
# is precisely the sentence the report could not say before.
#
# An event the kernel does not have is not an error: perfetto records the rest
# and lists it under unknown, the same tolerance frametimeline relies on above.
ENVIRONMENT_EVENTS = [
    "power/cpu_frequency",
    "sched/sched_blocked_reason",
    "thermal/thermal_temperature",
    "thermal/cdev_update",
]

# Memory pressure, once a second. No counter list on purpose: naming counters
# means naming enum constants, and a misspelled one is a config that does not
# parse on the device. Without the list perfetto records the whole of
# /proc/meminfo and /proc/vmstat, which at 1 Hz is a rounding error.
#
# Single braces, unlike the template above: this block is substituted into the
# result of `str.format`, so it never passes through one.
SYS_STATS_SOURCE = """
data_sources: {
  config {
    name: "linux.sys_stats"
    sys_stats_config {
      meminfo_period_ms: 1000
      vmstat_period_ms: 1000
    }
  }
}"""

# Callstack sampling, `runner.sampling`: what a thread was running when no
# slice says, named in the round that finds it rather than one round of
# `trace {}` blocks later. Off unless asked for, because it costs the app time.
#
# The first two choices were settled by recording on a device (SM-A515F,
# Android 13, a `user` build, the platform's own traced_perf):
#
# - No process filter. traced_perf judges a new process once, by its
#   /proc/pid/cmdline at its first sample, and one that does not match is
#   dropped for the rest of the session. A process just forked from zygote
#   still carries zygote's name, so `scope { target_cmdline }` lost the cold
#   start in four recordings of seven and caught nothing of it at all. Without
#   the filter it was caught three times of three. Every process is sampled
#   then, and only a profileable or debuggable one gets a stack, so the trace
#   holds the app's stacks and little else.
# - User frames only. Kotlin and Java frames came through by name, whether
#   interpreted, JIT-compiled or compiled ahead of time; the kernel's frames
#   name nothing a project can change.
#
# The third is a buffer of its own: samples that fill it stop there, and never
# push out the sched and atrace events every detector reads.
#
# Doubled braces, like the template: this one goes through str.format for
# the rate before it is substituted.
PERF_BUFFER = "buffers: {{ size_kb: {buffer_kb} fill_policy: DISCARD }}\n"
PERF_SOURCE = """
data_sources: {{
  config {{
    name: "linux.perf"
    target_buffer: 1
    perf_event_config {{
      timebase {{ frequency: {hz} }}
      callstack_sampling {{ kernel_frames: false }}
    }}
  }}
}}"""

# `sampling: true` means this rate. On the A51 a cold start of 1.36 s,
# recorded the way `collect` records it, took 186 ms longer at this rate than
# without a sampler (95% sure: 68 to 263). At 250 Hz, filtered to the app, it
# had cost much the same, 156 ms: the sampler costs by being there more than
# by its rate. What the rate decides is whether the unwinding keeps up. At
# 100 Hz every one of the app's samples in the window came with a stack,
# wherever the recording got stacks at all; at 250 Hz 77 to 85% did, and at
# 1 kHz one in seven came without, every one of them in the busiest seconds.
# Perfetto advises staying under 200 Hz for Java and Kotlin stacks, which are
# expensive to unwind.
SAMPLING_HZ = 100
SAMPLING_ADVISED_MAX_HZ = 200

DEVICE_TRACE = "/data/misc/perfetto-traces/echolot.pftrace"

# Config keys that describe a recording we make ourselves. In gradle mode the
# macrobenchmark has already made it, so every one of these is inert — and
# they are the keys most likely to be copied in from a launch-mode config and
# believed.
RECORDING_KNOBS = ("environment", "atrace_categories", "buffer_kb",
                   "duration_ms", "reset_policy", "sampling")


class RunnerError(Exception):
    """A device or scenario problem, said as a sentence rather than a traceback.

    `gist` is the one line of it worth keeping where there is room for no
    more: the `collect` line of `echolot`, and the head of the run log's
    `error`. It is the first line unless whoever raised it knows better, and
    for a scenario command that failed it does. That message opens with "the
    scenario command returned 1:" every time, the progress file used to keep
    exactly that line, and so after any gradle failure at all the `collect`
    line of `echolot` named no cause. See `failure_gist`.
    """

    def __init__(self, message: str = "", gist: str | None = None):
        super().__init__(message)
        first = next((ln.strip() for ln in message.splitlines() if ln.strip()), "")
        self.gist = gist or first


class AdbNotFound(RunnerError):
    """adb itself is missing: a fact about this machine, not about a device.

    A class of its own because `scan` answers it differently from every other
    failure. No adb is worth one quiet note; an adb that is there and timed
    out, or refused, is a problem worth its own words — and both used to read
    "adb is not on PATH".
    """


# --- what a failed scenario command said, and what to do about it -------------
#
# Gradle writes the useful half of a failure to stdout — the instrumentation's
# own exception, the benchmark's refusal — and the boilerplate half to stderr
# ("FAILURE: Build failed with an exception"). `run_command` used to print
# stderr when there was any, which on every macrobenchmark failure was the
# half that says nothing. The lines that matter are picked out of both.
_INTERESTING = re.compile(
    r"FAILED|What went wrong|Exception|Error|ERRORS|checksum|suppress|"
    r"not found|No online|offline|denied|INSTALL_FAILED", re.IGNORECASE)
_BOILERPLATE = re.compile(
    r"^\s*(\*|>)?\s*(Try:|Run with --|Get more help|BUILD FAILED|"
    r"Deprecated Gradle|You can use|See https://docs\.gradle)")

# Failures a macrobenchmark run produces by the shape of the device or the
# build rather than by anything in the scenario, each with the one thing that
# fixes it. Every entry came off a real run, and each cost an agent a
# round of reading gradle output to arrive at the same sentence.
KNOWN_FAILURES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"perfetto ?sdk|libtracing_perfetto|binary (verification|version|missing)",
                re.IGNORECASE),
     "the Perfetto SDK half of the benchmark's tracing could not be set up in the "
     "app — usually a stale libtracing_perfetto.so in its code_cache. Reinstall "
     "the app or `adb shell pm clear <package>` where the device allows it; or "
     "turn that half off and keep the atrace half, which still carries the "
     "app's own sections: add "
     "-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark."
     "perfettoSdkTracing.enable=false to runner.gradle_args"),
    (re.compile(r"ERRORS \(not suppressed\)|suppressErrors", re.IGNORECASE),
     "the benchmark refuses this device or its state (EMULATOR, LOW-BATTERY, "
     "UNLOCKED, DEBUGGABLE …). Fix the state it names, or add "
     "-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark."
     "suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED to runner.gradle_args"),
    (re.compile(r"No online devices|no devices/emulators found|device offline|"
                r"DeviceException|No connected devices", re.IGNORECASE),
     "gradle found no device it could use: `adb devices` should list one as "
     "`device`, and a serial named with --device or runner.device — which "
     "reaches gradle as ANDROID_SERIAL — has to be one of those listed"),
    (re.compile(r"INSTALL_FAILED|Installation failed|signatures do not match",
                re.IGNORECASE),
     "the APK did not install: uninstall the app on the device first — a build "
     "signed differently cannot go over the one that is there"),
]


def failure_lines(out: str, err: str, limit: int = 20) -> list[str]:
    """The lines of a failed command worth reading, out of both streams.

    Both, in order, deduplicated: the instrumentation error on stdout and
    the "What went wrong" block on stderr are two halves of one story. When
    nothing matches, the tail of whatever was printed — a failure that says
    nothing recognisable is still a failure.
    """
    picked: list[str] = []
    seen: set[str] = set()
    for stream in (out or "", err or ""):
        for line in stream.splitlines():
            text = line.strip()
            if not text or text in seen or _BOILERPLATE.match(line):
                continue
            if _INTERESTING.search(text):
                seen.add(text)
                picked.append(text[:300])
    if not picked:
        tail = (err or out or "").strip().splitlines()
        picked = [ln.strip()[:300] for ln in tail[-limit:] if ln.strip()]
    return picked[-limit:]


def hints(text: str) -> list[str]:
    """What to do, for every known failure the text names."""
    return [hint for pattern, hint in KNOWN_FAILURES if pattern.search(text or "")]


def _fixes(lines: list[str], out: str, err: str) -> list[str]:
    return hints("\n".join(lines) + "\n" + (out or "")[-4000:] + (err or "")[-4000:])


def failure_message(command: str, code: int, out: str, err: str) -> str:
    lines = failure_lines(out, err)
    msg = f"the scenario command returned {code}:\n  {command[:300]}\n"
    msg += "\n".join(f"  {ln}" for ln in lines)
    for hint in _fixes(lines, out, err):
        msg += f"\n→ {hint}"
    return msg


# An exception with its message, or an error line of adb's own:
# `java.lang.IllegalStateException: Issue while enabling …`, `adb: error: …`.
# Gradle's frame around it — "Execution failed for task", "What went wrong"
# — says where the failure happened, which the command line already said.
_THROWN = re.compile(r"(Exception|Error)\b\s*:\s*\S", re.IGNORECASE)


def failure_gist(code: int, out: str, err: str) -> str:
    """The one line of a failed command that says why, and what fixes it.

    Of the lines `failure_lines` picked: one a known failure matches, else an
    exception with its message, else the first that looked interesting, else
    the last thing the command printed. Then every hint, whole — the hint is
    the half an agent acts on, and the run log cuts what it keeps at 800
    characters, so it goes near the front rather than after twenty lines of
    gradle.
    """
    lines = failure_lines(out, err)
    said = ([ln for ln in lines if hints(ln)]
            or [ln for ln in lines if _THROWN.search(ln)]
            or [ln for ln in lines if _INTERESTING.search(ln)]
            or lines[-1:]
            or [f"the scenario command returned {code} and printed nothing"])[0]
    return " → ".join([said[:200], *_fixes(lines, out, err)])


# --- where a collect stands, for whoever asks while it runs -------------------
#
# A macrobenchmark round is minutes of silence: fifteen iterations of a
# cold start, a gradle build in front of them. The agent that started it
# waits on a background task and asks `echolot` what is going on, and the
# answer used to be the trace count from the last run. This file is the
# answer: written when the run starts, once per iteration, and when it ends,
# with why, when it ended badly. `status` reads it.
PROGRESS_FILE = Path(".echolot") / "log" / "collect.json"


class Progress:
    """The collect in flight, as a file `status` can read."""

    def __init__(self, path: Path):
        self.path = path
        self.state: dict = {}

    def update(self, **fields) -> None:
        self.state.update(fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # Progress is a courtesy; a collect that cannot write it still
            # collects.
            pass


def _run(args: list[str], stdin: str | None = None, timeout: int = 120) -> str:
    try:
        done = subprocess.run(args, input=stdin, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise AdbNotFound(
            "adb not found. It ships in the Android SDK platform-tools; "
            "make sure that directory is on PATH."
        ) from None
    except subprocess.TimeoutExpired:
        raise RunnerError(
            f"no answer within {timeout}s from: {' '.join(args)}") from None
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()
        # The last line is the one that says why: adb's `error: …`, or
        # perfetto's own complaint under the lines it logs on the way.
        last = said.splitlines()[-1].strip() if said else f"exit {done.returncode}"
        raise RunnerError(f"{' '.join(args)}\n{said}",
                          gist=f"{' '.join(args)[:120]}: {last[:200]}")
    return done.stdout


# One state in adb's listing is more than a word. On Linux a device whose USB
# node this user may not open reads `no permissions (missing udev rules? user
# is in the plugdev group); see [http://developer.android.com/tools/device.html]`,
# and a split on whitespace made that a device in state `no` — which the
# message then repeated back as "state is no, expected device".
_NO_PERMISSIONS = re.compile(
    r"^no permissions(?P<detail>\s*(\([^)]*\))?\s*(;?\s*see \[[^\]]*\])?)")


def parse_devices(out: str) -> list[dict]:
    """`adb devices`, with or without -l, as serial, state, detail and props.

    A line is the serial, a tab and the state; with -l the serial is padded
    with spaces and `key:value` pairs follow the state. What adb prints
    before its header — `* daemon started successfully` — is not a device,
    and a carriage return is not part of a state: Windows ends every line
    with one.
    """
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("List of devices"):
            lines = lines[i + 1:]
            break
    found = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("*"):
            continue
        serial, _, rest = text.partition("\t" if "\t" in text else " ")
        rest = rest.strip()
        detail = ""
        m = _NO_PERMISSIONS.match(rest)
        if m:
            state, detail, rest = "no permissions", m.group("detail").strip(), rest[m.end():]
        else:
            state, _, rest = rest.partition(" ")
        if not state:
            continue
        props = dict(p.split(":", 1) for p in rest.split() if ":" in p)
        found.append({"serial": serial, "state": state, "detail": detail,
                      "props": props})
    return found


def devices() -> list[tuple[str, str]]:
    """(serial, state) for every device adb lists — see parse_devices."""
    return [(d["serial"], d["state"])
            for d in parse_devices(_run(["adb", "devices"], timeout=30))]


def pick_device(serial: str | None = None) -> str:
    """Picks a device, and explains what to do when there is nothing to pick.

    `serial` is `--device`, or `runner.device` when the flag is absent — the
    caller passes whichever was given. Without either, the one attached
    device in state `device` is the answer, and anything else is a sentence:
    none, several, or one that adb lists but cannot use.
    """
    found = parse_devices(_run(["adb", "devices"], timeout=30))
    if serial:
        for d in found:
            if d["serial"] == serial:
                if d["state"] != "device":
                    raise RunnerError(_state_hint(d["serial"], d["state"], d["detail"]))
                return serial
        raise RunnerError(
            f"device {serial} is not among the connected ones: "
            f"{', '.join(d['serial'] for d in found) or 'none'}")

    ready = [d["serial"] for d in found if d["state"] == "device"]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        if not found:
            raise RunnerError(
                "adb sees no devices at all. Plug in a phone or start an "
                "emulator.")
        # The most common case with a real phone — and the fix is not ours.
        raise RunnerError("; ".join(_state_hint(d["serial"], d["state"], d["detail"])
                                    for d in found))
    raise RunnerError(
        f"several devices connected: {', '.join(ready)}. "
        f"Pick one with --device <serial>, or runner.device in local.yml.")


def _state_hint(serial: str, state: str, detail: str = "") -> str:
    if state == "unauthorized":
        return (f"{serial}: debugging not authorised. The device screen should "
                f"be showing an 'Allow USB debugging?' prompt — accept it and "
                f"retry")
    if state == "offline":
        return (f"{serial}: device is offline. Usually cured by replugging the "
                f"cable or running `adb kill-server`")
    if state == "no permissions":
        return (f"{serial}: adb may not open this device"
                f"{f' — {detail}' if detail else ''}. On Linux that is the udev "
                f"rules: add one for the phone's USB vendor, replug the cable "
                f"and run `adb kill-server`")
    return f"{serial}: state is {state}, expected device"


def resolve_activity(device: str, package: str) -> str:
    out = _run(["adb", "-s", device, "shell", "cmd", "package",
                "resolve-activity", "--brief", package], timeout=60)
    activity = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if "/" not in activity:
        raise RunnerError(
            f"could not determine the launcher activity for {package}. "
            f"Is the app installed? Otherwise set runner.activity explicitly.")
    return activity


# A package name and an atrace category are both substituted between quotes in
# the protobuf text above. Nothing that could end the string early has any
# business being in either — a package name is dotted identifiers, a category
# is one word — so the answer is to refuse rather than to escape.
#
# The failure it prevents is the one the template's own comment names: a config
# that does not parse on the device turns every capture into a puzzle, and the
# message would be perfetto's, about a line number in a file nobody wrote.
_NAME_SHAPE = re.compile(r"^[A-Za-z0-9_.:\-]+$")


def trace_config(package: str, duration_ms: int, categories: list[str],
                 buffer_kb: int, environment: bool = True,
                 sampling_hz: int | None = None) -> str:
    """The device's trace config. `sampling_hz` adds callstack sampling at that rate.

    The samples get a buffer the size of the main one: perfetto fills a
    buffer as it writes, so a cap nobody reaches costs nothing.
    """
    for what, value in [("project.package", package),
                        *(("runner.atrace_categories", c) for c in categories)]:
        if not _NAME_SHAPE.match(str(value)):
            raise RunnerError(
                f"{what}: {value!r} is not a name — the device's trace config "
                f"is a text format, and this would not parse there. Letters, "
                f"digits, dot, colon, dash and underscore.")
    lines = "\n".join(f'      atrace_categories: "{c}"' for c in categories)
    env = "".join(f'      ftrace_events: "{e}"\n' for e in ENVIRONMENT_EVENTS)
    return TRACE_CONFIG.format(
        package=package, duration_ms=duration_ms, categories=lines,
        buffer_kb=buffer_kb,
        environment=env if environment else "",
        sys_stats=SYS_STATS_SOURCE if environment else "",
        perf_buffer=PERF_BUFFER.format(buffer_kb=buffer_kb) if sampling_hz else "",
        perf=PERF_SOURCE.format(hz=sampling_hz) if sampling_hz else "")


def sampling(section: dict, log: Callable[[str], None] = print) -> int | None:
    """`runner.sampling` as a rate in Hz, or None when it is off.

    Off unless set: `false` or no key at all. `true` is SAMPLING_HZ; a number
    is the rate itself. A rate above what Perfetto advises is recorded as
    asked, with a warning, since only the device can say whether it keeps up.
    """
    value = section.get("sampling")
    if value is None or value is False:
        return None
    if value is True:
        return SAMPLING_HZ
    if not isinstance(value, (int, float)) or not 1 <= value < float("inf"):
        raise RunnerError(
            f"runner.sampling: {value!r} is not a rate. It is a number of Hz "
            f"in digits, {SAMPLING_HZ} being what `true` means, or `false` to "
            f"leave sampling off.")
    hz = int(value)
    if hz > SAMPLING_ADVISED_MAX_HZ:
        log(f"[!] runner.sampling: {hz} Hz is above the "
            f"{SAMPLING_ADVISED_MAX_HZ} Perfetto advises for Java and Kotlin "
            f"stacks. In the busiest stretches the device falls behind "
            f"unwinding them, and those samples arrive without a stack.")
    return hz


def run_command(command: str, timeout: float, knob: str = "runner.timeout_s",
                cwd: Path | None = None,
                env: dict[str, str] | None = None) -> float:
    """Lets something else drive the scenario while we record the trace.

    The command comes from the project's own config — the same level of trust
    as the gradle task next to it. Anything that can move the app goes here:
    adb input, uiautomator, maestro, your own script.

    `cwd` is where it runs. Gradle mode passes `runner.project_root`, because
    `./gradlew` is a path and a relative path means nothing until you say what
    it is relative to. Without it the wrapper resolved against wherever
    `echolot` happened to be started, which is the directory holding
    `echolot.yml` and need not be the one holding the app.

    `env` is laid over the environment the command inherits. `collect` puts
    the device there as ANDROID_SERIAL, which adb and gradle's connected
    tasks both read: the scenario's own `adb shell …` then reaches the device
    being recorded, rather than failing with "more than one device" the
    moment a second one is plugged in — `--device` or not.

    Two things about the timeout, both learned the hard way.

    It is a `RunnerError` like every other failure in this module. Left as
    subprocess's own exception it came out of `echolot collect` as a traceback
    — past the `except RunnerError` that exists to turn a device problem into
    a sentence — and `reflect` then filed it under "a traceback out of the CLI
    is a bug in echolot", which it was not.

    And it kills the scenario's whole process tree, not just the shell. With
    `shell=True` the child is `sh -c`; killing that leaves what it started —
    a gradle run, an adb shell driving the app — going into the next
    iteration, where it records over the trace we are recording.
    """
    import time
    started = time.monotonic()
    if cwd is not None and not Path(cwd).is_dir():
        raise RunnerError(
            f"runner.project_root: {cwd} is not a directory. That is where the "
            f"gradle task would have run, and the wrapper is found relative "
            f"to it.")
    proc = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(cwd) if cwd is not None else None,
        env={**os.environ, **env} if env else None,
        # POSIX only, and the reason the kill below can reach the whole tree.
        start_new_session=(os.name == "posix"))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise RunnerError(
            f"the scenario command was still running after {timeout:g}s and "
            f"was stopped:\n  {command[:300]}\n"
            f"Raise {knob} if it honestly takes that long.",
            gist=f"the scenario command was still running after {timeout:g}s "
                 f"and was stopped → raise {knob} if it honestly takes that long"
        ) from None
    if proc.returncode != 0:
        raise RunnerError(failure_message(command, proc.returncode, out, err),
                          gist=failure_gist(proc.returncode, out, err))
    return time.monotonic() - started


def _kill_tree(proc: subprocess.Popen) -> None:
    """The scenario and everything it started. Falls back to the shell alone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, AttributeError):
        proc.kill()


def harvest(search_root: Path, since: float, out_dir: Path,
            name: str, before_copy: Callable[[], None] | None = None) -> list[dict]:
    """Collects the traces a macrobenchmark wrote by itself.

    It drops them per iteration into an artifact directory whose path depends
    on the build variant and the device model — awkward to find by hand. We
    take everything that appeared after the run started.

    `before_copy` runs once there is something to copy and before the first
    copy lands: `collect` makes room there, by setting the previous set
    aside. A run that found nothing leaves that set where it was.
    """
    # The modification time is read once and carried, rather than read again
    # in the sort key. Gradle is still tidying up while this walks its output
    # directory, and a file that goes away between the two reads took the
    # whole `collect` with it — an OSError from a lambda, past the
    # `except RunnerError` that exists to turn a device problem into a
    # sentence.
    found = []
    for pattern in ("*.perfetto-trace", "*.pftrace"):
        for path in search_root.rglob(pattern):
            try:
                when = path.stat().st_mtime
            except OSError:
                continue
            if when >= since:
                found.append((when, path))
    found.sort()
    if found and before_copy is not None:
        before_copy()

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (_when, src) in enumerate(found):
        dst = out_dir / f"{name}_iter{i:03d}.perfetto-trace"
        dst.write_bytes(src.read_bytes())
        results.append({"path": dst, "size": dst.stat().st_size,
                        "source": src})
    return results


# What `collect` names a trace it wrote, read back by set_aside to tell
# whose a file is.
_ITERATION = re.compile(r"^(?P<scenario>.+)_iter\d+\.perfetto-trace$")


def set_aside(out_dir: Path, name: str,
              log: Callable[[str], None] = print) -> Path | None:
    """Moves an existing set of `<name>_*` traces out of the way.

    The set before a change is the baseline the whole hunt compares against,
    and it is the first thing lost: a re-record writes the same file names.
    So a previous set is never overwritten — it moves into a sibling
    directory stamped with when it was recorded, and the log says where.
    Returns that directory, or None when there was nothing to move.

    Everything of this scenario moves, not only what `collect` itself wrote.
    It used to take `<name>_iter*` alone, and a probe capture a setup had
    saved beside them — `coldStart_probe_2026-09-05.perfetto-trace` — stayed
    behind through every later round. The documented
    `analyze .echolot/traces/*.perfetto-trace` then merged it with each fresh
    set: medians across four traces of two different sittings, one detector
    row coming from the stray file alone, and a report reading `Runs: 4`
    after a `collect` that had just recorded three.

    Traces of another scenario are still left alone. A project that records
    `coldStart` and `scroll` into one directory keeps both, and re-recording
    one must not sweep away the other — including a scenario whose name only
    begins with this one's. The `<name>_*` glob took `startup_warm_*` along
    with `startup_*`, so re-recording `startup` moved the warm set as well.
    A file now belongs to the longest scenario name it begins with, among
    this one and every scenario that has `_iterNNN` traces here:
    `startup_warm_iter000` is `startup_warm`'s, and so is a probe saved as
    `startup_warm_probe_…`, while `startup_probe_…` is still `startup`'s.
    """
    import time

    traces = sorted(out_dir.glob("*.perfetto-trace")) if out_dir.exists() else []
    scenarios = {name} | {m.group("scenario") for p in traces
                          if (m := _ITERATION.match(p.name))}

    def owner(p: Path) -> str | None:
        return max((s for s in scenarios if p.name.startswith(f"{s}_")),
                   key=len, default=None)

    existing = [p for p in traces if owner(p) == name]
    if not existing:
        return None
    newest = max(p.stat().st_mtime for p in existing)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(newest))
    aside = out_dir / f"{name}-{stamp}"
    n = 1
    while aside.exists():
        n += 1
        aside = out_dir / f"{name}-{stamp}-{n}"
    aside.mkdir(parents=True)
    for p in existing:
        p.rename(aside / p.name)
    log(f"previous run set aside: {len(existing)} trace(s) → {aside}/")
    return aside


RESET_POLICIES = ("force-stop", "none")


def reset_policy(section: dict, log: Callable[[str], None] = print) -> str:
    """What happens to the app between iterations — the value collect uses.

    `force-stop` (cold, the default) or `none` (warm). Anything else used to
    be announced as force-stop and run as none: `cmd_collect` printed "Using
    force-stop." while the loop here stopped the app on the exact string
    only, so a typo — `force_stop`, or an empty value — measured warm starts
    under a cold start's name. Decided once, here, and what is announced is
    what runs.

    `pm clear` is not on the list on purpose: it changes the scenario rather
    than repeating it. A cold start with an empty database and a user's cold
    start are different things.
    """
    value = section.get("reset_policy", "force-stop")
    if value in RESET_POLICIES:
        return value
    shown = "(empty)" if value in (None, "") else value
    log(f"[!] runner.reset_policy: {shown} is not supported. Available: "
        f"force-stop (cold) and none (warm). Using force-stop.")
    return "force-stop"


def _positive(section: dict, key: str, default: int) -> int:
    """A size or a duration from the runner section, as a whole number.

    `int(...)` used to be the whole check, and `duration_ms: 12s` came out of
    `collect` as a ValueError traceback. The unit is in the key's name, so
    the value is digits alone.
    """
    value = section.get(key, default)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 1 <= value < float("inf")):
        raise RunnerError(
            f"runner.{key}: {value!r} is not a positive number. The unit is "
            f"in the name; the value is digits alone.")
    return int(value)


def _listed(section: dict, key: str) -> list[str]:
    """A list from the runner section, where one string is a list of one.

    `gradle_args: "-P…"` written without brackets used to go into the
    command a character at a time, each separated by a space.
    """
    value = section.get(key) or []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def collect(package: str, out_dir: Path, iterations: int,
            section: dict | None = None,
            device: str | None = None,
            name: str = "run",
            log: Callable[[str], None] = print,
            on_set_aside: Callable[[Path], None] | None = None,
            progress: Callable[..., None] | None = None) -> list[dict]:
    """N repeats of a scenario. The mode decides who drives it.

    launch  — we do: force-stop and launch the activity. Cold start.
    command — someone else's command; we record the trace around it.
    gradle  — the macrobenchmark writes traces itself, we only collect them.

    Whatever can be checked is checked before anything is touched: the mode
    and what it needs, the numbers, the trace config, the device. A set
    already in out_dir is set aside, never overwritten (see set_aside), and
    only once the first new trace is about to take its place. It used to move
    first thing, so a collect that failed at once — no device, a typo in the
    mode — still emptied .echolot/traces of the set the next report was to
    be compared against, and a gradle build that failed after ten minutes
    did the same. `on_set_aside` is handed the directory that set went to,
    when there was one — the caller files it under the investigation it
    belongs to. Without it the return value was dropped here and a
    multi-round hunt kept no record of the rounds it reasoned from.

    `device` is `--device`, else `runner.device`. Launch and command modes
    pick with it (see pick_device), and a command-mode scenario gets the
    device it picked as ANDROID_SERIAL. Gradle picks for itself; a device
    named here reaches it as ANDROID_SERIAL too.

    `progress` is told where the run stands — `done` out of `iterations`
    once per iteration where we drive them, and only that it started where
    the macrobenchmark does. See `Progress`.
    """
    import time

    section = section or {}
    mode = str(section.get("mode", "launch"))
    tell = progress or (lambda **kw: None)
    tell(scenario=name, mode=mode, started=time.time(), pid=os.getpid(),
         iterations=None if mode == "gradle" else iterations, done=0)

    moved = False

    def make_room() -> None:
        nonlocal moved
        if not moved:
            moved = True
            aside = set_aside(out_dir, name, log)
            if aside is not None and on_set_aside is not None:
                on_set_aside(aside)

    if mode == "gradle":
        task = section.get("gradle_task")
        if not task:
            raise RunnerError("runner.mode: gradle needs runner.gradle_task")
        root = Path(section.get("project_root", "."))
        timeout = _positive(section, "timeout_s", 3600)
        command = " ".join([str(section.get("gradle", "./gradlew")), str(task),
                            *_listed(section, "gradle_args")])
        log(f"gradle: {command}")
        log(f"  in {root.resolve()}")
        env = None
        if device:
            # The connected test tasks run on every device adb lists unless
            # ANDROID_SERIAL names one. `--device` and runner.device used to
            # stop at the other two modes, and the hint for gradle's own "no
            # device" failure recommended runner.device to a mode that never
            # read it.
            env = {"ANDROID_SERIAL": str(device)}
            log(f"  on {device} (ANDROID_SERIAL)")
        elif os.environ.get("ANDROID_SERIAL"):
            log(f"  on {os.environ['ANDROID_SERIAL']} (ANDROID_SERIAL, from the environment)")
        ignored = [k for k in RECORDING_KNOBS if k in section]
        if ignored:
            # Nothing here builds a trace config: the macrobenchmark wrote
            # these traces and chose what went into them. Said out loud
            # because the knobs look like they apply and do not — on the first
            # real run of this mode, `environment: true` sat in the config
            # while the report came back with the thermal counters missing,
            # and the config was not the reason for either half of that.
            log(f"  [!] the macrobenchmark chose what to record, so "
                f"{', '.join('runner.' + k for k in ignored)} "
                f"{'does' if len(ignored) == 1 else 'do'} not apply here. "
                f"What the traces carry is up to the benchmark's own perfetto "
                f"config; the report says which platform state it found.")
        since = time.time()
        spent = run_command(command, timeout=timeout, knob="runner.timeout_s",
                            cwd=root, env=env)
        # A build that failed raised above, and one that wrote nothing
        # copies nothing: either way the previous set stays where it was.
        results = harvest(root, since, out_dir, name, before_copy=make_room)
        if not results:
            raise RunnerError(
                f"gradle finished but no new traces appeared under "
                f"{root.resolve()}. Check that the task really is a "
                f"macrobenchmark, that it actually ran, and that "
                f"runner.project_root points at the project that wrote them.")
        log(f"  traces collected: {len(results)} in {spent:.0f}s")
        for r in results:
            log(f"    {r['path'].name}  {r['size'] / 1e6:.1f} MB")
        return results

    if mode not in ("launch", "command"):
        raise RunnerError(
            f"unknown runner.mode: {mode}. Available: launch, command, gradle.")
    command = section.get("command")
    if mode == "command" and not command:
        raise RunnerError("runner.mode: command needs runner.command")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise RunnerError(
            f"-n / runner.iterations: {iterations!r} is not a number of "
            f"repeats. One or more, in digits.")
    duration_ms = _positive(section, "duration_ms", 12000)
    reset = reset_policy(section, log)
    hz = sampling(section, log)
    config = trace_config(package, duration_ms,
                          _listed(section, "atrace_categories") or DEFAULT_CATEGORIES,
                          _positive(section, "buffer_kb", 131072),
                          environment=bool(section.get("environment", True)),
                          sampling_hz=hz)

    dev = pick_device(device)
    activity = None
    if mode == "launch":
        activity = section.get("activity") or resolve_activity(dev, package)
        log(f"device {dev}, activity {activity}")
    else:
        log(f"device {dev}, scenario: {command}")
    if hz:
        log(f"  sampling callstacks at {hz} Hz (runner.sampling): the app "
            f"runs slower for it, so compare this set only with another "
            f"sampled at the same rate")

    results = []
    for i in range(iterations):
        out = out_dir / f"{name}_iter{i:03d}.perfetto-trace"
        if reset == "force-stop":
            _run(["adb", "-s", dev, "shell", "am", "force-stop", package])
        _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
        _run(["adb", "-s", dev, "shell", "perfetto", "-c", "-", "--txt",
              "-o", DEVICE_TRACE, "--background-wait"],
             stdin=config, timeout=120)

        info: dict = {}
        if mode == "launch":
            info = _launch(dev, activity)
        else:
            spent = run_command(command, timeout=duration_ms // 1000 + 300,
                                knob="runner.duration_ms",
                                env={"ANDROID_SERIAL": dev})
            if spent * 1000 > duration_ms:
                log(f"  [!] the scenario ran {spent:.0f}s against a "
                    f"{duration_ms / 1000:.0f}s recording window — the trace "
                    f"covers only its beginning")

        _run(["adb", "-s", dev, "shell",
              "while pidof perfetto > /dev/null; do sleep 0.5; done"],
             timeout=duration_ms // 1000 + 300)
        # A trace is recorded and about to take the first name: the moment
        # the previous set has to make room, and not a moment earlier.
        make_room()
        info.update(_pull(dev, out))
        results.append(info)

        extra = ""
        if info.get("total_time_ms") is not None:
            extra = f", am start -W: {info['total_time_ms']} ms"
        if info.get("launch_state"):
            extra += f" ({info['launch_state']})"
        log(f"  [{i + 1}/{iterations}] {out.name}  "
            f"{info['size'] / 1e6:.1f} MB{extra}")
        tell(done=i + 1)
    return results


def _launch(device: str, activity: str) -> dict:
    started = _run(["adb", "-s", device, "shell", "am", "start", "-W",
                    "-n", activity], timeout=120)
    info: dict = {}
    for key, field in (("TotalTime", "total_time_ms"),
                       ("LaunchState", "launch_state")):
        m = re.search(rf"^{key}:\s*(.+)$", started, re.MULTILINE)
        if m:
            value = m.group(1).strip()
            info[field] = int(value) if value.isdigit() else value
    return info


def _pull(device: str, out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["adb", "-s", device, "pull", DEVICE_TRACE, str(out_path)],
         timeout=300)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RunnerError(
            f"the trace did not arrive or is empty: {out_path}. "
            f"Check that perfetto is permitted on the device.")
    return {"path": out_path, "size": out_path.stat().st_size}
