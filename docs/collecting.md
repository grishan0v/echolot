# Collecting traces

[← Docs index](README.md) · [README](../README.md)

```bash
echolot collect -c echolot.yml -n 5
echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml
```

The first command captures N repeats of a scenario into `.echolot/traces/`
beside the config, the second merges them into one report. That directory is
the one `echolot`, `hunt` and `compare` read, so the default is the place to
keep them; `-o` exists for the rare trace that belongs elsewhere.

Repeating is not belt-and-braces. A single run cannot tell a regression from a
random spike — on a live cold start the spread between iterations reaches tens
of percent. Both threshold calibration and report merging stand on the
distribution across repeats; without them, both are guessing.

A second `collect` into the same directory does not overwrite the first: the
existing `<scenario>_*` set moves into a sibling directory stamped with
when it was recorded (`.echolot/traces/cold_start-20260815-153720/`), and the
log says so. The set from before a change is the baseline every after-the-fix
comparison stands on, and it is the first thing lost otherwise — a
macrobenchmark's output directory is cleaned by gradle on the next run, a
rename inside it goes with the cleaning. If you record around the tool, copy
the traces out before re-recording; `echolot reflect` flags a re-record that
did not.

The set moves when the first new trace is about to take its place, and not
before. A `collect` that stops earlier — no device, a typo in the mode, a
gradle build that fails, a benchmark that writes nothing — leaves it where it
was, so the traces line of `echolot` and the next `analyze` still see it.
Everything of this scenario moves, a probe capture saved beside the set
included; another scenario's traces stay, and so do those of a scenario whose
name merely begins with this one's: re-recording `startup` leaves
`startup_warm_*` alone.

## Three modes

```yaml
runner:
  mode: launch              # launch | command | gradle
  iterations: 5
  duration_ms: 12000
  reset_policy: force-stop  # force-stop (cold) | none (warm)
```

`duration_ms` is sized for a start, and a freeze does not fit in it. Hunting
one needs the block plus the five seconds the system waits before declaring an
ANR — forty-five is a working number, and twelve finds neither. See
[ANRs](anr.md).

The numbers are digits alone, in the unit the key's name ends with:
`duration_ms: 12000`. A value such as `12s` stops `collect` with a sentence
before the device is touched. A `reset_policy` other than `force-stop` or
`none` gets a warning that says `force-stop` will run, and then it does.

### `launch` — we drive it

`force-stop`, start recording, `am start -W`, wait, pull. A cold start. With
`reset_policy: none` it becomes a warm one.

`am start -W` prints `TotalTime`, which the runner reports — an independent
check that the window in the report is plausible. `TotalTime: 0` is the
system saying it timed no start, usually because the activity was already in
front and nothing was launched; `collect` counts those launches and leaves
them out of the spread.

### `command` — something else drives it

We record the trace around a command of your choosing. Anything that can move
the app goes here: `adb input`, uiautomator, maestro, your own script.

```yaml
runner:
  mode: command
  reset_policy: none
  command: >
    adb shell am start -n com.example.app/.MainActivity &&
    adb shell input swipe 500 1500 500 600 400
```

The command comes from the project's own config — the same level of trust as
the gradle task next to it. If it outlasts the recording window, the runner
says the trace covers only its beginning.

It runs with `ANDROID_SERIAL` set to the device being recorded, so a bare
`adb shell …` inside it reaches that device — with a second one plugged in it
would otherwise stop at "more than one device", whatever `--device` said.

### `gradle` — the macrobenchmark writes its own

```yaml
runner:
  mode: gradle
  gradle_task: ":benchmark:connectedBenchmarkBenchmarkAndroidTest"
  gradle_args: ["-Pandroid.testInstrumentationRunnerArguments.class=…"]
  project_root: ../my-app        # where ./gradlew lives
  gradle: ./gradlew              # the wrapper, relative to project_root (default)
  timeout_s: 3600                # the whole task, build included (default)
```

Macrobenchmark drops traces per iteration into an artifact directory whose path
depends on the build variant and the device model — awkward to find by hand.
The runner takes everything that appeared after the run started.

The benchmark runs as many iterations as its own `measureRepeated` asks for,
so `-n` and `runner.iterations` do not reach it, and `collect` says so when
either is given. `timeout_s` bounds the whole gradle run — the build, the
install and every iteration — and a run that outlasts it is stopped with its
whole process tree. `gradle` is the command in front of the task, run from
`project_root`.

