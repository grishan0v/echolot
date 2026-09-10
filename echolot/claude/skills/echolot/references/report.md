# The Marker Report — what to read and how to read it

`echolot analyze` writes two files into `.echolot/out/` next to the config:
`report.json` for you and `report.md` for humans. One set of data, two formats.
A relative `-o` is taken from the config's directory, so running from a build
folder full of traces still lands the report in the project.

Two flags for one run only, no config edited: `--set detector.param=value`
overrides one threshold; `--defaults` ignores the config's `detectors` section
and runs every detector with its built-in numbers. Both leave a mark in the
report (see `config` and `params_source` below), so a report made that way
cannot be mistaken for the project's.

## The `report.json` schema

```json
{
  "schema": 1,
  "generated_at": "2026-08-15T12:00:00+00:00",
  "trace": "path",
  "toolchain": { "perfetto_package": "0.57.2",
                 "trace_processor": "v56.1", "source": "pinned" },
  "window": { "ts_start": …, "ts_end": …, "duration_ms": 772.3,
              "process": "com.example.app", "pid": 4100,
              "start_anchor": { "glob": "bindApplication", "matches": 1 },
              "end_anchor":   { "glob": "…", "matches": 1 } },
  "environment": { "cpu": { "mean_mhz": 1481.2, "min_mhz": 300.0,
                            "max_mhz": 2400.0, "on_cpu_ms": 1945.0,
                            "measured_ms": 1902.4 },
                   "thermal": { "max_celsius": 61.5, "hottest_zone": "cpu-therm",
                                "throttled": false, "throttle_device": null },
                   "memory": { "available_mb_min": 1536.0, "major_faults": 250 },
                   "missing": [] },
  "window": { "…": "…",
              "main_thread": { "on_cpu": 268.4, "waiting_for_cpu": 21.5,
                               "in_kernel": 55.0, "sleeping": 195.1,
                               "other": 0.0, "accounted_ms": 540.0,
                               "window_ms": 541.5, "accounted_pct": 99.7 } },
  "summary": { "detectors_run": 6, "detectors_fired": 4,
               "fired_ids": ["main_thread_block", "…"] },
  "config": { "path": "/abs/project/echolot.yml", "sha": "fadc1a11b903",
              "local": null, "defaults": false, "set": null },
  "markers": { "prefix": "AGENTTMP_",
               "globs": ["AGENTTMP_*", "collection_mapping", "Screen.firstFrame"],
               "rows": [ { "location": "AGENTTMP_fill_decks", "runs": "5/5",
                           "count": 1, "self_ms": 8.0, "total_ms": 60.0,
                           "max_ms": 60.0, "spread": { … },
                           "detail": "SeedWorker" } ],
               "absent": ["Screen.firstFrame"] },
  "detectors": [
    { "id": "…", "title": "…", "why": "…",
      "params": { … }, "params_source": "config",
      "defaults": { "min_slice_ms": 16 },
      "rows": [ { "location": "draw", "runs": "5/5", "count": 4,
                  "self_ms": 125.4, "total_ms": 130.1, "max_ms": 61.2,
                  "spread": { "self_ms": { "min": …, "max": …, "values": [ … ] },
                              "max_ms":  { … } },
                  "detail": "main" } ],
      "error": null }
  ]
}
```

**`config`** — which file made this report, and its content hash. Two
reports with different `sha` were not made against the same thresholds;
`defaults: true` means `--defaults` ran every detector with its built-in
numbers, `set` lists what `--set` overrode for that run.

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

`thermal.throttled` is the one to act on. A hot device is not a slowed device;
a cooling device above zero is the kernel saying it took capacity away, and
the numbers below then understate the app.

**`detectors[].params_source`** — where this detector's thresholds came from:
`default` (the numbers shipped in the .sql), `config` (calibrated or
hand-set in `echolot.yml`), `cli` (`--set`), or `config+cli`. When it is not
`default`, `defaults` holds the shipped values for the overridden parameters,
so you can see how far the bar moved without running `explain`. A silent
detector with calibrated thresholds means "nothing above the calibrated
bar" — not necessarily "nothing above the default one".

**`window.main_thread`** — where the window went, in milliseconds, for the
main thread only. `summary` counts detectors; this counts time, and it is the
number that decides whether a quiet report means a clean run. Four buckets and
a remainder, summed from thread states clipped to the window, so the parts
cannot exceed the whole.

