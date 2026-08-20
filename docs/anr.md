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

Three places produce the same artifact, and the frames in them are identical:

| source | how to get it | what it adds |
|---|---|---|
| Crashlytics | export from the issue page | version and build, unminified frames |
| Play Console | an ANR cluster in Android Vitals | ART's own format, so the same reader — see below |
| the device itself | `adb shell dumpsys dropbox --print data_app_anr` | **the reason** — `Subject: Input dispatching timed out …` |

Only the device's own record carries why the system fired. Checked across ten
Crashlytics exports: the header is the same six fields every time, and none of
them is a reason.

The thread signature differs per source — Crashlytics writes
`main (blocked):tid=1 systid=8413`, ART writes `"main" prio=5 tid=1 Blocked` —
so the reader decides which it is from how the file announces its threads. A
file in a form it does not know exits with that as the reason rather than
reporting zero threads as a clean bill.

> [!NOTE]
> The Crashlytics reader was written against ten live exports and the ART one
> against a record made on purpose on a phone. **Play Console has not been
> checked against a real export**: it shows ART's own format, so the ART reader
> should take it, and that is an expectation rather than a fact. If one is
> refused, the message names the forms it does know — and the file is worth
> keeping, because that is what the third reader would be written from.

### Making one on purpose

Useful for checking the reader, and for seeing the whole path work before a
real report arrives. Freeze a debuggable app's main thread, tap it, then:

```bash
adb shell 'dumpsys dropbox --print data_app_anr' > record.txt
echolot anr record.txt
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
whose monitor it wants. The raw name stays in the output beside the resolved
one.

**Then the main thread**, and the case worth knowing about before you read one:
`nativePollOnce` means it was **idle** when the dump was taken. Whatever caused
the freeze had already let go, or never ran on that thread at all. Reading the
top frame as the culprit sends an investigation into Android's message queue.

**When every frame belongs to the platform or a library**, it says that in
those words. One of the ten sample reports had not a single frame outside them
in any of its fifty-three threads — every busy one was inside `androidx.work`.
That is a finding, and it reads differently from a report with thin sections.

**Then the threads that were doing something.** A dump holds fifty threads on a
quiet app and three hundred on a busy one, nearly all asleep. Striking out the
idle ones is most of the work — pools waiting on a queue, coroutine workers
parked, binder pools, runtime daemons, loopers. What the vocabulary does not
cover sinks to the bottom of the list rather than being struck out on a guess.

**Then what else the device was doing**, when the source carries a CPU table.
The device's own record does. A machine where `system_server` and
`surfaceflinger` were eating most of two cores is a different story from an app
that blocked itself, and the table is the only thing in a report that can tell
them apart.

**Then what it cannot say.** No reason, when the source has none. No durations
at all — a dump is one moment, and how long anything took comes from a trace.
Lines it could not read, counted.

## From a frame to a file

```bash
echolot anr report.txt --root .
```

A java frame carries its own source location: the compiler wrote the file name
and the line into it. So the repository is asked to confirm a path rather than
to find one, which is a shorter road than the one `domains` takes from a slice
name.

Two modules holding a `Mapper.kt` is ordinary, so the package from the frame's
own symbol decides between them. One file of that name in the whole checkout is
not a guess whatever the package says. Several candidates and nothing to choose
by is the only case that prints a caveat.

> [!IMPORTANT]
> **Check out the build the report came from.** Line numbers are the first
> thing to go stale. On a report from 26.15.1 read against a working tree,
> 103 frames of 116 landed on an import, on a blank line, on a constant, in a
> different function, or past the end of the file. `mark --from-anr` says so in
> one sentence rather than refusing each frame on its own merits.

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
  named after one function around the body of another;
- **a `return` in the body** — a begin/end pair leaks the section on the early
  path;
- **the body is on one line** — `remove` could not take it out without taking
  the code with it.

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

To catch either, record long enough. The default `duration_ms: 12000` does not
hold a five-second freeze plus the five the system waits before declaring
anything.

## What this does not do

It does not reproduce the freeze. ANRs from the field live on particular
devices, particular data and races, and the report rarely says which action led
to the block. The honest end of the offline half is a list of places and the
build to check out; the scenario is yours.
