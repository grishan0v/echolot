# Capturing a trace

`echolot collect` does this for you. The commands below are what it runs, and
what to fall back on when you need something the runner does not cover yet.
With several scenarios in the config, `collect --scenario <name>` records
that one, with its runner keys and its name on the traces.
Run them as a script: a step that fails stops it, as it stops `collect`,
rather than pulling a trace an earlier run left on the device.

## Cold start

```bash
PKG=com.example.app
ACT=$(adb shell cmd package resolve-activity --brief $PKG | tail -1)

# Unquoted, so that $PKG below is the package set above.
cat > /tmp/trace.cfg <<EOF
buffers: { size_kb: 131072 fill_policy: DISCARD }
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "sched/sched_process_exit"
      ftrace_events: "sched/sched_process_free"
      ftrace_events: "task/task_newtask"
      ftrace_events: "task/task_rename"
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "sched/sched_blocked_reason"
      ftrace_events: "thermal/thermal_temperature"
      ftrace_events: "thermal/cdev_update"
      atrace_categories: "am"
      atrace_categories: "wm"
      atrace_categories: "gfx"
      atrace_categories: "view"
      atrace_categories: "dalvik"
      atrace_categories: "binder_driver"
      atrace_categories: "res"
      atrace_categories: "database"
      atrace_apps: "$PKG"
    }
  }
}
data_sources: { config {
    name: "linux.process_stats"
    process_stats_config { scan_all_processes_on_start: true }
} }
data_sources: { config {
    name: "android.surfaceflinger.frametimeline"
} }
data_sources: { config {
    name: "linux.sys_stats"
    sys_stats_config { meminfo_period_ms: 1000 vmstat_period_ms: 1000 }
} }
duration_ms: 12000
EOF

# The screen on and unlocked: a launch behind the lock screen starts nothing
# that can be measured. A lock screen with a PIN is the user's to open.
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
adb shell am force-stop $PKG
adb shell rm -f /data/misc/perfetto-traces/t.pftrace
adb shell perfetto -c - --txt -o /data/misc/perfetto-traces/t.pftrace \
    --background-wait < /tmp/trace.cfg || exit 1
adb shell am start -W -n $ACT
adb shell 'while pidof perfetto > /dev/null; do sleep 0.5; done'
adb pull /data/misc/perfetto-traces/t.pftrace ./
```

`am start -W` prints `TotalTime` — an independent check that the window in the
report is plausible. `LaunchState: UNKNOWN` with no `TotalTime` after a
`force-stop` is a launch the system measured no start for, most often behind a
dark or locked screen.

## What the config must contain

**`sched/sched_switch`** — without it there is no `thread_state`, which means
`runnable_starvation`, `uninstrumented_cpu` and `io_wait` go silent, and
`anr_risk` keeps only its slice source: a stall in code nobody traced drops
out.

**`linux.process_stats` with `scan_all_processes_on_start`** — the only source
of process names. Without it `process.name` is empty and the config matches
nothing.

**`atrace_apps`** with the package name — otherwise there will be no
application slices, only system ones.

**`android.surfaceflinger.frametimeline`** — a data source rather than an
atrace category, and the only thing `frame_jank` reads. Android 12 and up; on
older devices it does not exist, perfetto records the rest without complaining,
and the detector is silent. Silence there looks exactly like "no bad frames",
so check the Android version before reporting a scenario as smooth.

## Callstack samples

`runner.sampling` adds a second buffer, after the first, and a sampler that
writes into it:

```
buffers: { size_kb: 131072 fill_policy: DISCARD }
data_sources: { config {
    name: "linux.perf"
    target_buffer: 1
    perf_event_config {
      timebase { frequency: 100 }
      callstack_sampling { kernel_frames: false }
    }
} }
```

No `scope { target_cmdline }` when the scenario starts the process. The
sampler judges a process once, by its name at its first sample, and a process
just forked from zygote still has zygote's: with the filter a cold start was
lost in four recordings of seven. A sampled round runs slower than a plain
one, so compare it only with another sampled at the same rate.

## What the app itself may be missing

Application slices only arrive if the app is **profileable or debuggable**. The
manifest needs:

```xml
<profileable android:shell="true" />
```

Without it the trace will hold system slices and scheduler data but not a
single `trace{}` from the code.

## Through a macrobenchmark

If the project has a module with `MacrobenchmarkRule`, the traces pile up by
themselves:

```
<module>/build/outputs/connected_android_test_additional_output/
    <variant>/connected/<device>/<Class>_<method>_iterNNN_<date>.perfetto-trace
```

`echolot collect` with `runner.mode: gradle` runs the task and gathers those
artifacts. Running it by hand on an emulator needs a suppress, or the run
refuses to start:

```bash
./gradlew :benchmark:connected<Variant>AndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED
```

The task name is often ambiguous (the baselineprofile plugin adds flavours) —
`./gradlew :benchmark:tasks | grep -i connected` lists the options.

A Compose app that ships `androidx.compose.runtime:runtime-tracing` names its
composables in the trace only when the run switches its Perfetto SDK on:
`-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=true`
in `runner.gradle_args`. Without it Macrobenchmark 1.5 recorded none. In the
other two modes `runner.compose_names: true` does it: `collect` switches the
app's SDK on for the set and off after it. What the names do to a report is
under Compose in the naming reference.

Traces are written per iteration, but `collect` harvests only after the task
succeeds: a run that failed halfway gathers nothing, and `.echolot/traces`
keeps the previous set. What the half-run wrote stays in the module's output
directory shown above, and `echolot analyze` reads it there if it is worth a
look.

## While it runs, and when it fails