Read it before the tables. A compound stall — 40% waiting for a CPU beside 35%
blocked in the kernel — is one line here and two unrelated rows in different
sections anywhere else. And the contrast between two runs is often the finding
itself: on the same phone a freshly installed app spent 10% of its cold start
blocked in the kernel against 1% for one whose pages were already cached.

`sleeping` is one bucket and stays ambiguous on purpose: an idle looper and a
blocking call inside a message are both `S`, and telling them apart needs the
slices rather than the states. A high `sleeping` share is a question, not a
finding.

`accounted_pct` short of 100 means the main thread was not there for the whole
window — the process started inside it, or the recording has a hole. The
shares are then of what was seen rather than of the scenario, and the report
says so.

## `markers` — your own names, measured every time

A detector shows a marker only where a threshold says so. `markers` shows
every name that is the project's — whatever carries
`instrumentation.temp_prefix`, and whatever `domains` lists — with the same
columns as a detector's rows and the same merge across repeats: `runs`,
medians, `spread`. `self_ms` subtracts the children, so a marker that wraps
another reads as the difference: `AGENTTMP_store_update` minus
`AGENTTMP_store_update_locked` is how long the lock was waited for.
`detail` is the thread, or `(async)` for a `beginAsyncSection` span, which
is what most of a project's own markers are.

This is the table for the markers you planted. Read it from the report;
do not rebuild it by running `names` once per trace.

`absent` lists the `domains` names the window never held in any repeat: a
map pointing at something this scenario does not run, or a name that
changed under it. The markdown says the same under "Not in the window".

## Views — `echolot report`

The json is the contract; it is not the thing to read whole. On a real hunt
the agent cut it up sixteen times with jq and python one-liners, and each
one put a window's worth of json into the context to get at a line. The
views are those one-liners, done once:

```bash
echolot report                                 # what fired: one line per detector
echolot report --detector monitor_contention   # its rows, longest first, 5 of them
echolot report -d main_thread_block --top 12   # more; --wide keeps the evidence whole
echolot report --window                        # anchors, the main thread, the device
echolot report --markers                       # your own names, measured
echolot report -d repeated_work --json         # the same selection as json — `places` included
echolot report path/to/report.json --window    # an older report, a round's own copy
```

Without a path it reads `.echolot/out/report.json` next to the config.

## Check these before drawing conclusions

**`window.start_anchor.matches == 0`** — the anchor never matched and the
window expanded to the whole trace. None of the numbers are about your
scenario. Do not investigate, fix the config: look at the real names via
`echolot probe` and correct `scenario.start`.

**`window.opened_inside` with `material: true`** — the scenario window opened
while the main thread was already blocked, and more of that block happened
before the anchor matched than inside the window. Thread states are clipped to
the window, so the waiting in the report is the tail of a stall whose cause is
outside it. The numbers below are a partial account rather than a wrong one,
which is why it is easy to miss. Move `scenario.start` earlier and analyse
again if the cause is what you are after.

Sleeping is only counted when a message was open at the time. A main thread
idle at the message queue looks identical in the state alone — on one real
scenario it sat there for 1615 ms waiting for a finger — and that is the app
working, not a stall.

**`window.process_alternatives` present** — the mask matched several processes
and the largest by slice count was taken. If you are analysing `:pushservice`
instead of the main process, narrow `project.process`. The list holds the
next few by slice count; `process_alternatives_total` is how many there were.

**`summary.absent_ids` non-empty** — this project's config names detectors,
so only those ran, and the ones listed here were shipped after it was written.
They are not silent; they never ran. `echolot analyze … --defaults` runs every
shipped detector without touching the config, which is the quickest way to see
what they would have said.

**`detectors[].error != null`** — that detector failed while the rest ran. SQL
is version-fragile; report the error, but do not treat the absence of findings
as an answer.

**`environment.cpu` is `null`, or `environment.thermal.throttled` is true** —
in the first case nobody measured the machine, so a duration cannot be read as
a statement about the code alone; in the second the kernel was taking capacity
away while these numbers were made. Neither is a reason to discard the report,
and both are a reason to say so out loud before drawing a conclusion from a
number that moved.

**`toolchain.trace_processor`** — the parser version. If the numbers diverge
between two reports, check this first: a version change alters trace semantics
while the SQL stays identical.

## Row columns

The contract is shared, but not every detector fills every column.

