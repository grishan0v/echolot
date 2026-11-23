# Comparing two reports

[← back to the documentation index](README.md) · **English** · [Русский](ru/compare.md)

`echolot analyze` answers where the time went in one set of traces. The
question people actually arrive with has two halves — *it was 3 s, now it is
7 s, where did that go* — and the second half needs two sets.

`echolot compare` takes two Marker Reports and returns one table, sorted by how
far each row moved. The top row is usually the answer.

```bash
echolot compare                       # the open investigation: previous round vs latest
echolot compare --hunt 3              # investigation 3: its first report vs its last
echolot compare old.json              # that report vs the latest of its scenario
echolot compare old.json new.json     # exactly those two
```

With no arguments, inside an open investigation, the two sides are its last
two rounds among the reports filed there: every `analyze --hunt <n>` files
one, and an `analyze` without the flag runs outside the investigation and
files nothing. A look is not a
round and is left out: a report that read fewer traces than the round before
it, read only traces that round read already, or ran with other `--set` or
`--defaults`. A single-run look at one trace of round 2 would otherwise be the
after side, with no spread to judge a move by. The line on stderr names the
two reports taken and the looks left out; name two paths for any other pair.

It writes `comparison.md` and `comparison.json` next to the report, by the same
rule `analyze` uses: a relative `-o` is taken from the config's directory, so
running it from a build folder full of traces still lands the output in the
project. The config is `echolot.yml` in the working directory, or else the
nearest one up the tree within the checkout, so `compare` run from a build
folder finds the project and its open investigation; `-c` names another,
although `--help` does not list the flag. With one report named, the newer
side is `.echolot/out/report.json` next to the config, whatever `-o` says: `-o`
is where the comparison goes. For a config with several scenarios it is the
latest report of the scenario the named report measured, in
`.echolot/out/<scenario>/`, and `--scenario` names another. Two reports of
different scenarios are refused: a cold start against a feed scroll moves
every row and means nothing. A report from before reports named their
scenario is compared as it is. Two reports whose windows were not defined the
same way are refused too, under [Two windows](#two-windows) below. Run from a folder with no config, or with one
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

### Markers

The report's markers table — the domains the config lists and the temporary
markers, each measured every run whatever fired — is compared too, in a
section of its own under the table:

| Where | Before | After | Δ | N | Holds | Calls |
|---|---|---|---|---|---|---|
| loadCollection | 2079.4 ±19 | 4158.8 ±39 | **+2079.4 ×2.0** | 1 → 2 | yes, +2040.6 … +2100.6 | 1 → 2, yes, +1 … +1 |

Its rows are paired by name and judged by self time as a detector's rows are.
They matter most for an async section (`Trace.beginAsyncSection`): no detector
reads one, since it has no thread to answer for, so the markers table is the
only place it is measured. **Calls** judges the calls per run on their own: a
load that ran twice per run instead of once is work done twice, and is listed
when the medians are a whole call apart and more than the relative floor, with
its range read from each run's count. The section lists the markers that moved
by time or by calls; the header counts them as `markers moved`. A report
written before the table existed is compared without it.

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

"Found" means the thing happened, over the detector's threshold or under it.
`analyze` of a set looks each row up in every repeat with the thresholds open,
so a row that cleared the bar in 3 of 8 repeats and was there below it in the
other 5 is `8/8, over the bar in 3`, its median covers all 8, and no zero is
put in for it. A zero stands only for a repeat where the row was not there at
all. A row that one report has and the other does not was never looked up on
the other side, and keeps no verdict.

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

### Markers added between rounds

A marker placed directly under a slice becomes its child, and self time is a
slice less its direct children. So the round that adds a marker under
`Compose:recompose` reads, by self time, as `Compose:recompose` getting
cheaper by exactly the marker's time — on a real pair 3714 → 105 ms, the
largest move in the table, while its total did not move. `analyze` records
for each temporary marker the slices it sits directly in and how long it
spent there (`parents` on its row of the markers table). A slice whose self
time went into markers one side added, or came back from markers one side
took out, is judged on its total instead: the row says so in the table,
`*(by total)*`, and in `comparison.json` with `metric: "total_ms"` and
`into_markers` (`ms`, the `markers`, and the `side` that has them). The
`instrumentation` and `instrumentation-removed` warnings name these slices.

A set whose repeats carry a temporary marker in some runs and not others is
two rounds merged as repeats — `analyze` of a round with markers and one
without, to get more runs a side. The report's markers section says so and
names the slices the markers sit in: their self-time medians mix two
measures. Analyze the two rounds apart.

### Frame-paced scenarios

A scripted scroll or drag, recorded for a fixed time, lasts as long as the
gesture whatever the app does, and a build that janks draws fewer frames in
that time. Every row of work done once a frame — the main thread's per-frame
slices, RenderThread, the GPU driver's threads — then shrinks with the frame
count. On one screen five times fewer frames turned `postAndWait` from 894 into
400 ms, a shrink that held, while each occurrence went from 1.33 to 2.48 ms;
the fix, the other way round, read as a list of regressions.

`analyze` keeps the frames drawn in the window (`window.frames`). When the two
sides differ by the relative floor or more, the comparison opens with the
`frames` warning. A row whose count moved the way the frames did, by more than
the floor, gets its time per occurrence beside its count — `670 → 161.5 · 1.33
→ 2.48 ms each` — and no verdict on its total (`per_occurrence` in the JSON,
`holds: null`). The rows that moved against the frames lead the table: work
that grew while the frames fell, such as the frames the app itself made late,
is what the frame count does not explain. A row without a count on both sides
keeps its verdict, so read rows like `uninstrumented_cpu` with the warning in
mind.

## When two reports may not be compared

Subtraction always produces a number, including for two reports that have
nothing to do with each other. Nothing here refuses to subtract — a table with a
reason above it is more useful than an error — but every reason is stated at the
top, the way the Marker Report already states an anchor that never matched.

| warning | why it matters |
|---|---|
| `process` | two different apps. `comparable: false`; nothing below is a comparison, and the Holds column stays empty |
| `window` | the windows were not defined the same way. `comparable: false`. `echolot compare` refuses such a pair outright, see [Two windows](#two-windows); the warning is for a caller that builds the comparison itself |
| `thresholds` | detector parameters differ. **appeared** and **gone** mean "the bar moved", so they say nothing about the app. Named parameter by parameter, with both values |
| `defaults` | one side ran with `--defaults` and the other did not |
| `config` | the config's hash changed between the two. What the reports themselves show changed is named — an anchor's glob (end anchor `list_shown_mark` → `list_shown`), the config's path — and the hash alone where nothing visible moved: anchors, process mask and thresholds all live there |
| `anchor-before` / `anchor-after` | that side's start or end anchor never matched in any repeat, so its window runs to the edge of the trace rather than the scenario's: `comparable: false`, and the Holds column stays empty. With no launch in that side's traces either, the warning points at the runner (`runner.reset_policy`) rather than the config. An anchor missed in some repeats only — `not found in 1 of 15 after repeats` — keeps the pair comparable and names those repeats, the warm ones when the set mixes cold and warm starts, as the ones to leave out |
| `frames` | one side drew fewer frames in the window than the other, by the relative floor or more — see [Frame-paced scenarios](#frame-paced-scenarios) |
| `runs` | different numbers of repeats. Holds allows for it; the ± beside each median does not, and the narrower side is the smaller sample |
| `single` | one trace on a side: no spread, so the Holds column is empty throughout |
| `few` | too few repeats to be 95% sure of any move, so the Holds column is empty throughout. How many are enough is under [the Holds column](#the-holds-column) |
| `detectors` | the two runs did not use the same set of detectors |
| `detector-failed` | a detector failed on that side, with its error. It did not look there, so its rows on the other side are left out of the table rather than listed as gone or new, its params stay out of `thresholds`, and "Detectors that changed state" says `failed` |
| `not-checked-before` / `not-checked-after` | detectors that could not run on that side, because its recording does not hold what they read, named with their reasons (see [When a detector could not look](analysing.md#when-a-detector-could-not-look)). Their rows are left out of the table on both sides rather than listed as gone or new, and "Detectors that changed state" says `not checked` |
| `instrumentation` | markers that appeared, each named once, carry the config's `instrumentation.temp_prefix`: markers added between the rounds, a breakdown of what was already there rather than new work. The slices they sit in directly are named too: their self time went into the markers, so those rows are judged on their total — see [Markers added between rounds](#markers-added-between-rounds). Only with that key in the config — without it they are ordinary appeared rows |
| `instrumentation-removed` | the same in the other direction: temporary markers that are gone were taken out between the rounds, not work that went away, and the slices they sat in, whose self time came back, are judged on their total |
| `environment` | the clock the two rounds ran at differs by the floor or more, 10% unless `--floor-pct` says otherwise, either way, or a side carries no clock at all, or a side read its clock or thermal state in only some of its repeats. Two rounds that recorded no platform state get no warning — see below |
| `environment-thermal` | the kernel throttled the device during one round and not the other. Only when both sides recorded thermal state |
| `environment-thermal-one-side` | the kernel throttled the device during one round, and the other has no thermal record at all. The sentence says how many repeats throttled and that it cannot be ruled out on the other side, never that the other side ran cool |
| `environment-load` | other processes took 40% or more of the CPUs' time in one round's window, 15 points or more above the other round. That round ran on a busy device. Both reports have to carry the share (`window.crowding.outside`) |
| `sampling` | a callstack sampler ran during one round and not the other, or at another rate, or in only some repeats of one — see below |
| `compose_names` | one round was recorded with composable names and the other not, or only some of its repeats were. The names come with the app's Perfetto SDK loaded at its start, which cost a cold start 67 ms on the device it was measured on |
| `compile` | the app was compiled differently in the two rounds, or in the repeats of one, or — where the traces could not say how — the JIT compiled several times more methods on one side — see below |
| `recording` | the two sides were not recorded the same way: another runner or reset policy, a force-stop that found the app running on one side and already stopped on the other, or another trace config — data sources, atrace categories and ftrace events, each named on the side that had it. The detectors that read what one side recorded and the other did not are named — see [How the two were recorded](#how-the-two-were-recorded) |
| `recording-mixed` | one side's own repeats were not recorded alike, by runner, reset policy or trace config, so its medians mix two ways of recording |
| `recording-unknown` | one side cannot say how it was recorded while the other can: a report from before reports kept it, traces recorded before `collect` wrote its account beside them or by something else, or traces with no config of their own |
| `version` | the two reports were written by different versions of echolot, or one does not say which wrote it. A side that does not say leaves every row without a verdict — see [Which echolot wrote each](#which-echolot-wrote-each) |
| `detector-changed` | a detector read the trace with different code on the two sides. Its rows stay, without a verdict |
| `same-traces` | both sides are the same recordings — the same trace names, pid and window to the hundredth of a millisecond — so whatever moved is the tool's or the config's, and no row carries a verdict |

### Two windows

A report says how its window was defined when the scenario's window is the
launch (`scenario.window: launch`): `window.defined_by` is `launch` for a
window that closed at the launch's end, and `launch+reportFullyDrawn` for one
that ran on to the app's `reportFullyDrawn()` call. A window cut by the
config's anchors carries no such key.

These are different stretches of the same launch. Against anchors from
`bindApplication`, a launch window starts earlier, at the intent. A launch
that ran on to `reportFullyDrawn()` is longer than one closed at its end, by
a median 56 ms on the launches this was measured on, and held a
`runnable_starvation` row the shorter window did not in 15 traces of 35.
Every row moves for that reason alone. Nothing else in the two reports says
so: the scenario's name is the same, and a changed config only warns.

So `echolot compare` refuses the pair, the way it refuses two scenarios, with
exit code 2 and no table:

```
error: these two reports cannot be compared: the windows were defined differently: as the launch, closed at its end before, as the launch, closed on `reportFullyDrawn` after (…)
```

It refuses a merged report whose own repeats did not close the same way
against anything, itself included: its medians mix two windows.

To compare the two sides, analyze both sets of traces with one config. When
the change under test adds or removes the `reportFullyDrawn()` call, a launch
window cannot be the same on both sides: name `start` and `end` in the
scenario for that comparison.

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

`compile` is about the app rather than the recording. ART runs the same code
at another speed compiled ahead of time from a profile (`speed-profile`) than
verified only (`verify`) or straight from the APK (`run-from-apk`), when the
interpreter and the JIT carry it. A large app's launch on the SM-A515F took
~1.4 s on its benchmark build and ~8.8 s on a debuggable build of it that
nothing had compiled: a comparison of the two calls every row a regression,
and the clock check above passes it, since the clock did not move. The state
is ART's own slice for the app's dex, read from each trace, so a build that
changed under the same config is caught. The reason is part of it: the same
filter from a profile background dexopt widened (`bg-dexopt`) is not the build
the install brought (`install-dm`). A side whose traces held no state — the
process was already running when the recording began, as in every scroll or
drag recorded with the app up — says nothing about its filter, so the JIT's
work in the window speaks instead: when the two sides' JIT-compiled methods
are three times apart or more, and at least 50 methods apart, the warning
names both counts. On an SM-A515F one scroll JIT-compiled 64 methods on its
profile, 328 after `cmd package compile -m verify` and 2 after `-m speed`.
A report from before the field gets no warning.

### How the two were recorded

`recording` is about the recording rather than the device. Every report
carries a `recording` block: what the trace's own config asked the device for
— its data sources, atrace categories and ftrace events, which the tracing
service writes into every trace — and what `collect` knew of how it drove each
repeat, which it keeps beside the trace as
`<trace>.recording.json`: the runner, the reset policy, and whether the
force-stop before the repeat found the app still running. A set from a
Macrobenchmark's own trace config against one `collect` recorded with
echolot's gave 44 rows that held and 185 that were gone, the main thread's
`io_wait` among them, with nothing to say which of them moved because the
recording changed. The warning names what each side had alone and the
detectors that read it.

The stop is there because of a pair that differed in nothing else anyone
could see. The same APK on an SM-A515F, recorded on two days with the same
config, every start cold and both sides throttled alike: one day a plain
`collect`, whose force-stop found the app still running from the repeat before
in 14 of 15 repeats, so each start followed a kill by half a second; the other
day a script stopped the app first and ran `collect -n 1` fifteen times. The
window came out 0.68 s shorter on the second, and the comparison listed 60 rows
that held, with no warning. No cooling wait came between the stop and the
start on either day; a repeat that waits now begins long after its stop, since
`collect` stops the app before the device cools. Whether the stop found the
app running is kept, and a difference in it is said; what most of a side's
repeats did is that side's, since the first repeat of every `collect` finds
the app as the last session left it.

A side that cannot say — traces recorded before `collect` kept its account,
by another tool, or a report from before the block — gets `recording-unknown`
rather than silence, and two sides that both cannot say get nothing.

### Which echolot wrote each

`version` and `detector-changed` are about the tool. Every report says which
echolot wrote it (`toolchain.echolot`), and every detector what it read the
trace with (`sha`: its queries, its `@identity` and its stdlib modules, comment
lines aside). Two versions that differ in a detector read one trace
differently: two reports of the very same fifteen traces, written by 0.7.0 and
0.17.0, gave 11 rows that held, `monitor_contention` −3.1 s among them, from a
double count fixed in between. A detector whose `sha` differs keeps its rows
and loses their verdict. A side that does not say which echolot wrote it — a
report from before the stamp — leaves every row without a verdict, since which
of its detectors read the trace the way the other side's did cannot be known;
analyze its traces again, which the traces kept beside every round allow. Two
reports that both predate the stamp are compared as they always were.

`same-traces` needs nothing new in a report, so it works on old ones too:
the same trace names, the same pid and the same window — median, shortest and
longest — to the hundredth of a millisecond is one set of recordings read
twice, and nothing in the app can have moved between them.

`not-checked-before` and `not-checked-after` are about a detector rather than
a number. `io_wait` reads why the kernel put a thread to sleep, and a round
recorded with `runner.environment: false` has none of it, so `io_wait` is
silent there for a reason that has nothing to do with the app. Against a round
that recorded it, its rows would read as gone. A detector whose report says
`checked: false` is left out of the table on both sides, and the warning names
it with the reason from each side that could not look. One that could not run
is not silent in both either, and a report written before `checked` existed is
unknown and is compared as it always was. A detector that failed keeps its own
warning, `detector-failed`, and a set that failed in some repeats is still
compared on the rest.

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

## A plain reading

`echolot compare --plain` says the same comparison to someone who has just
changed their code: did it get better, is it enough, and which checks moved.

```bash
echolot compare old.json new.json --plain
```

```
BETTER        Faster by 0.7 s. The launch went from 2.1 s to 1.4 s, 33% less. Measured on 3
              launches before and 3 after.

 !  Still over the limit. The launch takes 1.4 s. You allowed 0.5 s, so it takes 2.7 times as
    long.
 ~  Not certain check by check yet. 3 launches before and 3 after are too few to say which
    single changes hold. It takes four a side, or three against five.

 GONE       The screen waits for background work
            This check finds nothing now. Before: The main thread waited 0.7 s for
            `worker_pool_#`, in all 3 launches. That is 33% of the launch.
 NO CHANGE  Heavy work on the main thread
            Added up, what this check found was 0.5 s before and 0.5 s after.

 ok 1 check is fine both times     -  1 could not be checked on one side or both
```

No model writes any of it: the same two reports give the same words. The
verdict is by the scenario's window. Up to three notes follow it: whether the
launch is within `scenario.budget_ms`, in the sentence the report's own
[plain reading](analysing.md#a-plain-reading) has for it; that the repeats are
too few for the Holds column, or, with fewer than three a side, for anything to
be called a change, and then that note comes first; and that the two sides were
not recorded alike,
which is the warnings of
[When two reports may not be compared](#when-two-reports-may-not-be-compared)
said for a person. Then one item per check, with a sentence of what changed in
the words that reading uses for the check.

A movement is not a level. On a report a check has a level of attention: how
much it needs you. Here it has a movement: which way it went.

| movement | when |
|---|---|
| better, worse | what the check found, added up, clearly moved |
| gone | it found something before and finds nothing now, and what it found was more than a small or occasional thing |
| new | the same the other way round |
| no change | it found something on a side, and no move can be called: the sentence says why |
| fine both times | it looked both times and found nothing |
| not checked | it could not look on one side or on both, or one of the reports is too old to say (it says why); `compare` leaves such a check out of its table too |

**A check is judged by the sum of its rows**, not by its largest row. A fix
can put five rows under one section: the largest row grows while the check as
a whole shrinks, and judged by its largest row a fix reads as a regression.
The sum is of a typical launch: each row's median over every launch of its
side, a launch without the row counted as nothing. A row seen in one launch of
three adds nothing, so one slow launch does not read as a move.

**When a move is called**, for the window and for a check's sum alike:

- there are at least three launches a side;
- the two differ by a fifth of the larger one or more, and a check's sum also
  by a twentieth of the launch;
- the launches agree. For the window: more than half of the faster side's
  launches beat the fastest launch of the slower side, so one slow launch does
  not hide a change. For a check: the most its sum can have been in any launch
  of the lower side is under the least it can have been in any launch of the
  higher side.

This is a coarser rule than [the Holds column](#the-holds-column), on purpose:
it has to give an answer at three launches a side, where no row can be 95%
sure, and it must not call the same app better or worse. It was measured on
190 recordings of one app on one phone, each analysed on its own and merged
the way `analyze` merges repeats. Pairs of sets with nothing changed between
them, three to seven launches a side:

| the two sets | pairs | the verdict moved | a check moved |
|---|---|---|---|
| drawn from one session | 1,300 | 0 | 0.1 to 0.2% of the checks that found something |
| from sessions of one day | 139 | 0 | 0.2 to 0.4% |
| from two days | 40 | 0 | 0.7 to 0.8% |

A real fix that took a launch from 2.1 s to 1.4 s reads as better, and the
checks it emptied read as gone. A fifth, because the same build recorded on
two days differed by up to 18% with every launch of one day slower than every
launch of the other: at 10%, the floor a row has, half of those pairs read as
a change. A change smaller than a fifth is said with its two times and called
no clear change. All of this is one app on one phone; the numbers are
`SHARE`, `LAUNCH_SHARE` and `LEAST_RUNS` in `echolot/moves.py`, with the
reasoning beside them.

With the flag the reading is kept in `comparison.json` as well, under `plain`.
`comparison.md` stays the table. Without `--plain` and `--html` `compare`
computes, prints and writes exactly what it did before the reading existed.
Nothing fails over a movement: the exit code does not depend on one.

## The comparison as a page

`echolot compare --html` writes one more file beside the comparison,
`comparison.html`: the plain reading as a page for a person. One file that
opens from disk, nothing fetched, and the same two reports give the same
bytes.

```bash
echolot compare old.json new.json --html      # comparison.html beside comparison.json
```

Top to bottom:

- the verdict, its notes, and one sentence on what a colour means here;
- the whole window before and after as a chart, with the time limit as a line;
- a filter by movement, which is the page's counts as well: all, got better,
  did not change, got worse (when anything did), fine both times, not checked;
- the table: one row per check, with what changed in a sentence, the movement,
  and what the check found added up before and after. "nothing" is a side that
  found none, "at times" one whose findings are in a minority of the launches,
  and a dash a side with no figure to give;
- **Details** on a row opens a panel: what each report said of that check, its
  level in that report in words, and its rows as bars, before above after, with
  a dot per launch;
- three folds: how a movement is decided, every row of the comparison on one
  axis, and `comparison.md` itself as HTML, every line of it.

A colour on this page says which way something moved: green got better, grey
did not change, red got worse. On [the report's page](analysing.md#the-report-as-a-page)
the same colours say how much attention a check needs. The page says so in
words under the verdict, and a movement is always an icon with a shape of its
own and a word, never a colour alone.

Everything the page says about a check is the reading's; it adds labels and
judges nothing. `--html` keeps the reading in `comparison.json` under `plain`,
as `--plain` does, so the page and the file beside it say the same. A
`comparison.html` that already stands there is written again from the next
comparison, flag or no flag, with the reading beside it: left as it was, the
page of the comparison before would read as this one's. Where nothing is
written to disk (no config and no `-o`), `--html` has nowhere to write and
says so.

`echolot ui` shows the same page as its step **After the fix** (see
[The page in the browser](ui.md#the-other-pages)).

What the page does not draw: a timeline of the window, and a flamegraph of the
difference. A comparison holds rows, each a name and a time before and after;
it does not hold when a row began, nor what was inside it. A timeline needs
each row's start within the window, per launch, in the report; a flamegraph
needs the slices under a row, or the sampled stacks, kept for both sides.

## The comparison JSON

```json
{
  "schema": 2,
  "kind": "comparison",
  "comparable": true,
  "warnings": [ { "id": "thresholds", "text": "…" } ],
  "before": { "path": "…", "runs": 5, "generated_at": "…",
              "config_sha": "a3f9c21b07de", "config_path": "/…/echolot.yml",
              "defaults": false },
  "after":  { "…": "the same shape" },
  "window": { "before_ms": 1184.0, "after_ms": 2960.4,
              "delta_ms": 1776.4, "ratio": 2.5 },
  "noise_floor": { "abs_ms": 5.0, "ratio": 0.1 },
  "confidence": 0.95,
  "summary": { "moved": 4, "appeared": 2, "vanished": 1, "steady": 17,
               "fired_before": [ … ], "fired_after": [ … ],
               "state_changed": [ { "id": "binder_txn",
                                    "before": "silent", "after": "1 row(s)" } ],
               "not_compared": [ { "id": "io_wait", "before": null,
                                   "after": "The recording does not carry the phone's own state, …" } ],
               "silent_both": [ "gc_pressure", … ] },
  "rows": [
    { "location": "TeamRepository.loadAll", "detector": "main_thread_block",
      "metric": "self_ms", "change": "grew", "matched_by": "exact",
      "before": { "self_ms": 12.1, "min": 10.4, "max": 14.0,
                  "values": [ … ], "count": 1, "runs": "5/5",
                  "detail": "com.example.app" },
      "after":  { "self_ms": 883.4, "min": 843.4, "max": 923.4,
                  "values": [ … ], "count": 1, "runs": "5/5",
                  "detail": "com.example.app" },
      "delta_ms": 871.3, "ratio": 73.01,
      "shift": { "ms": 871.3, "low_ms": 831.3, "high_ms": 911.3,
                 "resolves_ms": 40.0, "runs_needed": null },
      "holds": true, "detail": "com.example.app" }
  ]
}
```

`summary.not_compared` lists the detectors one side or both could not run,
with each side's reason, `null` for a side that could. Their rows are not in
`rows`, and `silent_both` does not name them.

`markers` holds the report's markers table paired across the two sides, each
row in the shape of `rows` with `detector: "markers"`, and, where both sides
found it, `calls`: `before` and `after` (median calls per run), `delta`,
`moved`, and `shift` (`low`, `high`) with `holds`, read from each run's count.
`summary.markers_moved` counts the rows that moved by time or by calls.

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

With `--plain` or `--html` the file holds one more key, `plain`, after all the
others: `scenario` and `process`, what the two reports are of; `verdict`
(`movement`: `better`, `worse`, `no_change` or `not_compared`; a
`headline` and the sentence, `text`), `window` (each side's median, least and
most launch, and the runs), `notes` (`id`: `budget`, `few_runs`, `unlike`;
`attention`, a `title` and a `text`) and `checks`, one item per detector:
`movement`, `title`, `why`, the sentence `text`, how many `findings` went, came
and stayed, and under `before` and `after` what that report said of the check:
its `attention`, its findings added up for a typical launch (`ms`, `null` for
none), and its own sentence (`found`). A movement is never under a key called
`level` or `attention`: those are the reports' words for another thing.

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
`analyze` copies it into the report, so that the plain reading can say how the
launch stands against it (see "A plain reading" in the analysing guide); that
is a sentence, and nothing fails over it.

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
  - uses: grishan0v/echolot@v0.17.0
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
| `scenario` | the config's first | which of the config's `scenarios:` the traces are, and `analyze` measures; a name the config lacks fails the step |
| `baseline` | — | a `report.json` to compare against, in place of the lookup |
| `baseline-branch` | the base branch, or else the default branch | whose last good run holds the baseline |
| `baseline-workflow` | this workflow | the workflow file whose runs keep the baseline, such as `nightly.yml`, for a pull request job that compares against a nightly |
| `artifact-name` | `echolot-report` | what the reports are kept as and looked up by; each leg of a matrix, and each scenario, needs its own |
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
traces for every pull request here, on the demo's planted change, where
exactly the lock's two rows have to move, and on one of two scenarios in one
config, where the report has to be that scenario's.

### Several scenarios

A config that names several scenarios, as
[Collecting](collecting.md#several-scenarios) shows, is guarded one
scenario a step. The `scenario` input says which one the traces are, and
`analyze` measures it, with its window. Without the input, the action
measures the first, as `analyze` does without `--scenario`.

Each scenario keeps its reports under an `artifact-name` of its own, so each
run is compared with the last report of its own scenario. A matrix gives
each leg one:

```yaml
jobs:
  benchmark:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - scenario: coldStart
            test: StartupBenchmark_startup
          - scenario: feed_scroll
            test: ScrollBenchmark_feed
    steps:
      # … check out, start the device, run the benchmark's ${{ matrix.test }} …
      - uses: grishan0v/echolot@v0.17.0
        with:
          traces: app/benchmark/build/outputs/**/${{ matrix.test }}_iter*.perfetto-trace
          scenario: ${{ matrix.scenario }}
          artifact-name: echolot-${{ matrix.scenario }}
```

When one benchmark run writes the traces of both scenarios, call the action
twice in that job instead, a step for each scenario, each with its own
`artifact-name`. Two calls under one name fail, since a run keeps one
artifact of each name. A baseline of another scenario, found under a name
the two once shared, is refused by `compare`: the report is kept without a
comparison, and the summary says so.

## Related

- [Collecting traces](collecting.md) — why repeats, and why the sets must be repeats of one scenario
- [Calibrating](calibrate.md) — where the threshold warning comes from
- [Analysing](analysing.md) — from a row in the table to a place in the code
