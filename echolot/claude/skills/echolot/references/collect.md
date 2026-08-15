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

## For calibration

`echolot calibrate` expects **repeats of one scenario** on a known-healthy
build. Mixing a cold start with a minute of scrolling yields thresholds for
nothing; the command warns when the windows diverge more than twofold.

Between repeats: `force-stop`, never `pm clear`. Wiping data changes the
scenario rather than repeating it.
