# Row columns

The contract is shared, but not every detector fills every column.

| column | meaning |
|---|---|
| `location` | the slice or thread name — what you hook onto; `main` for the main thread's row where the detector says so below |
| `threads` | on a row named by a thread, how many threads it adds up — a pool's workers share one cut name |
| `runs` | in a merged report, how many repeats the row was found in, of how many — `3/5` |
| `count` | how many times it occurred inside the window |
| `self_ms` | self time, with children subtracted |
| `total_ms` | total time, children included |
| `max_ms` | the longest single occurrence |
| `covered_ms` | how much on-CPU time ran inside instrumented code |
| `code` | where that is in the checkout, when the row names a method or a class — see below |
| `detail` | the evidence: thread, state, lock name with the owner's tid; in a blind spot of a sampled recording, what the samples named |
| `places` | the json behind `code`: every symbol the row names, with `file`, `line` and `role`, and `retraced` on one the build's mapping named back |
| `stacks` | the json behind what `detail` says the samples named: `samples`, `with_stack`, and the ten largest of `leaf` and `ours`, each `frame` with its `samples` and `pct` |
| `spread` | the per-run values behind the medians, for three columns — see below |
| `at` | where the longest occurrence is on the timeline: `ts` and `dur` in the trace's own nanoseconds, `tid` and `thread` where it ran on one, and `run`, the repeat's index in `traces` — see below |

**The rows come ranked, and in one order everywhere.** One trace orders them
by `self_ms`, or `total_ms` where there is no self time. A merged report ranks
them by that median times the share of repeats in `runs`: the median is over
the repeats that found the row, so a 200 ms spike in `1/5` counts for 40 ms
and comes after 60 ms in `5/5`. `report.json`, `report.md`, the overview's top
row and `report -d` all keep that order; `report -d` shows the first five and
says how many it left out, and how many of those one repeat alone found.

**`code` and `places` save the grep.** A contention slice names both sides of
the lock — the thread holding it and where it is, the method that waited and
where that is — and `main_thread_block` names a class when the slice is a
View being inflated. A blind spot with samples behind it names the project's
method the samples fell under most, as `sampled`. `analyze` looks those up
in the checkout the config sits in and writes what it found:

```json
"code": "owner at StoreRepository.kt:30 · blocked at StoreRepository.kt:61",
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

A minified build's lock slice says `a.a.run()(SourceFile:6)`. With
`project.mapping` set, both frames are named back before they are placed,
and such a place carries `retraced: true`: `symbol`, `file` and `line` are the
source's, and the evidence still shows R8's names. Without the mapping the
places name `a.a.run` with `file: null`. When the header warns that lock
frames have no place in `project.mapping`, the mapping is from another build.

**`spread` is what makes a number checkable.** Merging repeats reduces each row
to a median, and a median cannot say whether a number is steady: 120 ms from
(118, 119, 121) and 120 ms from (12, 120, 890) read identically, and only the
second means the next run will say something else.

```json
"spread": { "self_ms": { "min": 118.2, "max": 340.1,
                         "values": [118.2, 121.0, 125.4, 133.7, 340.1],
                         "resolves_ms": 214.7, "resolves_pct": 171.2 } }
```

Present for `self_ms`, `total_ms` and `max_ms`, whichever of them the row
carries. The one to read is the ranking metric's — `self_ms` where the
detector measures it, `total_ms` otherwise — which is also the one `compare`
tests. `values` holds one entry per repeat the row was **found in**, which is
what `runs` counts: a `3/5` row has three. Absent when `analyze` ran on a
single trace — there is nothing to spread.

`resolves_ms` is how far this row's strays reach against as many runs spread
the same way; `resolves_pct` is the same against the median. A later
`compare` calls a move real when it is larger than the row's floor (5 ms or a
tenth of the row) and this together, so it is the number
to read before promising anyone a comparison: the row above resolves nothing
under 215 ms, because one run in five went slow. It shrinks with the square
root of the runs, four times the runs for half the move. Absent below four
runs, where nothing can be 95% sure.

Use it before acting on a number. A row whose `max` is several times its median
holds one slow occurrence rather than a steady cost, and that is a different
bug in a different place.

**`self_ms` and `total_ms` are not duplicates.** The first answers "where was
it spent", the second "how long did it take altogether". A slice with
`total_ms: 354` and `self_ms: 79` barely works itself — it waits on its
children, and that is where to dig. Never add `total_ms` across rows: they
nest. `self_ms` adds up within one detector's rows — self times do not
overlap — and not across detectors: a disk wait inside a slice is in both.
How much of the window the findings cover is `window.main_thread.in_rows_pct`.

**A row named by a thread can be a whole pool.** The kernel cuts a name to
fifteen characters, so every coroutine worker is `DefaultDispatch`, and
`monitor_contention`, `runnable_starvation`, `uninstrumented_cpu`,
`binder_txn` and `io_wait` add a name's threads up. `threads` says how many,
and the report says it under the table. Eight workers' 3.8 s of waiting in a
5.7 s cold start is their time together, not time of the start: there
contention was 2% of the start, which `--window` says. Read a row with
`threads` above one as the pool's, never as the window's.

**`at` is where to look, not a number to read.** A detector with a
`-- @longest` query names, for each row, the occurrence its `max_ms` is the
longest of (`frame_jank`: the frame that overran its deadline the most, `dur`
its own length), and a set takes it from the repeat where that is. It
decides nothing and is not compared. `report.md` turns it into a link that
opens Perfetto UI zoomed to the occurrence with its thread pinned, for a
person who then opens the trace there; `echolot ui` opens it with the trace.
To point someone at a moment, name the trace (`traces[run]`) and the
occurrence; there is nothing in it to measure from. A row of a detector
without `@longest`, or of a report written before it, has none.
