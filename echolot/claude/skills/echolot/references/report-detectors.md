# The detectors

`echolot explain <id>` is one of them on a screen, read off its own file: what
the trace has to hold for it, each threshold with its unit and the value this
project runs it with, and what each column of its rows means there — and then
its part of "What matters about individual ones" below, read off this page:
each detector's notes sit under a heading that is its id, which is how
`explain` finds them.

| id | what it catches | what to hook onto |
|---|---|---|
| `main_thread_block` | where the main thread spent its time | `location` — the slice name; `detail` is the thread's comm, always the main thread here, cut to 15 characters by the kernel |
| `app_init` | what ran after the Application was created, before the first Activity | an Initializer or a library's section by name; `(no section)` is the ContentProviders and `Application.onCreate` nobody traced |
| `gc_pressure` | collection cycles and allocation waits | frequent GC = many intermediate objects; `thread` names the row, `detail` says what a cycle freed and how large the heap grew before one, or which collection a wait was for. Blind to a cold start's first ~2 s |
| `monitor_contention` | monitor contention | the main thread's row is `main`; `threads` says how many threads a row adds up; `places` names both sides of the lock in the checkout; `detail` is the row's longest wait that names both methods, or else the owner's tid with its thread and what it and the waiter were in; then `owner:` and what the owner was doing over all of the row's waits, which says which fix applies |
| `binder_txn` | synchronous IPC into another process | `count` and `total_ms`, not just `max_ms`; `detail` names who answered — `system_server 94%` — and the AIDL method where the recording has the `aidl` category; on the main thread `phase` says where in the launch, and `before the app's code` is the platform's own start |
| `runnable_starvation` | thread ready but preempted | `state` names the row with the thread; the main thread's row is `main`, and on it `detail` says who held its core — the app's own threads point at the app, other processes and idle cores at the device |
| `uninstrumented_cpu` | threads burning CPU with no instrumentation | an address for adding `trace{}`, `main` for the main thread; `threads` above one is a pool's CPU together; in a sampled recording, the method after `ours:` in `detail`, at the file `code` names |
| `frame_jank` | frames that missed their deadline | `location` is why it missed, `detail` says whose fault |
| `main_thread_outlier` | one occurrence far longer than usual | `detail` carries the median it is measured against |
| `anr_risk` | a stretch where the main thread never got back to the message queue | `detail` splits it into on-CPU, waiting for a CPU, and neither |
| `anr` | an ANR the system recorded during the trace | `location` is the platform's own reason, `detail` the error id |
| `repeated_work` | the same named work entered from more than one caller | `detail` names the callers; a `near miss` row means the occurrences are too unlike to be one work |
| `main_thread_kernel_waits` | the main thread's uninterruptible sleeps that are not the disk, by the thread of the app that ended them | `location` is that thread, digits folded; `detail` says what it was inside when it woke the main thread (`waking inside dlopen: libfoo.so 61%`), then where the main thread was. Most often the process's memory-map lock; the trace cannot say which lock |
| `main_thread_waits` | the main thread's waits: sleeps it began inside its own work, by the thread of the app that let it go | `location` is that thread, its digits folded (`arch_disk_io_#`, `DefaultDispatch`); `detail` names the slice it waited longest in (`blocked in animation`), then `its thread:` and what that thread was doing while the main thread slept. A sleep with no slice open is the idle looper's, and the thread that ended it only posted the next message: it is no row, however often a WebView's thread does it. A report written before #926 counted it and says `idle` or `67% idle · 33% blocked in X`; only the blocked part was a wait. Lock waits, binder replies and `RenderThread` stay with their own detectors |
| `io_wait` | threads the kernel parked waiting for a block device | `thread` says `main` or `background`, and so does `detail`; there is no code to look at, only I/O to remove or move |
| `composables` | a Compose app's own composables, by the composition time they hold | `code` is the declaration the name carries; `detail` says what composed the calls: `in a first composition`, a view composed for the first time; `in a frame`, a recomposition after state changed; `during measure`, a lazy list composing its items; `elsewhere`, anything else. Silent without `runtime-tracing` in the app |

