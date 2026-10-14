# How ART names things

These facts were taken from a live Android 14 trace (emulator, ART, concurrent
copying). Names change between Android versions — when in doubt, look at the
real set:

```bash
echolot names <trace> --process '<package>*'
echolot names <trace> --grep 'contention'     # one family of names, whole
echolot names <trace> --json                  # the inventory as json
```

A pipe gets every name whole; cells are cut only on a terminal, and
`--wide` keeps them there too. Do not grep the table: `--grep` matches the
family name before anything is cut.

The command collapses names that differ only by numbers, sorts them into
sections, and shows which detector masks land on them. The **"Missed by the
masks"** section is a ready-made list of things that look like a problem but
are covered by nobody.

## Garbage collection

Collection cycles live on `HeapTaskDaemon`, at **depth 0**, and all of them end
in `GC`:

```
Background young concurrent copying GC
Background concurrent copying GC
Explicit concurrent copying GC          ← from System.gc()
```

Hence the `*GC` suffix mask.

Inside a cycle (**depth 1**) sit the phases: `CopyingPhase`, `MarkingPhase`,
`ReclaimPhase`, `InitializePhase`, `FlipThreadRoots`, `ScanCardsForSpace`.
**They must not be counted separately** — that is the same time as the parent's.
On a live trace `CopyingPhase` reported 295 ms against 282 ms for the whole
cycle.

Traps on the same thread that are NOT garbage collection:
`TrimIndirectReferenceTables`, `TrimSpaces`, `TrimMaps`,
`LocalReferenceTable::Trim` (305 of them in one run), `Thread::Init`,
`Delete thread pool`. That is why the detector has no thread-name mask.

On `Jit thread pool` live `GarbageCollectCache`, `Code cache collection` and
`DoCollection` — that is JIT cache collection, a different phenomenon with
nothing to do with application allocations.

The other side of GC shows up on application threads:
`waitWhileAllocatingLocked` — an allocation stalled waiting for the collector.

## Locks

ART writes application-level contention in exactly two shapes:

```
Lock contention on a monitor lock (owner tid: 13533)
monitor contention with owner main (13533) at void java.lang.Object.wait(…)
    waiters=0 blocking from <class>.<method>(…)
```

The second shape is the more valuable one: it carries the owner, the method
being waited on, and the call site. Under minification the names are
obfuscated, but the `file:line` parts survive. `analyze` reads both frames
and places them in the checkout — the row's `places` and its `code` column —
so a contention row is an address, not a string to grep for. When ART did not
know the owner's method it leaves out ` at <frame>` — about one slice in fifty
on real cold starts — and the row is placed by the waiter alone.

Everything else shaped `Lock contention on <something> lock` is a
**runtime-internal lock** with no application code behind it:

```
Lock contention on ClassLinker classes lock     ← 129 of them on a cold start
Lock contention on runtime shutdown lock
Lock contention on InternTable lock
Lock contention on linear alloc
Lock contention on thread list lock
Lock contention on thread suspend count lock
Lock contention on GC barrier lock
```

The detector masks are narrowed so as not to drag those in.

**The owner's tid sits inside the name.** That is why the detector groups by
thread rather than by slice name: otherwise one finding shatters into a dozen
rows, one per owner.

## Binder

Trace Processor emits four kinds of slice:

```
binder transaction          ← synchronous, the sender blocks
binder reply                ← the server side's reply
binder transaction async    ← asynchronous, the sender does NOT block
binder async rcv
```

The detector takes only the synchronous ones. `skip_glob` excludes the async
one: it is not the cost of synchronous IPC, however similar the name looks.

## Thread names

Linux keeps **15 characters** of a thread's name (`comm`), and which 15
depends on the thread. A pool thread loses its end: the trace holds not
`DefaultDispatcher-worker-1` but `DefaultDispatch`. The main thread is named
after the process and loses its front: `com.example.myapp` arrives as
`m.example.myapp`, while `com.example.app` is exactly fifteen characters and
arrives whole. Write thread masks with the truncation in mind.

The consequence worth holding on to: a pool whose workers differ only past
the fifteenth character arrives as **one** name, so every
`DefaultDispatcher-worker-N` is a single `DefaultDispatch` row. One whose
digits fall inside the cut stays several — `arch_disk_io_0` … `arch_disk_io_3`
are four rows and one pool.

## System slices of a cold start

These appear by themselves, need no instrumentation, and have no counterpart in
application code:

```
bindApplication          Application creation
activityStart            activity launch
activityResume
performCreate:<Activity>
Choreographer#doFrame N  a frame; N is the vsync number, different every run
traversal                measure + layout + draw
OpenDexFilesFromOat      dex loading
AppImage:Loading         app image loading
createClassloaderNamespace
```

`Choreographer#doFrame` carries a number inside its name, so a scenario anchor
needs a wildcard: `Choreographer#doFrame*`.

## The app's own async sections

```
collection_load          (async)    ← Trace.beginAsyncSection, on no thread
screen_shown  (async)
```