A macrobenchmark round is minutes of silence — a gradle build, then the
iterations. `echolot` (status) has a `collect` line while one is in flight:
`running: coldStart, 7/15 iterations, started 3m ago`, or `the macrobenchmark
drives the iterations` where gradle does. Between two repeats that
`runner.cooldown` holds back, it adds what the wait is for: `cooling: cpu
40.4 °C, waiting for 34`, a minute or two in which nothing else moves and
nothing is stuck. A run whose process is gone and
that never finished reads `interrupted`; one that failed reads `failed 2m
ago: …` with the line of the output that names the cause — the exception the
benchmark threw, say — and the fix when the failure is a known one, until a
newer set of traces exists.

**An app that is not on the device stops `collect`** in launch mode, before
anything is recorded: `<package> is not installed on <serial>`. Install the
build the scenario measures and collect again. An `Error:` from `am start`
after a launch, an activity that is not there, stops the set at that repeat
with the line. Neither used to: `am start` exits 0 either way, and the traces
held the device and no app.

**`collect` records the build that is on the phone** in launch and command
modes, and builds nothing. After a change to the code — `echolot mark
--apply`, a marker added by hand, `mark --remove` — build and install the
variant the config measures before recording again: `mark --apply` ends with
the gradle task that does it. A round recorded without that measures the old
build. In gradle mode the macrobenchmark builds and installs the app itself.

**When the code has temporary markers, `collect` reads the build for them
first.** It picks every prefixed name out of the installed APKs' code on the
phone, and a build that lacks any of the code's markers stops it before
anything is recorded: `the build of <package> on <serial> lacks 1 of the 3
temporary markers in the code (AGENTTMP_x in <file>), so no trace of it would
hold it. Build and install the app with ./gradlew …, then collect again.` Do
exactly that: run the task it names, then `collect` again. If a build made
from this code still lacks a marker, the file it is in is not part of that
build (a test source, another module or flavour): move the marker into code
the app runs, or take it out. `the build on the phone has all N temporary
markers in the code` is the line when it has them. In command mode a build
that lacks markers before the set is read again after the first repeat, since
the scenario may install the app, and the set stops there if it still lacks
them. `[!] whether the build on the phone has … could not be read` means the
phone's tools could not tell, and the set goes on; `analyze` then says it if
the traces lack the markers.

`analyze` says it afterwards both ways, at the top of the report: when none of
the code's temporary markers is in the traces (`markers.unbuilt`), and when
the traces hold markers that no string in the code makes any more
(`markers.removed`: the build on the phone was made before they were taken
out, and has none of the changes since). Either way, build, install, and
record again; read nothing off that round.

**The screen is `collect`'s to wake** in launch and command modes. Before the
set and before each repeat it reads the screen, wakes it when it is dark, and
dismisses a lock screen that asks for nothing, a line each. A lock screen with
a PIN, a pattern or a password stops it with `<serial> is locked (…)`: ask the
user to unlock the phone, and never type a PIN over adb. A screen that went
dark between repeats is said with whether the phone charges.
`stay_on_while_plugged_in` holds the screen on only while it does, and
`runner.cooldown`'s waits are long enough for the screen to time out.

**Run one iteration first** whenever the runner config is new or changed. In
launch and command modes that is `echolot collect -c echolot.yml -n 1`: one
trace, enough to see that the device, the activity or the command, and the
anchors work. In gradle mode `-n` does not reach the benchmark — it runs as
many iterations as its own `measureRepeated` asks for, and `collect` says so —
so narrow the task instead: one test through
`-Pandroid.testInstrumentationRunnerArguments.class=<Class>#<method>` in
`runner.gradle_args`. A wrong variant or a device that refuses fails after the
whole build either way, and once is enough to find that out.

A failure prints the lines that matter from both of gradle's streams — the
instrumentation's own exception lives on stdout, "What went wrong" on stderr.
`.echolot/log/runs.jsonl` keeps it as the run's `error`: the line that names
the cause and the fix first, then as much of the full message as fits in 800
characters. Four failures come with what fixes them, each learned on a real
device:

| the line says | what it is | the fix |
|---|---|---|
| `Perfetto SDK` / `binary verification` | the SDK half of the tracing could not be set up in the app — a stale `libtracing_perfetto.so` in its code_cache | reinstall or `pm clear`; or `-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=false` in `runner.gradle_args` — the atrace half still carries the app's sections |
| `ERRORS (not suppressed): EMULATOR, …` | the benchmark refuses the device or its state | fix the state, or `…androidx.benchmark.suppressErrors=` with the names it lists, `EMULATOR,LOW-BATTERY` for one |
| `No online devices found` | gradle found no device it could use | `adb devices`; with several attached, `--device` or `runner.device` picks one — `collect` hands it to gradle as `ANDROID_SERIAL`, so it has to be one `adb devices` lists |
| `INSTALL_FAILED` / `signatures do not match` | the APK did not install | uninstall the app from the device first — a build signed differently cannot go over the one that is there |

## For calibration

`echolot calibrate` expects **repeats of one scenario** on a known-healthy
build. Mixing a cold start with a minute of scrolling yields thresholds for
nothing; the command warns when the windows diverge more than twofold. With
several scenarios in the config, `--scenario` says which one the traces are,
as for `collect` and `analyze`, and the section it prints is that scenario's
own, `scenarios.<name>.detectors`, to go under it.

On a phone, record them with `runner.cooldown`. Repeats recorded back to back
warm the device, and the set drifts slower repeat by repeat: thresholds taken
from it carry the heat as well as the app.

Between repeats: `force-stop`, never `pm clear`. Wiping data changes the
scenario rather than repeating it.