## What matters about individual ones

### `composables`

**`composables`** bends `self_ms`: it is the calls less the calls of the
app's own composables nested in them, with the library's composables under
them counted in. A composable's slice holds a millisecond of its own and the
rest is `material3` and `foundation` underneath, so plain self time ranks
nothing; counted this way the rows still add up without counting a moment
twice. `total_ms` is the calls end to end. A count is not a recomposition
count: seven calls can be seven icons, and `detail` says what composed them.
Only the app's own are rows, by the project's package or by a file the
checkout holds where the package says.

### `repeated_work`

**`repeated_work`** asks a question of shape rather than of size. Every other
detector gates on a magnitude — a sum, a maximum, a count — and a duplicate is
not a magnitude problem: the repeated half of one real finding was 270 ms among
a dozen other three-hundred-millisecond things. What made it a bug is that the
work had already been done.

Two things follow. It looks only at names no other detector claimed, so a row
here is never a restatement of one above it. And it depends on how markers are
named: a marker takes the name of the work it wraps, never of the place it was
put, or the two entries arrive under two names and there is nothing left to
compare.

### `app_init`

**`app_init`** is the stretch of a cold start between the Application and the
first Activity: every ContentProvider's `onCreate`, then `Application.onCreate`.
The platform traces neither, so what has a name here is what libraries write
themselves — each androidx.startup Initializer is a row of its own, and so is
a library's section such as `Firebase` — and `(no section)` is the rest.

An Initializer or a library's section is something to defer or make lazy, and
its name is the address. `(no section)` has no address yet: it is the app's
own providers and `Application.onCreate` plus any library that writes
nothing. Put `AGENTTMP_` markers around the app's own there, or `echolot mark`,
and record again; what stays unnamed after that is libraries, which the
manifest's `<provider>` entries list.

### `runnable_starvation`

**`runnable_starvation`** says who kept a thread off the CPU. A thread in
state `R` is ready to work and the scheduler will not let it in. One row per
thread and state: `state` (`R`, or `R+`, woken and not yet run) is part of the
row's name. For the main thread `detail` goes on:

    state R · its core held by the app 45% (DefaultDispatch most), other processes 48%, idle 7% · 0.3 other cores idle on average

The share is of the time the main thread waited, by what ran on the core it
was put on next. **Mostly the app** — the cause is in the app, however real
the device: too many of its threads at once, work that could start after the
first frame, a pool larger than the cores. The thread named is the one that
held the core longest; `window.crowding` says how many there were. **Mostly
other processes** — the device was busy with something else, and on an
emulator it nearly always is: make sure the run happened on a real device
with nothing else loading it before sending anyone to the code. **Mostly
idle**, or many other cores idle on average — capacity stood by and the
scheduler was slow to use it; neither doing less nor moving work fixes that.
Background threads' rows carry the state alone.

### `uninstrumented_cpu`

**`uninstrumented_cpu`** is the only one that finds a problem inside
uninstrumented code. `total_ms` is the thread's time on a CPU outside every
top-level slice, `covered_ms` its time inside one; the two add up to all of
its CPU. Another thread is a row only when slices cover less than half of its
CPU (`max_covered_pct`). The main thread is a row whenever its time outside
slices clears the bar, however much of the rest is marked: that time is the
window's, and a row on the main thread is the blind spot to take first.
`compare` judges the row on `total_ms`, so a loop nobody named that grew is a
row that grew. Without callstack samples, what it did is unknown: this
is where adding `AGENTTMP_` instrumentation and re-recording makes sense, and
there is no code behind the finding to look for.

A recording made with `runner.sampling` says what ran, at the end of `detail`:

    100.0% of CPU outside slices · 28 stacks: GzipSink.write 54%, ReadBarrier::Mark 21% · ours: Store.save 36%, cut 29%

