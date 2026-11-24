# `environment` — the device the numbers were made on

**`environment`** — what the device was doing to the app while the scenario
ran. Not a finding: it is the condition every duration in this report is
stated under. A slice is 40 ms partly because of the code in it and partly
because of the clock it ran on, and this is the only place that says which
clock. `cpu.mean_mhz` is weighted by the time this app's own threads held a
core, so it describes the machine the app got rather than the machine.

Each of `cpu`, `thermal` and `memory` is either a measurement or `null`, and
`missing` names the null ones. **`null` means the trace was recorded without
those sources, never that the device was fine** — a trace from an older
echolot, or from a config with `runner.environment: false`, arrives this way.
Treat a missing clock as "unknown", and say so rather than calling a run
clean.

Temperature and free memory are sampled about once a second, and a value
holds until the next sample. A window shorter than that, a hot start or a
tap, often holds no sample of its own: it reads the last one before it
opened, and `sampled_before_ms` says how long before. With a sample inside
the window the key is absent.

`thermal.throttled` is the one to act on. A hot device is not a slowed device;
a cooling device above zero is the kernel saying it took capacity away, and
the numbers below then understate the app.

`compose_names` is whether composables were named while the trace recorded,
read from the trace: `asked`, the recording's config held a `track_event`
source (`runner.compose_names`, or a macrobenchmark's
`perfettoSdkTracing.enable=true`); `calls`, the composable calls on the main
thread in the window; merged, `runs` counts the repeats that had any. `null`
is neither. Names come with the app's Perfetto SDK loaded at its start, which
cost a cold start 67 ms on the device it was measured on, so a round with
names compares only with another with names, and `compare` says so when they
differ. Asked for and none arrived is a warning in the header: an app
without `runtime-tracing`, or an SDK nobody switched on.

`compile` is how ART had compiled the app before it ran, read from the
trace, and what it compiled while it ran. The same code runs at another speed
under each: a large app's launch on one phone took ~1.4 s on its benchmark
build and ~8.8 s on a debuggable build of it that nothing had compiled.

- `filter`, `reason`, `status` — ART's slice for the app's own dex,
  `location=… status=… filter=… reason=…`, read over the whole trace because
  the process opens it at its start. `speed-profile` is compiled from a
  profile, which is how an install from Google Play usually arrives, with
  `reason` saying where the profile came from (`install-dm` from the build,
  `bg-dexopt` widened later on the device). `verify` and `run-from-apk`
  compiled none of the app's code ahead of time, and the report's header
  warns. `null` when the trace does not hold the slice: the process was
  already running when the recording began.
- `jit` — the methods the JIT compiled inside the window, and its time.
- `verified` — the classes ART verified at run time inside the window,
  their time, and the part on the main thread (`main_ms`), which the user
  waited for. Compiled code brings its classes verified; a library loaded
  from elsewhere, or an app nothing compiled, verifies them here.

Merged, the state is the one every repeat agrees on, or all of them joined
by `and` — `run-from-apk and speed-profile` is a set of two builds, and the
header says its medians mix two measurements. `runs` counts the repeats whose
trace held the state. `compile` itself is `null` for a trace recorded without
the `dalvik` atrace category, which `collect` records by default.

`sampling` is whether a callstack sampler ran while the trace recorded —
`runner.sampling`, or whatever config recorded the trace — read from the
trace itself. `null` is a definite answer here: nothing sampled it, which is
the default, and it is never listed in `missing`. Otherwise:

```json
"sampling": { "hz": 100, "started": true, "samples": 1482, "with_stack": 1466,
              "runs": "5/5", "runs_with_stack": "5/5" }
```

`samples` counts this process's samples inside the window and `with_stack`
the ones that came with a callstack; merged, both are medians, `runs` counts
the repeats a sampler ran in and `runs_with_stack` those among them that got
any stack. The sampler takes time from the app, so a sampled round compares
only with another sampled at the same rate, and `compare` says so when they
differ. The report puts a warning in place of the line for the three ways it
comes back empty: `started: false` — asked for, and the device's sampler
never ran; `samples: 0` — the sampler ran and none of it is this process's,
the mark of a recording filtered by process name that missed a starting
process; and no repeat with a stack. That last one has two causes: an app
the sampler may not unwind, which on a `user` build of Android means the
manifest lacks `<profileable android:shell="true" />`, or a start the
sampler lost, which a profileable app's cold start does now and then. If
some repeats have stacks and others none, it was the second.
