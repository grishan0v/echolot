# Comparing two reports

[← back to the documentation index](README.md)

`echolot analyze` answers where the time went in one set of traces. The
question people actually arrive with has two halves — *it was 3 s, now it is
7 s, where did that go* — and the second half needs two sets.

`echolot compare` takes two Marker Reports and returns one table, sorted by how
far each row moved. The top row is usually the answer.

```bash
echolot compare                       # the open investigation: previous round vs latest
echolot compare --hunt 3              # investigation 3: its first report vs its last
echolot compare old.json              # that report vs .echolot/out/report.json
echolot compare old.json new.json     # exactly those two
```

It writes `comparison.md` and `comparison.json` next to the report, by the same
rule `analyze` uses: a relative `-o` is taken from the config's directory, so
running it from a build folder full of traces still lands the output in the
project. The config is `echolot.yml` in the working directory, or else the
nearest one up the tree within the checkout, so `compare` run from a build
folder finds the project and its open investigation; `-c` names another,
although `--help` does not list the flag. With one report named, the newer
side is `.echolot/out/report.json` next to the config, whatever `-o` says: `-o`
is where the comparison goes. Run from a folder with no config, or with one
that does not load, it writes to `-o` when one is given, taken from the working
directory; with neither it prints the comparison, writes nothing, and says so
on stderr, with the reason when the config is there and does not load.

## What the table says

| Where | Evidence | Detector | Before | After | Δ | N | Holds |
|---|---|---|---|---|---|---|---|
| SyncAdapterThre | — | uninstrumented_cpu | — | 1402.0 ±61 | **new** | — → 0 | — |
| TeamRepository.loadAll | com.example.app | main_thread_block | 12.1 ±2 | 883.4 ±40 | **+871.3 ×73.01** | 1 → 1 | yes, +831.3 … +911.3 |
| inflate | com.example.app | main_thread_block | 47.3 ±31 | 121.9 ±88 | +74.6 ×2.58 | 12 → 31 | no, -13.4 … +162.6 · ~7 runs a side |

**One table, not one section per detector.** The Marker Report is grouped by detector
because each answers a different question. A comparison has one question, so
the detector moves into a column and the rows are sorted across all of them.

**The measure is each detector's own.** Self time where a detector reports it,
total time otherwise — the same rule the report itself sorts by, so the number
in the Δ column is the number the report ranked on.

**`N` is not decoration.** `inflate` doubling at 12 → 31 occurrences means it
is being called more often. `TeamRepository.loadAll` growing 73× at one
occurrence means it became slower inside. Those are different bugs in different
places, and the millisecond column alone cannot tell them apart.

**`±` is the furthest a repeat strayed from the median**, not a standard
deviation. Exact bounds and the per-run values are in `comparison.json`.

**`Evidence` appears where it tells two rows apart.** It is filled only for a
detector that names its rows by `detail` as well as by location.
`main_thread_block` groups by thread, and its evidence is the main thread's
name as the kernel keeps it — `com.example.app` here. Everywhere else the cell
is `—`: evidence that differs between the rounds would be a coin toss to show.
Thread names are cut the same way wherever they appear: `uninstrumented_cpu`
names threads, and `SyncAdapterThread-1` reaches the trace as
`SyncAdapterThre`, fifteen characters.

## The Holds column

This is the column that decides whether a row is worth acting on. It gives
the range the move lies in, 95% sure, and a verdict read off that range.

| value | meaning |
|---|---|
| `yes, +831.3 … +911.3` | the whole range is on one side of zero: the move survives a re-record, and the end nearer zero is the least it moved |
| `no, -13.4 … +162.6 · ~7 runs a side` | the range runs through zero: the runs disagree among themselves by more than the row moved, and about seven runs a side would settle a move this size |
| `—` | nothing to test: the row is on one side only — it appeared or went — or there are too few runs to be 95% sure of anything |

`no` does not mean the row is uninteresting. It means the honest next step is
another round of `collect` rather than a conclusion, and the range says how
open the question still is: `-13.4 … +162.6` is anything from no move at all
to more than a doubling. The count after it says how big that round is.

**How the range is made.** Every run after is paired with every run before,
and each pair gives a difference. The median of those differences is the move.
The range is what is left of them once as many as possible come off each end
while the chance of the true move lying outside stays at 5% or less (the
Hodges–Lehmann estimate and its Moses interval, for anyone checking the
arithmetic). Nothing is assumed about the shape of the runs, which on a cold
start is anything but a bell curve, and nothing is drawn at random: the same
two reports give the same range every time.

