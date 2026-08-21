# Detectors

[← Docs index](README.md) · [README](../README.md)

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

Not everything worth detecting is a slice. `frame_jank` reads
`actual_frame_timeline_slice` and `expected_frame_timeline_slice` directly —
SurfaceFlinger's own per-frame record, which has no context view because
nothing else needs it. A detector reaching outside the views takes on two jobs
the views were doing for it: joining `_proc` so it stays inside our process,
and clipping to `_window` itself. Both are visible at the top of that file.

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

### A slice that never closed is the longest one, not a zero

trace_processor gives a slice still open when the trace stopped `dur = -1`.
`_slice_win` reads that as running to the end of the window and sets
`unfinished` beside it, so a detector that wants to say "at least" has the fact
rather than inferring it from a number that happens to land on the window's
edge.

The reading matters more than it sounds. On a real freeze the main thread sat
in ART's contention slice for twenty seconds, the lock was never released, the
slice never closed — and folding that to zero made the worst thing in the trace
the one thing invisible in it. Two detectors missed it and neither had a bug of
its own.

### A stretch is a third shape, and the window is not always right

`anr_risk` asks neither "how much went into this name" nor "was this one
occurrence unusual" but "how long did the main thread go without returning to
the message queue". Fifty different pieces of work back to back freeze an app
exactly as one long one does, and a detector that groups by name cannot see it.

It takes evidence from slices at depth 1 and below, plus the thread's own
states. **Depth 0 is deliberately excluded.** A scenario anchor — `AppStart`
around a cold start, and every marker of that shape `echolot mark` proposes —
is a top-level slice covering the whole run including its idle moments.
Counting it would report every scenario as one unbroken stall on any project
that has an anchor at all, which is every project this tool asks to add one.

`anr` breaks the other rule: it is the one detector not clipped to the scenario
window. An ANR fires five seconds after the event that could not be served, and
a cold start's window closes at the first frame. Clipped, it would be absent
from nearly every trace that contains one — and absence reads as "no freeze",
which is the thing that detector exists to contradict. It reads the whole trace
and says in `detail` where the record fell relative to the window.

A detector needing a Perfetto stdlib module declares it in the header:

```sql
-- @module: android.anrs
```

The runner includes it once per session, before any query. It belongs in the
header rather than in the SQL because a detector runs as a single statement,
and an `INCLUDE` in front of the `SELECT` would make it two.

### Sums and single occurrences are different detectors

Most of the shipped ones gate on an accumulated sum for a name: how much time
went into `inflate` across the window. That is the right shape for "the whole
thing got slower" and blind to a heavy tail — one occurrence of 86 ms among
thousands dissolves into any sum you care to take, which is how a benchmark
reporting P50 14.7 ms and P99 86 ms met silence from six detectors at once.

`main_thread_outlier` is the other shape: a single occurrence against the
median for that same name. Worth knowing before writing a detector, because
the question decides the gate, and a `HAVING SUM(...)` answers only one of
them. Two floors go under a ratio, or it is worthless at small sizes — an
absolute one, so "four times longer than 0.5 ms" is not a finding, and a
minimum number of occurrences, because a name seen three times has no typical
duration to be an outlier from.

### A column may mean something else, if the header says so

The contract is shared, and one detector bends it. In `frame_jank` `total_ms`
and `max_ms` are time **past the deadline** rather than duration, because the
loss is the actionable number and the frame's own length is not. The frame
duration is in `detail` instead, since that is the number a benchmark's
percentiles are quoted in.

That is allowed and it is not free: anything reading the report generically —
`compare`, the markdown table — treats the numbers as milliseconds and gets
milliseconds, but a reader who assumes duration reads them wrong. So the
deviation is stated in the detector's own header, in the report reference, and
here. A detector that bends the contract quietly is a bug.

### Window functions are available, and worth reaching for

The bundled SQLite is 3.50 and has had window functions since 3.25. A median
per group — which SQLite has no aggregate for — is one sort rather than a
correlated subquery per group:

```sql
ranked AS (
    SELECT name, dur,
           ROW_NUMBER() OVER (PARTITION BY name ORDER BY dur) AS rk,
           COUNT(*)     OVER (PARTITION BY name)              AS n
    FROM _slice_win
),
baseline AS (SELECT name, n, dur AS median_ns FROM ranked WHERE rk = (n + 1) / 2)
```

`main_thread_outlier` is built on exactly that. The alternatives considered
before checking what the bundled SQLite could do were a per-group `ORDER BY …
LIMIT 1 OFFSET n/2`, an `AVG` that the outlier itself would drag, and computing
the base in Python and substituting numbers. None was needed.

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
