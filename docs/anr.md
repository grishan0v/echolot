# ANRs

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/anr.md)

A trace answers "where did the time go". An ANR is a different question: the
app stopped answering, the system gave up waiting, and the report arrived from
a phone you do not have. echolot reads that report, points its frames at files
in your checkout, and — when you can record the freeze — measures it.

## The report, and where it comes from

```bash
echolot anr report.txt
```

It reads the file and prints. It opens no investigation, and the one thing it
writes is its own line in `.echolot/log/runs.jsonl` under the working
directory — the run log every command keeps, which `ECHOLOT_NO_RECORD=1`
switches off.

Three places produce the same artifact, and the frames in them are identical:

| source | how to get it | what it adds |
|---|---|---|
| Crashlytics | export from the issue page | version and build, unminified frames |
| Play Console | an ANR cluster in Android Vitals | nothing extra, and **no lock ownership** — see below |
| the device itself | `adb shell dumpsys dropbox --print data_app_anr` | **the reason** — `Subject: Input dispatching timed out …` |

Only the device's own record carries why the system fired. Checked across ten
Crashlytics exports: the header is the same six fields every time, and none of
them is a reason.

The thread signature differs per source — Crashlytics writes
`main (blocked):tid=1 systid=8413`, ART writes `"main" prio=5 tid=1 Blocked` —
so the reader decides which it is from how the file announces its threads.
Zero threads is never reported as a clean bill: it exits 2, with the reason, on
a file that is not there, on one in which nothing announces a thread the way
any of the three sources does, and on one that reads as a source and yields no
threads.

> [!IMPORTANT]
> **Play Console strips who holds a monitor.** Its export says a thread is
> blocked and never says by whom — `held by thread N` is not in it, and neither
> is a header, a version or a reason. Most of the finding survives anyway: a
> blocked thread is standing in the method it could not enter, so several of
> them standing in methods of the same class are queued on the same monitor,
> and the report names that class and says the holder is not in the file. If
> you can get the same freeze out of Crashlytics or off a device, that export
> is worth more.

### Making one on purpose

Useful for checking the reader, and for seeing the whole path work before a
real report arrives. Freeze a debuggable app's main thread, tap it, then:

```bash
adb shell 'dumpsys dropbox --print data_app_anr' > record.txt
echolot anr record.txt
```

The drop box keeps every app's ANRs for days, and `--print` gives all of them,
oldest first. Only the first entry in the file is read, and the report says
when the file held more. `dumpsys dropbox` keeps only the entries whose time
contains a word passed after the tag, which narrows it to the freeze you made:

```bash
adb shell 'dumpsys dropbox --print data_app_anr 21:22' > record.txt
```

An entry over the drop box's size limit is cut and ends in `[[TRUNCATED]]`,
and a dump of hundreds of threads reaches it. The report then says, under what
it does not say, how many of the threads ART listed are missing, and that a
holder named as not in the dump may be one of them; `--json` has it as `cut`.

## What it prints

**The lock chain first**, because it is the strongest thing in the file. A
blocked thread carries the monitor it wants and the tid holding it, so the
chain resolves inside the report — and a monitor held by a thread that is
itself parked on a blocking call, with the main thread queued behind it, is the
mechanism rather than a coincidence. That kind of finding is fixable without
recording anything.

A holder that is itself blocked is a link, not a cause: it is queued exactly
like the threads behind it. The chain is walked to the bottom, and the stack
shown is the one thread standing on something of its own — naming the direct
holder as the answer names a victim. A walk that comes back to a thread it
passed is a deadlock, and a deadlock has no such thread: the report shows each
thread of the loop with its stack and names no root, and says whether the
holder waits on one of the waiters or the holders block each other while the
waiters queue behind them. `--json` lists the loop as `deadlock` and the
first case as `cycle`.

The holder is the thread the note names (`held by thread N`). ART adds that
tail only when it can read the owner as it dumps; without it, or when the
named tid is not in the dump, the holder is the thread that printed
`- locked` for the same address.

