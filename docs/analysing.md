# From a trace to a place in the code

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/analysing.md)

Three commands sit between a trace you know nothing about and a finding you can
act on: `probe` tells you what is inside, `names` tells you what the detectors
will see, `domains` maps a finding back to a file.

## `probe` — what is inside at all

```bash
echolot probe trace.perfetto-trace --process 'com.example.*'
```

Processes, threads, the longest slices and the start candidates. This is what
fills in the config during setup: the anchor candidates it prints come from a
real trace rather than from someone's memory.

A mask that matches several processes is read as `analyze` reads it: the one
with the most slices, with a line on stderr naming the others. The mask
`scan` writes, `com.example.app*`, also matches a macrobenchmark's own runner,
`com.example.app.benchmark`, and its threads and slices are not the app's.

The thread table is sorted by **CPU time, not slice count**. A thread with zero
slices and hundreds of milliseconds of Running is exactly the blind spot you
are looking for; sorted by slice count it would sit at the bottom.

The process table counts `async` apart from `slices`, and the anchor
candidates carry `(async)` where a thread name would be. Those are the
process's own `Trace.beginAsyncSection` spans, on a track of their own and on
no thread — usually the app's hand-written markers, and almost always the
macrobenchmark's end marker. An anchor may name one. A detector never sees
one, and an agent reading the `slices` column alone would have reported an
app with two dozen named markers as uninstrumented.

**Start candidates** come after the longest slices: the sections Android writes
itself when a journey begins, the ones a journey driven by a command starts
with. A tap reaches the window as `deliverInputEvent`, an intent reaches a
running activity as `activityNewIntent`, a new screen is `activityPause`,
`activityStart` and `activityResume`, a rotation `activityRestart`, a cold
start `bindApplication`. Each is a few milliseconds long, and none reaches a
list of the longest slices in a recording that holds seconds of other work.

```
| start              | event                                | thread  | first_at_ms | n |
|--------------------|--------------------------------------|---------|-------------|---|
| deliverInputEvent* | input reached a window: a tap, a key | (async) | 650.0       | 4 |
| activityPause      | the activity in front paused, …      | main    | 730.0       | 1 |
| activityStart      | an activity was created              | main    | 745.0       | 1 |

Without a `start:`, the window opens at the process's first slice, at 40.0 ms.
```

The first column is what `scenario.start` takes: an input event's name carries
the event's own numbers, so it is offered as `deliverInputEvent*`. `first_at_ms`
is where `analyze` would open the window with it, from the start of the trace:
the first slice of the process, a thread's or an async section, that the name
matches — Android writes each input event twice, an async span first, and that
is why the tap's thread reads `(async)`. The rows are in that order, earliest
first, and the earliest is usually where the journey began. They are the
process's own: a macrobenchmark runner's `bindApplication` is not listed. A
fragment or a navigation library writes nothing of the kind, and its journey
starts at the tap or the intent that led to it. A process with none of them
says so; they need the `am` and `view` atrace categories, which `collect`
records.

It is also the cheapest check that a pulled or downloaded file is a whole
trace. A file with no trace data in it — an empty file, a `report.json` — is
refused, exit 2, as `analyze` refuses it. A recording that lost its tail (a
pull that was interrupted, a full disk, a download that stopped early) is
listed with a warning first: trace_processor reads the packets before the cut
and drops the rest without a word, and the end of a launch recording is where
the process list and the last frames are. `names` warns the same way;
`analyze` and `calibrate` refuse such a file by name. Pull or copy it again.

## `names` — how ART names things here

Most detectors are structural: duration, thread, scheduler state, or a fact
the platform recorded itself. They do not care what anything is called. But `gc_pressure`,
`monitor_contention` and `binder_txn` search by **name**, and the names are
invented by ART, differing across Android versions and vendors. You cannot
guess them — you can look:

```bash
echolot names trace.perfetto-trace --process 'com.example.*'
echolot names trace.perfetto-trace          # next to echolot.yml: its process
echolot names trace.perfetto-trace --grep 'contention|AGENTTMP_'   # only those families
echolot names trace.perfetto-trace --json   # sections, families, threads, masks
```

Cells are cut to fit a terminal, and only a terminal: a pipe or an agent
gets every name whole, and `--wide` does the same on a screen. On a real
hunt the subagent grepped the table for a name the cut had taken and set
`COLUMNS=300` by hand, which never was the reason; `--grep` is the filter it
wanted, over the family name before anything is cut.

Without `--process` and without a config the process with the most slices is
taken — on a real device that is `surfaceflinger`, not the app, and the
command says so on stderr. With `echolot.yml` in the working directory
`project.process` is the default, as it is for `analyze`.

The command collapses names that differ only by numbers (`owner tid: 1234` and
`owner tid: 5678` are one phenomenon — without this, a real trace yields
thousands of rows), sorts them into sections, and shows which detector masks
land on them.

The section that matters most is **"Missed by the masks"**: everything that
looks like GC, locks or binder yet no detector will see. That is the list to
act on. A detector the config turned off (`gc_pressure: false`) sees nothing,
so the names only it would have seen are listed there too.

A section takes a name by the shapes those slices have, never by a word
inside it: `… GC` and `waitWhileAllocating…` for the collector, `Lock
contention on …` and `monitor contention with owner …` for locks, `binder
transaction`, `binder reply` and the `AIDL::` stubs for binder. A JIT
compilation, a class being initialised (`Lkotlin/collections/CollectionsKt;`)
and the agent's own markers go to "Everything else" whatever their names
hold, and a lock of the runtime's own (`Lock contention on ClassLinker
classes lock`) is listed under locks but not as missed, since no mask is
meant to see it.

Two subtleties in the output. Something excluded on purpose via `skip_glob` is
marked as excluded rather than missed — it is a decision, not a gap. And the
mask column speaks only about detectors that search by name; a dash next to
`AppStart` does not mean nobody will find it. The one place the dash is exact
is a row whose thread reads `(async)`: an async section is listed here because
an anchor may name it, and no detector reads it. Such a section goes under
"Everything else" whatever its name says, since no mask could reach it.

### Masks live in the config

```yaml
detectors:
  gc_pressure:
    name_glob: "*GC"
```

The convention is in the parameter's name: one with `name_glob` in it masks
the slice name (`name_glob_alt` is a second mask of the same kind), one with
`skip_glob` is an exclusion. `names` also reads `thread_glob` as a mask over
the thread name, although no shipped detector declares one. It takes them from
the detector files and the config itself, so a new detector with masks
appears in its output without any registration.

They live in the config rather than in SQL because adapting to a device must
not require editing a query. What ART actually calls things on Android 14 is
written down in `echolot/claude/skills/echolot/references/naming.md`.

## `domains` — the slice-to-code map

```bash
echolot domains --root .
```

`domains` is the central abstraction of the config: it turns a name from the
report into a hypothesis without scanning the repository blindly, and blind
scanning is the main context eater.

It is assembled mechanically, because a slice name is a string literal that
survives minification and is found by exact search:

```yaml
domains:
  - slice: "collection_mapping"
    module: ":feature:collection"
    hint: "Mapper.kt:5 — fun mapEntities"
```

The module comes from the nearest ancestor holding a build script. `hint` is
for humans; the engine never reads it, so fix the wording when it is imprecise.

A name held in a constant reaches the map too. Most projects that name their
markers keep them in one place — `object Marks { const val LOAD
= "collection_load" }` — and call a wrapper of their own with the constant:
`AppTraces.start(LOAD)`, `traces.trace<Items>(Marks.LOAD)`.
The literal is at the declaration; the place worth naming is the call, and
that is what the map points at, with the identifier the call used so you know
what to grep for at that line:

```yaml
  - slice: "collection_load"
    module: ":domain:base"
    hint: "CollectionLoader.kt:52 — fun load, via Marks.LOAD"