Before `ours:` is what ran: the first named Kotlin or Java method on each
stack, from the top — the runtime's and native frames above it are passed
over; a stack with no method shows its first native name, or `[boot.oat]`
when nothing on it had a name. After `ours:` is the nearest frame of the
project's own code — the place that asked for the work — and `code` names
the file of the first one. Put the markers in that method rather than
spending a round to find it. A stack with nothing of the project on it is
`none` when it went down to its thread's start: a coroutine pool runs a task
without its caller, so mark where the project hands that work to the pool.
It is `cut` when the unwinder stopped in the framework first, which on a
phone is most stacks: what asked is unknown, and the row is an address for
markers again, with what ran as the hint. `no samples` or `none with a stack`
means the samples name nothing here. When the header warns that the app's
sampled methods read as minified, the names here are R8's, `a.b.c`, and no
file holds them: set `project.mapping` to the build's `mapping.txt` — R8
writes it to `build/outputs/mapping/<variant>/` — and analyze again before
acting on them. When it warns that they still read as minified after the
mapping, the mapping is from another build, and the names it did give may be
wrong. A few minified names with no warning are code that arrived that way,
from an SDK.

### `io_wait`

**`io_wait`** is about work the app asked the disk to do, and it is the only
detector whose finding has nothing to profile behind it. The thread is in
state `D`, uninterruptible sleep, and the kernel has flagged that sleep as
block I/O. It burns no CPU, holds no lock of yours, and has no slice around
it — which is why a cold start can spend hundreds of milliseconds here while
every other detector stays silent.

`detail: main` is the one to act on first: the main thread parked on the disk
is a frozen frame, and no amount of making the code faster helps, because the
code is not running. The fix is upstream of the code — read less, read it off
the main thread, or read it later.

It names the thread, not the kernel function. The blocking address needs
`/proc/kallsyms` to become a name and that file is unreadable on a production
build: on an SM-A515F the function was empty for all 6683 uninterruptible
sleeps in the trace while the disk flag was set on 6486 of them. If `detail`
carries a function name, the run was on a userdebug kernel and you have a
bonus, not the norm.

Two readings that come with it. If the same scenario is clean on the second
run, the first one was populating the page cache, and that is a first-launch
problem rather than a code one. And a thread in `D` that this detector does
*not* claim was parked for something other than the disk. For the main
thread, `main_thread_kernel_waits` says which of the app's threads let it out.

### `monitor_contention`

**`monitor_contention`** names who waited for a lock, and `places` both sides
of it. What decides the fix is what the owner was doing while the thread
waited, and the end of `detail` says it, over all of the row's waits:

    … blocking from … · owner: running 60%, waiting for a CPU 25%, in the kernel 10%, asleep 5%

**Running** — the critical section is long: make it shorter, or move work
out from under the lock, at the owner's place in `places`. **Waiting for a
CPU** — the owner held the lock in the queue for a core: a shorter section
barely helps, fewer threads at once does; read `runnable_starvation` and
`window.crowding` next. **In the kernel** or **asleep** — the owner waited for
something else while it held this lock: the disk, another lock, a reply. An
owner parked in `Unsafe.park` says the same from its frame; read `io_wait`,
`binder_txn` and this detector's row for the owner's own thread. `unknown` is
time the trace has no state of the owner for. A row with no `owner:` part
has no state of any owner: a recording without `sched_switch`, or an owner
that is no thread of the process.

### `main_thread_waits`

**`main_thread_waits`** names the thread of the app that let the main thread
go. What it was doing while the main thread slept follows the slice:

    blocked in loadConfig · its thread: running 80%, waiting for a CPU 10%, in the kernel 6%, asleep 4%

**Mostly running** — the main thread's way went through that thread's code:
make that code cheaper, or stop waiting for it; `uninstrumented_cpu` and the
thread's own slices say where its time went. **Mostly anything else** — that
thread was held up too, and its code is not the cost: what held it up is,
`runnable_starvation`, `io_wait` or `monitor_contention` for that thread. A
row that folds a pool (`its threads:`) reads each sleep by the worker that
ended it. One hop only: the thread that held up the waking thread is not
followed further.

### `main_thread_kernel_waits`