R8 leaves the monitor's class obfuscated while the frames come back
unminified, and no mapping file is needed to bridge them: a blocked thread is
standing in the method it could not enter, so its own top frame names the class
whose monitor it wants. Where the two names differ, the raw one stays in the
output beside the resolved one. Only a name R8 wrote is resolved — a few
lower-case letters where a class name would be. A monitor that kept its name
stays as printed: `synchronized(cache)` inside `Repository.get` locks a
`Cache`, not a `Repository`. Given the build's mapping, a monitor's class is
named back directly ([A minified build](#a-minified-build)).

When the file carries no lock note at all — Play Console strips them, and so
do some Crashlytics exports — no chain can be read off it, and the report says
so under what it does not say rather than printing nothing: an empty chain
list is the file's limit, not a fact about the freeze. `--json` carries it as
`lock_notes`. What such a file still yields is the queue: blocked threads
standing in the same class are listed as waiting on one monitor, marked
`inferred` in `--json`, with the holder named as absent rather than guessed.
The device's own record is not asked to find notes it lacks — ART writes one
for every thread that waits on a monitor, so a record without any says none
did.

**Then the main thread**, and the case worth knowing about before you read one:
`nativePollOnce` means it was **idle** when the dump was taken. Whatever caused
the freeze had already let go, or never ran on that thread at all. Reading the
top frame as the culprit sends an investigation into Android's message queue.
The main thread is the one named `main`, or, when the file calls it by the
process's name — as a stack written by a native unwinder does — the one running
`ActivityThread.main`; the report keeps that name and says it is the main
thread. In that format the looper wait can show only as native frames
(`Looper::pollOnce`, ART's JNI entry and exit) above `MessageQueue.next` or
`nextLegacy`, and that reads as idle too; the unwinder's own frames on top
(`unwindstack::SignalHandler` and what it calls) decide nothing. A file with no
main thread at all says so under what it does not say.

**When every frame belongs to the platform or a library**, it says that in
those words. One of the ten sample reports had not a single frame outside them
in any of its fifty-three threads — every busy one was inside `androidx.work`.
That is a finding, and it reads differently from a report with thin sections.
The library frames nearest to the app are then listed as leads: the platform is
a dead end, and a library the app drives points at the app's own setup of it.
A thread standing inside a library is one of them, and its stack shows the
frame where the library was entered: `Gson.fromJson` under the adapter's
`read`. A thread standing in a binder call into the platform keeps, under
`BinderProxy.transactNative`, the interface proxy that names the call and the
frame that made it: `IJobCallback$Stub$Proxy.acknowledgeStopMessage` under
`JobServiceEngine$JobHandler.ackStopMessage`. Some platform frames are leads
in their own right, printed after the verdict (`--json`: `leads`): that one says
the main thread was telling the system a JobScheduler job had stopped, and the
app's WorkManager or JobService setup is where to read next.

**Then the threads that were doing something.** A dump holds fifty threads on a
quiet app and three hundred on a busy one, nearly all asleep, and striking out
the idle ones is most of the work: loopers waiting in `nativePollOnce`, pools
waiting on a queue for work, coroutine workers parked, runtime daemons and the
runtime's own housekeeping, binder pools, a `Timer`, OkHttp's `TaskRunner`,
okio's watchdog waiting for a timeout and the GMS dynamite loop idling,
threads waiting on a descriptor in `epoll`, `ppoll` or a `Selector`, and
threads asleep — `Thread.sleep`, a futex, a pthread condition. A blocked
thread is never struck out, whatever its stack says, and neither is a
runnable one, except the `Signal Catcher` writing the dump itself
(`SignalCatcher::HandleSigQuit`). Nor is a thread with work above the idle
frame: a frame of the app's, `Binder.execTransact` serving a call, or a
network call under way — OkHttp's `RealCall` or `ConnectPlan`, a TLS
handshake, `Socket.connect` — such as a `Timer` running the app's task, the
finalizer daemon in an app's `finalize()`, a binder thread inside a
provider's `query`, or a worker in a TLS handshake reading the socket in
`ppoll`. OkHttp's HTTP/2 reader waits in the same read for as long as a
connection is open, and is still struck out. A stack the source cut short
(`... frames omitted ...`) is never struck out on its native top alone, since
the app's frames may be below the cut; it is listed with "stack cut after N
frames", and `--json` has the count as `cut`. The main thread is
idle only at its looper. A thread queued on a lock is listed once, under its
chain. The rest are listed by the frame nearest the app — its own where there
is one, a library's where there is not — with the top frame beside it. The top
is almost always `BinderProxy.transactNative` or `Unsafe.park`, true and
useless alone; `SystemJobScheduler.cancel` four frames down is what the thread
was doing. Threads running the app's own code come first, and what the
vocabulary does not cover sinks to the bottom rather than being struck out on a
guess. The markdown lists twelve and counts the rest; `--json` carries them
all.

**Then where those frames are in the checkout** — see the next section.

**Then what else the device was doing**, when the source carries a CPU table.
The device's own record does. A machine where `system_server` and
`surfaceflinger` were eating most of two cores is a different story from an app
that blocked itself, and the table is the only thing in a report that can tell
them apart. The markdown shows the six busiest rows; `--json` has the table.

**Then what it cannot say.** No reason, when the source has none — and the
sentence names the kind of file that was read and points at the one record that
carries a reason. No durations at all — a dump is one moment, and how long
anything took comes from a trace. Lines it could not read, counted.

## From a frame to a file

```bash
echolot anr report.txt --root .
```

`--root` is the working directory unless another is named, so run from the
checkout the command places frames without being asked; a directory with no
sources in it costs the report nothing.

A java frame carries its own source location: the compiler wrote the file name
and the line into it. So the repository is asked to confirm a path rather than
to find one, which is a shorter road than the one `domains` takes from a slice
name.

Two modules holding a `Mapper.kt` is ordinary, so the package from the frame's
own symbol decides between them — the directory has to end in it, so a frame of
`com.example.a` is not placed in `com/example/app`. One file of that name in
the whole checkout is not a guess whatever the package says. Several
candidates and nothing to choose by is the only case that prints a caveat,
and it names the others; that includes several whose directories all end in
the package, such as one class in `src/debug` and `src/release`.
`mark --from-anr` marks only frames placed for certain and names the rest.

A deobfuscated stack can name a Kotlin class's file `Guard.java`. When the
checkout has no file of that name, the frame is placed in `Guard.kt` of its
package, in the file a top-level function's `GuardKt` comes from, or in the
`.kt` file of its package that declares the class (`FooImpl` in `Foo.kt`). Such
a frame is placed for certain, and the placement says which file the frame
named (`--json`: `named`). A frame that is not placed even so is listed under
what the report does not say by its cause: the file it names and the class
its package does not declare.

The checkout also decides which frames are the project's at all. Without one
that is a list — the platform's packages and those of the libraries every app
carries — and a library missing from it reads as the app's own. With one, the
packages its sources declare are the project's, along with the report's own
package, and every other package is a library's whether the list has heard of
it or not: a frame of dagger, koin or sentry is neither placed nor counted as
code this checkout is missing. The list still vetoes, so a test stub declaring
`package android.util` does not make the platform yours. A report R8 renamed
into packages of its own (`a.b.c(SourceFile:12)`) is in nothing the checkout
declares until the build's mapping names it back, as the next section says.

> [!IMPORTANT]
> **Check out the build the report came from.** Line numbers are the first
> thing to go stale. On a report from 26.15.1 read against a working tree,
> 103 frames of 116 landed on an import, on a blank line, on a constant, in a
> different function, or past the end of the file. `anr` checks every line it
> places against the function the frame names, and marks one that is not in
> it: "line 138 is now inside `clearAll`, the frame names `await`" (`--json`:
> `astray`). `mark --from-anr` refuses each such frame with its own reason.
> When most of the frames land outside their function, both add one sentence
> saying the checkout is probably not the build that froze, with the build's
> version (`--json` of `anr`: `code.not_the_build`). A line inside another
> function counts towards that only when the build's mapping did not have to
> name the frames back: R8 moves lines on the build that froze too. A
> Kotlin `inline fun` does it on any build, so one such frame proves nothing.

### A minified build

```bash
echolot anr record.txt --mapping app/build/outputs/mapping/release/mapping.txt
```

A minified build's frames read as R8 named them, `a.b.run(SourceFile:3)`.
The device's own record is always like that, and so is an export from a
console that was never given the build's mapping. Such a frame is in no file
of the checkout and in no package it declares, so the report finds nothing
of the project in it and says every frame belongs to the platform or a
library. `--mapping` names the build's `mapping.txt`. Without the flag,
`project.mapping` of the `echolot.yml` under `--root` is used, the key
`analyze` reads; `mark --from-anr` reads it from the config it reads.

The frames are named back before anything else is read, the way R8's own
`retrace` does it: by the line, out through the methods inlined there. So
one frame can become several: `Store.read(Store.java:17)`, then the
`MainActivity.freeze(MainActivity.java:21)` it was inlined into. The number
in a frame is read whatever the frame calls its file: `SourceFile`,
`r8-map-id-…`, or `unavailable`. ART prints `unavailable` and an
instruction's offset for a build R8 left without a line table, and that
build's mapping is written in offsets, so such a frame is named exactly too
([what each build writes](collecting.md#a-minified-build)). A monitor's
class in a lock note is named back as well. A frame that arrived unminified,
from Crashlytics or Play Console with the mapping uploaded, is not R8's and
stays as it was.

The header says how many frames the mapping named back. A frame it has no
place for, with its line in no range of its method or a method its class
does not have, keeps R8's method name and loses the line. The right mapping
leaves no such frame and another build's leaves many, so from a tenth of the
frames up the header warns. A record whose frames name their files after a
mapping, `r8-map-id-<id>`, says which one it needs: when no frame carries
this mapping's `pg_map_id`, the header says so and no frame is named back.
A frame of a class R8 kept, which reads as the source does, is named back
on a line in one of R8's ranges only beside a frame that is unmistakably
R8's, so a record of a build that kept its names keeps its lines. Without a
mapping, the frames that still read as R8 named them are listed under what
the report does not say, with the flag that names them. `--json` has both:
`retrace` with `frames`, `outside` and, for another build's mapping,
`map_id`; and `minified`.

## Markers from a stack

```bash
echolot mark --from-anr report.txt        # the plan
echolot mark --from-anr report.txt --apply
echolot mark --remove                     # the same as always
```

[`mark`](mark.md) normally proposes where instrumentation usually belongs on a
project that has none. A stack is not a guess: it names the methods that were
on the thread when the system gave up. Everything after the plan is the same
code and the same tag.

Most proposals will not be applicable, and the reasons are the useful part:

- **the line falls inside another function than the frame names** — another
  build, an inlined function, or the compiler moved it, and bracketing where
  the line landed would put a marker named after one function around the body
  of another. The name is read the
  way the compiler wrote it: a member of a companion, a nested class or an
  `object` is that member, and a lambda — a class of its own,
  `load$lambda$0`, or javac's `lambda$load$0` — is the function it was written
  in;
- **an expression body** — `fun save(v: String) = store.put(v)` is a function
  with no block to bracket: rewrite it as a block, or mark the caller. It is
  not counted as a line from another build;
- **a `return` in the body** — a begin/end pair leaks the section on the early
  path;
- **the body is on one line** — `remove` could not take it out without taking
  the code with it;
- **code after the `{`, or before the `}`, on the brace's own line** — the
  new line could only go in by splitting a line of the project's, and
  `remove` deletes lines; it cannot join one back together. Move the code to
  a line of its own, or mark by hand;
- **a frame outside `instrumentation.allowed`** — shown as a row and refused,
  not left out: it is still on the stack, and the frame under it may be the
  allowed caller to mark instead.

A method on the stack twice is said to call itself, so its marker opens inside
itself. Not when one of the two frames is on a line in no function, such as
the class declaration: that is the bridge the compiler adds for a generic
interface (`create(Context): Object` for `Initializer<Unit>`), and it is
passed over.

## Measuring the freeze

Two detectors work on the trace side. See [Detectors](detectors.md) for how
they break the shared rules.

**`anr_risk`** measures a stretch where the main thread never got back to the
message queue. Its bar is the platform's five seconds and is never calibrated.
On a cold-start scenario the window is a second or two, so it is silent by
construction — it is for the longer recording an ANR hunt collects. `detail`
splits the stretch into time on a CPU, time waiting for one, and neither; the
third pointing at a lock or a disk.

**`anr`** reports what the system recorded, if a freeze fired while the trace
was running. It is the only detector not clipped to the scenario window, and it
carries the platform's error id — the same string the device's drop box record
has, so a trace and a report match by hand.

To catch either, record for long enough: the seconds until the freeze starts,
plus the five an unanswered input event is given before the system declares an
ANR, plus a few more while it writes its record down. A freeze that starts
eight seconds into the scenario needs about sixteen, which is past the default
`duration_ms: 12000`. `runner.duration_ms` sets the recording in `launch` and
`command` modes only; in `gradle` mode the macrobenchmark records, and decides
for how long.

## What this does not do

It does not reproduce the freeze. ANRs from the field live on particular
devices, particular data and races, and the report rarely says which action led
to the block. The honest end of the offline half is a list of places and the
build to check out; the scenario is yours.