**Why not the ranges of the runs.** This column used to be `Ranges`: `apart`
when every run after fell outside everything seen before, `overlap` otherwise.
That is the same range with nothing taken off, and so a test whose bar rose
with every run recorded: 90% sure at three runs a side, 99.2% at five, and at
fifteen a side all but certain, which a single slow run among the thirty is
enough to deny. It was checked on fifteen runs of one build of a real app,
split at random into a before and an after, with every run after made a fifth
slower. Seven against eight, the old test caught the move in 15 comparisons of
a hundred and this range in 51; five against five, in 27 and 36. More runs
made the old test see less; they make this one see more.

**What it costs.** At 95%, up to one row in twenty that did not move lands on
one side of zero by chance. The floor below keeps most of those out of the
table: on the same splits with nothing made slower, a row cleared the floor
and held in about 2 comparisons of a hundred, where the old test called fewer
than 1 apart. A `yes` that stands alone, with nothing around it in the table to
explain it, is worth a second round before a conclusion.

**How many runs.** Four a side is enough. With fewer on one side, the other
has to make up for it: three need five against them, two need eight, one needs
thirty-nine. Below that the column stays empty and the `few` warning at the
top says why. The count is the row's own: a row found in `2/5` runs has two
values, and against five it gets `—` while the rest of the table has a verdict.

**What the runs can resolve.** A run after minus a run before is the move of
the medians plus what the two runs strayed from their own medians. So the
range is the strays' own range moved along by the move, exactly, and a row
holds when its medians moved further than the strays reach on the far side.
That reach is `shift.resolves_ms`: `inflate` above moved 74.6 ms, and these
runs resolve nothing under 88.0. It shrinks with the square root of the runs,
so a `no` carries about how many runs a side would bring it under this move —
seven here, a round of `collect` away. A count in the hundreds is a different
answer: more rounds will not settle the row, and either the spread comes down
first — a settled device, a quieter scenario — or the move is below what this
scenario can resolve. The count assumes the next runs stray as much as these
did, which is why it is about.

