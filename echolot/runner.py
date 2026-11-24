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

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import files, recording, sandbox
from .config import FLAG, INTEGER, NUMBER, SHAPES, TEXT, TEXTS, Key, key

# Default atrace categories. sched in ftrace_events is mandatory: without it
# there is no thread_state, so runnable_starvation, uninstrumented_cpu and
# io_wait go silent, anr_risk keeps only its slice source, and the clock
# weighting in environment.sql loses its input.
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
# The keys of `runner:` read here, with the two `collect` in main.py reads
# before it calls here, `iterations` and `device`. stabilize.py,
# composenames.py and power.py declare the rest; Config.load refuses any
# other key by name, so `iteratons: 3` is not five repeats without a word
# (#737, #828).
MODE = key("runner", "mode", TEXT, "launch",
           "How a repeat is recorded: launch, command or gradle.")
ITERATIONS = key("runner", "iterations", INTEGER, 5,
                 "How many repeats `collect` records; `-n` wins over it.")
DEVICE = key("runner", "device", TEXT, None,
             "The serial of the device to record on; `--device` wins over it.")
ACTIVITY = key("runner", "activity", TEXT, None,
               "The activity launch mode starts; the launcher's when left out.")
COMMAND = key("runner", "command", TEXT, None,
              "What command mode runs on the host for each repeat.")
DURATION_MS = key("runner", "duration_ms", NUMBER, 12000,
                  "How long each repeat records, in milliseconds.")
RESET_POLICY = key("runner", "reset_policy", TEXT, "force-stop",
                   "What happens between repeats: force-stop or none.")
SAMPLING = key("runner", "sampling", SHAPES, None,
               "Stack sampling: true, false, or a rate in Hz.")
ATRACE_CATEGORIES = key("runner", "atrace_categories", TEXTS, None,
                        "The atrace categories recorded, over the default ones.")
BUFFER_KB = key("runner", "buffer_kb", INTEGER, 131072,
                "Perfetto's buffer, in kilobytes.")
ENVIRONMENT = key("runner", "environment", FLAG, True,
                  "Whether thermal, frequency and battery counters are recorded.")
GRADLE_TASK = key("runner", "gradle_task", TEXT, None,
                  "The macrobenchmark task gradle mode runs.")
GRADLE = key("runner", "gradle", TEXT, "./gradlew",
             "The gradle wrapper gradle mode runs.")
GRADLE_ARGS = key("runner", "gradle_args", TEXTS, None,
                  "The arguments after the task, each passed as one.")
PROJECT_ROOT = key("runner", "project_root", TEXT, ".",
                   "Where gradle mode runs, from the config's directory.")
TIMEOUT_S = key("runner", "timeout_s", NUMBER, 3600,
                "How long gradle mode waits for its task, in seconds.")

RECORDING_KNOBS = (ENVIRONMENT.name, ATRACE_CATEGORIES.name, BUFFER_KB.name,
                   DURATION_MS.name, RESET_POLICY.name, SAMPLING.name, "compose_names")

# What `runner.compose_names` adds to the recording: the Perfetto SDK's events
# in the category Compose writes to, and nothing any other SDK on the device
# sends (see composenames.py).
TRACK_EVENT_SOURCE = """
data_sources: {
  config {
    name: "track_event"
    track_event_config {
      disabled_categories: "*"
      enabled_categories: "rendering"
    }
  }
}
"""

# `perfetto --background-wait` returns once every data source has started,
# and a `track_event` source waits on every process on the device that has
# the Perfetto SDK. Frozen background apps never answer: on an SM-A515F it
# came back after 22 s, past the end of a 12 s recording, and the launch
# was missed whole. So a recording with composable names detaches at once,
# and the scenario starts this long after — every one of seven cold starts
# on that device was in its recording.
TRACK_EVENT_START_S = 2


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
# A warning the build printed on its way, long before anything failed: D8's
# `WARNING: …/isoparser-1.9.41.jar: D8: … error: Could not assign …`, Kotlin's
# `w: …`. Words like "error" are in them, and the first such line was taken
# for the cause of a test that failed minutes later (#521).
_WARNING = re.compile(r"^\s*(WARNING\b|w:\s)")

# Failures a macrobenchmark run produces by the shape of the device or the
# build rather than by anything in the scenario, each with the one thing that
# fixes it. Every entry came off a real run, and each cost an agent a
# round of reading gradle output to arrive at the same sentence.
def _suppress(text: str) -> str:
    """The suppressErrors hint, with the names the benchmark refused.

    A fixed list suppressed nothing that failed for `DEBUGGABLE`, and the
    next run failed the same way after a full gradle build.
    """
    m = re.search(r"ERRORS \(not suppressed\):\s*([A-Z][A-Z0-9_,\- ]*)", text or "")
    names = ",".join(n.strip() for n in m.group(1).split(",") if n.strip()) if m else ""
    return ("the benchmark refuses this device or its state (EMULATOR, LOW-BATTERY, "
            "UNLOCKED, DEBUGGABLE …). Fix the state it names, or add "
            "-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark."
            f"suppressErrors={names or '<the names it lists>'} to runner.gradle_args")


