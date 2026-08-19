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
  "summary": { "detectors_run": 6, "detectors_fired": 4,
               "fired_ids": ["main_thread_block", "…"] },
  "config": { "path": "/abs/project/echolot.yml", "sha": "fadc1a11b903",
              "local": null, "defaults": false, "set": null },
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

**`detectors[].params_source`** — where this detector's thresholds came from:
`default` (the numbers shipped in the .sql), `config` (calibrated or
hand-set in `echolot.yml`), `cli` (`--set`), or `config+cli`. When it is not
`default`, `defaults` holds the shipped values for the overridden parameters,
so you can see how far the bar moved without running `explain`. A silent
detector with calibrated thresholds means "nothing above the calibrated
bar" — not necessarily "nothing above the default one".

## Check these before drawing conclusions

**`window.start_anchor.matches == 0`** — the anchor never matched and the
window expanded to the whole trace. None of the numbers are about your
scenario. Do not investigate, fix the config: look at the real names via
`echolot probe` and correct `scenario.start`.

**`window.process_alternatives` present** — the mask matched several processes
and the largest by slice count was taken. If you are analysing `:pushservice`
instead of the main process, narrow `project.process`. The list holds the
next few by slice count; `process_alternatives_total` is how many there were.

**`detectors[].error != null`** — that detector failed while the rest ran. SQL
is version-fragile; report the error, but do not treat the absence of findings
as an answer.

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
| `detail` | the evidence: thread, state, lock name with the owner's tid |
| `spread` | the per-run values behind the medians, for two columns — see below |

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
| `main_thread_block` | where the main thread spent its time | `location` — the slice name |
| `gc_pressure` | collection cycles and allocation waits | frequent GC = many intermediate objects |
| `monitor_contention` | monitor contention | `detail` carries the owner's tid |
| `binder_txn` | synchronous IPC into another process | `count` and `total_ms`, not just `max_ms` |
| `runnable_starvation` | thread ready but preempted | this is about the device, not the code |
| `uninstrumented_cpu` | threads burning CPU with no instrumentation | an address for adding `trace{}` |
| `frame_jank` | frames that missed their deadline | `location` is why it missed, `detail` says whose fault |

### What matters about individual ones

**`runnable_starvation`** talks about hardware, not code. A thread in state `R`
is ready to work but the scheduler will not let it in. On an emulator it fires
almost always. Do not send an agent to fix code over this finding — first make
sure the run happened on a real device with nothing else loading it.

**`uninstrumented_cpu`** is the only one that finds a problem inside
uninstrumented code. `covered_ms` far below `total_ms` means the thread worked
and what it did is unknown. This is where adding `AGENTTMP_` instrumentation
and re-recording makes sense. There is no code behind the finding yet; looking
for it is pointless.

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
name family second — `DefaultDispatcher-worker-2` and `-worker-5` are one pool.
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
