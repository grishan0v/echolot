# Collecting traces

[← Docs index](README.md) · [README](../README.md)

```bash
echolot collect -c echolot.yml -n 5 -o traces/
echolot analyze traces/*.perfetto-trace -c echolot.yml
```

The first command captures N repeats of a scenario, the second merges them into
one report.

Repeating is not belt-and-braces. A single run cannot tell a regression from a
random spike — on a live cold start the spread between iterations reaches tens
of percent. Both threshold calibration and report merging stand on the
distribution across repeats; without them, both are guessing.

A second `collect` into the same directory does not overwrite the first: the
existing `<scenario>_iter*` set moves into a sibling directory stamped with
when it was recorded (`traces/cold_start-20260815-153720/`), and the log says
so. The set from before a change is the baseline every after-the-fix
comparison stands on, and it is the first thing lost otherwise — a
macrobenchmark's output directory is cleaned by gradle on the next run, a
rename inside it goes with the cleaning. If you record around the tool, copy
the traces out before re-recording; `echolot reflect` flags a re-record that
did not.

## Three modes

```yaml
runner:
  mode: launch              # launch | command | gradle
  iterations: 5
  duration_ms: 12000
  reset_policy: force-stop  # force-stop (cold) | none (warm)
```

### `launch` — we drive it

`force-stop`, start recording, `am start -W`, wait, pull. A cold start. With
`reset_policy: none` it becomes a warm one.

`am start -W` prints `TotalTime`, which the runner reports — an independent
check that the window in the report is plausible.

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

### `gradle` — the macrobenchmark writes its own

```yaml
runner:
  mode: gradle
  gradle_task: ":benchmark:connectedBenchmarkBenchmarkAndroidTest"
  gradle_args: ["-Pandroid.testInstrumentationRunnerArguments.class=…"]
```

Macrobenchmark drops traces per iteration into an artifact directory whose path
depends on the build variant and the device model — awkward to find by hand.
The runner takes everything that appeared after the run started.

Two practical notes. On an emulator the run refuses to start without a
suppress:

```bash
-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.suppressErrors=EMULATOR,LOW-BATTERY,UNLOCKED
```

And the task name is often ambiguous, because the baselineprofile plugin adds
flavours. `./gradlew :benchmark:tasks | grep -i connected` lists the options.

## `pm clear` is deliberately unsupported

`reset_policy` accepts `force-stop` and `none`. Wiping application data changes
the scenario rather than repeating it: a cold start with an empty database and
a real user's cold start are different things, and only one of them is worth
measuring.

## What the trace config must contain

The runner builds the perfetto config itself, but if you capture by hand
(recipe in `echolot/claude/skills/echolot/references/collect.md`), four things
are mandatory:

**`sched/sched_switch`** — without it there is no `thread_state`, which means
`runnable_starvation` and `uninstrumented_cpu` both go silent. Those are the
two structural detectors.

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
  device is under load and thresholds from such runs will be noisy;
- the report flags repeat windows that diverged more than twofold — those are
  not repeats of one scenario, and a median over them means nothing.