A task that fails harvests nothing, whatever the benchmark wrote before it
failed: the traces of a half-finished run stay in the module's output
directory, and the previous set stays in `.echolot/traces`.

The macrobenchmark chose what to record, so this mode builds no trace config
of its own. `environment`, `atrace_categories`, `buffer_kb`, `duration_ms` and
`reset_policy` are inert here, and `collect` says so when it finds them —
they look like they apply, and copying them in from a launch-mode config is
the obvious mistake. What the traces carry is the benchmark's decision, and
the report says which platform state it actually found. On the run these notes
come from that was the clock and memory but no thermal.

`project_root` is both where the task runs and where the traces are looked
for. A relative one is taken from the directory `echolot.yml` is in, the way
`-o` and the investigation are, and it defaults to that directory. It matters
as soon as `echolot.yml` does not sit beside `gradlew`: `./gradlew` is a
relative path, and it used to be looked for in whatever directory `echolot`
happened to be started from, so the same config ran in two places depending
on the shell.

"Everything that appeared after the run started" is a deliberate rule rather
than a convenience. A benchmark module keeps every trace it has ever written
— the directory this was first run in held fifteen from twelve days earlier —
and gathering by name would quietly merge two sittings into one report and
take medians across them.

Two practical notes. On an emulator the run refuses to start without a
suppress:

```bash
-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED
```

And the task name is often ambiguous, because the baselineprofile plugin adds
flavours. `./gradlew :benchmark:tasks | grep -i connected` lists the options.

## Which device

In `launch` and `command` modes, in the order they are consulted:

1. `--device <serial>` on the command line;
2. `runner.device` — usually in `local.yml`, since a serial belongs to one
   machine rather than to the project;
3. the one device `adb devices` lists in state `device`.

When none of them settles it, `collect` stops before anything is recorded and
says why: adb sees no devices at all; several are ready and none was named;
the device is `unauthorized` (accept the "Allow USB debugging?" prompt on its
screen); it is `offline` (replug, or `adb kill-server`); or, on Linux, `no
permissions` — adb can see it but may not open it, which a udev rule for the
phone's USB vendor fixes. A serial that is named but not attached is refused
with the list of those that are.

The chosen device reaches everything that runs on its behalf. In `command`
mode the scenario gets it as `ANDROID_SERIAL`. In `gradle` mode the task picks
its own device, and the connected test tasks run on every device adb lists
unless `ANDROID_SERIAL` names one — so `--device` or `runner.device` is passed
to gradle as `ANDROID_SERIAL`. With neither, an `ANDROID_SERIAL` already in
your environment passes through untouched, and `collect` says it is using it.

## While it runs, and when it fails

A macrobenchmark round is minutes of silence. `collect` writes where it stands
to `.echolot/log/collect.json` — when it starts, after every iteration it
drives, and when it ends — and `echolot` turns that into a `collect` line:
`running: coldStart, 7/15 iterations, started 3m ago`, or `the macrobenchmark
drives the iterations` in gradle mode. A run whose process is gone without
finishing reads `interrupted`. One that failed reads `failed 2m ago:` followed
by the line of the output that names the cause, such as the exception the
benchmark threw, and the fix when the failure is a known one. That line stays
until a newer set of traces exists.

The run log, `.echolot/log/runs.jsonl`, keeps the same cause and fix at the
head of the run's `error`, followed by as much of the full message as fits in
the 800 characters it keeps.

Four gradle failures come with the one thing that fixes them:

| the output says | what it is | the fix |
|---|---|---|
| `Perfetto SDK` / `binary verification` | the SDK half of the tracing could not be set up in the app — usually a stale `libtracing_perfetto.so` in its code_cache | reinstall, or `pm clear` where the device allows it; or turn that half off with `-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=false` in `runner.gradle_args` |
| `ERRORS (not suppressed): EMULATOR, …` | the benchmark refuses the device or its state | fix the state, or `…androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED` in `runner.gradle_args` |
| `No online devices found` / `DeviceException` | gradle found no device it could use | `adb devices` should list one as `device`; a serial named with `--device` or `runner.device` reaches gradle as `ANDROID_SERIAL` and has to be one of those listed |
| `INSTALL_FAILED` / `signatures do not match` | the APK did not install | uninstall the app from the device first — a build signed differently cannot go over the one that is there |

## `pm clear` is deliberately unsupported