| column | meaning |
|---|---|
| `location` | the slice or thread name — what you hook onto |
| `count` | how many times it occurred inside the window |
| `self_ms` | self time, with children subtracted |
| `total_ms` | total time, children included |
| `max_ms` | the longest single occurrence |
| `covered_ms` | how much on-CPU time ran inside instrumented code |
| `code` | where that is in the checkout, when the row names a method or a class — see below |
| `detail` | the evidence: thread, state, lock name with the owner's tid |
| `places` | the json behind `code`: every symbol the row names, with `file`, `line` and `role` |
| `spread` | the per-run values behind the medians, for two columns — see below |

**`code` and `places` save the grep.** A contention slice names both sides of
the lock — the thread holding it and where it is, the method that waited and
where that is — and `main_thread_block` names a class when the slice is a
View being inflated. `analyze` looks those up in the checkout the config
sits in and writes what it found:

```json
"code": "owner at StoreRepository.kt:30 · blocked at StoreRepository.kt:66",
"places": [
  { "role": "owner",   "symbol": "com.example.app.data.StoreRepository.update",
    "file": "data/src/main/java/com/example/app/data/StoreRepository.kt",
    "line": 30, "exact": true },
  { "role": "blocked", "symbol": "com.example.app.data.StoreRepository.find",
    "file": "…/StoreRepository.kt", "line": 66, "exact": true }
]
```

Open `places[].file` at `places[].line`; do not grep for the symbol first.
`line` is the runtime's when the build kept line numbers and the
declaration's when it did not (`(File.kt:-1)` is what a release build says).
A place with `file: null` is a symbol outside this checkout — an owner
parked in `jdk.internal.misc.Unsafe.park` is the holder waiting on something
else while holding the lock, which is the finding, not a gap. `exact: false`
means two files of that name and no package to choose by: check before
opening. Rows with nothing to place carry neither key.

**`spread` is what makes a number checkable.** Merging repeats reduces each row
to a median, and a median cannot say whether a number is steady: 120 ms from
(118, 119, 121) and 120 ms from (12, 120, 890) read identically, and only the
second means the next run will say something else.

```json
"spread": { "self_ms": { "min": 118.2, "max": 340.1,
                         "values": [118.2, 121.0, 125.4, 133.7, 340.1] } }
```

Present for the detector's ranking metric (`self_ms` where it has one,
`total_ms` otherwise) and for `max_ms`. `values` holds one entry per repeat the
row was **found in**, which is what `runs` counts: a `3/5` row has three. Absent
when `analyze` ran on a single trace — there is nothing to spread.

Use it before acting on a number. A row whose `max` is several times its median
holds one slow occurrence rather than a steady cost, and that is a different
bug in a different place.

**`self_ms` and `total_ms` are not duplicates.** The first answers "where was
it spent", the second "how long did it take altogether". A slice with
`total_ms: 354` and `self_ms: 79` barely works itself — it waits on its
children, and that is where to dig. Never add `total_ms` across rows: they
nest. Adding `self_ms` is fine — self times do not overlap.

## The detectors

| id | what it catches | what to hook onto |
|---|---|---|
| `main_thread_block` | where the main thread spent its time | `location` — the slice name; `detail` is the thread's comm, always the main thread here, cut to 15 characters by the kernel |
| `gc_pressure` | collection cycles and allocation waits | frequent GC = many intermediate objects |
| `monitor_contention` | monitor contention | `places` names both sides of the lock in the checkout; `detail` carries the owner's tid |
| `binder_txn` | synchronous IPC into another process | `count` and `total_ms`, not just `max_ms` |
| `runnable_starvation` | thread ready but preempted | this is about the device, not the code |
| `uninstrumented_cpu` | threads burning CPU with no instrumentation | an address for adding `trace{}` |
| `frame_jank` | frames that missed their deadline | `location` is why it missed, `detail` says whose fault |
| `main_thread_outlier` | one occurrence far longer than usual | `detail` carries the median it is measured against |
| `anr_risk` | a stretch where the main thread never got back to the message queue | `detail` splits it into on-CPU, waiting for a CPU, and neither |
| `anr` | an ANR the system recorded during the trace | `location` is the platform's own reason, `detail` the error id |
| `repeated_work` | the same named work entered from more than one caller | `detail` names the callers; a `near miss` row means the occurrences are too unlike to be one work |
| `io_wait` | threads the kernel parked waiting for a block device | `detail` says `main` or `background`; there is no code to look at, only I/O to remove or move |

### What matters about individual ones

**`repeated_work`** asks a question of shape rather than of size. Every other
detector gates on a magnitude — a sum, a maximum, a count — and a duplicate is
not a magnitude problem: the repeated half of one real finding was 270 ms among
a dozen other three-hundred-millisecond things. What made it a bug is that the
work had already been done.