The report carries the same number for a single set, per row, under
`spread` — see [below](#what-analyze-keeps-for-it) — so what a comparison
could see is known before there is a second set.

This works because `analyze` keeps the per-run values when it merges repeats —
see [`spread`](#what-analyze-keeps-for-it) below. With one trace on each side
the column is empty for every row, and the comparison is a difference of two
single measurements.

## What is a move at all

A row is listed when it moves by more than **5 ms or 10 %**, whichever is
larger. Both halves earn their place: the absolute floor stops a 4 ms wobble on
a 6 ms slice reading as "×1.7 slower", and the relative floor stops a 40 ms move
on a 900 ms slice reading as a finding.

```bash
echolot compare --floor-ms 20 --floor-pct 25    # only the large moves
```

Everything below the floor is counted in the summary and listed on one line
under **Steady**. Nothing is dropped silently.

How often a row was found is part of the move. A merged row's median covers
the repeats it was found in, and Before and After print that share when it is
short of all of them: `120.0 (1/5)`. Where the two shares differ, each side's
repeats without the row count as zero, and the move, Holds and the runs it
would take are read from those. A row found in 1 of 5 repeats before and in
all 5 after, at the same milliseconds, now happens on every run, and is listed
as grew rather than steady.

## The same thing under a new name

Thread pools hand work to whichever worker is free, so `arch_disk_io_0` in
one set and `arch_disk_io_3` in the next is one phenomenon under two names.
(A pool whose workers differ only past the fifteenth character never reaches
this pass — Linux has already cut them to one shared name.) Matching on the literal name reports a large row
gone and an unrelated large row appeared — twice wrong, in the two places it
matters most.

So rows are paired in two passes: exact name first, then by name family, which
collapses digits and hex the way `echolot names` does when it builds its
inventory.

A name carrying the config's `instrumentation.temp_prefix` keeps its digits:
`AGENTTMP_fill_v4` and `AGENTTMP_fill_v6` are two markers somebody wrote to
tell two things apart, not one marker renamed. Without the key, or without a
config, the prefix is `AGENTTMP_`, as for `analyze` and `mark`.

The second pass only fires when the family is unambiguous — exactly one
unmatched row on each side. With two workers before and three after there is no
honest way to say which became which, and they stay listed as appeared and
gone. A row paired this way is marked `*(family)*` in the table and
`"matched_by": "family"` in the JSON.

## When two reports may not be compared

Subtraction always produces a number, including for two reports that have
nothing to do with each other. Nothing here refuses to subtract — a table with a
reason above it is more useful than an error — but every reason is stated at the
top, the way the Marker Report already states an anchor that never matched.

| warning | why it matters |
|---|---|
| `process` | two different apps. `comparable: false`; nothing below is a comparison |
| `thresholds` | detector parameters differ. **appeared** and **gone** mean "the bar moved", so they say nothing about the app. Named parameter by parameter, with both values |
| `defaults` | one side ran with `--defaults` and the other did not |
| `config` | the config's hash changed between the two: anchors, process mask and thresholds all live there |
| `anchor-before` / `anchor-after` | that side's start or end anchor never matched, so its window runs to the edge of the trace rather than the scenario's |
| `runs` | different numbers of repeats. Holds allows for it; the ± beside each median does not, and the narrower side is the smaller sample |
| `single` | one trace on a side: no spread, so the Holds column is empty throughout |
| `few` | too few repeats to be 95% sure of any move, so the Holds column is empty throughout. How many are enough is under [the Holds column](#the-holds-column) |
| `detectors` | the two runs did not use the same set of detectors |
| `detector-failed` | a detector failed on that side, with its error. It did not look there, so its rows on the other side are left out of the table rather than listed as gone or new, its params stay out of `thresholds`, and "Detectors that changed state" says `failed` |
| `instrumentation` | rows that appeared carry the config's `instrumentation.temp_prefix`: markers added between the rounds, a breakdown of what was already there rather than new work. Only with that key in the config — without it they are ordinary appeared rows |
| `environment` | the clock the two rounds ran at differs by the floor or more, 10% unless `--floor-pct` says otherwise, either way, or a side carries no clock at all, or a side read its clock or thermal state in only some of its repeats. Two rounds that recorded no platform state get no warning — see below |
| `environment-thermal` | the kernel throttled the device during one round and not the other. Only when both sides recorded thermal state |
| `sampling` | a callstack sampler ran during one round and not the other, or at another rate, or in only some repeats of one — see below |

The one to read first is `environment`, because it is the only one that can
make the whole table say the opposite of what it looks like. A duration is the
work done divided by the speed the machine was doing it at. Drop the clock by a
third between rounds and every row grows by half: the table reads as a
regression, and nothing in the app moved.

The bar is the relative floor, 10% unless `--floor-pct` sets another: the
number a row has to clear to be called moved at all, so a clock that differs by
as much can produce every row on the page. The drift is the faster clock over
the slower, less one, which is how much a row bound by the CPU moves: from 2200
to 2000 MHz is 10%, and such a row grows by 10%. The clock is
weighted by the time this app's own threads held a core, so a device whose
little cores idled through the scenario is not reported as a slow device.

The clock and the throttle are checked separately because they do not move
together. The same scenario on an SM-A515F, once at 76 °C with the big cluster
throttled in two runs of three and again at 55 °C with none, came back at
2220 MHz against 2209 MHz — half a percent apart. Throttling takes away the
headroom rather than the frequency the app was actually using, so the clock
check alone would have passed that pair without a word. The same pair is why
temperature on its own earns no warning: 21 °C of difference, and the speed
did not move.

Silence here means checked and steady. A side recorded without the
platform-state sources — an older echolot, or `runner.environment: false` —
says so instead, and two such reports say nothing at all rather than repeating
a config problem on every comparison.

`sampling` is the same kind of warning about a cause the recording brought
along. A callstack sampler interrupts the app on every tick and copies its
stack out, so a round recorded with `runner.sampling` runs slower than a plain
one on the same code: on the SM-A515F, a cold start of 1.36 s sampled at
100 Hz took 186 ms longer than the same start without it, 95% sure between
68 and 263 ms. Whether a sampler ran is read from each trace, so a round
recorded some other way is judged the same. A round that asked for one on a
device whose sampler never started ran like a plain one and counts as plain,
and a report written before the field existed gets no warning, since nobody
can say how it was recorded.

Then `thresholds`. After `echolot calibrate` the numbers in
`echolot.yml` are derived from particular runs, and comparing a calibrated
report against a default one produces a page of rows that appeared and vanished
without anything in the app changing. Re-run both sides with `--defaults` when
that happens.

## What `analyze` keeps for it

Merging repeats used to reduce every row to a median, and a median cannot say
whether a number is steady: 120 ms from (118, 119, 121) and 120 ms from
(12, 120, 890) read identically, and only the second one means the next run will
say something else.

So `analyze` now keeps the per-run values of three columns — `self_ms`,
`total_ms` and `max_ms`, whichever of them a row carries — under `spread`:

```json
{ "location": "draw", "runs": "5/5", "self_ms": 125.4, "total_ms": 130.1,
  "max_ms": 61.2,
  "spread": { "self_ms":  { "min": 118.2, "max": 340.1,
                            "values": [118.2, 121.0, 125.4, 133.7, 340.1],
                            "resolves_ms": 214.7, "resolves_pct": 171.2 },
              "total_ms": { "…": "the same shape" },
              "max_ms":   { "…": "the same shape" } } }
```

Three of the five rather than all of them, because the report staying small is
the point of it. `self_ms` and `total_ms` because one of the two is the
detector's ranking metric — self time where it measures one, total otherwise —
and every conclusion is drawn from it. `max_ms` because that is where a single
slow occurrence shows up at all, and a median over maxima across repeats is
exactly what hides one. `count` and `covered_ms` stay medians alone.

`values` holds one entry per repeat **the row was found in**, which is what the
`runs` column counts: a row with `3/5` has three values, not five. `report.md`
is unchanged — this lives in the JSON, where the readers that need it are.
`compare` reads every value, not only the two ends: the Holds column pairs each
one after with each one before.

`resolves_ms` is the smallest move of this row a comparison could call real,
against as many runs spread the same way, and `resolves_pct` the same against
the median — see [What the runs can resolve](#the-holds-column). Here it is
214.7 ms on a 125.4 ms row, and the reason is the one slow run: five a side
leaves two of the pairs with it at each end, and the next comparison of this
row will not see less than that unless there are more runs or the slow one
goes away. Absent below four runs.

## The comparison JSON

```json
{
  "schema": 2,
  "kind": "comparison",
  "comparable": true,
  "warnings": [ { "id": "thresholds", "text": "…" } ],
  "before": { "path": "…", "runs": 5, "generated_at": "…",
              "config_sha": "a3f9c21b", "defaults": false },
  "after":  { "…": "the same shape" },
  "window": { "before_ms": 1184.0, "after_ms": 2960.4,
              "delta_ms": 1776.4, "ratio": 2.5 },
  "noise_floor": { "abs_ms": 5.0, "ratio": 0.1 },
  "confidence": 0.95,
  "summary": { "moved": 4, "appeared": 2, "vanished": 1, "steady": 17,
               "fired_before": [ … ], "fired_after": [ … ],
               "state_changed": [ { "id": "binder_txn",
                                    "before": "silent", "after": "1 row(s)" } ],
               "silent_both": [ "gc_pressure", … ] },
  "rows": [
    { "location": "TeamRepository.loadAll", "detector": "main_thread_block",
      "metric": "self_ms", "change": "grew", "matched_by": "exact",
      "before": { "self_ms": 12.1, "min": 10.4, "max": 14.0,
                  "values": [ … ], "count": 1, "runs": "5/5" },
      "after":  { "self_ms": 883.4, "min": 843.4, "max": 923.4,
                  "values": [ … ], "count": 1, "runs": "5/5" },
      "delta_ms": 871.3, "ratio": 73.01,
      "shift": { "ms": 871.3, "low_ms": 831.3, "high_ms": 911.3,
                 "resolves_ms": 40.0, "runs_needed": null },
      "holds": true }
  ]
}
```

`change` is one of `appeared`, `grew`, `shrank`, `vanished`, `steady`, so an
agent can take the rows worth looking at without parsing any numbers. As a
JMESPath filter:

```
rows[?change == 'appeared' || (change == 'grew' && holds)]
```

A bare `true` in JMESPath is a field name, so `holds == true` would keep the
rows with no verdict and drop the ones that hold; the literal is `` `true` ``.

`holds: true` means the range in `shift` — `low_ms` to `high_ms` — is on one
side of zero. `shift.ms` is the move it was read from: the median of every
run-after-minus-run-before pair, close to `delta_ms`, the difference of the two
medians, and not always equal to it. `shift.resolves_ms` is the smallest move
of the medians these runs could call real, in this move's direction, and
`shift.runs_needed` — only where the move does not hold — about how many runs
a side would. `null` in both `shift` and `holds` means there was nothing to
test: the row is on one side only — every `appeared` and `vanished` row — or
there were too few runs. `confidence` is the level the ranges are drawn at.

Schema 2 is this shape. Schema 1 carried `overlap` — whether the min–max
ranges of the two sides touched — where `shift` and `holds` are now.

## There is no performance gate, on purpose

An earlier plan had `analyze` exit non-zero against `scenario.budget_ms`, so a
build could fail on a slow run. It is not being built, and this is the reason.

"Did it get slower" is already answered. Macrobenchmark writes percentiles per
iteration right next to the traces echolot collects from it, and comparing a
median against a number is a few lines of anything. An eleventh implementation
of that adds nothing. Worse, detector thresholds on a shared CI runner would
fire on properties of the runner — the same caution this tool already gives
about `runnable_starvation` on a loaded machine.

Where echolot is hard to replace is the other question: *where* the time went.
So the useful shape in CI is the opposite of a gate, and the next section is
that shape.

`scenario.budget_ms` stays in the config. It records what a team considers
acceptable, which is worth writing down whether or not anything enforces it.

CI does hold one gate, and it measures this repository rather than a device:
`pytest` fails when statement coverage drops below the threshold in
`pyproject.toml`. That number comes out the same on every runner, which is
exactly what a trace threshold does not.

## In CI

Run `echolot doctor -q` as a precondition — it already answers "does this
machine compute correctly" with an exit code — then `analyze` over the traces
the benchmark has already written, `compare` against the last run's
`report.json`, and keep `report.json` and `comparison.json` as build
artefacts.

The exit code is 0 whatever the comparison says. This command reports; it does
not stand guard.

When someone asks a day later why the nightly regressed, the answer is already
sitting next to the commit — the window, the thresholds, the evidence and the
delta: no device, no re-recording.

### The action

`action.yml` at the root of this repository does all of that as one step,
after the one that runs the benchmark:

```yaml
permissions:
  contents: read
  actions: read            # to fetch the report the last run kept

steps:
  # … check out, start the device, run the benchmark …
  - uses: grishan0v/echolot@main
    with:
      traces: app/benchmark/build/outputs/**/StartupBenchmark_startup_iter*.perfetto-trace
```

It does five things:

1. It installs the echolot of the ref it was called at, in a virtualenv of its
   own, and caches `trace_processor`. A release tag pins the action and the
   tool together; `@main` runs both as they are on `main`. The virtualenv is
   named after the ref and the Python, so a second call in one job at another
   ref installs its own echolot.
2. It runs `echolot doctor -q` with the `config` input, so it checks the
   `trace_processor` the report is computed with. A runner that fails it fails
   the job: nothing it computes can be trusted, and that is a fact about the
   runner, never about the app. A `trace_processor` that could not be
   downloaded fails it too, and the annotation says so.
3. It runs `echolot analyze` over the traces. Name the repeats of one test.
   The benchmark's output directory holds every test it ran, and `analyze`
   merges whatever it is given as repeats of one scenario. A line that names
   no trace fails the step.
4. It fetches the `report.json` that the last successful run of the same
   workflow kept on the base branch. That is the pull request's base, or the
   default branch for a push or a nightly. Then it runs `compare` against
   it.
5. It writes the comparison to the job summary, with the report under it, and
   keeps the report, the comparison and the baseline as the artifact
   `echolot-report`.

The job fails when the runner cannot compute or the traces are missing,
never over what moved. The first run on a branch has nothing to compare
against and says so; the next run compares against it. A baseline the token
cannot read, or a lookup that names a workflow with no runs, is a warning
annotation as well as a line in the summary. The action runs on
Linux and macOS runners.

| input | default | what it is |
|---|---|---|
| `traces` | — | the repeats of one test: paths or globs, one per line, relative to the workspace; `**` reaches into subdirectories |
| `config` | `echolot.yml` | the project's config |
| `baseline` | — | a `report.json` to compare against, in place of the lookup |
| `baseline-branch` | the base branch, or else the default branch | whose last good run holds the baseline |
| `baseline-workflow` | this workflow | the workflow file whose runs keep the baseline, such as `nightly.yml`, for a pull request job that compares against a nightly |
| `artifact-name` | `echolot-report` | what the reports are kept as and looked up by; each leg of a matrix needs its own |
| `comment` | `false` | `true` posts the comparison on the pull request and edits that one comment on every push. It needs `pull-requests: write`, which a pull request from a fork does not get |
| `python` | `python3` | the Python, 3.10 or newer, echolot is installed with |
| `token` | the job's token | reads the earlier runs' artifacts and posts the comment |

Its outputs are:

- `report`: this run's `report.json`;
- `baseline`: the report it was compared with;
- `comparison`: `comparison.json`;
- `moved`: how many rows grew or shrank past the noise floor;
- `appeared` and `vanished`: how many rows appeared or vanished, counted
  apart from `moved` — a new block of the main thread is one that appeared.

All but the first are empty when there was nothing to compare against.
`.github/workflows/action.yml` runs the action this way on the demo app's
traces for every pull request here, and on the demo's planted change,
where exactly the lock's two rows have to move.

## Related

- [Collecting traces](collecting.md) — why repeats, and why the sets must be repeats of one scenario
- [Calibrating](calibrate.md) — where the threshold warning comes from
- [Analysing](analysing.md) — from a row in the table to a place in the code
