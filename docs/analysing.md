# From a trace to a place in the code

[← Docs index](README.md) · [README](../README.md)

Three commands sit between a trace you know nothing about and a finding you can
act on: `probe` tells you what is inside, `names` tells you what the detectors
will see, `domains` maps a finding back to a file.

## `probe` — what is inside at all

```bash
echolot probe trace.perfetto-trace --process 'com.example.*'
```

Processes, threads and the longest slices. This is what fills in the config
during setup: the anchor candidates it prints come from a real trace rather
than from someone's memory.

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

## Detectors for code nobody traced

Three of the detectors find a problem where nobody wrote a `trace{}` call,
which makes them the ones to read first on a project with none.

`uninstrumented_cpu` does not guess — it states a fact:

> thread `DefaultDispatch` was Running for 340 ms, zero slices

Which is exactly where to add `trace{}` and record again. The name is cut
at fifteen characters by Linux rather than by echolot: every worker of that
pool reaches the trace as `DefaultDispatch`, so write thread masks with the
truncation in mind.

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

`frame_jank` needs no instrumentation at all: SurfaceFlinger records every
frame's deadline and what it actually took, and says whose fault a miss was.
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

Names that carry the temporary prefix keep their digits. `names` folds
numbers into families — `worker-2` and `worker-5` are one pool — and leaves a
prefixed name whole: `AGENTTMP_fill_v4` and `AGENTTMP_fill_v6` are two markers
somebody wrote to tell two things apart. `names` takes the prefix from the
config or falls back to `AGENTTMP_`; `compare` takes it from the config only.
The Markers table folds nothing at all.

## Reading a report without opening the json

`report.json` is the contract, and it is not the thing to read whole.
`echolot report` prints one view of a report already on disk and writes
nothing but its own line in `.echolot/log/runs.jsonl`:

```bash
echolot report                                 # what fired: one line per detector
echolot report --detector monitor_contention   # its rows, longest first, five of them
echolot report -d main_thread_block --top 12   # more rows; -d repeats for several detectors
echolot report --window                        # the anchors, the main thread, the device
echolot report --markers                       # the Markers table, fifteen rows of it
echolot report -d repeated_work --json         # the same selection as json, places included
echolot report path/to/report.json --window    # another report, a round's own copy
```

Without a path it reads `.echolot/out/report.json` next to the config, or
under the working directory when there is none. A detector's view cuts the
location at 60 characters and the evidence at 100; `--wide` keeps both
whole. `--json` keeps everything and cuts only the rows, and only when
`--top` says how many.

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

It costs time only on a trace that has a launch to break down: the breakdown's
tables are computed when the module is included, which took 0.8 s on each of
those starts.

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

## Once you have two of them

Everything here reads one set of traces. The question that brought you —
*it was 3 s, now it is 7 s* — has a second half that needs two, and reading
two twenty-row tables against each other by eye is the work this tool exists
to remove. That is [`echolot compare`](compare.md).