```

On a real project every marker was written that way, and the map came back
empty over the whole tree.

Precision rules worth knowing:

- a bare `trace("…")` or `traceAsync("…", …)` counts only in files that
  import `androidx.tracing`, otherwise every logging function with that name
  lands in the map. A `trace` called on `log` or `logger` is a log line
  wherever it is, with a literal, a constant or a variable;
- a constant resolves only through a call whose name says `trace` (or
  `beginSection` and its kin): `TimeProfiler.start(Marks.X)` writes no
  slice. A callee that puts, sets or gets, or says metric, attribute or
  counter, is handing the trace something other than a name;
- a call that closes a section — one whose last part starts with `end`,
  `stop` or `finish`, such as `Trace.endAsyncSection(Marks.LOAD, 7)` or
  `AppTraces.stop(LOAD)` — is no site: its begin already named the slice;
- `const val` and Java's `static final String` only. A plain `val` shares
  its name with every local in the project, and a map keyed on the simple
  name would resolve them into each other. Two constants of one name
  holding different strings resolve to neither;
- `src/test`, `src/androidTest` and `src/testFixtures` hold no sites: a
  benchmark reading a marker with `TraceSectionMetric` is not the app
  writing it. Their lines still count as source;
- `build` and `generated` are not scanned — generated code is no place for
  hypotheses.

What reaches the map, then: a literal in `Trace.beginSection`,
`Trace.beginAsyncSection` or their `TraceCompat` twins, a bare `trace("…")`
or `traceAsync("…", …)` under the import above, and a constant handed to a
call whose name says `trace`. A literal is written as the string it holds:
Kotlin's `"cost \$5"` is `cost $5` in the map. A name with `[`, `*` or `?`
is written with each of them escaped, `cache[[]hit]`, since `analyze` reads
every entry as a GLOB and the name must match itself.

A name built at runtime cannot reach the map, and only some of those calls
are counted: `Trace.beginSection(…)`, `Trace.beginAsyncSection(…)` and their
`TraceCompat` twins, and `trace(…)` or `traceAsync(…)` under the import, with
an identifier where the literal would be and no constant to resolve it; and
a Kotlin literal with a template in it, `Trace.beginSection("load_$id")`.
Those are mentioned in the header — they are visible in the trace, and
staying quiet about them would pass a gap off as its absence. Anything else
is neither mapped nor counted, so that count is a floor: a wrapper of the
project's own by any other name, called with a literal or a variable —
`Traces.createTrace("OkHttp CALL $path")` is one.

With `-c echolot.yml`, the map is followed by the config's own `domains:`
entries that no Kotlin or Java string of the code holds, as comments, each
with what the code has that reads like it. A marker taken out of the code
stays in the map otherwise, and `analyze` lists it as absent from every
window after it. The platform's sections are left out, as for the
[anchors](collecting.md#anchors-the-code-no-longer-writes).

### When there is no instrumentation

Then the output is not an empty section but a coverage report and the modules
with the most code and none of it instrumented:

```
# Instrumentation: 0 tracing calls across 43654 lines of source.
#
# No instrumentation. There is nothing to attach findings to…
#
#   :app                           37901 lines, 583 files
#   :design-system                  4235 lines, 48 files
```

That is an honest answer to "how much do we need to instrument before this
works": the modules with the most code and none of it, by name.

`uninstrumented_cpu` works without any instrumentation anyway — it will show
which of those modules actually burns CPU. And where the first markers go —
the entry points, from the manifest and the SDK, with a source on every row
— is `echolot mark`, in [mark.md](mark.md).

## `explain` — one detector, in words

What a detector reads, what each of its thresholds means and is counted in,
the value it runs with in this project and where that value came from, and
what each column of its rows means there:

```bash
echolot explain                                # the detectors, a line each, and what the config changed
echolot explain frame_jank                     # one of them
echolot explain frame_jank --scenario scroll   # with a scenario's own thresholds
echolot explain --json                         # all of it, as fields
```

```
frame_jank — Frames that missed their deadline
…
Thresholds, as echolot.yml runs them:
  min_frames = 7 frames · from the config, default 3 frames · calibrate: top10(count) * 1.5
      late frames of one verdict in the window, Self Jank or Other Jank, all its types together, below which none of that verdict's rows is reported
  min_overrun_ms = 4 ms · default
      how far past its deadline a frame has to finish to count as late at all

Columns, a row per location:
  …
  max_ms    bends the contract: the worst frame's overrun past its deadline, not its duration, which is `longest` in detail; 0 on a Buffer Stuffing row
```

The values are planned as `analyze` plans them — the default, then the
config's `detectors:`, then the scenario's own section — so what `explain`
prints is what the next run uses, and a config `analyze` would refuse is
refused here with the same reason. A name that is no detector exits 2 with
the nearest ones and the list. A terminal gets the words wrapped; a pipe gets
each on one line, to grep. The words are the detector's own, from its file
([the header](detectors.md)), so there is no second copy of them to drift.

## Detectors for code nobody traced

Three of the detectors find a problem where nobody wrote a `trace{}` call,
which makes them the ones to read first on a project with none.

`uninstrumented_cpu` does not guess — it states a fact:

> thread `DefaultDispatch` was Running for 340 ms, zero slices

Which is exactly where to add `trace{}` and record again. The name is cut
at fifteen characters by Linux rather than by echolot: every worker of that
pool reaches the trace as `DefaultDispatch`, so write thread masks with the
truncation in mind.

So a row can be a whole pool, and `threads` says how many threads it adds
up — here, and in `monitor_contention`, `runnable_starvation`, `binder_txn`
and `io_wait`, which group by thread name too; the report says so under the
table. Eight workers' 3.8 s of waiting in a 5.7 s cold start is their time
together and not the start's: contention there was 2% of the start, which
`report --window` shows. The main thread's row of `uninstrumented_cpu`,
`runnable_starvation` and `monitor_contention` is `main`. It is told apart by
its tid, the pid: its own name, the last fifteen characters of the
process's, reads as nothing in particular, and a worker that never named
itself carries it too.

A row's `total_ms` is the thread's time on a CPU outside every top-level
slice, and `covered_ms` its time inside one: the blind spot and the rest,
which add up to the thread's CPU. Rows are ranked and compared on the blind
spot. A thread other than the main one is a row only when slices cover less
than half of its CPU (`max_covered_pct`): a pool that names nine tenths of what
it does is not where the next marker goes, and the slivers between its slices
would bury the rows that are. The main thread is a row whenever its time
outside slices clears `min_running_ms`, however well the rest of it is marked.
That time is the window's own, and the first place a regression in the app's
code hides: on a large app's cold start, whose main thread named 72–80% of
its CPU in every run, a 200 ms busy loop planted on it with no section around
it grew the window by 277 ms and was in no row until the share stopped
applying to the main thread.

A recording with callstack samples ([`runner.sampling`](collecting.md#callstack-sampling))
answers the next question in the same round: what the thread ran. The row
reads the samples that fell in its blind spot — on a CPU, outside every
top-level slice — and says so at the end of its evidence:

> 100.0% of CPU outside slices · 28 stacks: GzipSink.write 54%, ReadBarrier::Mark 21% · ours: Store.save 36%, cut 29%

Before `ours:` is what ran: on each stack, the first Kotlin or Java method
with a name, counted from the top. The frames above it are passed over,
because on a phone they are mostly the runtime's: of 8,092 stacks in the blind
spots of six sampled cold starts, 47% had ART's own code on top — the
interpreter, class loading, the collector — and 21% the C library's. A stack
with no method named is counted under its first named frame, a C++ one read
back from its mangled form, and a stack with no name at all under its file,
`[boot.oat]`.

After `ours:` is the nearest frame of the project's own code on each stack,
the place that asked for the work, and the `code` column names its file:
`sampled at Store.kt:12`. A stack with nothing of the project on it is one of
two things. `none` went down to its thread's start: work nobody of ours asked
for, or asked for by handing it to a pool, whose task runs without its
caller. `cut` ended before that, in the framework's compiled code, which
comes back without names and where the unwinder stops: 69% of those stacks
ended there. What asked for the work is not in a cut stack, and the row
says so rather than guessing.

A minified build's frames come back as R8 named them, `a.b.c`, and the row
is only worth reading once `project.mapping` names the build's `mapping.txt`
([A minified build](collecting.md#a-minified-build)); the header says when the
app's methods still read as minified.

The shares are of the samples that came with a stack. `report.json` keeps
the ten largest of each list in the row's `stacks`, with their counts; the
kernel cuts a thread's name to fifteen characters, so a pool's workers share
one row and one set of samples. Which frames are the project's is decided the
way it is for an ANR, by the packages the checkout's sources declare
([ANR](anr.md#from-a-frame-to-a-file)), so a benchmark build installed under
a suffix of its own still finds its code. A config kept outside the checkout
leaves only the package to go by, and then a library the platform lists do
not name reads as the project's. When repeats are merged, the evidence comes
from the worst repeat that has stacks: one recording of six can lose every
stack. A recording without samples gets the row it always got.

`io_wait` needs none either, and it is the one that answers a question the
other two cannot even ask. A thread waiting for the disk burns no CPU, holds
no lock of yours and has no slice around it: there is nothing to profile and
nothing to instrument, so a cold start can spend hundreds of milliseconds
there with every other detector silent. The kernel is the only witness, and
it says so through `sched/sched_blocked_reason`.

It names the thread and the milliseconds, not the kernel function. Turning the
blocking address into a name needs `/proc/kallsyms`, which a production build
does not let anyone read — on an SM-A515F the function came back empty for all
6683 uninterruptible sleeps in the trace while the disk flag was set on 6486
of them. The name appears on a userdebug kernel and the row is worth acting on
without it.

`main_thread_waits` needs none either. A main thread asleep has no slice that
says what it waits for, and the kernel names the thread that woke it. A row
is a thread of the app that let the main thread go from sleeps it began inside
its own work, with its digits folded, so that Room's `arch_disk_io_0` to `_3`
are one row and every coroutine worker is `DefaultDispatch`, as the kernel's
fifteen characters leave it. `detail` names the slice it waited longest in. On
a large Kotlin app it blocked inside two SDKs' initializers until the thread
loading SharedPreferences let it go, 58 ms on the median. Those traces caught
the end of a start the system had begun on its own, right after a force-stop,
and the launch that followed was warm. A sleep with no slice open is not a
wait (#926): it is the looper with nothing to do, and the thread that ended it
posted the next message. An app that shows a WebView has one of Chromium's
threads doing that hundreds of times a run, and counted as waits it ranked
first by seconds. So a launch that sits idle until its background work posts,
as a Compose app's first launch on an A51 did for 0.7 s of 2.1 s while Room
filled its database, has no row here; the start's breakdown shows the main
thread sleeping, and `uninstrumented_cpu` the threads that worked. A lock's
wait stays with `monitor_contention`, a binder reply with `binder_txn`, and a
sleep that `RenderThread` or another process ended is left out.

`main_thread_kernel_waits` is the same reading of a different sleep: the main
thread in the kernel for something other than the disk (`D`, `io_wait = 0`),
by the thread of the app that let it out. Here `RenderThread` and binder
threads count, since whichever thread released the lock is the finding. On a
large Kotlin app's cold launch on an A51 that was ~106 ms a launch, 97% of it
ended by the app's own threads, and where they had a slice open it was work
that maps memory: native libraries loading, threads starting, the EGL
context, dex files. A test app on the same phone confirmed the mechanism:
with three threads mapping memory beside it, its main thread spent 2.3 s
there instead of 5 ms. Which kernel lock it was, the trace does not say on a
production build; on a userdebug kernel `detail` names the function.

`frame_jank` needs no instrumentation at all: SurfaceFlinger records every
frame's deadline and what it actually took, and says whose fault a miss was.
Frames it tags `Buffer Stuffing` only waited in a queue behind the frame
before: they reach the screen late and smoothly, so their row counts them
with nothing past the deadline, after every late frame.
Android 12 and up — see [requirements](../README.md#requirements).

## Your own names, measured every time

A detector shows a marker only when it clears a threshold, and a marker you
planted is one you want the number for whatever it cleared. So a report
carries a **Markers** table above the detectors' sections whenever there is
anything to put in it, and two things decide what that is:

- every name `domains[].slice` lists, each read as a GLOB;
- every name that starts with `instrumentation.temp_prefix` — `AGENTTMP_`
  when the config sets none.

Thread sections and async ones both, since a project's own markers are
usually async. One row per name, with the threads it ran on and `(async)` for
a section on no thread. Self time subtracts the children, so a marker wrapping
another reads as the difference between the two. A `domains` name that no
repeat's window held is listed under the table as not in the window: the map
points at something this scenario does not run, or the name changed under it.

That makes `domains` more than a map for the reader. It is also what `analyze`
measures of the project's own vocabulary, run after run, without a threshold
in the way.

Temporary markers that are in the code and in none of the traces get a
warning at the top of the report. The code's markers are the prefixed names
on lines tagged `// echolot:mark`, in the checkout the config is in, and they
are held against every prefixed name anywhere in the traces, the window or
not. When none of them is there, the app that was recorded was built before
them. In launch and command modes `collect` records the build on the phone,
and the warning names the gradle task that builds and installs the variant
the config measures, by the rule `mark --apply` names it with
([mark](mark.md#what-comes-next-the-build)); in gradle mode the
macrobenchmark builds the app, so the traces are older than the markers. A
marker missing beside others that are there gets no warning here: from the
traces alone it reads as code the scenario does not run. `collect` reads the
build itself before the round, and stops at a build that lacks any of them
([Collecting](collecting.md#the-build-on-the-phone)). A name the code puts
together at run time, `"AGENTTMP_load_$id"`, counts as there when a name in
the traces starts with its fixed part. `report.json` carries the names as
`markers.unbuilt`, and every prefixed name the traces hold as
`markers.in_trace`.

The other way round gets a warning too: prefixed names the traces hold that
no string in the code makes any more. `mark --remove` took them out, or a
hand did, after the app that was recorded was built, so it has none of the
changes made to the code since. A fix is among them as often as not, and a
round compared as if it had one says the fix did nothing. Any string in the
code counts, tagged or not: a marker added by hand without the tag is in the
code all the same. A name put together at run time — a template, a format, a
string joined to more with `+` — makes every name that starts with its fixed
part. A config kept where there is no Kotlin or Java says nothing, since
every name would read as taken out. The warning names the gradle task as the
other one does, and `report.json` carries the names as `markers.removed`.

Names that carry the temporary prefix keep their digits. `names` folds
numbers into families — `worker-2` and `worker-5` are one pool — and leaves a
prefixed name whole: `AGENTTMP_fill_v4` and `AGENTTMP_fill_v6` are two markers
somebody wrote to tell two things apart. `names` takes the prefix from the
config or falls back to `AGENTTMP_`; `compare` takes it from the config only.
The Markers table folds nothing at all.

## Reading a report without opening the json

`report.json` is the contract, and it is not the thing to read whole.
`echolot report` prints one view of a report already on disk and writes
nothing but its own line in `.echolot/log/runs.jsonl`. `--html` is the one
view that writes a file, the page beside the report:

```bash
echolot report                                 # what fired: one line per detector
echolot report --detector monitor_contention   # its rows by median × share of runs, five of them
echolot report -d main_thread_block --top 12   # more rows; -d repeats for several detectors
echolot report --window                        # the anchors or the launch, the main thread, the device
echolot report --markers                       # the Markers table, fifteen rows of it
echolot report -d repeated_work --json         # the same selection as json, places included
echolot report path/to/report.json --window    # another report, a round's own copy
echolot report --plain                         # the report in plain words, one line per check
echolot report --html                          # the same reading as a page, report.html
```

Without a path it reads `.echolot/out/report.json` next to the config, or
under the working directory when there is none. A config with several
scenarios keeps each one's latest report in `.echolot/out/<scenario>/`, and
`--scenario` says whose to read, the first one's without it. A detector's view cuts the
location at 60 characters and the evidence at 100; `--wide` keeps both
whole. `--json` keeps everything and cuts only the rows, and only when
`--top` says how many.

A detector's rows come in one order everywhere — `report.json`, `report.md`,
the overview's top row and the views — and over a set of repeats it is not
the order of the median alone. A row's median is taken over the repeats that
found it, so a 200 ms spike in one repeat of five has a median of 200 ms and
came before 60 ms in all five; a view that shows the first five rows showed
the spikes. Rows are ranked by the median times the share of the repeats
that found them: the spike counts for 40 ms, the steady row for 60. One
trace, or a row every repeat found, is ranked by its median as before. The
view says what it cut, and how much of that one repeat alone found:

```text
_Showing 5 of 38; of the 33 left, 15 were found in one run of 5. `--top 38` for all._
```

The cut is the view's. `report.md`, which a terminal prints and the GitHub
Action puts in the job summary, and `report.json`, which `compare`, the
`--json` views and the Action read, keep every row.

### What `analyze` prints

In a terminal `analyze` prints the whole `report.md`, as it always did.
Anywhere else — a pipe, a script, an agent — it prints what `echolot report`
prints with no view named, and says so in its last line; the files are the
same either way. An agent reads what a command prints into its context: on
a large app's cold start the whole report was 35 KB, 466 lines, in every
round of a hunt, and the layer then had it read the report again with
`echolot report`. The overview keeps every warning the whole report puts
above its findings — an anchor that missed in some repeats, repeats whose
windows diverged, repeats of different processes, a window that opened
inside a wait — and its pointers name the report's path whenever
`echolot report` with no path would read another one: an `-o` elsewhere, a
config with several scenarios, a run from outside the config's directory.

```bash
echolot analyze traces/*.perfetto-trace | head        # the overview, about 35 lines
echolot analyze traces/*.perfetto-trace --full > r.md # the whole report, into a pipe too
```

The test is the one `names` makes, turned the other way: the screen gets what
fits a screen, a pipe gets what its reader acts on, and nothing either leaves
out is out of reach. `reflect` of one session follows the same rule: outside
a terminal it prints the row `--all` gives that session, and `--full` the
whole report.

### One recording, one repeat

`analyze` counts each recording once, however it was named. A path given
twice, two globs that overlap, a symlink beside its file or a set copied under
another directory name would otherwise read as more repeats than there are:
every value twice on its side, and `compare` seeing twice the agreement there
is. The same file by its resolved path, or the same bytes by size and hash, is
one recording; the first path is kept, a line on stderr and one under the
report's Traces line name the others, and `same_recording` in `report.json`
lists them. `Runs` is the number of distinct recordings.

A trace `collect` set aside as not cold, given among cold ones, is left out
the same way: `collect` stopped the app before it and found the process
running again by the start, so it holds neither the process start nor
`bindApplication`. `analyze` reads that from the account `collect` kept beside
the trace, not from the trace, names it on stderr and under the Traces line,
and lists it in `report.json` as `not_cold`. A set of such traces alone is
read as given. See [launch mode](collecting.md#launch--we-drive-it).

A launch that ended on another activity than most of the set's is left out
the same way, and named with its repeat and the activity it ended on: an app
that opens the page read last, a login or an onboarding by itself ends its
launch there, and the window runs on through that screen. `analyze` reads
where each launch ended from `collect`'s account (`am start -W`'s `Activity:`)
or, without one, from the trace's last `performCreate:` inside the window, and
lists the ones left out in `report.json` as `ended_elsewhere`. Where as many
repeats ended on two activities, the one `collect` started is read; with none
of them that one, every repeat is, `window.activities` counts them, and the
report says the medians mix two journeys. See
[A launch that ended elsewhere](collecting.md#a-launch-that-ended-elsewhere).

### Runs that drift from the first to the last

A set's medians are one state of the device measured again and again — when
the runs are alike. A device that warms up, heats, fills its caches or catches
up with background work between the first run and the last makes them a slope,
and the median of a slope is no one state of it. A comparison reads the slope
as moves: three sets of 15 cold starts of one build on an SM-A515F, each split
in two, held 7 to 12 rows past the floor first half against second half, where
random halves of the same sets held 1 to 3.

So `analyze` asks of every merged set whether its runs follow their order. It
reads the rows found in every run whose median is 5 ms or more, the floor below
which `compare` calls nothing moved, and for each one how closely its values
follow the order of the runs: Spearman's ρ, made of ranks, so one slow run
weighs no more than any other. The squares of those, averaged, are the set's
score; with nothing tied to the order it averages 1 / (runs − 1). The same
score for a thousand orders of the runs drawn at random, the same order for
every row at once, says how often chance scores as high: `p`. At 5% or less
the set drifts, and the report says so above its findings, the overview too:

```text
> ⚠️ The runs drift from the first to the last: the 62 rows found in every run follow the order of the runs more closely than 15 runs in a random order would (p = 0.026). Closest: `io_wait` com.example.app · main 610.3 → 687.1 ms (ρ +0.74); … A median over them is no one state of the device, and a comparison with this set can read the drift as a move. …
```

One test for the whole set, rather than one per row or one for the window,
because neither of those saw it. Tested row by row, no row of those three sets
survived a correction for testing sixty of them; together the rows moved enough
to make the two halves differ. The window's own trend was weak in two of the
three, and gone in all three with the first two runs left out, while the halves
still differed. Rows that move together because one run was slow in all of
them move together under every order drawn too, so they count once, not sixty
times. Shuffled, forty-nine real sets of 10 to 60 runs drifted in 52 tries of
980: 5.3%, the share a test at 95% should give.

The order is the set's: by file name, numbers as numbers, which is the order
`collect` recorded them in. Fewer than 8 runs are not judged — eight are the
fewest a comparison splits four against four — and `report.json` keeps the
block either way:

```json
"drift": { "runs": 15, "rows": 62, "score": 0.117, "chance": 0.071, "p": 0.026,
           "drifts": true,
           "most": [ { "detector": "io_wait", "location": "com.example.app · main", "rho": 0.74,
                       "first_ms": 610.3, "last_ms": 687.1 }, … ] }
```

`drifts` is `null` with no `p` under 8 runs or with no row to read; `most`, the
three rows that follow the order closest with the median of their first and
last third of runs, only when it is `true`. Where only the first runs stand
apart the device was still settling, and the set without them is one state;
where the values climb or fall all the way, `runner.cooldown` holds the device
to one temperature. `compare` warns when either side drifts — see
[the comparison](compare.md#runs-that-drift).

### A set read side by side

`analyze` reads the traces of a set at once, each in a trace_processor of its
own. All the work of reading a trace happens in that process, and echolot only
waits for its answers, so one after another kept one core busy while the rest
waited. How many at once is the smallest of three numbers: the traces, the
cores, and how many trace_processors half the machine's memory holds, each
taken at twelve times the size of the largest trace (one holds its whole
trace, up to 260 MB for a cold start of 16–24 MB). `-j N` says how many instead;
`-j 1` reads them one after another, in the same process, the way it always
did.

The report is the same either way. The reports are merged in the set's order
— by file name, numbers read as numbers, whatever order the traces were named
in — however the reading finishes, and what each trace's reading printed comes
out whole and in that order: a warning about the second trace after the first
trace's lines, never in the middle of them. A trace that cannot be read ends
the run with its own error, the first one in that order, as before; the traces
still being read are stopped rather than waited for. Ctrl-C, SIGTERM and SIGHUP
end every trace_processor of the set, not only the one that happened to be
reading.

Each trace_processor builds the window's sets once — `_slice`, `_slice_win`,
`_child_sum`, `_aslice_win` and `_tstate_win` are tables, not views, and
`_slice_win` is indexed by id — so a detector reads stored rows instead of
running the join under them again. On
fifteen cold starts of a large Kotlin app, 16–24 MB each, on an 11-core
laptop with 36 GB, eleven at once:

| | one after another | side by side |
|---|---:|---:|
| views | 225.3 s | — |
| tables | 57.4 s | 8.8 s |

The table was measured while about a second of each trace was
trace_processor's start: perfetto's client asked the shell whether it
listened and, finding it not yet up, waited a second before asking again,
for a shell that listens in 30 ms. echolot starts the shell itself now and
asks every 10 ms, so one after another each trace after the first is about a
second shorter than the table says, and `doctor`, which opens a few dozen
sessions, takes seconds rather than forty. Most of what is left is each
detector asked twice on a set, at its thresholds and with them open.

Each trace_processor listens on a port of its own on 127.0.0.1. echolot asks
the kernel for the port and lets it go before the shell starts, and a shell
whose port another process took in between does not stop: it listens on
[::1] alone, and the client reached whoever held 127.0.0.1 — another
session, fed this one's trace and running this one's SQL. That is how tests
run side by side on Linux, where the kernel hands a released port out again
at random, failed with `view _proc already exists`. Now a lease holds the
port from the draw until its shell listens — a lock on the port's byte of
one file in the temporary directory — and another echolot draws another
port. And before anything is sent to what answered, echolot reads its own
shell's stderr: a shell that says it could not listen on 127.0.0.1 means the
answer came from another process, and it is started again on another port.

## When a detector could not look

A detector with no rows says it ran and nothing cleared its bar. Some are
silent for another reason: the trace does not hold what they read. `io_wait`
reads the kernel's reason for a thread's sleep, and a recording made with
`runner.environment: false` has none; `composables` reads the names an app
writes only when the recording asked for them; `frame_jank` reads a frame
timeline that phones older than Android 12 do not write. Each detector says
what it reads in its header, and `analyze` asks the trace
([the sources](detectors.md#when-the-trace-does-not-hold-what-a-detector-reads)).

Every entry of `detectors[]` in `report.json` carries two keys for it:

```json
{ "id": "io_wait", "rows": [], "error": null,
  "checked": false,
  "reason": "The recording does not carry the phone's own state, such as why a thread went to sleep, so there was nothing for this check to read." }
```

`checked` is true when the detector ran, to the end, on a trace that held what
it reads. `reason` is there only when it is false: one sentence, in plain
words, that names what is missing. A detector that failed is not checked
either, and its reason says to read its `error`. A set of repeats is checked
only when every repeat was, and the reason then says in how many recordings it
was not. A report written before the keys existed carries neither, which means
unknown, and never checked.

`report.md` says it in one line beside the one for detectors a config turned
off, grouped by what they lack, and the **Silent** line does not name them:

```
> ⚠️ 2 detector(s) could not run, because the trace does not hold what they read. Their silence says nothing about the app. `io_wait`, `main_thread_kernel_waits` — The recording does not carry the phone's own state, such as why a thread went to sleep, so there was nothing for this check to read. A recording that holds what they read lets them look; until then those checks are open.
```

One source is asked of the window and not of the recording: the start of the
app's process, its `bindApplication`, which `app_init` reads. A scroll, a tap,
a warm start, or a recording with no launch in it holds none in its window,
and `app_init` is not checked there either. Nothing is wrong with such a
recording, and no other recording of the same window would let the check
look, so that is a line of its own, without the warning sign:

```
> 1 detector(s) did not look, because what they read is not in this window. Their silence says nothing about the app. `app_init` — The window holds no start of the app's process (`bindApplication`): what this check measures runs only as the process starts, on a cold start, so there was nothing for it to read.
```

`echolot report` says `not checked` in its table where it said `silent`, and
`echolot report -d io_wait` gives the reason. The rows of such a detector are
whatever the trace gave it; none is not the answer "clean". `compare` leaves a
detector that could not run on either side out of its table, and says so (see
[Comparing](compare.md#when-two-reports-may-not-be-compared)).

## A plain reading

`echolot report --plain` reads a report for someone who has never opened a
trace: the verdict on top, one item for each check that found something, and
the rest as a count.

```
NEEDS ATTENTION   The launch takes 1.9 s. You allowed 0.5 s, so it takes 3.8 times as long.

 !  The screen waits for background work
    The main thread waited 0.7 s for `arch_disk_io_#`, in all 3 launches. That is 36% of the launch.
 ~  Work nobody can name
    5 threads or pools of threads spent 1.2 s of processor time between them on work that nothing in
    the recording names. The largest part is on `arch_disk_io_0`: 0.5 s, in all 3 launches.

 ok 14 checks are fine
```

No model writes any of it, so the same report gives the same words. Each
detector carries three fixed texts in its `.sql` header, beside `@title` and
`@why`: what was checked (`@plain_title`), how it touches the app
(`@plain_why`) and what a clean result says (`@plain_clean`). What was found is
one sentence from a template per detector over its rows, in `echolot/plain.py`.
The same words are in `report.json`, so the terminal, the page and an agent
read alike (see `references/report.md` for the keys). The stretch is called "the
launch" when the trace holds a startup, and "the run" otherwise.

How much attention a check needs:

| level | when |
|---|---|
| needs attention | one thing the main thread spent or lost holds at least 15% of the launch and at least 0.15 s, in every launch. A freeze the system itself recorded (`anr`) is always here |
| worth a look | the check found something else: smaller, on another thread, or not in every launch |
| a marker to move | the only rows are near misses of `repeated_work`: a marker put one level off. It is advice on where the marker sits, not a finding, and it is not offered to an agent |
| fine | the check ran and found nothing above its limit |
| not checked | it found nothing, and it could not run on these recordings (it says why), or the report is too old to say whether it could |

A check with rows reads its rows, whatever `checked` says: a set of recordings
may have lacked what the check reads in some of them, and the rest have rows
(see [When a detector could not look](#when-a-detector-could-not-look)). Only an
empty check can be "not checked", and it is "fine" only when its entry says
`checked: true`. `analyze` always writes that key, so an entry without it is from
a report written before echolot recorded it, and says so: "This report was
written before echolot recorded whether a check could run. Analyse the
recordings again to find out." `echolot report --plain` says such a reason once
for all the checks it covers.

What the numbers rest on. The 15% is a share of the launch *as it is measured
now*: after a fix has shortened the launch, a piece that did not change is a
larger share of what is left, and can move from "worth a look" to "needs
attention". Every sentence carries both the time and the share, so a steady
piece reads steady. The 0.15 s is there because every launch has a biggest
piece, and in a short one that piece is a large share: on fifteen recordings of
a healthy half-second cold start, 15% alone marked a launch red on about
half of the three-recording sets, and with the floor on none. Both numbers
come from one app.

Only time of the main thread can need attention. What other threads did is set
against the launch's length, which is a yardstick rather than a share of it: the
CPU time of five background threads can add up to two thirds of the launch while
the main thread waits for them for a third of it, and that wait is the row that
says so. A row on another thread is at most "worth a look".

A main thread that slept with no slice open was idle, and idle time is not time
it lost, in any window: it could draw and answer taps the moment either came,
and the thread that woke it only posted its next message. `main_thread_waits`
counts the sleeps it began inside a slice and no others (#926). A report
written before then added the idle ones to a row and said so in its evidence
(`idle`, or `67% idle · 33% blocked in X`); read again, such a row counts and
says only the part blocked inside a slice, and that the rest is not counted. A
journey that an end anchor closed used to count the idle part in full, and a
WebView's thread waking the idle looper read as seconds of waiting.

The verdict sets the launch against `scenario.budget_ms`: within it is fine, up
to a quarter over is worth a look, more is needs attention. Nothing fails
either way, since there is no gate. With no budget the verdict says how long the
launch takes and that no limit is set, and its level is "no time limit", in
grey: green always means that something was looked at and found fine. [`echolot ui`](ui.md#setting-a-project-up)
takes the limit in a field when it writes a project's first config.

A window that is not the scenario's is not judged. When an anchor matched
nothing, in some recordings or in all, or the end never closed before the
recording stopped, the verdict is "not checked" with or without a limit, and
says "There is no measured launch to judge" instead of how long the window
took: that length is the recording's. Where the trace holds the platform's own
startup, the verdict gives it as the platform's number. The plain reading and
the page say what was not found before anything else ("echolot did not find
where this scenario ends (`app_marker`) in 5 of 5 recordings, so the numbers
below are about everything the app did in the recording, not about the scenario"), the shares in
the sentences are "of the recording", and `report.json` keeps that line as
`verdict.warning`.

## The report as a page

`report.md` is a table for an agent and a terminal. For a person there is
the same report as a page (and [`echolot ui`](ui.md) opens it without writing
a file):

```bash
echolot analyze coldStart_*.perfetto-trace --html   # report.html beside report.json and report.md
echolot report --html                               # the page of the last report
echolot report path/to/003.json --html              # of another one: 003.html, beside it
```

It is the plain reading laid out as a document, for someone who has never
opened a trace. From the top:

- **The verdict**: the level, how long the launch takes against the limit,
  and how many recordings that was measured on.
- **A filter by level**, which is the counts as well: all, needs attention,
  worth a look, fine, not checked.
- **A table, one row per check**, the ones that need the reader first. A row
  says what was checked and what was found, how that touches the app, and the
  level. Nothing technical is in a row. A check that is fine carries its clean
  result there, and one that could not run carries the reason.
- **Details** on a row opens a panel with that check's measurements: the
  detector's id and title, its thresholds and where they came from, and its
  rows as bars on a millisecond axis, the median as a bar and each recording
  as a dot. A line marks where the check fires for a detector whose one
  threshold is a floor under the measure drawn (`main_thread_block`'s
  `min_slice_ms`, say); a detector that fires on either of two thresholds
  has no single line, and its numbers are in the text.
- **How the colours are chosen**, in a sentence per level.
- **The full technical report**: where the main thread was during the window,
  every row of every detector on one axis, and then `report.md` itself as
  HTML, line for line.

A check that found something can be ticked. **Dig deeper** puts together the
request for an agent from what is ticked, to copy and paste into an agent
opened on the project:

```text
/echolot-hunt `coldStart` takes 1,136 ms, the median of 5 launches, and has no time limit set. Find what is behind the rows below, and where that is in the code.

The report warns, and these come first:
- 1 detector(s) could not run, because the trace does not hold what they read. Their silence says nothing about the app. `composables` — The recording does not carry the names of composables, which have to be switched on when recording. A recording that holds what they read lets them look; until then those checks are open.

The rows of the report it stands on:
- App Deadline Missed · frame_jank · 427.51 ms, 5/5 runs · Self Jank · 4 of 19 frames · longest 188.7 ms
- inflate · main_thread_block · 222.68 ms, 5/5 runs · com.example.app
- draw · main_thread_block · 120.8 ms, 5/5 runs · com.example.app
- TextLayout:initLayout · main_thread_block · 73.95 ms, 5/5 runs · com.example.app
- Lock contention on a monitor lock (owner tid: #) · main_thread_block · 61.6 ms, 5/5 runs · com.example.app

Traces: the 5 behind .echolot/out/report.json (the last report). After which change: unknown.
```

That is the demo app with "Frames that came late" and "Heavy work on the main
thread" ticked. One investigation answers one question, so the request leads
with one: how long the scenario takes, against its limit where one is set
("against a time limit of 500 ms. Find what makes it slower than the limit"),
and what to find. The warnings at the top of the report come next, in its own
words, an anchor that matched nothing first: they say how far its numbers can
be trusted. The ticked checks follow as the evidence, the first four rows of
each in the report's order, a row every recording found before a longer one
found once, and a line saying how many more it has; the agent reads the rest
from the report.

The button beside **Dig deeper** ticks where to start: the checks that need
attention, or with none of those the two that found the largest thing. With
more than three ticked, the dialog says that one investigation looks at one
question and suggests ticking fewer. A page written to disk starts nothing
itself; the same report served by `echolot ui` can start Claude Code on the
request ([Dig deeper from the page](ui.md#dig-deeper-from-the-page)).

What holds for the file:

- **One file, nothing fetched.** The styles and the script are written into
  it, the icons are inline SVG and the fonts are the system's. It names no
  address, and it carries a Content-Security-Policy that refuses any, so it
  opens from disk, from a CI artefact or from an attachment.
- **The same report gives the same bytes.** No clock is read and nothing is
  numbered at random.
- **Every word about a check is the report's**: `plain`, `attention`,
  `checked` and `reason` per detector, `verdict` at the top. The page adds
  labels and works out no level of its own. A report from before those keys
  is read the way `echolot report --plain` reads it, and the file is left as
  it was.
- **Light and dark** by the system's setting, and readable on a phone: the
  table becomes a list and the panel a screen of its own.
- **With script switched off** every row and its three sentences are there,
  and the technical report opens with the measurements. The filter, the panel
  and the request need script, and the page says so at the top.
- **It gates nothing.** A red verdict is still exit 0.

The page beside a report is always of that report. Once a `report.html`
stands in the output directory, every later `analyze` there writes it again
from the new report, with `--html` or without, and names it on stderr with the
other files. Left as it was, the page of the analysis before would read as
this one's. `analyze` removes nothing, and where there is no page it writes
none unless `--html` asks for one.

An investigation has a page of its own, with the agent's answer in the same
shape: `echolot hunt --show --html`, described under
[The answer as a page](agent-layer.md#the-answer-as-a-page).

## Where a cold start went

The header's budget says where the window went by thread state. When the
trace holds a launch of the app, a line under it says where the launch went,
by Perfetto's own account:

```
Startup: cold, 1359 ms from the launch to the first frame: 42% `bind_application` · 13% `monitor_contention` · 12% `choreographer_do_frame` · 7% `binder` · …
That is the platform's measure rather than the window: the startup began 121 ms before the window opened, and ended 4039 ms before it closed.
```

The standard library finds the launch (`android_startups`) and gives every
moment of it to one reason (`android_startup_opinionated_breakdown`): the
platform's sections on the main thread, `binder`, the lock waits, `io`, the
thread states for what no section covers, and `launch_delay` for the time
before the app's main thread ran at all. On six cold starts of a large Kotlin
app on an SM-A515F it found the launch every time and accounted for all of
it. The reasons go into `report.json` as `window.startup`, in milliseconds,
merged across repeats by the median of each.

The launch is the platform's measure, from the intent to the first frame. The
window is the config's. They rarely coincide: a window opened at
`bindApplication` misses the process starting, and one with no end anchor
runs on for seconds after the first frame, as it did above. The second line
says how far apart they are, and warns when they do not overlap at all. For a
cold-start question, the launch's reasons are the account of the time the
user waited.

## A window that is the launch

A scenario can take the launch as its window and name no anchor at all:

```yaml
scenario:
  name: coldStart
  window: launch
```

The window then starts at the intent, as the platform records it, and ends
at the later of the launch's end and the end of the app's first
`reportFullyDrawn()` call. The report says in its header which the trace
gave:

```
Scenario window: **696.31 ms**
Window: the launch as the platform records it, from the intent to the end of the app's `reportFullyDrawn` call, which came after the first frame.
Main thread: 61% on a CPU · 6% waiting for a CPU · 3% blocked in the kernel · 20% sleeping · 10% before the process started
Startup: cold, **617 ms** from the launch to the first frame: …
That is the platform's measure rather than the window: the startup began with the window, and ended 79 ms before it closed.
```

An app that does not call `reportFullyDrawn()` gets `from the intent to the
launch's end, its first frame`, and `report.json` says `window.defined_by:
"launch"` where the first says `"launch+reportFullyDrawn"`. When the app's own
slice of the call is not in the trace, system_server's record of the same
call closes the window, and the line says so.

The line this `analyze` leaves in the run log, `.echolot/log/runs.jsonl`,
keeps the same value among its facts, as `window_defined_by`. The report is
overwritten by the next `analyze` and the log is not, so a launch run can
still be told later from an anchored one, whose line counts its two anchors
and has no such key.

A launch ends when the first frame is drawn. That is the whole wait for an
app that holds its splash screen until its content is ready. An app that
draws a placeholder first is still loading after that frame, and only its
`reportFullyDrawn()` call says when it is done: without the call, the work
after the first frame is outside the window.

The line ends with the activity the launch ended on, `The launch ended on
`org.example.MainActivity`.`, where the trace names it: Android 12 and on
write a `performCreate:<class>` slice for every activity created, and the
last one inside the window is it (`window.activity` in `report.json`).

Above the findings, the report names the app's `BOOT_COMPLETED` receiver when
the main thread ran it inside the window, with its time over the repeats
(`window.boot_receivers`): on Android 15 every start after a force-stop runs
it, and a user's start does not. See
[Android 15: what a force-stop adds to a cold start](collecting.md#android-15-what-a-force-stop-adds-to-a-cold-start).
The line is said of any window that holds the receiver, an anchored one cut
from `bindApplication` too.

Such a window opens before the app's process exists, so the main thread has
no state for its first stretch: `10% before the process started` above. That
is the launch's own time, named in the budget, and the warning about a
window only partly accounted for does not fire on it.

The [plain reading](#a-plain-reading) calls this window the launch. Its
shares are of the window's length, with the stretch before the process
started and whatever follows the first frame in it, and its verdict holds
that length against `scenario.budget_ms`. The launch window is longer than a
window cut from `bindApplication` to the same call, by about 0.14 s on the
launches this was measured on. So a limit chosen for an anchored window is a
limit for a shorter stretch, and a share read under one window is a point or
two lower under the other for the same milliseconds.

`analyze` refuses a trace that holds no launch of the process: the app has to
be started inside the recording, as `launch` mode and a startup
macrobenchmark do. A trace `collect` recorded in `launch` mode is not sent to
that mode again: when the trace's launch is the permission controller's, the
refusal says a permission dialog took it and names the command that grants
the permission (see
[A permission dialog over the launch](collecting.md#a-permission-dialog-over-the-launch)),
and otherwise it says `collect` started the app and the platform recorded no
launch of it. The detectors read this window as they read any other.
Reports of two launch windows that closed differently are not compared; see
[compare](compare.md#two-windows).

It costs time only on a trace that has a launch to break down: the breakdown's
tables are computed when the module is included, which took 0.8 s on each of
those starts.

Under the device line the header says how ART had compiled the app, and what
it compiled and verified while the window ran:

```
Compiled: **speed-profile** (install-dm) · JIT 7 methods, 97 ms · 9 classes verified at run time, 68 ms, 5 ms of it on the main thread
```

It is read from ART's own slice for the app's dex, so it holds for whatever
build was on the device, whichever way it got there. `speed-profile` is the
app compiled from a profile, which is how an install from Google Play usually
arrives. `verify` and `run-from-apk` compiled none of it ahead of time, and
the header warns: the interpreter and the JIT carried the launch, and its
numbers are slower than what most users see. On the SM-A515F a large app's
launch took ~1.4 s on its benchmark build and ~8.8 s on a debuggable build of
it that nothing had compiled. `compare` warns when two rounds differ in it.

## The whole trace as the window

A recording made outside the config's scenarios — a scroll recorded by hand,
a teammate's trace, a `.pftrace` from Android Studio — has none of the
scenario's anchors in it. Measured as the scenario, its anchors miss, the
window falls back to everything the process did, and the report opens with a
warning that its numbers are not about the scenario, under the scenario's
name. `--whole-trace` measures it as what it is:

```bash
echolot analyze scroll.perfetto-trace --whole-trace
```

The window is the trace, from its first moment to its last; no anchor is
looked for, so none can miss. The report's scenario is `whole-trace`, it
runs on the config's top-level thresholds, and it goes to
`.echolot/out/whole-trace`, where no scenario's latest report is:

```
Scenario: `whole-trace`
Process: `com.example.app` (pid 4100)
Window: **400.0 ms**
The window is the whole trace, from its first moment to its last: no scenario cut it, so every number below is about everything the app did while the recording ran.
```

`report.json` says `window.defined_by: "whole"`, and the run log keeps it as
`window_defined_by`. This is the one window a report calls the whole trace:
one whose anchors matched nothing runs over the process's own first and last
slices, and says so. A scenario the config names for such recordings can ask
for the same window with `window: whole` in place of `start` and `end`; it
keeps its own name and thresholds, and takes no `budget_ms`, since the
recording is as long as somebody let it run. `--whole-trace` beside
`--scenario` is refused: the two are two answers to where the window is.

The plain reading calls such a window the recording and holds it against no
limit. A launch inside the trace is still described in the header, as the
platform's measure set against the window. Where the trace holds no start of
the app's process, `app_init` says it did not look (see
[When a detector could not look](#when-a-detector-could-not-look)). `compare`
refuses a whole-trace report against one of a scenario's window, and says how
to make the two alike: both with `--whole-trace`, or both without (see
[compare](compare.md#two-windows)).

## What the device was set to do

Two settings of Developer options change what a repeat measures, and the
trace carries neither, so `collect` reads them once a round and keeps them
beside each trace (see [collecting](collecting.md)). The
header says them only when they are not as a phone comes:

```
Animation scales: all three `5`, where a phone comes with 1: an animation lasts its length times its scale, and the frames that draw it, with the work behind them, change with it. Developer options set them back.
> ⚠️ "Don't keep activities" was on: the system destroyed each activity as soon as it left the screen, so a return to the app created it again, a warm start where a phone without it starts hot. Switch it off in Developer options: `settings put` changes the setting and not the running system.
```

At 0 the scales turn animations off, and the line says that the work of
drawing them is not in the numbers; a scale of 0 is how UI tests run, and it
is said rather than refused. "Don't keep activities" is a warning where the
scenario returns to a living process, as a `reset_policy: none` round or a
command scenario does; for cold starts recorded with a force-stop it is a
plain line, since a cold start creates its activity anyway. A set whose
repeats split on either says in how many of them. A report of traces
recorded before `collect` read these says nothing about them, which is not
the same as a phone as it comes, and `compare` says so.

## From a row to a line, without `domains`

Some rows name the code themselves. ART's contention slice carries both
sides of the lock as frames — `at void pkg.StoreRepository.update(…)(StoreRepository.kt:30)
waiters=0 blocking from … StoreRepository.find()(StoreRepository.kt:61)` — and
`main_thread_block` names a class when the slice is a View being inflated.
When ART did not know the owner's method it leaves out ` at <frame>`, about
one contention slice in fifty on real cold starts, and the waiter's side is
placed alone.
A blind spot with samples behind it names the project's method the samples
fell under most. `analyze` looks those up in the checkout the config sits in
and writes the answer into the row: a `code` column in the markdown, and
`places` in the json with the symbol, the file relative to the project, the
line and which it is (`owner`, `blocked`, the `location` itself, or
`sampled`).

A minified build writes the lock's frames as R8 named them,
`a.a.run()(SourceFile:6)`, and no file of the checkout holds them. With
`project.mapping` set, `analyze` names both back before it places them: the
place is the method the runtime stood in, inlined into another or not, at the
line it came from, and it carries `retraced: true`. The evidence keeps the
frame as it was recorded, and one line in the header says so. A frame the
mapping has no place for, with its line in no range of its method, gets its
class back and keeps R8's method name. From two such frames and a tenth of
them up the header warns that the mapping is probably another build's; one
alone it only counts, since the right mapping can miss a line of a synthetic
class R8 merged. Frames that name their files after a mapping,
`r8-map-id-<id>`, are named back only by that mapping, and then no frame it
misses makes it another build's; a frame of a class R8 kept is named back
only in a build something shows is minified
([A minified build](collecting.md#a-minified-build)).

The line is the runtime's when the build kept line numbers and the
declaration's when it did not — a release build says `(File.kt:-1)` for
everything, and the declaration is where a reader opens the file anyway.
A symbol outside the checkout keeps its name and gets no file: an owner
parked in `jdk.internal.misc.Unsafe.park` is the holder waiting on something
else while holding the lock, which is a finding rather than a gap. Two files
of one name are told apart by the package in the symbol; when nothing tells
them apart the first is taken and `exact` is false.

On the hunt this was built from, the subagent spent forty-six percent of its
window reading the application to find a method whose file name had been in
the row it was shown. The whole checkout is indexed — once per `analyze`, and
only when a row has something to place — rather than `project.source_root`:
a lock in `domain/` is exactly the kind of place a project with several
modules has, and that key's example value names one module.

The checkout is the config's directory. A config kept elsewhere, to analyse
traces on their own, names it: `analyze --root <checkout>`, or `project.root`
in `echolot.yml`, a path from the config's directory. When the checkout holds
no Kotlin or Java sources while the report has rows to place or composables
to tell apart, `analyze` says so on stderr and in the report's header: every
row is then left without a file, and `composables` finds none of the
project's own to rank.

Which project a command works on is decided once, the same way for every
command and for the run log: the config `-c` names, else the nearest
`echolot.yml` from the working directory up to the top of the git checkout,
else the working directory, as a project not set up yet. So `analyze`,
`hunt`, `mark` and the rest run from `app/` or a build folder work on the
project above it, and `echolot` alone prints which. The checkout is then
`--root`, else `project.root`, else the config's folder — for `analyze`,
`mark`, `anr`, `domains` and `scan` alike. `project.source_root` is read by
no command: a config that still has it is told so, and the next release
refuses it.

## Two rows about one name

A name can appear under both `main_thread_block` and `main_thread_outlier`,
and they are not saying the same thing twice. The first gates on the **sum**
for that name — the work is expensive every time it runs, and the fix is in
the work. The second gates on a **single occurrence** against the median for
that same name — the work is usually fine and once was not, so the cause is
the state it hit that once: a cold cache, a lock, a first-run path, an
allocation that stalled.

Walking down to the code the same way for both wastes a round.
`main_thread_outlier` puts the median and how many occurrences it came from in
`detail`, which is also the number to hold a benchmark's percentiles against.

## Rows of Compose

A Compose app writes its phases into the trace with nothing set up, and they
become rows like any slice. On a large Compose app's warm start on an
SM-A515F they were 5 of the 20 `main_thread_block` rows, `Compose:recompose
×55, 224 ms` among them, and 6 of the 12 `main_thread_outlier` rows, one
`Recomposer:recompose` of 314 ms among those. Where each one sits in the trace
says what it is:

| slice | sits under | what it is |
|---|---|---|
| `Recomposer:recompose` | `animation`, the frame's first stage | the frame's recomposition, every composition whose state changed |
| `Compose:recompose` | `Recomposer:recompose`, `AndroidOwner:onMeasure`, `traversal`, or nothing | one composition: recomposed in a frame, where state changed; composed during measure, where a lazy list or another subcomposing layout composes its items; or a view composed for the first time, under `traversal` when the frame that attaches the view composes it, or under nothing |
| `Compose:applyChanges` | the same four | that composition's result applied to the UI tree |
| `AndroidOwner:onMeasure`, `onLayout` | `measure`, `layout` | a `ComposeView` measuring and placing its layout nodes |
| `AndroidOwner:draw` | `Record View#draw()` | drawing them |
| `TextStringSimpleNode::measure` | `AndroidOwner:onMeasure` | a text measured, its layout built under it as `TextLayout:initLayout` |

The self time of `Compose:recompose` is the composable functions running: none
of them has a slice of its own, so their time is the pass's, 195 of the
224 ms above. The row says that composition is expensive. No slice in the
trace says which composable.

A composable gets a name in the trace when the app adds
`androidx.compose.runtime:runtime-tracing` and the recording switches the
app's Perfetto SDK on. A macrobenchmark does it with
`-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=true`
in `runner.gradle_args`; without the flag, Macrobenchmark 1.5 recorded no
names. In launch and command modes, `runner.compose_names: true` switches it
on for the set and off after it ([Collecting](collecting.md#composable-names)).
Each call
is then a slice on the main thread, `fully.qualified.Name (File.kt:line)`, at
the composable's declaration.

That moves the time out of the phases' rows. On a cold start with names,
`Compose:recompose` kept 5 ms of self time out of 144, and left
`main_thread_block`; no composable came close to the 16 ms of self time a row
needs there, since a composable's time is mostly in the library's composables
under it. The `composables` detector ranks the app's own by the time they
hold, the library's under them counted in, each placed at the declaration
its name carries. On that start its rows were the navigation host, 29 ms of
its 42, an icon painter seven calls and 19 ms, six of them during measure, a
bottom bar and a button. A row's `detail` says which of the places in the
table above composed its calls: `in a first composition`, `in a frame`,
`during measure`, or `elsewhere` for any other. `echolot names <trace>
--grep '\.kt:'` lists every composable, the library's too.

## Once you have two of them

Everything here reads one set of traces. The question that brought you —
*it was 3 s, now it is 7 s* — has a second half that needs two, and reading
two twenty-row tables against each other by eye is the work this tool exists
to remove. That is [`echolot compare`](compare.md).
