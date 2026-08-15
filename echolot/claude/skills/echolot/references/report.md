# The Marker Report — what to read and how to read it

`echolot analyze` writes two files into `.echolot/out/`: `report.json` for you
and `report.md` for humans. One set of data, two formats.

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
  "detectors": [
    { "id": "…", "title": "…", "why": "…",
      "params": { … }, "rows": [ … ], "error": null }
  ]
}
```

## Check these before drawing conclusions

**`window.start_anchor.matches == 0`** — the anchor never matched and the
window expanded to the whole trace. None of the numbers are about your
scenario. Do not investigate, fix the config: look at the real names via
`echolot probe` and correct `scenario.start`.

**`window.process_alternatives` present** — the mask matched several processes
and the largest by slice count was taken. If you are analysing `:pushservice`
instead of the main process, narrow `project.process`.

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