`Trace.beginAsyncSection` and `endAsyncSection` are what an app writes for
work that starts on one thread and ends on another, and what most hand-rolled
tracing wrappers write for everything. The section lands on a track owned by
the process rather than by a thread, and `names` and `probe` list it with
`(async)` where a thread name would be. On one real project every named
marker was this kind.

What reads them: the scenario anchors — `scenario.end` is usually one — this
inventory, and the Markers table in every report, for a name `domains` lists
or one that carries the temporary prefix. What does not: every detector. They
read thread slices, and a section that belongs to no thread has no self time
on anyone's thread and no place in anyone's coverage. A dash in the mask
column next to an `(async)` row is exact. Do not go changing the app's
tracing to synchronous sections so a detector can see them. To have one
measured, list its name under `domains`; to have a detector see the work,
plant an `AGENTTMP_` marker on the thread that does it.

## Waiting on the disk

There are no names here to match on, which is the point: a thread waiting for
a block device has no slice, no CPU time and nothing written down. It shows up
as thread state `D` — uninterruptible sleep — with the kernel's disk flag set,
and `io_wait` is the only detector that reads it.

What to make of what it reports:

- **the main thread in `io_wait`** is a frozen frame, and making the code
  faster does not help, because the code is not running. The fix is to read
  less, read it off the main thread, or read it later;
- **clean on the second run** means the first was populating the page cache.
  That is a first-launch problem rather than a code one, and comparing a cold
  first launch against a warm one says nothing about either;
- **a thread in `D` that `io_wait` does not claim** was parked for some other
  reason. Look for threads holding a memory lock — `Jit thread pool` and
  work that maps files are the usual pair;
- **the kernel function is normally absent.** Turning the blocking address
  into a name needs `/proc/kallsyms`, which a production build does not let
  anyone read. Measured on an SM-A515F running Android 13: empty for all 6683
  uninterruptible sleeps in a trace, while the disk flag was set on 6486 of
  them. A function name in `detail` means a userdebug kernel.

## Compose

A Compose app writes its phases into the trace with nothing set up, and they
reach a report like any slice: on a large Compose app's cold start,
`Compose:recompose ×55, 224 ms` in `main_thread_block`, and one
`Recomposer:recompose` of 314 ms in `main_thread_outlier`. Where each one sits
says what it is; the parents below are read off real cold starts.

| slice | sits under | what it is |
|---|---|---|
| `Recomposer:recompose` | `animation`, the frame's first stage | the frame's recomposition, every composition whose state changed |
| `Compose:recompose` | `Recomposer:recompose`, `AndroidOwner:onMeasure`, or nothing | one composition recomposed: in a frame; during measure, where a lazy list or another subcomposing layout composes its items; or the first composition, from `setContent` |
| `Compose:applyChanges` | the same three | that composition's result applied to the UI tree |
| `Compose:onRemembered` | the same three | `remember` observers told they entered the composition |
| `AndroidOwner:onMeasure` | `measure` | a `ComposeView` measuring its layout nodes |
| `AndroidOwner:onLayout` | `layout` | placing them |
| `AndroidOwner:draw` | `Record View#draw()` | drawing them; `AndroidOwner:measureAndLayout` there is a measure left for the draw |
| `TextStringSimpleNode::measure` | `AndroidOwner:onMeasure` | a text measured; `TextLayout:initLayout` under it builds its layout |
| `getAllUncoveredSemanticsNodesToIntObjectMap` | | a walk of the semantics tree. It can be expensive, and it is a leaf: its self time is its total, spent right there |

**The self time of `Compose:recompose` is the composable functions
running.** None of them has a slice of its own, so their time is the pass's:
195 of the 224 ms above. The row says composition is expensive, and nothing
in the trace says which composable.

**Composables get names from the app and from the recording, both.** The app
adds `androidx.compose.runtime:runtime-tracing`. The recording switches the
app's Perfetto SDK on, which a macrobenchmark does with
`-Pandroid.testInstrumentationRunnerArguments.androidx.benchmark.perfettoSdkTracing.enable=true`
in `runner.gradle_args`; Macrobenchmark 1.5 recorded none without it.
In launch and command modes `runner.compose_names: true` switches it on for
the set and off after it. Each call is then
a slice on the main thread, `fully.qualified.Name (File.kt:line)`, nested
under `Compose:recompose`. The file and line are the composable's
declaration, so open the file there.

That moves the time out of the phases' rows. On a cold start with names,
`Compose:recompose` kept 5 ms of self time out of 144, and its row left
`main_thread_block`. No composable took its place there: summed by name,
none had the 16 ms of self time a `main_thread_block` row needs. The
library's `NavHost` came nearest with 8, and the app's own had about a
millisecond each, their time being in the library's composables under them.
`composables` is where they are: the app's own, by their calls with the
library's under them counted in, each at the declaration its name carries.
`echolot names <trace> --grep '\.kt:'` lists every composable, the
library's too, with how many calls each and their total time; it folds the
line into `#`, as it does every number in a name.