# Each hint is a sentence, or a function of the failure's text for one that
# has to quote it.
KNOWN_FAILURES: list[tuple[re.Pattern, str | Callable[[str], str]]] = [
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
     _suppress),
    # The phrases that say there is no device. A bare `DeviceException` is
    # not one: AGP wraps a failed install in it, and that failure was sent
    # to `adb devices` before it was told to uninstall the app.
    (re.compile(r"No online devices|no devices/emulators found|device offline|"
                r"No connected devices", re.IGNORECASE),
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
            if not text or text in seen or _BOILERPLATE.match(line) or _WARNING.match(line):
                continue
            if _INTERESTING.search(text):
                seen.add(text)
                picked.append(text[:300])
    if not picked:
        # The first stream with anything in it: a stderr of one newline is
        # truthy, and the cause on stdout was reported as nothing printed.
        tail = ((err or "").strip() or (out or "").strip()).splitlines()
        picked = [ln.strip()[:300] for ln in tail[-limit:] if ln.strip()]
    return picked[-limit:]


def hints(text: str) -> list[str]:
    """What to do, for every known failure the text names."""
    return [hint(text) if callable(hint) else hint
            for pattern, hint in KNOWN_FAILURES if pattern.search(text or "")]


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
# A JVM class that was thrown, by its name: `SetupHelper$SetupException: …`,
# `AssertionError: …`. A lowercase `error:` is a word in a sentence as often.
_CLASS_THROWN = re.compile(r"[A-Za-z0-9_$.]*[a-z0-9_$](Exception|Error)\s*:\s*\S")
# Gradle's line for a test that failed; what the test threw comes after it.
_TEST_FAILED = re.compile(r"\bFAILED\s*$")


def failure_gist(code: int, out: str, err: str) -> str:
    """The one line of a failed command that says why, and what fixes it.

    Of the lines `failure_lines` picked: one a known failure matches, else
    what was thrown — a JVM exception by its class name, the first after a
    test's `FAILED` line before any other — else a lowercase `error:` line,
    else the first that looked interesting, else the last thing the command
    printed. Then every hint, whole — the hint is the half an agent acts on,
    and the run log cuts what it keeps at 800 characters, so it goes near the
    front rather than after twenty lines of gradle.
    """
    lines = failure_lines(out, err)
    failed = next((i for i, ln in enumerate(lines) if _TEST_FAILED.search(ln)), None)
    after = lines[failed + 1:] if failed is not None else []
    said = ([ln for ln in lines if hints(ln)]
            or [ln for ln in after if _CLASS_THROWN.search(ln)]
            or [ln for ln in lines if _CLASS_THROWN.search(ln)]
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
        # Shared with the reader (`files.read_collect`).
        self.state: dict = {"schema": files.COLLECT_SCHEMA}

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
        if Path(args[0]).name == "adb" and sandbox.adb_refused(said):
            # An agent's sandbox refused adb's server its port on localhost.
            # What adb prints then is twenty lines of its startup log, and
            # `collect` passed them on whole: no word in them says sandbox
            # (#190). The sentence that does, and adb's own last line.
            told = sandbox.adb_message(last[:200])
            raise RunnerError(told, gist=told.splitlines()[0])
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


def _join(parts: list[str]) -> str:
    """A command line the shell hands over word for word."""
    if os.name == "posix":
        import shlex
        return shlex.join(parts)
    return subprocess.list2cmdline(parts)


def installed(device: str, package: str, run: Callable[..., str] | None = None) -> bool:
    """Whether the package is on the device, by `pm path`.

    It prints a `package:` line for each of the app's APKs, and nothing for a
    package that is not there, exiting 1 then; `|| true` keeps that from
    reading as adb failing, which still stops everything as it should. Asked
    because nothing else does when `runner.activity` names the activity:
    `am start` of an app that is not installed prints an error and exits 0,
    and `collect` recorded five traces of the device and no app (#385).
    """
    out = (run or _run)(["adb", "-s", device, "shell", f"pm path {package} || true"],
                        timeout=60)
    return any(line.startswith("package:") for line in out.splitlines())


def _gradle_phone(device: str | None) -> str | None:
    """The phone a gradle round runs on, when adb can tell; else None (#524).

    The one named (`--device`, `runner.device`, `ANDROID_SERIAL`), else the
    one device adb lists ready. With several and none named, the connected
    task runs on each, and none is asked. Gradle mode does not need adb on
    PATH, so an adb that is not there, or that fails, is a phone not asked.
    """
    named = device or os.environ.get("ANDROID_SERIAL")
    if named:
        return named
    try:
        ready = [serial for serial, state in devices() if state == "device"]
    except RunnerError:
        return None
    return ready[0] if len(ready) == 1 else None


def _gradle_look(phone: str | None, package: str):
    """Before a gradle round: whether the app is on the phone, and the
    phone's power (screen.read); None for what could not be asked."""
    if not phone:
        return None, None
    from . import screen
    try:
        return installed(phone, package), screen.read(phone, _run)
    except RunnerError:
        return None, None


def _uninstalled(phone: str | None, package: str, had: bool | None,
                 log: Callable[[str], None]) -> None:
    """After a gradle round: an app that was on the phone before it and is
    not now, said (#524). Nothing is said when it was not there before: the
    round left the phone as it found it."""
    if not phone or not had:
        return
    try:
        if installed(phone, package):
            return
    except RunnerError:
        return
    log(f"  [!] {package} is no longer installed on {phone}: the connected test "
        f"task uninstalls the app it tested when it ends, passed or failed, and "
        f"the install that was there went with it, its data too. Install the app "
        f"again before a launch or command round; "
        f"-P{KEEP_INSTALLED}=true in runner.gradle_args keeps it installed "
        f"(Android Gradle plugin 8.2 and later).")


# The Android Gradle plugin's switch, from 8.2, for leaving the app and its
# test APK on the device after a connected test task. Not passed by default:
# what is on the phone after a round stays what the plugin documents, and the
# line above says what it did.
KEEP_INSTALLED = "android.injected.androidTest.leaveApksInstalledAfterRun"


# --- the build on the phone against the markers in the code (#393) -------------

@dataclass(frozen=True)
class Planted:
    """The temporary markers in the code, which the build on the phone must have.

    `names` maps each to the files it is in, as `mark.planted` lists them.
    `install` is the step that puts them on the phone, in the words `mark
    --apply` ends with: "build and install the app with `./gradlew …`".
    """

    prefix: str
    names: dict[str, list[str]]
    install: str


# Run on the phone, where the build is. A marker's name is a string constant in
# the app's code, and every string there ends with a zero byte: `grep -z` reads
# one string at a time, and `-o` prints the name from the prefix to its end.
# What could make the answer wrong is put in the answer (`_build_names_read`).
# The first line hands the same grep a string whose answer is known; `unzip -l`
# counts each APK's code files; the last line is grep's exit status. A phone
# without unzip, a grep without -z, an APK the shell may not read: each reads
# as "could not tell", never as a build without the markers. Measured on an
# A51, Android 13: 30 MB of code in a large Kotlin app's APK, about 2 s.
#
# How grep ends what `-z -o` prints is its own (#907): toybox on Android, like
# GNU grep, ends each name with the zero byte it was read by, and BSD grep, on
# a Mac, with a line break. The reader takes either. The lines the script
# writes itself are kept apart from grep's: each APK's line is written before
# grep starts, not while it may hold half a name unwritten, and a line break
# ends grep's output and the test's before the next line.
_BUILD_NAMES = """\
printf 'x\\000\\021%secholot\\000y\\000' {prefix} | grep -a -z -o -E {pattern} | sed 's/^/echolot-selftest /'
echo
apks=$(pm path {package} 2>/dev/null | sed -n 's/^package://p')
for a in $apks; do
  echo "echolot-apk $(unzip -l "$a" 'classes*.dex' 2>/dev/null | tail -n 1)"
done
for a in $apks; do
  unzip -p "$a" 'classes*.dex' 2>/dev/null
done | grep -a -z -o -E {pattern}
printf '\\necholot-grep %s\\n' $?
"""

# Where one record of that answer ends: a zero byte, a line break, or the CR LF
# adb's older shell sent through a terminal. An empty record is nothing.
_RECORD_END = re.compile(r"\r\n|[\0\r\n]")


def build_names(device: str, package: str, prefix: str,
                run: Callable[..., str] | None = None) -> tuple[set[str] | None, str]:
    """Every name with the prefix in the code of the build on the device.

    None, with why, when the phone cannot say. Read on the phone rather
    than by the install's time against the files': a checkout or a
    formatter makes a file newer than a build that has its markers, an old
    APK installed late reads as new, and a time says nothing of a marker in
    a file the build leaves out.
    """
    import shlex

    script = _BUILD_NAMES.format(
        package=shlex.quote(package.split(":", 1)[0]), prefix=shlex.quote(prefix),
        pattern=shlex.quote(re.sub(r"([.\[\](){}*+?|^$\\])", r"\\\1", prefix) + ".*"))
    try:
        out = (run or _run)(["adb", "-s", device, "shell", script], timeout=180)
    except RunnerError as e:
        return None, f"the phone did not answer: {e.gist}"
    except UnicodeDecodeError:
        return None, "a name in the app's code is not text this can read"
    return _build_names_read(out, prefix)


def _build_names_read(out: str, prefix: str) -> tuple[set[str] | None, str]:
    """What `_BUILD_NAMES` printed: the names, or why they cannot be trusted.

    grep's own test is read first. An answer this misreads made every line
    after it wrong at once: on a phone whose grep ended the test's name with a
    zero byte, the APK's line was read as part of it, and the reason given was
    that `pm path` lists no APK (#907). What the test printed is in the reason.
    """
    names: set[str] = set()
    tested: str | None = None
    apks = files = 0
    status = None
    for record in _RECORD_END.split(out):
        if record.startswith("echolot-selftest "):
            tested = record[len("echolot-selftest "):]
        elif record.startswith("echolot-apk"):
            apks += 1
            m = re.search(r"(\d+)\s+files?\s*$", record)
            files += int(m.group(1)) if m else 0
        elif record.startswith("echolot-grep "):
            status = record[len("echolot-grep "):].strip()
        elif record.startswith(prefix):
            names.add(record)
    if tested != prefix + "echolot":
        printed = "nothing" if tested is None else repr(tested[:80])
        return None, (f"`grep` on the phone does not read the app's code the way this "
                      f"needs (handed a string whose answer is {prefix}echolot, it "
                      f"printed {printed})")
    if not apks:
        return None, "`pm path` lists no APK of the app"
    if not files:
        return None, "`unzip` on the phone lists no code in the app's APKs"
    if status is None:
        return None, ("the phone's answer stops before `grep` on the app's code said "
                      "how it ended")
    if status not in ("0", "1"):
        return None, f"`grep` on the phone ended with status {status} on the app's code"
    return names, ""


def unbuilt(device: str, package: str, planted: Planted,
            run: Callable[..., str] | None = None) -> tuple[list[str] | None, str]:
    """The code's markers the build on the device lacks; None, with why, when it cannot tell.

    A name the code puts together at run time is matched by its fixed start,
    the only part of it that is a string in the build.
    """
    from .mark import head

    names, why = build_names(device, package, planted.prefix, run)
    if names is None:
        return None, why
    lacking = []
    for name in planted.names:
        fixed = head(name)
        if name not in names and (fixed is None or not any(n.startswith(fixed) for n in names)):
            lacking.append(name)
    return sorted(lacking), ""


def _has_line(planted: Planted) -> str:
    n = len(planted.names)
    return (f"  the build on the phone has "
            f"{'the temporary marker' if n == 1 else f'all {n} temporary markers'} in the code")


def _unread_line(planted: Planted, why: str) -> str:
    """The set goes on as before, and `analyze` is what says it afterwards."""
    n = len(planted.names)
    return (f"  [!] whether the build on the phone has "
            f"{'the temporary marker' if n == 1 else f'the {n} temporary markers'} in the "
            f"code could not be read: {why}. `analyze` says so if the traces lack "
            f"{'it' if n == 1 else 'all of them'}")


def unbuilt_sentence(device: str, package: str, planted: Planted, lacking: list[str]) -> str:
    """Why a set stops before it records: the build on the phone lacks the code's markers."""
    n, k = len(planted.names), len(lacking)
    if n == 1:
        said = f"{_lacks(planted, lacking)}, {_where(planted, lacking)},"
        them, one = "it", "it"
    else:
        said = f"{_lacks(planted, lacking)} ({_where(planted, lacking)}),"
        them, one = ("it", "it") if k == 1 else ("them", "one")
    install = planted.install[:1].upper() + planted.install[1:]
    return (f"the build of {package} on {device} {said} so no trace of it would hold "
            f"{them}. {install}, then collect again. If a build made from this code still "
            f"lacks {one}, the file it is in is not part of that build, such as a test "
            f"source or another module or flavour: move the marker, or take it out "
            f"(`echolot mark --remove` takes out every marker).")


def _lacks(planted: Planted, lacking: list[str]) -> str:
    """`has none of the 3 temporary markers in the code`, `lacks 2 of the 7 …`."""
    n, k = len(planted.names), len(lacking)
    if n == 1:
        return "lacks the temporary marker in the code"
    if k == n:
        return f"has none of the {n} temporary markers in the code"
    return f"lacks {k} of the {n} temporary markers in the code"


def _where(planted: Planted, lacking: list[str], shown: int = 5) -> str:
    """`AGENTTMP_a in app/src/main/kotlin/A.kt`, for the first few, and how many more."""
    said = [f"{name} in {', '.join(planted.names[name])}" for name in lacking[:shown]]
    if len(lacking) > shown:
        said.append(f"{len(lacking) - shown} more")
    return "; ".join(said)


def resolve_activity(device: str, package: str) -> str:
    out = _run(["adb", "-s", device, "shell", "cmd", "package",
                "resolve-activity", "--brief", package], timeout=60)
    activity = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if "/" not in activity:
        raise RunnerError(
            f"could not determine the launcher activity for {package}. "
            f"Is the app installed? Otherwise set runner.activity explicitly.")
    # A package with more than one launcher activity has no single match,
    # and the platform answers with its own chooser,
    # `android/com.android.internal.app.ResolverActivity`. Taken as the app's,
    # every repeat started the chooser and measured nothing of the app.
    if activity.split("/", 1)[0] != package:
        raise RunnerError(
            f"{package} has more than one launcher activity, and the device "
            f"answered with {activity} instead of one of them. Set "
            f"runner.activity to the one the scenario starts.")
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
                 sampling_hz: int | None = None, compose_names: bool = False) -> str:
    """The device's trace config. `sampling_hz` adds callstack sampling at that rate,
    and `compose_names` the Perfetto SDK's events that name composables.

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
        perf=PERF_SOURCE.format(hz=sampling_hz) if sampling_hz else "",
    ) + (TRACK_EVENT_SOURCE if compose_names else "")


def sampling(section: dict, log: Callable[[str], None] = print) -> int | None:
    """`runner.sampling` as a rate in Hz, or None when it is off.

    Off unless set: `false` or no key at all. `true` is SAMPLING_HZ; a number
    is the rate itself. A rate above what Perfetto advises is recorded as
    asked, with a warning, since only the device can say whether it keeps up.
    """
    value = SAMPLING.read(section)
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
    # In a group of its own, the command is out of reach of the Ctrl-C a
    # terminal sends to the foreground group, and of nothing at all when an
    # agent's harness sends echolot SIGTERM. Either way it ran on: a gradle
    # run kept driving the device under the next `collect`. Both now reach
    # the same cleanup as a timeout.
    previous = _terminate_as_exit()
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
    except BaseException:
        _kill_tree(proc)
        proc.communicate()
        raise
    finally:
        _restore(previous)
    if proc.returncode != 0:
        raise RunnerError(failure_message(command, proc.returncode, out, err),
                          gist=failure_gist(proc.returncode, out, err))
    return time.monotonic() - started


def _terminate_as_exit():
    """SIGTERM raised as `SystemExit` while a command runs, so that it unwinds
    through the cleanup rather than ending the process where it stands.
    Returns what to put back; None where a handler cannot be set."""
    def exit_(signum, _frame):
        raise SystemExit(128 + signum)
    try:
        return signal.signal(signal.SIGTERM, exit_)
    except (ValueError, OSError):   # not the main thread, or no SIGTERM here
        return None


def _restore(previous) -> None:
    if previous is not None:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError):
            pass


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
    for _when, src in found:
        # The same race as above, after the previous set was moved aside: a
        # trace gradle removed in between is skipped, and the numbering
        # counts the ones copied, so the set has no hole in it.
        try:
            data = src.read_bytes()
        except OSError:
            continue
        dst = out_dir / f"{name}_iter{len(results):03d}.perfetto-trace"
        dst.write_bytes(data)
        results.append({"path": dst, "size": len(data), "source": src})
    return results


# What `collect` names a trace it wrote, read back by set_aside to tell
# whose a file is.
_ITERATION = re.compile(r"^(?P<scenario>.+)_iter\d+\.perfetto-trace$")
# A recording `collect` set aside as not cold (#523, `_aside_path`).
_ASIDE = re.compile(r"^(?P<scenario>.+)_iter\d+-(?:warm|hot|relaunch|not-cold)(?:-\d+)?"
                    r"\.perfetto-trace$")


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
        # What `collect` kept beside it goes with it: left behind, it would
        # be the account of a trace that is no longer there, and the next
        # one of that name would take it for its own until overwritten.
        kept = recording.sidecar(p)
        if kept.exists():
            kept.rename(aside / kept.name)
    log(f"previous run set aside: {len(existing)} trace(s) → {aside}/")
    return aside


# --- what collect kept beside a trace, read back for analyze -----------------
#
# Whether `runner.cooldown` was on, and how warm the device was before each
# repeat, are facts of the recording that the trace does not hold. The report
# told a reader to "let it cool and record again" after a set that had cooled
# to 35 °C before every repeat and throttled all the same (#522). `collect`
# keeps them in the account beside each trace (recording.py).
def launch_record(trace: Path) -> dict | None:
    """How the repeat that recorded `trace` started, as `collect` kept it
    beside the trace (#619); None for a trace it did not record, or recorded
    before it kept this.

    `repeat` and `attempt` (2 and on: recorded again), `state` (`COLD`,
    `WARM`, `HOT`; None in command mode), `total_time_ms` from `am start -W`,
    `cold` (True: stopped, and the launch said COLD; False: stopped, and the
    process was running again by the start; None: nothing to judge by),
    `notes`, the lines `collect` said under the repeat's own, and
    `no_launch`, True for a repeat that started nothing because nothing
    stopped the app before it (#593, #856), absent otherwise.

    The output of a `collect` is cut by whoever reads it, and with the lines
    went the only record of which repeats were cold: an agent that had piped
    it through `tail` recorded the whole round again to read them back.
    """
    rec = recording.kept(trace, "launch")
    return rec if isinstance(rec, dict) else None


def markers_in_build(trace: Path) -> list[str] | None:
    """The temporary markers `collect` found in the build on the phone before it
    recorded `trace`; None when it did not look, or found some missing (#536)."""
    names = recording.kept(trace, "markers_in_build")
    return [str(n) for n in names] if isinstance(names, list) else None


def cooldown_record(trace: Path) -> dict | None:
    """What `collect` kept about the cooldown of the repeat that recorded `trace`.

    `{"on": True, "max_c", "sensor", "before_c"}` when it was on, `{"on": False}`
    when collect recorded the trace with it off, None when nothing says: a
    trace from gradle mode, from an older echolot, or recorded by hand.
    """
    unknown = object()
    cooldown = recording.kept(trace, "cooldown", unknown)
    if cooldown is unknown:
        return None
    if not isinstance(cooldown, dict):
        return {"on": False}
    before = cooldown.get("before_c")
    return {"on": True, "max_c": cooldown.get("max_c"), "sensor": cooldown.get("sensor"),
            "before_c": before if isinstance(before, (int, float)) else None}


RESET_POLICIES = ("force-stop", "none")


def reset_policy(section: dict, log: Callable[[str], None] = print) -> str:
    """What happens to the app between iterations — the value collect uses.

    `force-stop` (cold, the default) or `none`, which resets nothing. A
    force-stop sends the app to the background first and waits for its
    process to be gone after (see _stop). `none` is not a warm start: in
    launch mode the app is still in front from the repeat before, `am start
    -W` has nothing to start, and only the first repeat measures one.
    Anything else used to be announced as force-stop and run as none:
    `cmd_collect` printed "Using force-stop." while the loop here stopped the
    app on the exact string only, so a typo — `force_stop`, or an empty value
    — measured no start under a cold start's name. Decided once, here, and
    what is announced is what runs.

    `pm clear` is not on the list on purpose: it changes the scenario rather
    than repeating it. A cold start with an empty database and a user's cold
    start are different things.
    """
    value = RESET_POLICY.read(section)
    if value in RESET_POLICIES:
        return value
    shown = "(empty)" if value in (None, "") else value
    log(f"[!] runner.reset_policy: {shown} is not supported. Available: "
        f"force-stop (cold) and none (nothing between repeats). Using force-stop.")
    return "force-stop"


def _positive(section: dict, declared: Key) -> int:
    """A size or a duration from the runner section, as a whole number.

    `int(...)` used to be the whole check, and `duration_ms: 12s` came out of
    `collect` as a ValueError traceback. The unit is in the key's name, so
    the value is digits alone.
    """
    value = declared.read(section)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 1 <= value < float("inf")):
        raise RunnerError(
            f"{declared.dotted}: {value!r} is not a positive number. The unit is "
            f"in the name; the value is digits alone.")
    return int(value)


def _listed(section: dict, declared: Key) -> list[str]:
    """A list from the runner section, where one string is a list of one.

    `gradle_args: "-P…"` written without brackets used to go into the
    command a character at a time, each separated by a space.
    """
    value = declared.read(section) or []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def collect(package: str, out_dir: Path, iterations: int,
            section: dict | None = None,
            device: str | None = None,
            name: str = "run",
            log: Callable[[str], None] = print,
            on_set_aside: Callable[[Path], None] | None = None,
            progress: Callable[..., None] | None = None,
            planted: Planted | None = None,
            check_first: Callable[[Path], tuple[str | None, str | None]] | None = None,
            ) -> list[dict]:
    """N repeats of a scenario. The mode decides who drives it.

    launch  — we do: HOME, force-stop, and launch the activity. Cold start.
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

    `planted` is the temporary markers in the code, when it has any. Launch
    and command modes record the build on the phone, and a set from one
    that lacks them holds none of them: it stops before it records (#393).
    In command mode the scenario may install the app itself, so a build
    found lacking is read again after the first repeat, and the set stops
    there. Gradle mode builds the app from the code and is not asked.

    `check_first` reads the first repeat's trace in launch and command modes
    and says whether the scenario got where it was going (#536): `(stop,
    note)`, a reason the round stops for, or a line worth saying. A round
    whose app drew no frame, or whose end never closed, used to be recorded
    whole and accepted, and only `analyze` said so, as a build too old for
    its markers. That trace is read before it takes the set's first name: it
    waits in a folder of its own beside the set (`.<name>-first`), and the
    set before moves once the check lets the trace in. Pulled under its name
    with the set before moved for it, a round that then stopped had emptied
    .echolot/traces of that set all the same, and filed it as a round the
    investigation never got to follow (#857).

    Returned: every recording made, in order. In launch and command modes
    with a stop between repeats, one whose app was running again by its start
    carries `aside`: it was kept under a name of its own and its repeat
    recorded again (#523), and the caller leaves it out of the set. How each
    repeat started is kept beside its trace (`launch_record`, #619).
    `progress` is told the names of those set aside as `not_cold`, so that
    an interrupt can name them (#860).
    """
    import time

    from . import composenames, power, screen, stabilize

    section = section or {}
    mode = str(MODE.read(section))
    # The device's state is checked with the rest of the section, before
    # anything is touched: a typo in `cooldown` stops here, not after a build.
    settle = stabilize.wanted(section)
    names = composenames.wanted(section)
    under = power.floor(section)
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
        task = GRADLE_TASK.read(section)
        if not task:
            raise RunnerError("runner.mode: gradle needs runner.gradle_task")
        root = Path(PROJECT_ROOT.read(section))
        timeout = _positive(section, TIMEOUT_S)
        # Every part quoted, so each element of the list reaches gradle as one
        # argument with nothing expanded. Joined with spaces, the shell read
        # `$Startup` in a nested test class's name as a variable and ran the
        # benchmark under another filter, and split `-Dorg.gradle.jvmargs=…`
        # at its space. The line logged is the line run.
        command = _join([str(GRADLE.read(section)), str(task),
                         *_listed(section, GRADLE_ARGS)])
        log(f"gradle: {command}")
        log(f"  in {root.resolve()}")
        env = None
        if settle and not device:
            # To be held still the device has to be known before gradle
            # picks it, and it is picked the way gradle would: the one adb
            # lists, or the one ANDROID_SERIAL names.
            device = pick_device(os.environ.get("ANDROID_SERIAL"))
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
            if "compose_names" in ignored:
                log("      Composable names are the benchmark's to switch on: "
                    "-Pandroid.testInstrumentationRunnerArguments.androidx."
                    "benchmark.perfettoSdkTracing.enable=true in runner.gradle_args.")
        # The phone the round runs on, when adb can tell which (#524): how it
        # is powered before the task, and whether the app is still on it
        # after. The connected test task uninstalls the app it tested when it
        # ends, passed or failed, and the next launch-mode round stopped on
        # an app that was not installed, with nothing said of why.
        phone = _gradle_phone(device)
        had, began = _gradle_look(phone, package)
        if phone and began is not None:
            power.check(phone, began, under, log)
        since = time.time()
        try:
            if settle:
                with stabilize.Settled(section, str(device), run=_run, log=log,
                                       notify=tell) as settled:
                    if settled.cooldown is not None:
                        log("  cooldown: before the task only; the macrobenchmark "
                            "runs its repeats back to back")
                    settled.cool()
                    spent = run_command(command, timeout=timeout, knob="runner.timeout_s",
                                        cwd=root, env=env)
            else:
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
        except RunnerError:
            _uninstalled(phone, package, had, log)
            raise
        for r in results:
            # The macrobenchmark drove these, and how it reset the app
            # between them is its own: none of it is ours to say. Nor are
            # the device's animation scales, which a run with AGP's
            # `animationsDisabled` turns to 0 for its own length and back.
            # How the phone was powered when the task began is.
            recording.write(r["path"], {"runner": "gradle",
                                        **(power.facts(began) if began else {})})
        log(f"  traces collected: {len(results)} in {spent:.0f}s")
        for r in results:
            log(f"    {r['path'].name}  {r['size'] / 1e6:.1f} MB")
        _uninstalled(phone, package, had, log)
        return results

    if mode not in ("launch", "command"):
        raise RunnerError(
            f"unknown runner.mode: {mode}. Available: launch, command, gradle.")
    command = COMMAND.read(section)
    if mode == "command" and not command:
        raise RunnerError("runner.mode: command needs runner.command")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise RunnerError(
            f"-n / runner.iterations: {iterations!r} is not a number of "
            f"repeats. One or more, in digits.")
    duration_ms = _positive(section, DURATION_MS)
    reset = reset_policy(section, log)
    hz = sampling(section, log)
    config = trace_config(package, duration_ms,
                          _listed(section, ATRACE_CATEGORIES) or DEFAULT_CATEGORIES,
                          _positive(section, BUFFER_KB),
                          environment=bool(ENVIRONMENT.read(section)),
                          sampling_hz=hz, compose_names=names)

    dev = pick_device(device)
    activity = None
    if mode == "launch":
        if not installed(dev, package):
            raise RunnerError(
                f"{package} is not installed on {dev}. Install the build you "
                f"mean to measure, and collect again: `am start` of an app that "
                f"is not there prints an error and exits 0, and every trace "
                f"would hold the device and no app. A gradle-mode round "
                f"uninstalls the app it tested when it ends.")
        activity = ACTIVITY.read(section) or resolve_activity(dev, package)
        log(f"device {dev}, activity {activity}")
    else:
        log(f"device {dev}, scenario: {command}")
    # Markers reach a trace only through a build, and a set recorded straight
    # after `mark --apply` held none of them (#390). Read before the device
    # is held still, so that the cooldown takes the heat of the reading.
    reread = False
    # The markers the build on the phone was found to carry, kept beside the
    # set: a report that finds none of them in the traces then knows the build
    # was not the reason (#536).
    built: list[str] | None = None
    if planted:
        lacking, why = unbuilt(dev, package, planted)
        if lacking and mode == "launch":
            raise RunnerError(unbuilt_sentence(dev, package, planted, lacking))
        if lacking == []:
            built = sorted(planted.names)
            log(_has_line(planted))
        elif mode == "command":
            reread = True
            if lacking:
                log(f"  [!] the build on the phone {_lacks(planted, lacking)}; the "
                    f"scenario may install another, so it is read again after the "
                    f"first repeat")
        else:
            log(_unread_line(planted, why))
    if hz:
        log(f"  sampling callstacks at {hz} Hz (runner.sampling): the app "
            f"runs slower for it, so compare this set only with another "
            f"sampled at the same rate")
    if mode == "launch" and reset == "none" and iterations > 1:
        # Nothing stops the app between repeats, so from the second one on it
        # is still in front and `am start -W` starts nothing: a set that
        # looks recorded and holds one launch at most (#593).
        log(f"  [!] runner.reset_policy: none in launch mode: nothing stops the app "
            f"between repeats, so only the first of these {iterations} can record a "
            f"launch; the others find it still in front. For {iterations} cold "
            f"starts, use reset_policy: force-stop")
    # A launch behind a dark, locked screen starts nothing that can be
    # measured (screen.py). Read before anything is touched, and again
    # before each repeat: a set that waits to cool can outlast the screen.
    # The same read says how the phone is powered (power.py, #524).
    last = screen.ready(dev, _run, log)
    power.check(dev, last, under, log)
    # What the device is set to do with the app's animations and activities,
    # once a round and before the device is held still: kept beside every
    # trace of the round, for the report and for `compare` (#612, #616).
    # Nothing is said here: a scale of 0 is how UI tests run on purpose, and
    # "Don't keep activities" changes little in a cold start. The report says
    # it where it matters.
    device_settings = recording.settings(dev, _run)

    # Every recording, in the order made: the set's, and those set aside as
    # not cold (`aside`), which the caller counts and leaves out.
    results = []
    # A repeat the app was stopped for and that started warm all the same is
    # set aside, out of the set, and recorded again (#523). The set gets as
    # many recordings more as it has repeats, and no more: a device that
    # starts the app again after every stop would be recorded forever. Past
    # them, a repeat that is not cold leaves the set one cold start short.
    spare = iterations if reset == "force-stop" else 0
    kept = again_used = short = 0
    checked = False
    # The activity started, and the one the repeat before ended on (#909).
    asked = component(activity) if mode == "launch" else None
    ended_before: str | None = None
    # Composable names go on after the device is held still and off before
    # it is put back, so that the app is the last thing touched and the
    # first thing restored.
    switched = (composenames.Names(package, dev, run=_run, log=log) if names
                else contextlib.nullcontext())
    # Where the set's first trace waits for `check_first` (#857), swept away
    # however the round ends: a stop, an error, Ctrl-C in the middle of it.
    first = out_dir / f".{name}-first"
    with stabilize.Settled(section, dev, run=_run, log=log, notify=tell) as settled, \
            switched, _swept(first):
        cooling = settled.cooldown
        if settled.cooldown is not None and reset == "force-stop":
            log("  the app is stopped before each wait, and stopped again if it "
                "is running by the recording")
        # Which recording of the repeat this is: 2 and on, recorded again.
        attempt = 0
        while kept + short < iterations:
            # Which repeat of the set this is, counted over every one it had:
            # a repeat the spent re-recordings left short is one of them, and
            # counted with the kept alone, the next recording was taken for
            # the same repeat again, a fifth recording of repeat 1 after the
            # line that said none was left, and the last never came (#858).
            i = kept + short
            attempt += 1
            # The set's names run on without a gap: a recording set aside
            # leaves its name to the one that records the repeat again, and
            # a repeat left short leaves it to the next.
            out = out_dir / f"{name}_iter{kept:03d}.perfetto-trace"
            # HOME, the force-stop and a wait for the process to be gone (see
            # _stop), before the device cools and not after (#611). Cooled
            # with the app in front, a job it scheduled during the wait was
            # due by the force-stop, and the system started the process for
            # it 95–112 ms after the kill: on an A51, 47 of 59 repeats that
            # had waited began warm, and 0 of 51 that had not. Stopped first,
            # nothing of the app runs while the device cools.
            left, was = _stop(dev, package) if reset == "force-stop" else ("", None)
            waited = settled.cool()
            during = f"before repeat {i + 1} of {iterations}"
            now = screen.ready(dev, _run, log, during=during)
            power.between(dev, last, now, during, log)
            last = now
            # A last look right before the recording: whatever started the
            # app during the wait, it is stopped again, and the repeat says
            # so. A process back after that stop is stopped once more
            # (RESTOPS): a recording made with it there is known not to be
            # cold before it begins (#523). What is left after that is said
            # under the repeat's line.
            again = _running(dev, package) if reset == "force-stop" else ""
            restops = 0
            while again and restops < RESTOPS and (restops == 0 or left):
                left, _ = _stop(dev, package)
                restops += 1
            _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
            # With composable names, `--background-wait` waits on every SDK
            # producer on the device and outlasts the recording (see
            # TRACK_EVENT_START_S).
            _run(["adb", "-s", dev, "shell", "perfetto", "-c", "-", "--txt",
                  "-o", DEVICE_TRACE, "--background" if names else "--background-wait"],
                 stdin=config, timeout=120)
            if names:
                time.sleep(TRACK_EVENT_START_S)

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
            # Command mode: whatever the scenario installed is on the phone
            # now, and it is what this repeat recorded. Stopped before the
            # trace takes a name, as a launch that failed is.
            if reread and planted:
                reread = False
                lacking, why = unbuilt(dev, package, planted)
                if lacking:
                    _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
                    raise RunnerError(f"repeat 1 of {iterations}: "
                                      + unbuilt_sentence(dev, package, planted, lacking))
                if lacking == []:
                    built = sorted(planted.names)
                log(_has_line(planted) if lacking == [] else _unread_line(planted, why))
            # `am start` refused, an activity that is not there most often,
            # and exited 0 all the same: the repeat recorded the device and
            # no app. Stopped before the trace takes a name (#385).
            if info.get("launch_error"):
                _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
                raise RunnerError(
                    f"repeat {i + 1} of {iterations}: `am start -W -n {activity}` "
                    f"started nothing: {info['launch_error']} Check "
                    f"runner.activity, and that the build installed is the one "
                    f"it names.")
            # After a force-stop `UNKNOWN` is a launch the system measured no
            # start for. A screen that went dark or locked since the check
            # explains it, and the set stops before the trace takes a name;
            # on a screen that is on and unlocked the line says so. Without a
            # stop, `UNKNOWN` or a `TotalTime: 0` is the app still in front from
            # the repeat before: this trace holds no launch (#593).
            unmeasured = (mode == "launch"
                          and (str(info.get("launch_state", "")).startswith("UNKNOWN")
                               or (reset == "none" and info.get("total_time_ms") == 0)))
            if unmeasured and reset == "force-stop":
                now = screen.read(dev, _run)
                if now.dark or now.locked:
                    _run(["adb", "-s", dev, "shell", "rm", "-f", DEVICE_TRACE])
                    raise RunnerError(screen.unmeasured_sentence(
                        dev, now, f"repeat {i + 1} of {iterations}"))
            # Stopped, and the process was running again by the start all the
            # same (#404): `am start -W` says so for a launch, and the pid left
            # after the stop says it where `am start` says nothing.
            state = str(info.get("launch_state", ""))
            restarted = (reset == "force-stop" and not unmeasured
                         and (state in NOT_COLD or (bool(left) and state != "COLD")))
            # What is said under the repeat's line, and kept beside its trace
            # with the rest of how it started (#619).
            notes: list[str] = []
            if again:
                notes.append(_stopped_again_line(package, again, waited, restops))
            if unmeasured and reset == "none":
                info["no_launch"] = True
                notes.append("[!] am start -W measured no start: the app was still in "
                             "front, and with reset_policy: none nothing stopped it, so "
                             "this trace holds no launch")
            elif unmeasured:
                notes.append("[!] am start -W measured no start, with the screen on and "
                             "unlocked: this trace may hold none")
            if restarted:
                info["not_cold"] = True
                notes.append("[!] " + _not_cold_line(package, left))
            # Where the launch ended, against where it was started (#909).
            # Said when it ended elsewhere, unless the repeat before ended
            # there too: a splash that hands over to the app's main activity
            # does that in every repeat, and one line says it.
            ended = info.get("activity")
            if ended and asked and ended != asked and ended != ended_before:
                notes.append(_elsewhere_line(ended, asked))
            if mode == "launch" and not restarted:
                ended_before = ended
            # The set's first trace, read before it takes its name: pulled
            # beside the set, and the set before stays until the check lets
            # the trace in (#857).
            checking = not restarted and not checked and check_first is not None
            # Any other trace is recorded and about to take its name: the
            # moment the previous set has to make room, and not a moment
            # earlier. A recording set aside is the round's own, and left
            # beside the set before it would be taken for one of that set's.
            if not checking:
                make_room()
            if restarted:
                # Out of the set, under a name no glob of its repeats takes,
                # and kept: it is what the system did to the app (#523).
                info["aside"] = True
                out = _aside_path(out, state)
            pulled = first / out.name if checking else out
            info.update(_pull(dev, pulled))
            facts = {
                "runner": mode, "reset_policy": reset, "app_at_stop": was,
                **device_settings,
                "cooldown": None if cooling is None else {
                    "max_c": cooling.max_c, "sensor": cooling.sensor,
                    "before_c": (round(settled.last_c, 1)
                                 if settled.last_c is not None else None)},
                **({"markers_in_build": built} if built is not None else {}),
                **power.facts(now),
                "launch": {
                    "repeat": i + 1, "attempt": attempt,
                    "state": state or None,
                    "total_time_ms": info.get("total_time_ms"),
                    # True: stopped, and `am start -W` said COLD. False:
                    # stopped, and it was not. None: nothing to judge by.
                    "cold": (False if restarted else True
                             if mode == "launch" and reset == "force-stop"
                             and state == "COLD" else None),
                    # The activity the launch ended on, as `am start -W`
                    # said, and the one it started; None in command mode
                    # (#909).
                    "activity": info.get("activity"),
                    "asked": asked,
                    "notes": notes,
                    # With nothing stopping the app, `am start -W` measured
                    # no start, and the trace holds no launch (#593): the
                    # next step's line leaves it out (#856).
                    **({"no_launch": True} if info.get("no_launch") else {}),
                }}
            info["repeat"], info["asked"] = i + 1, asked
            recording.write(pulled, facts)
            results.append(info)

            extra = ""
            if info.get("total_time_ms") is not None:
                extra = f", am start -W: {info['total_time_ms']} ms"
            if info.get("launch_state"):
                extra += f" ({info['launch_state']})"
            log(f"  [{i + 1}/{iterations}] {out.name}  "
                f"{info['size'] / 1e6:.1f} MB{extra}")
            for note in notes:
                log("      " + note)
            if restarted:
                tell(not_cold=[r["path"].name for r in results if r.get("aside")])
                if again_used < spare:
                    again_used += 1
                    log(f"      set aside, out of the set: repeat {i + 1} is recorded "
                        f"again ({again_used} of the {spare} re-recordings the set "
                        f"is given)")
                else:
                    short += 1
                    # The next recording is the next repeat's, its first.
                    attempt = 0
                    log(f"      set aside, out of the set; the {spare} re-recordings "
                        f"the set is given are spent, so it ends one cold start short")
                continue
            kept += 1
            attempt = 0
            # One look at the first trace before the rest are recorded: a
            # round in which the scenario never reached its screen stops here,
            # with the phone still in hand (#536). The first trace of the
            # set, not a recording set aside before it.
            if checking:
                checked = True
                stop, note = check_first(pulled)
                if stop and iterations == 1:
                    # The one trace there is: kept, to look at.
                    note = (f"{stop}: the scenario did not get where it was going. "
                            f"Look at the phone before recording more")
                elif stop:
                    # The trace goes with its folder, and the set before was
                    # never moved: .echolot/traces is as the round found it.
                    raise RunnerError(
                        f"repeat {i + 1} of {iterations}: {stop}. The scenario did not "
                        f"get where it was going, and nothing more was recorded. Look "
                        f"at the phone: an error screen, no network, a dialog in the "
                        f"way. Once it shows the scenario's screen, collect again.")
                # Let in: the set before makes room, and the trace and its
                # account take the set's first name.
                make_room()
                pulled.replace(out)
                recording.sidecar(pulled).replace(recording.sidecar(out))
                info["path"] = out
                if note:
                    log(f"      [!] {note}")
                    notes.append(f"[!] {note}")
                    recording.write(out, facts)
            tell(done=kept)
    if not kept:
        # Every recording set aside: nothing to hand on as a set, and the
        # traces are where the system's doing is to be read.
        aside = ", ".join(r["path"].name for r in results)
        raise RunnerError(
            f"none of the {len(results)} recordings started cold: the app was "
            f"stopped before each, and its process was running again by its start "
            f"every time. They are set aside in {out_dir}: {aside}. `adb logcat -b "
            f"events -d | grep am_proc_start` says what the system started it for; "
            f"to read these starts as they are, analyze those traces by name.",
            gist=f"none of the {len(results)} recordings started cold: the app's "
                 f"process was running again by each start")
    return results


# How long the app's process may take to be gone after `am force-stop`, in
# polls of 0.1 s: 5 s, as long as the Macrobenchmark library waits for it.
STOP_POLLS = 50
# And how long it has to stay gone, in seconds. On an A51 the system had it
# running again 65–95 ms after the kill, a moment after it was first gone.
STOP_SETTLE_S = 0.5
# How many times the app is stopped again when the last look before a
# recording finds it running: once, and once more if that stop leaves it
# there. A recording begun with the process up is known not to be cold
# before it starts (#523).
RESTOPS = 2

# What `am start -W` calls a launch that found the process running.
NOT_COLD = ("WARM", "HOT", "RELAUNCH")


# The first line the stop's shell prints: whether the app was running when
# the stop came, before HOME and the kill.
_BEFORE = re.compile(r"^was:(running|stopped)\s*$", re.MULTILINE)


def _stop(device: str, package: str) -> tuple[str, str | None]:
    """Stops the app before a cold start: the pids of it still running after,
    if any, and whether it was running when the stop came.

    HOME first. With an activity of the app on screen, `am force-stop` was
    followed at once by the system starting the process again for that
    activity: `am_proc_start … top-activity` in the events log, 65–95 ms
    after the kill on an A51 (Android 13), before the recording began. `am
    start -W` then said WARM, and the trace held neither the process start
    nor `bindApplication` — from the second repeat on, since the repeat
    before had left the app in front (#404). An app with one process stays
    dead; one whose activity is bound to a service in a second process of
    its own came back 8 times of 8. With the app in the background there is
    no activity of it on screen to start it for: 0 of 8.

    Then the process has to be gone, which takes the kernel a moment after
    the kill, and stay gone for STOP_SETTLE_S: the old process was gone at
    the first look, and the new one was there a moment later. The wait is
    bounded, and a pid there at its end is the answer: the process is
    running again. One `adb shell` call, so that nothing of the host's comes
    in between.

    Whether it was running is `running` or `stopped`, or None when the
    answer did not say, and it goes into the account `collect` keeps beside
    the trace (recording.py). A repeat whose stop found the app running
    begins soon after a kill, unless the device has to cool in between, and
    shares the device with the system cleaning up after the process it
    killed: the same APK recorded that way, and recorded after a stop made
    earlier, came out 0.68 s apart in the window with every start cold and
    nothing else to tell the two apart (#533).
    """
    import shlex

    app = shlex.quote(package)
    out = _run(["adb", "-s", device, "shell",
                f"if pidof {app} > /dev/null; then echo was:running; else echo was:stopped; fi; "
                f"input keyevent KEYCODE_HOME; am force-stop {app}; i=0; "
                f"while pidof {app} > /dev/null && [ $i -lt {STOP_POLLS} ]; do "
                f"sleep 0.1; i=$((i + 1)); done; sleep {STOP_SETTLE_S}; "
                f"pidof {app} || true"],
               timeout=60)
    was = _BEFORE.search(out)
    return " ".join(_BEFORE.sub("", out).split()), was.group(1) if was else None


def _running(device: str, package: str) -> str:
    """The pids of the app's process, if it has one; empty when it has none."""
    import shlex

    app = shlex.quote(package)
    pids = _run(["adb", "-s", device, "shell", f"pidof {app} || true"], timeout=60)
    return " ".join(pids.split())


def _stopped_again_line(package: str, pids: str, waited: float, stops: int = 1) -> str:
    """The line under a repeat whose app was running again by its recording
    (#611), and how many times it was stopped for it (#523)."""
    after = f"after the {waited:.0f}s cooling wait" if waited else "after the stop"
    times = "again" if stops == 1 else "again, twice,"
    return (f"{package} was running again {after} (pid {pids}), and was stopped "
            f"{times} before the recording")


def _elsewhere_line(ended: str, asked: str) -> str:
    """The line under a repeat whose launch ended on another activity than the
    one started (#909)."""
    return (f"the launch ended on {activity_class(ended)}, not on the "
            f"{activity_class(asked)} it started: the app went on to another "
            f"activity by itself")


def activities_said(results: list[dict]) -> str | None:
    """What `collect` says at the end of a launch set whose repeats did not all
    end on the activity started (#909), or None.

    Repeats that ended on different activities measured two journeys: an app
    that opens the page read last, a login or an onboarding by itself does
    that now and then, and the launch's window runs on through the second
    activity. Wikipedia's did it in 2 of 13 cold starts, 497 and 572 ms against
    273–317, and each was recorded as an ordinary `COLD`. Which repeat is the
    odd one out is the set's to say and not the activity started's: a splash
    that hands over to the main activity ends every launch elsewhere, and that
    is the app's start. So the set is counted, and `analyze` leaves out the
    activity fewer repeats ended on.
    """
    from collections import Counter
    ended = [(r.get("repeat"), r.get("activity")) for r in results if r.get("activity")]
    if not ended:
        return None
    counts = Counter(a for _, a in ended)
    asked = next((r.get("asked") for r in results if r.get("asked")), None)
    if len(counts) == 1:
        (only, n), = counts.items()
        if not asked or only == asked:
            return None
        every = "the launch" if n == 1 else f"every launch of the {n}"
        return (f"{every} ended on {activity_class(only)}, not on the "
                f"{activity_class(asked)} it started: the app went on to it by "
                f"itself, and the window runs until it drew. A splash that hands "
                f"over does that; a login or an onboarding screen is not the start "
                f"you meant to measure")
    # The most repeats' first, the one started where as many ended elsewhere.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] != asked))
    (most, n_most), rest = ranked[0], ranked[1:]
    parts = []
    for name, _ in rest:
        repeats = [str(r) for r, a in ended if a == name and r is not None]
        which = (f"repeat {repeats[0]}" if len(repeats) == 1
                 else f"repeats {', '.join(repeats[:-1])} and {repeats[-1]}")
        parts.append(f"{activity_class(name)} in {which}")
    led = n_most > rest[0][1] or most == asked
    then = ("analyze reads the activity most repeats ended on, and leaves out and "
            "names the others" if led else
            "as many ended on each, so analyze reads them all and says the medians "
            "mix two journeys")
    return (f"the launches did not end on one activity: {'; '.join(parts)}, "
            f"{activity_class(most)} in the other {n_most}. A launch that ended on "
            f"another activity measured another journey: the app went on to another "
            f"screen by itself (a page read last, a login, an onboarding). {then}")


def _aside_path(out: Path, state: str) -> Path:
    """Where a recording that did not start cold is kept: beside the set, as
    `<name>_iterNNN-warm.perfetto-trace` (`-hot`, `-relaunch`, `-not-cold`
    where `am start` gave no state), a name `_ITERATION` does not match, so
    that `status`, `next` and `collect`'s own list leave it out. It goes with
    its set when the set is set aside (#523)."""
    stem = out.name.removesuffix(".perfetto-trace")
    word = state.lower() if state in NOT_COLD else "not-cold"
    path, n = out.with_name(f"{stem}-{word}.perfetto-trace"), 1
    while path.exists():
        n += 1
        path = out.with_name(f"{stem}-{word}-{n}.perfetto-trace")
    return path


def _not_cold_line(package: str, left: str) -> str:
    """The line under a repeat that was stopped and did not start cold (#404)."""
    when = (f"{package} was running again before the recording began (pid {left})"
            if left else f"{package} was gone after the force-stop and running "
                         f"again by the launch")
    return (f"not a cold start: {when}. The trace holds neither its process start "
            f"nor bindApplication")


def component(name: str | None) -> str | None:
    """An activity's component as one string whichever way it was written:
    `pkg/.Main` as `pkg/pkg.Main`. `am start -W` prints the short form
    (`com.sec.android.app.clockpackage/.ClockPackage` on an SM-F711B), a
    config may hold either, and the two have to compare equal. None for
    nothing, or for what is no component."""
    if not name or "/" not in name:
        return None
    package, cls = name.strip().split("/", 1)
    return f"{package}/{package}{cls}" if cls.startswith(".") else f"{package}/{cls}"


def activity_class(name: str | None) -> str | None:
    """The class of a component, `org.wikipedia.page.PageActivity` of
    `org.wikipedia.alpha/org.wikipedia.page.PageActivity`: what a trace's
    `performCreate:` slice names, and what the report shows."""
    full = component(name)
    return full.split("/", 1)[1] if full else None


def _launch(device: str, activity: str) -> dict:
    started = _run(["adb", "-s", device, "shell", "am", "start", "-W",
                    "-n", activity], timeout=120)
    info: dict = {}
    # `Activity:` is the one the launch ended on, which is not always the one
    # started: an app that opens a page read last, a login or an onboarding
    # by itself ends its launch there (#909).
    for said, field in (("TotalTime", "total_time_ms"),
                        ("LaunchState", "launch_state"),
                        ("Activity", "activity")):
        m = re.search(rf"^{said}:\s*(.+)$", started, re.MULTILINE)
        if m:
            value = m.group(1).strip()
            info[field] = int(value) if value.isdigit() else value
    if "activity" in info:
        info["activity"] = component(str(info["activity"]))
    # What `am start` says when it starts nothing, and exits 0 with:
    # `Error: Activity class {…} does not exist.` under `Error type 3`.
    error = next((ln.strip() for ln in started.splitlines()
                  if ln.strip().startswith("Error:")), None)
    if error:
        info["launch_error"] = error
    return info


@contextlib.contextmanager
def _swept(folder: Path):
    """`folder` gone when the block ends, however it ends."""
    try:
        yield
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _pull(device: str, out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(["adb", "-s", device, "pull", DEVICE_TRACE, str(out_path)],
             timeout=300)
    except KeyboardInterrupt:
        # Ctrl-C in the middle of the copy leaves part of a trace under the
        # repeat's name, and the next `analyze` would read it as one (#484).
        out_path.unlink(missing_ok=True)
        raise
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RunnerError(
            f"the trace did not arrive or is empty: {out_path}. "
            f"Check that perfetto is permitted on the device.")
    return {"path": out_path, "size": out_path.stat().st_size}