`reset_policy` accepts `force-stop` and `none`; anything else is named in a
warning and runs as `force-stop`. Wiping application data changes the scenario
rather than repeating it: a cold start with an empty database and a real
user's cold start are different things, and only one of them is worth
measuring.

## What the trace config must contain

The runner builds the perfetto config itself, but if you capture by hand
(recipe in `echolot/claude/skills/echolot/references/collect.md`), four things
are mandatory and a fifth decides whether the report can tell a slower machine
from a slower app:

**`sched/sched_switch`** — without it there is no `thread_state`, and the two
detectors built on it, `runnable_starvation` and `uninstrumented_cpu`, both go
silent. They are also the two that need no instrumentation in the app, so on a
project with none they are the whole report.

**`linux.process_stats` with `scan_all_processes_on_start`** — the only source
of process names. Without it `process.name` is empty and the config matches
nothing.

**`atrace_apps`** with the package name — otherwise the trace holds system
slices only.

**`android.surfaceflinger.frametimeline`** — SurfaceFlinger's own record of
every frame, and the only source `frame_jank` has. It is a separate data
source rather than an atrace category:

```
data_sources: { config { name: "android.surfaceflinger.frametimeline" } }
```

Android 12 and up. On anything older the data source does not exist, perfetto
records the rest without complaining, and the detector is silent — which reads
exactly like "no bad frames". If jank is the question and the report says
nothing, check the Android version before believing it.

**The platform state** — `power/cpu_frequency`, `thermal/thermal_temperature`,
`thermal/cdev_update`, `sched/sched_blocked_reason`, and `linux.sys_stats`
polling memory once a second:

```
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "sched/sched_blocked_reason"
      ftrace_events: "thermal/thermal_temperature"
      ftrace_events: "thermal/cdev_update"
```
```
data_sources: { config {
    name: "linux.sys_stats"
    sys_stats_config { meminfo_period_ms: 1000 vmstat_period_ms: 1000 }
} }
```

Not mandatory, and the one thing on this page that changes what a comparison
means. A duration is the work done divided by the speed the machine was doing
it at: the same code on a lower clock takes longer, and `compare` without
these numbers reports that as a regression. With them it says the clock moved
instead. `runner.environment: false` leaves them out for a device whose buffer
cannot afford them, and the report then says the device state was not
recorded — never that it held steady.

`power/cpu_idle` is deliberately not in that list. It fires on every idle
transition on every core, which is the largest source of events on the page,
and it would add nothing: the clock is read only over intervals where the
app's own threads held a core, and a core running a thread is not idle.

What this costs, measured on an SM-A515F (Android 13, eight cores, two cpufreq
policies) over three twelve-second cold starts: `cpu_frequency` produced
4564–4940 samples per trace — about 400 a second, 1.6% of all ftrace events in
the same trace — and the buffer never overran. Thermal came to 160 samples and
memory to 396. The cost is not the reason to turn this off.

One of the four delivers less than it looks. `sched/sched_blocked_reason`
carries `io_wait`, which works, and `caller`, which perfetto turns into a
kernel function name by reading `/proc/kallsyms`. That file is unreadable on a
production build, so `blocked_function` comes back empty there — on the A51 it
was empty for all 6683 uninterruptible-sleep intervals in the trace, with and
without `symbolize_ksyms`, while `io_wait` was filled in for 6486 of them.
Expect the name of the blocking function on a userdebug kernel and nowhere
else; `io_wait` is what tells disk waiting from other blocking everywhere.

There is also a requirement on the app itself: slices arrive only if it is
**profileable or debuggable**. The manifest needs
`<profileable android:shell="true" />`.

## Merging repeats

Numbers are merged by **median** — not by mean (one outlier would drag the
conclusion along) and not by maximum (then any random hiccup becomes a
"finding").

The whole point of repeating is the **Runs** column:

| Where | Runs | N | Total, ms |
|---|---|---|---|
| `m.example.app` | **3/3** | 80 | 56.92 |
| `RenderThread` | **1/3** | 30 | 39.02 |

The first row reproduces, the second happened once. Without that column they
look equally convincing and the agent goes off investigating an accident.

Two warnings come out of merging:

- the runner prints the `am start -W` spread and flags it above 30% — the
  device is under load and thresholds from such runs will be noisy (a launch
  that reports `TotalTime: 0` was not timed, and is counted apart);
- the report flags repeat windows that diverged more than twofold — those are
  not repeats of one scenario, and a median over them means nothing.