Two things follow. It looks only at names no other detector claimed, so a row
here is never a restatement of one above it. And it depends on how markers are
named: a marker takes the name of the work it wraps, never of the place it was
put, or the two entries arrive under two names and there is nothing left to
compare.

**`runnable_starvation`** talks about hardware, not code. A thread in state `R`
is ready to work but the scheduler will not let it in. On an emulator it fires
almost always. Do not send an agent to fix code over this finding — first make
sure the run happened on a real device with nothing else loading it.

**`uninstrumented_cpu`** is the only one that finds a problem inside
uninstrumented code. `covered_ms` far below `total_ms` means the thread worked
and what it did is unknown. This is where adding `AGENTTMP_` instrumentation
and re-recording makes sense. There is no code behind the finding yet; looking
for it is pointless.

**`io_wait`** is about work the app asked the disk to do, and it is the only
detector whose finding has nothing to profile behind it. The thread is in
state `D`, uninterruptible sleep, and the kernel has flagged that sleep as
block I/O. It burns no CPU, holds no lock of yours, and has no slice around
it — which is why a cold start can spend hundreds of milliseconds here while
every other detector stays silent.

`detail: main` is the one to act on first: the main thread parked on the disk
is a frozen frame, and no amount of making the code faster helps, because the
code is not running. The fix is upstream of the code — read less, read it off
the main thread, or read it later.

It names the thread, not the kernel function. The blocking address needs
`/proc/kallsyms` to become a name and that file is unreadable on a production
build: on an SM-A515F the function was empty for all 6683 uninterruptible
sleeps in the trace while the disk flag was set on 6486 of them. If `detail`
carries a function name, the run was on a userdebug kernel and you have a
bonus, not the norm.

Two readings that come with it. If the same scenario is clean on the second
run, the first one was populating the page cache, and that is a first-launch
problem rather than a code one. And a thread in `D` that this detector does
*not* claim was parked for something other than the disk — look for other
threads holding a memory lock, `jit-thread-pool` and file-mapping work being
the usual pair.

**`frame_jank`** is the only detector that answers "which frames stuttered"
rather than "where did the total go", and the only one that needs no
instrumentation: SurfaceFlinger records every frame's deadline and what it
actually took. Read it with three things in mind.

*Its numbers are overruns.* `total_ms` is the time past the deadline summed
across those frames and `max_ms` is the worst single overrun — not durations,
which is what those columns mean everywhere else. The frame's own length is in
`detail` (`longest 86.2 ms`), because that is the number a benchmark's
percentiles are quoted in and the one to match a FrameTimingMetric run against.

*`detail` leads with whose deadline it was*, and it is the platform's verdict
rather than ours. `Self Jank` is the app — go and look. `Other Jank` is the
compositor or the display, and sending anyone into the app's code over it
wastes a round. `Buffer Stuffing` is the queue backing up.

*Silence has two causes here.* No bad frames, or no frame timeline in the
trace at all — Android 11 and below, or a capture that did not ask for the
`android.surfaceflinger.frametimeline` data source. Those look identical in the
report. Before calling a scenario smooth, check that some other frame row
exists or that the trace came from `echolot collect`.

**`anr_risk` measures a length of time, not a name.** The others ask how much
went into one slice name, or whether one occurrence was unusual. This asks how
long the main thread went without returning to the message queue — fifty
different pieces of work back to back freeze an app exactly as one long one
does, and nothing that groups by name can see that.

Its bar is the platform's five seconds and it is never calibrated. On a
cold-start scenario the window is a second or two, so it is silent by
construction; it is for the longer recording an ANR hunt collects. `detail`
splits the stretch three ways and that is the next step: time on a CPU points
at the code, time waiting for one at something else on the device, and the
remainder at a lock or a disk — read `monitor_contention` next when the
remainder dominates.

