# Capturing a trace

`echolot collect` does this for you. The commands below are what it runs, and
what to fall back on when you need something the runner does not cover yet.

## Cold start

```bash
PKG=com.example.app
ACT=$(adb shell cmd package resolve-activity --brief $PKG | tail -1)

cat > /tmp/trace.cfg <<'EOF'
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
      atrace_apps: "com.example.app"
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

adb shell am force-stop $PKG
adb shell perfetto -c - --txt -o /data/misc/perfetto-traces/t.pftrace \
    --background-wait < /tmp/trace.cfg
adb shell am start -W -n $ACT
adb shell 'while pidof perfetto > /dev/null; do sleep 0.5; done'
adb pull /data/misc/perfetto-traces/t.pftrace ./
```

`am start -W` prints `TotalTime` — an independent check that the window in the
report is plausible.

## What the config must contain

**`sched/sched_switch`** — without it there is no `thread_state`, which means
`runnable_starvation` and `uninstrumented_cpu` both go silent. Those are the
two structural detectors.

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

Traces are written per iteration, so even a run that failed halfway usually
leaves usable material.

## While it runs, and when it fails

A macrobenchmark round is minutes of silence — a gradle build, then the
iterations. `echolot` (status) has a `collect` line while one is in flight:
`running: coldStart, 7/15 iterations, started 3m ago`, or `the macrobenchmark
drives the iterations` where gradle does. A run whose process is gone and
that never finished reads `interrupted`; one that failed reads `failed 2m
ago: …` with the sentence it printed, until a newer set of traces exists.

**Run one iteration first** whenever the runner config is new or changed:
`echolot collect -c echolot.yml -n 1`. A wrong variant or a device that
refuses fails after the whole build either way, and once is enough to find
that out.

A failure prints the lines that matter from both of gradle's streams — the
instrumentation's own exception lives on stdout, "What went wrong" on stderr
— and the same sentence goes into `.echolot/log/runs.jsonl` as `error`.
Three failures come with what fixes them, each learned on a real device:

| the line says | what it is | the fix |
|---|---|---|
| `Perfetto SDK` / `binary verification` | the SDK half of the tracing could not be set up in the app — a stale `libtracing_perfetto.so` in its code_cache | reinstall or `pm clear`; or `-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=false` in `runner.gradle_args` — the atrace half still carries the app's sections |
| `ERRORS (not suppressed): EMULATOR, …` | the benchmark refuses the device or its state | fix the state, or `…androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED` |
| `No online devices found` | gradle found no device | `adb devices`; `runner.device` when several are attached |

## For calibration

`echolot calibrate` expects **repeats of one scenario** on a known-healthy
build. Mixing a cold start with a minute of scrolling yields thresholds for
nothing; the command warns when the windows diverge more than twofold.

Between repeats: `force-stop`, never `pm clear`. Wiping data changes the
scenario rather than repeating it.
