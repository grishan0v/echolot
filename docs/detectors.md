# Detectors

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/detectors.md)

A detector is one self-contained `.sql` file. Metadata in the header,
`{{param}}` placeholders substituted from the config:

```sql
-- @id: my_detector
-- @title: What we are looking for
-- @why: why it matters
-- @param: threshold_ms = 50 ms — how long one slice has to take to count
-- @identity: location, detail
-- @column: location — the slice name
-- @column: count — its slices that took the threshold or longer
-- @column: total_ms — their time, summed
-- @column: max_ms — the longest one
-- @column: detail — the thread they ran on
-- @plain_title: Work that takes long
-- @plain_why: One or two plain sentences on how it touches the app.
-- @plain_clean: What a result with nothing in it says.

SELECT name AS location, COUNT(*) AS count,
       ROUND(SUM(dur)/1e6, 2) AS total_ms,
       ROUND(MAX(dur)/1e6, 2) AS max_ms,
       thread_name AS detail
FROM _slice_win
WHERE dur >= {{threshold_ms}} * 1000000
GROUP BY name, thread_name
ORDER BY total_ms DESC
LIMIT 20;
```

Drop the file into `echolot/sql/detectors/` and it runs. There is no
registration in code, and `@param` values are the defaults the project config
overrides. Running is not the same as shipping, though — what else a new
detector needs is in [the checklist below](#adding-a-detector).

The column contract is shared: `location`, `count`, `self_ms`, `total_ms`,
`max_ms`, `covered_ms`, `detail`. Not every detector fills every column, but
the report renders uniformly and the agent reads a stable schema. A detector
that groups by thread name fills `threads` too: how many threads the row adds
up, since the kernel cuts a name to fifteen characters and a pool's workers
arrive under one. Where it tells the main thread apart by its tid, the main
thread's row is `main`.

What a parameter and a column mean is said in the header too, because a
name does not say it: `max_total_ms` of `binder_txn` is a floor, and
`frame_jank`'s `max_ms` is a frame's overrun past its deadline, not its
length. A number in `@param` is followed by its unit, a string by nothing,
and the words come after a dash, going on below for as many lines as they
need:

```sql
-- @param: min_overrun_ms = 4 ms — how far past its deadline a frame has to
--       finish to count as late at all
-- @param: name_glob = *GC — GLOB over slice names: ART's collection cycles
-- @column: max_ms — the worst frame's overrun past its deadline
```

A detector writes a `@column` line for every column its query returns,
after `@identity` and before the `@plain_*` keys, saying what each holds in
this detector: what `count` counts, what `detail` carries. `echolot explain
<detector>` prints them, with each threshold's value in this project, so the
words live here and nowhere else.

`@identity` names the columns that tell one row of the result from another —
what the query groups by, as it reaches the report. It defaults to `location`
and has to include it. The example groups by the thread as well as the name,
so it says `location, detail`. Left at the default, two threads running the
same name come out as two rows of one trace, and merging repeats — which folds
rows on their identity — puts a single median across both. `compare` pairs
rows on the identity too, and would match either thread against either. A
column named there is part of the row's name rather than evidence, which is
also why `compare` shows `detail` in its Evidence column only for detectors
that list it.

So a column in the identity carries nothing that can change from one repeat
to the next. `io_wait` is named by `location, thread`: `thread` says `main` or
`background`, and the kernel functions a thread stopped in stay in `detail`.
`runnable_starvation` is named by `location, state` the same way: the state,
`R` or `R+`, is the row's name, and who held the main thread's core is
evidence in `detail`. `binder_txn` and `gc_pressure` are named by
`location, thread`, and their `detail` says who answered a thread's calls
and what a collection freed. Each of the three had its thread or state in
`detail` once, and `compare` reads it back out of an older report's `detail`,
so a report from before the move still pairs row for row.
Which functions those are, and in what order, changes between repeats, and
with them in the name one thread became a row per variant, each seen once.

A detector measures every row, and its thresholds only say which rows to
show. Its query has no threshold in it; `@fires` is one predicate over the
columns it returns, and `analyze` wraps the query as `SELECT *, (<fires>) AS
over_bar FROM (…)`:

```sql
-- @fires: max_ms >= {{min_txn_ms}} OR total_ms >= {{max_total_ms}}
```

The report shows the rows the predicate passed. The rest it measured, down
to 1 ms of the row's metric, are kept as the detector's `below`: the merge
of repeats reads them with the rows shown, as one measurement, so a row
under the bar in one repeat is still there, with its number (`runs` counts
where it was found, `fired` where it passed). `compare` sets a row of one
side against what the other side measured, whatever either side's bar was,
and `calibrate` reads the same rows. A threshold that a query held in its
`WHERE` or `HAVING` dropped those rows before anyone else could see them,
and every reader had to rebuild them (#505, #715, #832).

A number the query does read decides what a row is, not whether it is
shown: `main_thread_outlier`'s `factor`, how far past its deadline a frame
counts as late. Such a parameter is named in `@shape`, and a string
parameter is a mask, a GLOB of what is counted. Every other number is in the
predicate. A test holds that, per detector, the rows shown at any
thresholds are every row measured, filtered by them. Every shipped
detector has `@fires`. One a project writes without it shows every row its
query returns, and has nothing under the bar.

`@needs` names what the trace has to hold for the detector's silence to mean
anything, and stands straight after the `@why` block:

```sql
-- @why: the thread is neither running nor runnable…
-- @needs: platform_state
-- @param: min_io_wait_ms = 20
```

A detector that reads only what every recording `collect` makes holds — the
app's slices, its threads' states, who woke whom — declares nothing. The
sources, and what `analyze` does with them, are
[below](#when-the-trace-does-not-hold-what-a-detector-reads).

`@plain_title`, `@plain_why` and `@plain_clean` are the detector in words for
someone who has never opened a trace: what was checked, how it touches the app,
and what a clean result says. One or two sentences each, with no jargon: they
are fixed text, shown as they are by `echolot report --plain` and kept in
`report.json`. They are the last keys of the header, followed by a blank `--`
line, since a line of prose straight after a key is read as the rest of its
value. [A plain reading](analysing.md#a-plain-reading) has the rest.

## The context views

They are already narrowed to our process and the scenario window, so nothing
project-specific belongs inside a detector.

| view | what is inside |
|---|---|
| `_proc` | the target process (exactly one) |
| `_slice` | every **thread** slice of the process, with its thread and an `is_main_thread` flag |
| `_aslice` | the process's **async** sections — `Trace.beginAsyncSection`, on a track of the process and on no thread |
| `_anchor` | `_slice` and `_aslice` together, name and time only — what the scenario anchors match |
| `_window` | `ts_start` / `ts_end` of the scenario window |
| `_slice_win` | thread slices **overlapping** the window |
| `_aslice_win` | async sections **overlapping** the window |
| `_tstate_win` | thread states **clipped** to the window |
| `_cpu_in_slice` | time **on CPU inside** top-level slices |
| `_samples_by_slice` | the process's **callstack samples** in the window, each marked inside a top-level slice or outside every one |
| `_claimed_name` | slice names a `*name_glob*` already speaks for |
| `_sched_win` | what **every CPU** ran in the window, and whose it was: `app`, `other`, or `idle` |
| `_main_runnable_held` | the main thread's **waits for a CPU**, split by what ran on the core it got next |
| `_main_runnable_idle` | the same waits against the **other cores' idle** time |

The async ones are kept apart on purpose. A section on no thread has no place
in a sum per thread — counted with the rest it would land under no thread, at
a depth of its own, and every self-time and coverage figure would be off by
its length. So no shipped detector reads `_aslice_win`. The markers table in
every report does: the project's own names are usually async, and it measures
them by name rather than by thread.

`_claimed_name` is the set's own vocabulary, collected from every shipped
detector's masks with the project's overrides applied. It is read by
`repeated_work` and `app_init`, the two detectors that look at every slice
name in their stretch: they ask about shape, not about a family of names, and
`NOT IN (SELECT name FROM _claimed_name)` is how they stay off names that
belong to another question. Widening a `*name_glob*` mask in `echolot.yml`
takes those names out of both.
Twice it reported a finding that was already in the report under its own
heading — an ART lock wait, then a GC phase — and this is the rule that
replaced naming those families one at a time.

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

The rest of the pipeline reads an open interval the same way:

- **the window's end.** An end anchor that never closed ends the window at the
  end of the trace, and the report marks it `end_anchor.unfinished`. Read as
  zero long, it cut the window off where the scenario got stuck. With no end
  anchor, the window ends where the process's last slice does, and an open one
  runs to the end of the trace.
- **thread states.** `_tstate_win` reads a thread's last state, still open when
  the recording stopped, as running to the end of the window. Dropped, it took
  each thread's last stretch out of the budget, `io_wait`,
  `uninstrumented_cpu` and `anr_risk`.
- **children.** `_child_sum` reads an open child as running to the end of the
  window, and stops each child of an open slice there, so self times still add
  up to no more than the window.

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

The runner includes it once per session, before any query, in `analyze` and
in `calibrate` alike. It belongs in the header rather than in the SQL because
a detector runs as a single statement, and an `INCLUDE` in front of the
`SELECT` would make it two.

A module is worth its include where its tables say what a slice's name does
not. A `binder transaction` slice names neither the process that answered it
nor the method; `android.binder` follows the flow trace_processor draws from
it to the server's `binder reply`, and `binder_txn` reads
`android_binder_txns` for both. A collection's slice does not say what it
freed; `android.garbage_collection` reads the runtime's heap counter around
it, and `gc_pressure` reads `android_garbage_collection_events`. Both join
the module on the slice's id for the evidence only: which slices count, and
the numbers, come from the window's slices as before. A trace where the
module holds nothing — binder slices without the driver's events behind
them, a recording without the heap counter — gives the same rows with less
to say, and the fixture holds both detectors to that (`fixture.build(answered=False,
heap=False)`). Where a module only reads the slice's name again, as
`gc_type` and `is_mark_compact` do, it adds nothing the row's `location` does
not already say. The include costs its time on every trace: on a large app's
cold start, about 0.3 s for these two together, 0.08 s of it `android.binder`.

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

Its `Buffer Stuffing` row bends it further. The platform tags a frame that way
when the app queued it while the frame before was still waiting to be shown:
it reaches the screen a frame or more late, without a stutter. That is
latency, not a missed frame, so the row keeps its count and has 0 in both
columns: it ranks after every late frame, the plain reading never weighs it,
and `compare` has no summed wait to call a move. Its `detail` leads with
`latency, not jank` and says how far behind the frames were. Summed with the
late frames, on a scroll where the render thread took most of every frame,
it was 1263 of 1446 frames and 16 s past the deadline in 40 s, the first row
above the 28 frames the app really missed. A frame that also missed a
deadline is tagged by that (`App Deadline Missed, Buffer Stuffing` is
`Self Jank`) and stays a late frame; its overrun includes the wait, and its
`detail` says so.

`composables` bends `self_ms` the same way. A composable's own slice holds
about a millisecond, the rest being the library's composables under it, so
its `self_ms` is the calls less the app's own composables nested in them,
the library's counted in. The rows still add up without counting a moment
twice, which is what self time is for.

That is allowed and it is not free: anything reading the report generically —
`compare`, the markdown table — treats the numbers as milliseconds and gets
milliseconds, but a reader who assumes duration reads them wrong. So the
deviation is stated in the detector's own `@column` line, which `echolot
explain` prints, in the report reference, and here. A detector that bends the
contract quietly is a bug.

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

Hence the phases: `context.sql` finds the window, the CLI reads it, and
`window.sql` builds everything else on top of plain numbers. `environment.sql`
is a third, run on the same numbers right after it: the clock, the
temperature, throttling and memory the report states every duration under.
Nothing in it is a detector — no threshold, no finding — and its views are
there for a detector that wants them all the same.

### Why the window's own sets are tables

The same holds one level up. A view is a query with a name, not rows: each
reference to `_slice_win` ran the join under it again, over every slice of the
process, and every detector reads it, most more than once. On trace_processor
v56.1 `app_init`, which names it four times, took 25 of 48 seconds of five cold
starts of a large app. On v59.1 fifteen of them took 225 s one trace at a time,
and take 57 s with the window's sets built once. So `_slice`, `_slice_win`,
`_child_sum`, `_aslice_win` and `_tstate_win` are `PERFETTO TABLE`s:
trace_processor's own columnar tables, built once when the script runs and
read as stored rows after that. The rest stay views, cheap or read once. A
detector reads both the same way.

`_slice_win` is indexed by `slice_id`. A view over `slice` handed a join on the
id down to slice's own lookup; a table has none until it is given one. Without
it, `app_init`'s walk down the androidx.startup tree, which joins `_slice_win`
on the id of every child at every step, read the whole window for each row:
2.5 s a query on one cold start, asked three times per trace of a set, three
quarters of the trace's time.

Each table is dropped with `DROP TABLE IF EXISTS` before it is made, and each
view with `DROP VIEW IF EXISTS`. trace_processor refuses the other pairing, and
`doctor` runs the scripts twice in one session to hold them to it.

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

## What time the rows stand for

A row says how much. The report also says how much of the window the findings
cover — `Covered by the findings below` under the budget,
`window.main_thread.in_rows_pct` in the JSON — and that needs to know *which*
moments each row stands for, so that a moment two rows describe is counted
once. A line reading `-- @intervals` after the query starts a second query
that says so:

```sql
ORDER BY total_ms DESC
LIMIT 20;

-- @intervals
SELECT s.ts, s.dur
FROM _slice_win s
JOIN _rows r ON r.location = s.name AND r.detail = s.thread_name
WHERE s.is_main_thread = 1;
```

`analyze` runs it straight after the first, with the same thresholds and with
the rows the first returned in a table named `_rows` — `location` and
`detail`, the latter NULL where a row has none, and each other column of the
detector's `@identity`, `thread` or `state`. It returns `ts` and `dur` in
nanoseconds, one line per stretch of the **main thread's** time. Stretches may
overlap and may run past the window: they are clipped and every moment is
counted once. A row about another thread returns nothing, because the
window's length is made of the main thread's time; a background thread
working in parallel explains none of it until the main thread waits for it,
and then the waiting is the main thread's, where a row of its own can stand
for it.

What decides the query:

- **The marker comes straight after the first query**, so that the first still
  ends in its `LIMIT` — `calibrate` strips it from there.
- **Stand for what the row counts.** `main_thread_block` counts self time, so
  its stretches are the slices with their children taken out; a detector that
  counts whole slices returns whole slices. Which rows cleared a threshold is
  already in `_rows` and is not decided again — only a selection inside a row
  is, the way `main_thread_outlier` picks the occurrences past its factor.
- **A slice that spans the whole window is the window.** Usually it is the
  scenario's anchor, and its self time is the part of the window no child
  accounts for. `main_thread_block` leaves it out, for the reason `anr_risk`
  does not take depth zero as evidence.
- **Rows that are not the main thread's time say so** with a query that
  returns nothing, and a comment saying why: `frame_jank`'s lateness is on the
  display's timeline, and `anr` is a record of the system giving up. A
  detector with no second query at all is named in the report as not counted.
- **Pieces found in steps are written down once.** Where the second query
  would have to take the first one's steps again — `app_init`'s stretch after
  `makeApplication` and the sections in it, `anr_risk`'s stretches and the
  name each is reported under, the waits of `main_thread_waits` with the slice
  each sleep fell in — the first query writes its pieces into a table of the
  detector's own before the statement that returns the rows, and both read it:

  ```sql
  CREATE OR REPLACE PERFETTO TABLE _det_my_detector AS
  WITH found AS (…), named AS (…)
  SELECT location, ts, dur FROM named;

  SELECT location, COUNT(*) AS count, ROUND(SUM(dur) / 1e6, 2) AS total_ms
  FROM _det_my_detector
  GROUP BY location
  HAVING SUM(dur) >= {{min_total_ms}} * 1000000
  ORDER BY total_ms DESC, location
  LIMIT 20;

  -- @intervals
  SELECT ts, dur
  FROM _det_my_detector
  WHERE location IN (SELECT location FROM _rows);
  ```

  One copy of the logic, and the time cannot drift from the rows; the steps
  are not taken twice on every trace either (#490). The table is named
  `_det_<id>`, or `_det_<id>_` and more, and is written with `OR REPLACE`, so
  that a second run of the query in one session does not fail. A file that writes another name, writes without
  `OR REPLACE`, or reads a `_det_` table its first query does not write is
  refused as it is read. A second query that is one filter over the window's
  tables, as `gc_pressure`'s is, gains nothing from a table and keeps that
  filter.

Every shipped detector has one, and the tests hold each to its own rows: on
the fixture, the stretches behind the rows about the main thread add up to
those rows' own milliseconds.

## What ran there

A line reading `-- @samples` starts a third query, which picks out the
callstack samples behind each row. `uninstrumented_cpu` has one:

```sql
-- @samples
SELECT t.name AS location, s.callsite_id
FROM _samples_by_slice s
JOIN thread t ON t.utid = s.utid
WHERE s.instrumented IS NULL
  AND t.name IN (SELECT location FROM _rows);
```

It sees the same `_rows` as the second query and returns `location` and
`callsite_id`, one line per sample, with a NULL callsite for a sample that
came without a stack. A row is a thread's name, so the samples are every
thread's of that name. The rest is the same for every detector and happens
in `echolot/stacks.py`: each stack is walked from its leaf, and two things are
counted — what ran, the first Kotlin or Java method with a name, and the
nearest frame of the project's own code, decided the way an ANR's frames are,
or whether the stack had none of it all the way down or was cut short. The
row gets `stacks` in the json and the gist at the end of its `detail`:

```
100.0% of CPU outside slices · 28 stacks: GzipSink.write 54%, ReadBarrier::Mark 21% · ours: Store.save 36%, cut 29%
```

Why those rules, from a phone's stacks, is in [Analysing](analysing.md#detectors-for-code-nobody-traced).

What decides the query:

- **It runs only where a sampler ran.** `analyze` asks the trace whether a
  sampler started, and a trace without samples gets the rows it always got.
- **`_samples_by_slice`, not a join on `ts`.** Placing a sample inside a
  stretch by hand is the nested loop the section above warns about.
  `_samples_by_slice` is `SPAN_JOIN` over `_cpu_by_slice`, with each sample
  given one nanosecond.
- **A `detail` in the identity stays as it is.** There it is part of the
  row's name, and a name that moved with the samples would not merge across
  repeats. Such a row carries `stacks` alone.
- **Repeats keep the evidence of one that has stacks.** The worst repeat
  gives a merged row its `detail` and `stacks`, unless its sampler lost the
  process while another repeat's did not; then the worst of those with
  stacks does.

## What trips people up

Three things that cost an afternoon each. All three are about trace_processor
rather than about SQL, which is why knowing SQL does not save you from them,
and all three were checked against the pinned version rather than taken on
trust.

### `SPAN_JOIN` does not fail on overlapping input — it double-counts

It merges two sets of intervals and needs them not to overlap **within a
partition**. Feed it overlap and there is no error: it produces rows, they
look ordinary, and the durations are wrong.

Two intervals of one partition, `[0,100)` and `[50,150)`, joined against
`[0,200)`:

| ts | dur | tag |
|---|---|---|
| 0 | 100 | first |
| 50 | 100 | second |

That sums to 200 against a true union of 150. The overlapping fifty is
counted twice, and any detector that sums durations — coverage, on-CPU time,
a share of the window — reports a number that is too large with nothing
anywhere saying so.

So the guarantee has to come from the input, and it has to be written down:

    DROP TABLE IF EXISTS _cpu_in_slice;
    CREATE VIRTUAL TABLE _cpu_in_slice
    USING SPAN_JOIN(_running_span PARTITIONED utid, _top_slice_span PARTITIONED utid);

`PARTITIONED utid` is safe here because two Running intervals of one thread
cannot overlap. `PARTITIONED cpu` in `environment.sql` is safe for a different
reason: one core runs one thread at a time. Both are properties of the machine
rather than of the query, so say which one you are leaning on — the next
person cannot re-derive it from the SQL, and nothing will tell them they were
wrong.

### Read the schema, do not guess it

`SELECT * FROM <table> LIMIT 1` prints the column names along with a row.
`LIMIT 0` is the tidier-looking version and is useless through the Python API:
zero rows means zero column names, because the columns arrive as the keys of
the rows.

Two minutes of this beats twenty minutes of a query that runs and answers the
wrong question. `thread_state.cpu` is filled in only while the thread is
Running; `counter_track.type` says which kind of counter a track is; neither
is guessable.

The same goes for what a value looks like after trace_processor has finished
with it. A thermal zone arrives as a track named `cpu-therm Temperature` and
not `cpu-therm`. A temperature arrives in millidegrees, as the kernel wrote it
— `environment.sql` divides by a thousand — while meminfo arrives in bytes
where `/proc` wrote kilobytes. One unit is converted on the way in and the
other is not, and nothing but a query says which. Build a five-line trace
carrying the events you care about, query it, and look.

### A thread's waker is on the state after its sleep

`thread_state.waker_utid` names the thread that woke this one, from
`sched/sched_waking`, and trace_processor writes it on the state the thread
was woken into, the `R` that follows the sleep. The sleep itself carries no
waker. `main_thread_waits` reads it with `LEAD` over the main thread's states
in order; a join of the table to itself on `ts + dur` does the same and costs
far more on a trace of a few hundred thousand states.

### Everything must survive being run twice

A detector runs against a session that already holds the views from the last
one, and a plain `CREATE VIEW` fails the second time. Hence `DROP VIEW IF
EXISTS` in front of every view in the context files.
`CREATE OR REPLACE PERFETTO {TABLE|VIEW|MACRO|FUNCTION}` does the same job for
the Perfetto forms. A virtual table from `SPAN_JOIN` has no `OR REPLACE` and needs the
explicit `DROP TABLE IF EXISTS` shown above.

The symptom is a detector that works alone and fails in a full run, which
sends you reading the query instead of the session.

## When the trace does not hold what a detector reads

A detector with no rows says it ran and nothing cleared its bar. Some are
silent for another reason: the trace does not hold what they read. `io_wait`
reads the kernel's reason for a thread's sleep, which a recording made with
`runner.environment: false` leaves out; `composables` reads names the app
writes only when the recording asked for them; `frame_jank` reads a frame
timeline that phones older than Android 12 do not write. Left alone, the two
silences read alike.

So each detector's header says what it reads, in `-- @needs:`, and `analyze`
asks each trace once for each source a detector in the run names. Every entry
of `detectors[]` in `report.json` carries `checked`, and `reason` when it is
false: one sentence a person can read, naming what the trace lacks. Where the
trace itself cannot say — no composable names might be an app that writes
none, or a recording that never asked — the recording's own account is used:
the config the tracing service writes into the trace, or what the report's
`environment` already holds.

| source | what it is | how the trace says it holds it | what changes the recording |
|---|---|---|---|
| `platform_state` | why a thread went to sleep in the kernel: the `sched/sched_blocked_reason` event, which fills `io_wait` | a thread state with `io_wait` set, anywhere in the trace | `runner.environment: true`, which is the default |
| `frame_timeline` | SurfaceFlinger's record of every frame, its deadline and what it took | a row of the frame timeline, the app's or SurfaceFlinger's own | Android 12 or newer; `collect` asks for it |
| `composable_names` | the names `runtime-tracing` writes, with the Perfetto SDK switched on | `environment.compose_names.calls`: composable calls on the main thread in the window | `runner.compose_names: true`, for an app that carries `runtime-tracing` |
| `atrace:am` | the activity manager's category: the ANR records, `bindApplication` | the recording's own config lists it | `am` in `runner.atrace_categories`, which is in the default list |
| `atrace:dalvik` | ART's category: collections and lock waits | the config lists it | `dalvik`, likewise |
| `atrace:binder_driver` | calls between processes | the config lists it | `binder_driver`, likewise |
| `process_start` | the app's process starting: its `bindApplication`, which a cold start writes and a warm start, a scroll or a tap does not | a main-thread `bindApplication` with a length **in the window** | a window that covers a cold start: a launch scenario's, or a recording that starts the app |

Seven shipped detectors declare nothing: `anr_risk`, `main_thread_block`,
`main_thread_outlier`, `main_thread_waits`, `repeated_work`,
`runnable_starvation` and `uninstrumented_cpu`. `io_wait` and
`main_thread_kernel_waits` need `platform_state`; `frame_jank`,
`frame_timeline`; `composables`, `composable_names`; `anr`, `atrace:am`;
`app_init`, `atrace:am` and `process_start`; `gc_pressure` and
`monitor_contention`, `atrace:dalvik`; `binder_txn`, `atrace:binder_driver`.

`process_start` is the one source asked of the window rather than of the
whole recording (#519). A recording holds a category it asked for wherever
the window lies, but a scroll, a tap, a warm start and a trace measured with
`--whole-trace` that holds no launch have no `bindApplication` in their
window, and `app_init` used to call that silent: "nothing took long before
the first screen" about a first screen that never was. Its reason says the
window holds no start of the app's process, and `report.md` says it in a line
of its own, without the warning sign: nothing is wrong with the recording,
and no other recording of the same window would let it look. A recording
without `am` has no `bindApplication` either, and says that first.

### A cold start's first two seconds run no collection

`@needs` says whether a detector could look at all. `gc_pressure` can look
and still see nothing for a stretch: the first two seconds or so of a cold
start. After the fork from zygote ART holds the heap's target at its growth
limit for `kPostForkMaxHeapDurationMS`, 2000 ms (`runtime/gc/heap.cc`), so
allocations that would start a collection in a settled process start none.
Measured on SM-A515F, Android 13, an app with `largeHeap`: a planted 300 MB
of short-lived arrays in the first half second took the heap to 379 MB with
no collection, against 92 MB without them, and the first cycle came at
1.96 s on both sides; the run held five cycles either way. The garbage went
with the first scheduled cycle, which would have run anyway.

So `gc_pressure` silent over a cold start, or steady in `compare`, says
nothing about allocations made before about two seconds. The heap's size
does: a collection's row says how large the heap grew before a cycle
(`the heap up to … MB`), and the `Heap size (KB)` counter is in every trace
recorded with `dalvik`. A check that plants allocation load to test this
detector on a cold start puts it after the first two seconds, or reads the
heap's size rather than the cycles.

Presence is positive evidence only. A trace that carries no config says
nothing of its categories, so a detector that reads one is not checked on it:
a check marked "not checked" for nothing costs a re-recording, and one marked
fine that never looked costs the answer. In gradle mode the macrobenchmark
decides what to record, and the report says what it found the same way.

What `checked` does not say is whether the checkout holds what a detector
needs of it. `composables` ranks the app's own composables, which the project's
package and the files beside the config name; a trace with composable names
and no checkout to read them against still gives no row.

A detector that failed is not checked either: its `reason` says to read its
`error`. What the trace lacks comes first when both are true. For a set of
repeats a detector counts as checked only when every repeat held what it
reads, and its reason says in how many recordings it did not. A report written
before the keys existed has neither, which means unknown, and never "checked".
`report.md` names the detectors that could not run in one line beside the one
for detectors a config turned off, `echolot report` says `not checked` where it
said `silent`, and `compare` leaves such a detector out on both sides rather
than calling its rows gone or new (see [Comparing](compare.md)).

## Adding a detector

A file in `echolot/sql/detectors/` runs on the next `analyze`. Shipping it
takes more. The first four things below fail the build until they are done.
The other four decide what the tool does with the detector once it runs —
what survives a merge, which overrides it accepts, which masks `names` can
see — and a mistake there fails nothing until somebody reads the report or
writes a config.

- **A problem planted for it in the fixture.** `doctor`'s self-check counts
  the detector files, and "every shipped detector ran, and every one fired"
  fails until `echolot/fixture.py` plants something the new one finds — or
  until `SILENT_ON_FIXTURE` in `echolot/selftest.py` names it, with the reason
  the fixture is the wrong shape to hold one, the way it names `anr_risk`.
  Then the checks that pin what it must find and what it must not, beside the
  others in `selftest.py`.
- **The README.** `tests/test_docs.py` reads the number in "runs sixteen
  SQL detectors" and in the sample report's `Detectors fired: **N of 16**`,
  and fails while either disagrees with the files. The sample report is what
  `analyze` prints in a terminal for the demo app, `echolot/demo.py`, and
  `tests/test_doc_samples.py` holds it to that character for character, down
  to the **Silent** line, which names every detector that did not fire there
  — a new detector included. The README's table of detectors is a picture
  drawn from each detector's `@title`, in the order `DETECTOR_ORDER` in
  `docs/assets/render.py` gives, and the script stops until the new one has
  a place there. Both tests fail until `python docs/assets/render.py` writes
  the sample and draws the table again. The table in
  `references/report-detectors.md` is a list kept by hand, and so are the
  notes under it, each below a heading that is the detector's id, which
  `echolot explain <id>` prints after the header's words.
- **The time its rows stand for.** A shipped detector needs an
  `-- @intervals` query, and a first query that ends in `LIMIT n`:
  `test_every_shipped_detector_has_one` in `tests/test_in_rows.py` fails
  without either, and once the detector fires on the fixture, `doctor`'s
  check that the findings' share of the window counts every detector fails
  too. A file only dropped in still runs without one, and the coverage line
  names it as not counted whenever it fires. See
  [What time the rows stand for](#what-time-the-rows-stand-for).
- **Its words.** The three `@plain_*` texts in the header, and a template in
  `echolot/plain.py` (`FOUND`) that says what its rows found in one sentence.
  `tests/test_plain.py` fails for a detector without any of the four. Write
  the template so that a sentence never claims more than its rows do, and
  decide in `_on_main` whether its rows are time of the main thread: only those
  can need attention by their size. The page (`echolot report --html`) draws
  a line where the check fires for a detector with exactly one `@calibrate`,
  on `self_ms` or `total_ms`: it takes that threshold for a floor under every
  row. `tests/test_report_page.py` pins which detectors those are, so a new
  one that joins them is looked at once: a row below the line would make the
  line wrong. And the words for an agent: what each `@param` means, with a
  number's unit, and a `@column` line for each column the query returns —
  `tests/test_explain.py` fails for a parameter or a column without them, and
  `doctor`'s check that the rows hold the columns the header explains fails
  for a column the query returns and the header does not name.
- **Only part of a row survives a merge.** Repeats are folded row by row, and
  a merged row keeps its `@identity` columns, `runs`, the numeric contract
  columns — `count`, `self_ms`, `total_ms`, `max_ms` and `covered_ms`, as
  medians — `threads`, the most any repeat found, and `detail`, taken from
  the worst repeat unless the identity
  already holds it. `stacks`, from a `-- @samples` query, comes from that same
  repeat, so the evidence and the list it quotes agree. A column the detector
  invents is rendered in a report of one trace and is gone from a report of
  five, so what a reader needs goes into `detail`.
- **A threshold keeps the kind of its default.** An override from
  `echolot.yml` or from `--set` must be what the `@param` default is — a
  number for a number, a string for a string, and a bool is neither — and a
  key the detector does not declare is refused. Both stop `analyze` with exit
  2 before a trace is opened, so the default decides: `16` makes a number of
  the parameter, `*GC` a string.
- **What it reads says so in its header.** A query that reads something a
  recording may lack — a data source, an atrace category, names the app writes
  only when asked — says `-- @needs:` with the source it reads, from
  `needs.SOURCES`, straight after `@why`. A name that is no source is refused
  when the detector loads. `tests/test_needs.py` pins what every shipped
  detector declares, so a change to it is a decision somebody made, and fails
  for a source no detector reads. A new source is an entry in `needs.SOURCES`
  — how the trace says it holds it, and the sentence for when it does not —
  and a case in the self-check, the fixture recorded without it.
- **A mask says so in its name.** A parameter with `name_glob` in its name is
  read as a mask over slice names — by `names`, and by `_claimed_name`, which
  keeps `repeated_work` and `app_init` off what another detector speaks for —
  and `names` reads one with `skip_glob` as an exclusion. A mask named
  anything else still works in the query and is invisible to both.

## Robustness

A failed detector never fails the run: the error goes to stderr and into
`report.json`, and the rest still execute. SQL is version-fragile — failures
are expected and must not cost a run.
