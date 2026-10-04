# ANRs

[← Docs index](README.md) · [README](../README.md)

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
holder as the answer names a victim.

R8 leaves the monitor's class obfuscated while the frames come back
unminified, and no mapping file is needed to bridge them: a blocked thread is
standing in the method it could not enter, so its own top frame names the class
whose monitor it wants. Where the two names differ, the raw one stays in the
output beside the resolved one.

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

**When every frame belongs to the platform or a library**, it says that in
those words. One of the ten sample reports had not a single frame outside them
in any of its fifty-three threads — every busy one was inside `androidx.work`.
That is a finding, and it reads differently from a report with thin sections.
The library frames nearest to the app are then listed as leads: the platform is
a dead end, and a library the app drives points at the app's own setup of it.
A thread standing inside a library is one of them, and its stack shows the
frame where the library was entered: `Gson.fromJson` under the adapter's
`read`.

**Then the threads that were doing something.** A dump holds fifty threads on a
quiet app and three hundred on a busy one, nearly all asleep, and striking out
the idle ones is most of the work: loopers waiting in `nativePollOnce`, pools
waiting on a queue for work, coroutine workers parked, runtime daemons and the
runtime's own housekeeping, binder pools, a `Timer`, OkHttp's `TaskRunner` and
the GMS dynamite loop idling, threads waiting on a descriptor in `epoll`,
`ppoll` or a `Selector`, and threads asleep — `Thread.sleep`, a futex, a
pthread condition. A blocked thread is never struck out, whatever its stack
says, and a thread queued on a lock is listed once, under its chain.
The rest are listed by the frame nearest the app — its own where there
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
candidates and nothing to choose by is the only case that prints a caveat.

The checkout also decides which frames are the project's at all. Without one
that is a list — the platform's packages and those of the libraries every app
carries — and a library missing from it reads as the app's own. With one, the
packages its sources declare are the project's, along with the report's own
package, and every other package is a library's whether the list has heard of
it or not: a frame of dagger, koin or sentry is neither placed nor counted as
code this checkout is missing. The list still vetoes, so a test stub declaring
`package android.util` does not make the platform yours. A report R8 renamed
into packages of its own (`a.b.c(SourceFile:12)`) is in nothing the checkout
declares; retrace it first.

> [!IMPORTANT]
> **Check out the build the report came from.** Line numbers are the first
> thing to go stale. On a report from 26.15.1 read against a working tree,
> 103 frames of 116 landed on an import, on a blank line, on a constant, in a
> different function, or past the end of the file. `mark --from-anr` does two
> things about that: it refuses each such frame with its own reason, and when
> most of the frames land somewhere it does not recognise, it adds one sentence
> saying the checkout is probably not the build that froze.

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

- **the line falls inside another function than the frame names** — the
  compiler moved it, and bracketing where the line landed would put a marker
  named after one function around the body of another. The name is read the
  way the compiler wrote it: a member of a companion, a nested class or an
  `object` is that member, and a lambda — a class of its own,
  `load$lambda$0`, or javac's `lambda$load$0` — is the function it was written
  in;
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
