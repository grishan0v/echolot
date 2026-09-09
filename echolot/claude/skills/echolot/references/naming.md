# How ART names things

These facts were taken from a live Android 14 trace (emulator, ART, concurrent
copying). Names change between Android versions — when in doubt, look at the
real set:

```bash
echolot names <trace> --process '<package>*'
```

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
obfuscated, but the `file:line` parts survive.

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

Linux truncates `comm` to **15 characters**. So the trace holds not
`com.example.app` but `m.example.app`, not `DefaultDispatcher-worker-1` but
`DefaultDispatch`. Write thread masks with the truncation in mind.

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
  reason. Look for threads holding a memory lock — `jit-thread-pool` and
  work that maps files are the usual pair;
- **the kernel function is normally absent.** Turning the blocking address
  into a name needs `/proc/kallsyms`, which a production build does not let
  anyone read. Measured on an SM-A515F running Android 13: empty for all 6683
  uninterruptible sleeps in a trace, while the disk flag was set on 6486 of
  them. A function name in `detail` means a userdebug kernel.

## Compose

```
Compose:recompose
Recomposer:recompose
AndroidOwner:onMeasure / onTouch / draw
TextLayout:initLayout
getAllUncoveredSemanticsNodesToIntObjectMap   ← semantics tree walk
```

The last one can be expensive and is a leaf: its self time equals its total,
meaning the time is spent right there.