**`anr` is the only detector not clipped to the scenario window**, and its
`detail` says where the record fell relative to it. An ANR fires five seconds
after the event that could not be served, and a cold start's window closes at
the first frame — clipped, it would be absent from nearly every trace that
contains one. Its `location` is the platform's own reason (`Input dispatching
timed out …`), which no Crashlytics export carries, and `detail` holds the
error id: the same string the device's `dumpsys dropbox` record has, so a
trace and a report can be matched by hand.

**`main_thread_outlier`** is the pair to `main_thread_block`, and reading one
as the other wastes a round. `main_thread_block` gates on the **sum** for a
name and answers "where did the main thread's time go". This one gates on a
**single occurrence** against the median for that same name, and answers "which
one was out of line". A name can appear in both, saying different things.

Take it to the code differently, too. A `main_thread_block` row means the work
is expensive every time it runs, and the fix is in that work. A
`main_thread_outlier` row means the work is usually fine and once was not, so
the cause is the state it hit that once — a cold cache, a lock, a first-run
path, an allocation that stalled. `detail` gives the median and how many
occurrences it came from, which is the number to compare a benchmark's
percentiles against.

It is also the row a merge across repeats does **not** smooth away. Its rows
only exist in the runs where something was out of line, so `runs: 1/10` means
"once in ten" rather than "we lost it in a median" — the opposite of what
`max_ms` does on a row present in every run.

**`gc_pressure`** also catches the other side: `waitWhileAllocatingLocked` on an
application thread means an allocation stalled waiting for the collector. GC
itself is the symptom; the cause is the number of intermediate objects.

**`binder_txn`** fires both on one long transaction and on the sum of short
ones. On a live startup there were 76 transactions of about 1 ms — 66 ms
together, an eighth of the cold start, and not one of them stood out alone.

## An empty report

No detector fired — two possibilities, and telling them apart is mandatory:

1. **The run is clean.** The window is plausible, the anchors matched
   (`matches > 0`).
2. **The config missed.** Check `window`: the duration is close to the whole
   trace, the anchors did not match, the process is the wrong one.

The second case is more common than the first.

## The comparison — `echolot compare`

Two Marker Reports in, one table out, sorted by how far each row moved. Written
to `comparison.json` and `comparison.md` beside the report.

```bash
echolot compare                      # inside an investigation: previous round vs latest
echolot compare --hunt 3             # its first report against its last
echolot compare old.json new.json    # or name them
```

```json
{
  "kind": "comparison",
  "comparable": true,
  "warnings": [ { "id": "thresholds", "text": "…" } ],
  "window": { "before_ms": 1184.0, "after_ms": 2960.4,
              "delta_ms": 1776.4, "ratio": 2.5 },
  "summary": { "moved": 4, "appeared": 2, "vanished": 1, "steady": 17,
               "state_changed": [ { "id": "binder_txn",
                                    "before": "silent", "after": "1 row(s)" } ] },
  "rows": [
    { "location": "TeamRepository.loadAll", "detector": "main_thread_block",
      "metric": "self_ms", "change": "grew", "matched_by": "exact",
      "before": { "self_ms": 12.1, "min": 10.4, "max": 14.0, "count": 1 },
      "after":  { "self_ms": 883.4, "min": 840.1, "max": 931.7, "count": 1 },
      "delta_ms": 871.3, "ratio": 73.0, "overlap": false }
  ]
}
```

**`change`** is `appeared`, `grew`, `shrank`, `vanished` or `steady`. Rows are
sorted by the size of the move, so the first one is usually the answer. Steady
rows stay in the JSON and are collapsed to one line in the markdown; nothing is
dropped silently.

**`overlap`** answers whether the repeats support calling it a change. `false`
means the two sets are apart — every run after was outside everything seen
before. `true` means they intersect, so the runs disagree among themselves by
more than the medians moved: record another round before concluding. `null`
means one side was a single trace.

**`count` before and after** separates "called more often" from "became slower
inside". `inflate` at 12 → 31 occurrences and `loadAll` growing 73× at one
occurrence are different bugs in different places.

**`matched_by`** is `exact` or `family`. Rows are paired by name first and by
name family second — `arch_disk_io_0` and `arch_disk_io_3` are one pool.
The family pass only fires when exactly one row on each side is unmatched;
otherwise the rows stay listed as appeared and gone rather than guessed at.

**`warnings`** — read before the table:

| `id` | what it means |
|---|---|
| `thresholds` | detector parameters differ. **appeared** and **gone** mean the bar moved, not that the app changed. Re-run both with `--defaults` |
| `instrumentation` | rows that appeared are `AGENTTMP_` markers added between the rounds — a breakdown of a blind spot, not new work |
| `process` | two different apps. `comparable: false` |
| `defaults` / `config` | one side used `--defaults`, or the config's hash changed |
| `anchor-before` / `anchor-after` | that side's window is the whole trace |
| `runs` / `single` | different repeat counts, or a single trace with no spread to test against |
| `detectors` | the two runs did not use the same set of detectors |

A row is listed as moved when it changes by more than 5 ms or 10 %, whichever
is larger — `--floor-ms` and `--floor-pct` change that. The exit code is 0
whatever the comparison finds.