**`main_thread_kernel_waits`** is the main thread in `D` with `io_wait = 0` —
asleep in the kernel, and the kernel says it is not the disk — counted by the
thread of the app that woke it. No slice names that time: the budget calls it
blocked in the kernel, and `main_thread_block` counts it as self time of
whatever slice was open, where it reads as slow code.

    waking inside dlopen: libimage.so 100% · main thread inside inflate 100%

Read `detail` for what the waking thread was doing. Loading a native library,
starting threads (ART's `InitStack`), creating the EGL context, opening dex or
oat files: work that maps memory and takes the process's memory-map lock for
writing, which every page fault of the main thread then waits behind. On a
large app's cold launch that was ~106 ms of ~1.45 s, the coroutine pool's
library loading first. The fix is in time, not in the main thread's code:
move that work past the first frame, do less of it at once, reuse threads
instead of starting new ones. What the trace cannot say on a production
kernel is which lock it was — the GPU driver's and the file system's leave the
same signature — so say "most likely the memory-map lock", not more. A
`kernel:` part in `detail` is the function's name on a userdebug kernel, and
settles it.

### `frame_jank`

**`frame_jank`** is the only detector that answers "which frames stuttered"
rather than "where did the total go", and the only one that needs no
instrumentation: SurfaceFlinger records every frame's deadline and what it
actually took. Read it with these in mind.

*Its numbers are overruns.* `total_ms` is the time past the deadline summed
across those frames and `max_ms` is the worst single overrun — not durations,
which is what those columns mean everywhere else. The frame's own length is in
`detail` (`longest 86.2 ms`), because that is the number a benchmark's
percentiles are quoted in and the one to match a FrameTimingMetric run against.

*`detail` leads with whose deadline it was*, and it is the platform's verdict
rather than ours. `Self Jank` is the app — go and look. `Other Jank` is the
compositor or the display, and sending anyone into the app's code over it
wastes a round.

*`Buffer Stuffing` is latency, not jank.* The platform tags a frame that way
when the app queued it while the frame before was still waiting to be shown:
it reaches the screen a frame or more late and smoothly. Nothing stutters;
input feels a little behind. Its row keeps the count and has 0 in `total_ms`
and `max_ms`, ranks after every late frame, and its `detail` reads `latency,
not jank · N of M frames queued behind the one before · up to X ms late`. A
large count there is not a finding to hunt: it is usually a render thread or
GPU that takes most of each frame, and the frames the app missed are the rows
above it. A frame that also missed its deadline keeps the miss's tag:
`App Deadline Missed, Buffer Stuffing` is `Self Jank`, and its overrun
includes the wait in the queue (its `detail` ends by saying so), so it reads
larger than the same miss without the queue. A report written before echolot
counted the queue this way has the summed wait as the `Buffer Stuffing` row's
total, often the largest row of the report: read it the same way.

*Silence has two causes here.* No bad frames, or no frame timeline in the
trace at all — Android 11 and below, or a capture that did not ask for the
`android.surfaceflinger.frametimeline` data source. Those look identical in the
report. Before calling a scenario smooth, check that some other frame row
exists or that the trace came from `echolot collect`.

*It sees the app's windows and nothing else.* The timeline keeps the frames
that carry a Choreographer vsync id, the ones the RenderThread draws for the
app's windows. A map, a game or a video that a thread of the app draws into a
`SurfaceView` with Vulkan or GL has none there, however many it presented.
The report says so above the findings and under `--detector frame_jank`:
`frame_jank judged the 321 frames the frame timeline holds of the app. The app
also presented 1000 frames on SurfaceView[…]@0 with QueuePresentKHR on
Thread-18, and the timeline holds none of those`, with the numbers in
`window.frames.unjudged`. A silent `frame_jank` there says nothing of the map.
To judge it, read the intervals between those presents on that thread in the
trace, against the input that moved it: the app paces them itself and draws
only while something moves, so a long gap may be a pause between two gestures
rather than a late frame.

### `anr_risk`

**`anr_risk` measures a length of time, not a name.** The others ask how much
went into one slice name, or whether one occurrence was unusual. This asks how
long the main thread went without returning to the message queue — fifty
different pieces of work back to back freeze an app exactly as one long one
does, and nothing that groups by name can see that.

Its bar is the platform's five seconds and it is never calibrated. On a
cold-start scenario the window is a second or two, so it is silent by
construction; it is for the longer recording an ANR hunt collects. `detail`
splits the stretch three ways and that is the next step: time on a CPU points
at the code, time waiting for one at something else on the device, and the
remainder at a lock or a disk — read `monitor_contention` next when the
remainder dominates.

### `anr`

**`anr` is the only detector not clipped to the scenario window**, and its
`detail` says where the record fell relative to it. An ANR fires five seconds
after the event that could not be served, and a cold start's window closes at
the first frame — clipped, it would be absent from nearly every trace that
contains one. Its `location` is the platform's own reason (`Input dispatching
timed out …`), which no Crashlytics export carries, and `detail` holds the
error id: the same string the device's `dumpsys dropbox` record has, so a
trace and a report can be matched by hand.

### `main_thread_outlier`

**`main_thread_outlier`** is the pair to `main_thread_block`, and reading one
as the other wastes a round. `main_thread_block` gates on the **sum** for a
name and answers "where did the main thread's time go". This one gates on a
**single occurrence** against the median for that same name, and answers "which
one was out of line". A name can appear in both, saying different things.

Take it to the code differently, too. A `main_thread_block` row means the work
is expensive every time it runs, and the fix is in that work. A
`main_thread_outlier` row means the work is usually fine and once was not, so
the cause is the state it hit that once — a cold cache, a lock, a first-run
path, an allocation that stalled. `detail` gives the median and how many
occurrences it came from, which is the number to compare a benchmark's
percentiles against, and where most of the worst occurrence went:
`mostly in performStart:…PageActivity › WebViewChromiumAwInit.startChromiumLockedSync
(57.2 ms)`, each slice holding more than half of the one before it. The last is
the place to start reading; its pieces may each be too small for a
`main_thread_block` row.

It is also the row a merge across repeats does **not** smooth away. Its rows
only exist in the runs where something was out of line, so `runs: 1/10` means
"once in ten" rather than "we lost it in a median" — the opposite of what
`max_ms` does on a row present in every run.

### `gc_pressure`

**`gc_pressure`** also catches the other side: `waitWhileAllocatingLocked` on an
application thread means an allocation stalled waiting for the collector. GC
itself is the symptom; the cause is the number of intermediate objects.
On a cold start it is blind for the first ~2 s: ART holds off collection
after the fork from zygote, so allocations there run no cycle, and silence or
`steady` from this detector says nothing about them. Read the heap's size
instead — `the heap up to … MB` in a collection row's `detail`, or the
`Heap size (KB)` counter — and put planted allocation load after the first
two seconds.

### `binder_txn`

**`binder_txn`** fires both on one long transaction and on the sum of short
ones. On a live startup there were 76 transactions of about 1 ms — 66 ms
together, 8.5% of the cold start, and not one of them stood out alone.
Its `detail` says where the calls went, by share of the row's time: `main
thread · to system_server 94%, surfaceflinger 2%`. A process alone means the
recording had no `aidl` category; with it, the method follows the process
(`system_server IPackageManager.getPackageInfo 89%`).

Who answered does not say whose the call was. The main thread's row says
where in the launch its calls were made, in `phase`, by share: `before the
app's code 25%, Application 31%, activity 22%, frames 18%, other 4%`.
`before the app's code` is the platform's own start — `attachApplication`
inside `ActivityThreadMain`, and `bindApplication` before `makeApplication`
— and nothing in the app moves it: do not spend markers looking for it in
the app's code. `Application` is the providers and `Application.onCreate`,
`activity` the activity's start, `frames` `Choreographer#doFrame`; those
hold the app's code and the platform's together, and the share says where
to look. A window that starts at `bindApplication` has no
`ActivityThreadMain` in it, and says less.
