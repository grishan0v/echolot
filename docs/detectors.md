# Detectors

A detector is one self-contained `.sql` file. Metadata in the header,
`{{param}}` placeholders substituted from the config:

```sql
-- @id: my_detector
-- @title: What we are looking for
-- @why: why it matters
-- @param: threshold_ms = 50

SELECT name AS location, COUNT(*) AS count,
       ROUND(SUM(dur)/1e6, 2) AS total_ms,
       ROUND(MAX(dur)/1e6, 2) AS max_ms,
       thread_name AS detail
FROM _slice_win
WHERE dur >= {{threshold_ms}} * 1000000
GROUP BY name, thread_name;
```

Drop the file into `echolot/sql/detectors/` and it is picked up. There is no
registration in code, and `@param` values are the defaults the project config
overrides.

The column contract is shared: `location`, `count`, `self_ms`, `total_ms`,
`max_ms`, `covered_ms`, `detail`. Not every detector fills every column, but
the report renders uniformly and the agent reads a stable schema.

## The context views

They are already narrowed to our process and the scenario window, so nothing
project-specific belongs inside a detector.

| view | what is inside |
|---|---|
| `_proc` | the target process (exactly one) |
| `_slice` | every slice of the process, with its thread and an `is_main_thread` flag |
| `_window` | `ts_start` / `ts_end` of the scenario window |
| `_slice_win` | slices **overlapping** the window |
| `_tstate_win` | thread states **clipped** to the window |
| `_cpu_in_slice` | time **on CPU inside** top-level slices |

### Two duration columns are not a duplicate

`_slice_win` carries both:

- `dur` — the real slice length. This is what humans are shown: a 200 ms block
  stays a 200 ms block even if the window cut it in half;
- `dur_win` — only the part inside the window. For share and coverage
  arithmetic.

Adding up `dur` when computing coverage is a way to get past 100% and miss the
problem. On `_tstate_win` the `dur` field is already clipped, because the
question there is always "how long did the thread spend in this state inside
the scenario".

### Use `_cpu_in_slice`, do not rebuild it

The question "what share of a thread's work ran inside instrumented code"
requires intersecting Running intervals with slices. Written directly as a
`JOIN ... ON` overlap, SQLite executes it as a nested loop: on a live trace
with 475k slices the run did not finish within ten minutes.

The prepared intersection uses `SPAN_JOIN`, a trace_processor operator built
for exactly this — merging two sorted sets of non-overlapping intervals within
a partition. On the same trace it takes 0.1 s. It is built once in the context;
do not write that join by hand.

### Why the window arrives as numbers

`ts_start` / `ts_end` are substituted into these views as literals, not
computed by a view. While the window *was* a view, every reference to
`_slice_win` recomputed it, and on a 475k-slice trace a run did not finish
within ten minutes.

Hence the two phases: `context.sql` finds the window, the CLI reads it, and
`window.sql` builds everything else on top of plain numbers.

## Self time versus total time

`main_thread_block` measures **self** time — the slice's duration minus its
children. The reason shows up immediately on a live trace, where sorting by
total duration produced:

```
d0  355.4 ms  Choreographer#doFrame 55112
d1  354.6 ms  Choreographer#doFrame - resynced to 55120 in 21.1ms
d2  354.4 ms  traversal
```

Three rows about one event. An agent reading that report would count the same
355 ms three times over. Meanwhile the real consumer — `draw` with its own
125 ms — sat fifth, and `TextLayout:initLayout` at nesting depth six never
appeared at all.

Self times do not overlap by construction: their sum across a thread cannot
exceed the thread's own time. That property is pinned by a check.

Total duration stays alongside in its own column, because the pair says more
than either number alone:

| Where | Self, ms | Total, ms | reading |
|---|---|---|---|
| `draw` | 125.6 | 134.3 | works itself |
| `traversal` | 79.7 | 354.4 | mostly waits on children |
| `bindApplication` | 58.7 | 245.0 | the same |
| `TextLayout:initLayout` | 18.5 | 18.5 | a leaf, pure work |

## Two conditions, not one

Several detectors fire on either a single large occurrence **or** an
accumulated total. That is not belt-and-braces either.

On a live cold start the main thread took 76 binder transactions totalling
65.9 ms, the longest 9.71 ms. Not one cleared the 10 ms bar, so the detector
stayed silent while synchronous IPC ate 8.5% of startup. The same shape
appeared with locks: 200 blocks of a quarter of a millisecond each.

Death by a thousand cuts is no less real than one long call.

## Robustness

A failed detector never fails the run: the error goes to stderr and into
`report.json`, and the rest still execute. SQL is version-fragile — failures
are expected and must not cost a run.
