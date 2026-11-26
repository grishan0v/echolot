# ANR field notes

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/anr-notes.md)

[ANRs](anr.md) says how to use the reader. This page says what it was built
on: ten real Crashlytics ANR exports from one production app, and an ANR made
by hand on a phone to check the half the exports could not. Each rule the
reader follows started as one of the observations below, and the reasons
behind them are here so that a change to a rule can be weighed against them.

As everywhere in these docs, the app is not named. Its package appears as
`com.example.app` and its own classes under neutral names; library and
platform names are real. Counts that could pin the app are rounded.

## What the real exports showed

Ten reports from one production app, taken across many releases, each with a
few dozen to a few hundred threads. They were read with a prototype of the
parser, and everything below comes from it.

**The header is the same in all ten:** Application, Platform, Version, Issue,
Session, Date. None of them names the cause of the ANR: there is no reason
line, no component, no intent, no device and no OS version. A reproduction
built on a Crashlytics export alone has no cause to start from; the scenario
has to be pieced together from the frames.

**A thread's signature has a continuation** that the first file did not show:

```
main (blocked):tid=1 systid=12345 | waiting to lock <0x0a1b2c3d> (com.example.app.data.t) held by thread 13
```

That settled the question of who holds the lock: Crashlytics prints it.
`held by thread N` is a `tid` from the same export, so the chain resolves
inside the file. A thread is in one of six states: `waiting`,
`timed waiting`, `native`, `runnable`, `blocked` and `unknown`.

**The lock chain is the strongest thing in a dump, and it needs no trace.**
Four reports out of ten carry one, and in two of them the main thread itself
is a link. The findings share one shape: **a monitor held across a blocking
wait.**

| report | waiting | holder | which is itself stuck on |
|---|---|---|---|
| A | five threads, `main` among them | a coroutine worker, `DefaultDispatcher-worker-#` | `Tasks.await` from GMS Block Store |
| B | `main` | a coroutine worker, `DefaultDispatcher-worker-#` | `Http2Stream.takeHeaders` from OkHttp |
| C, D | `queue-1-2`, `Measurement Worker` | `main` itself | `FirebasePerfEarly.<init>` |

The first unwinds down to a line in a file: `Store.setState` →
`IdSource.save` → `BlockStoreSource.save` → `BlockStoreSource.await` →
`Tasks.await`. The second is a network call, made through one of the app's
OkHttp interceptors while the monitor was held.

**The class name in the lock note is obfuscated; the frames are not.** The
signature says `com.example.app.data.t`, while the top frame of the same
thread says `com.example.app.data.Store.getState`. Crashlytics deobfuscates
the stack and leaves the name in the note as R8 wrote it. No mapping file is
needed to read it: **the top frame of a blocked thread names the class whose
monitor it waits for**, since the thread is standing in the method it could
not enter, and the package matches. This held on both reports with a chain,
and the reader names a monitor this way only while the packages agree.

**A thread explosion shows at once.** Two reports held two to three hundred
threads each, against forty to sixty-odd in the rest, and a quarter to a
third of them were `Firebase Blocking Thread ##`. That many threads is news by
itself, so the report gives the count of threads, and how many of them were
doing something, near its top.

**The main thread was idle in two reports out of ten**, in `nativePollOnce`.
In the rest it was `blocked` twice, in `BinderProxy.transactNative` twice, in
Firebase Performance's initialisation twice, once in a DI module and once in
`Unsafe.unpark`. The rule about `nativePollOnce` is still needed; judged by the
first file alone, its share looked larger than it is.

**The parser's job is crossing out.** In a report of about fifty threads, all
but a handful are idle; in one of about three hundred, a couple of dozen are
working. The value is in leaving just those few, the same move the trace
report makes to turn hundreds of thousands of slices into twenty rows.

**What is left after crossing out, across all ten:** `Store.getState`
(5 times), `FirebaseApp.getInstance` (4), `BinderProxy.transactNative` (3),
`FirebasePerfEarly.<init>` (2), `SQLiteConnection.nativeExecute` (2),
`SocketInputStream.socketRead0` (2), `CrossProcessLock.acquire`,
`TypeAdapters.<clinit>`. The families read at a glance, and that is the output
the reader should print.

**The list of idle states had to grow** after the first pass: `Object.wait` in
a timer loop, OkHttp's `TaskRunner`, `epoll`, a native sleep, the
`DelayedWorkQueue` and `SynchronousQueue` queues. It is a list, it will keep
growing, and every line in it is a separate check. A shape goes in only when
the frame below it can mean nothing but waiting: a family left out shows an
idle thread, which costs a reader a glance, and a family wrongly listed hides
a working one, which costs the investigation.

**Thread names repeat.** `GoogleApiHandler`, `WM.task-2` and
`ConscryptStatsLogWriter` showed up two or three times each, with different
tids. A thread is identified by its name and its tid together, and the tid is
what `held by thread N` refers to. Names that differ only in their numbers,
`Firebase Blocking Thread ##`, `DefaultDispatcher-worker-#`,
`pool-#-thread-#`, are one pool.

## Three bugs no amount of reading would have found

All three were found the same way: make a real ANR on a phone and see what
the tool says about it. None showed up on the fixture, and none was in code
that looked suspicious.

1. **An unfinished slice was read as zero-length.** `dur = -1` was folded by
   `MAX(dur, 0)`. ART wrote two records about the hang, and both stayed open,
   because the lock was not released before the recording ended. For every
   detector they were slices of zero length: the longest thing in the trace
   was the one thing nobody could see. The fix went into `window.sql`, which
   now runs an open slice to the end of the window, and the detectors did not
   change; six detectors fired instead of four.
2. **The statement splitter cut a script at a `;` inside a comment.** A
   sentence with a semicolon ended the script mid-word, and the error pointed
   at a line of English prose. Caught twice in ten minutes. Comment lines are
   now dropped before the split.
3. **The `anr` detector looked for a number the platform does not always
   write.** The counter comes in two forms, `ErrorId:<process> <pid>#<uuid>`
   and `ErrorId:<process>#<uuid>`. The phone wrote the second; Perfetto's
   module parsed it correctly and returned an empty `pid`, and the join was on
   `pid`. The record was in the trace and never reached the report. The
   detector now also matches on the process name.

The lesson: a detector checked only on synthetic data is half checked, and the
missing half lies outside the detector.

## How to get a real drop box record

This was needed to check the second half of the parser: what ART itself
prints, as opposed to what Crashlytics makes of it. The files in `/data/anr/`
have mode 600 and belong to `system`, the phone has no root, and a bug report
drags along a snapshot of the whole device. Making an ANR yourself is
simpler.

The bait is one `Activity` of about ten lines: a background thread takes a
monitor and sleeps for two minutes, and the tap handler enters the same
`synchronized` block on the main thread. It builds without Gradle, with the
tools from `build-tools`: `javac` against `android.jar`, `d8`, `aapt2 link`,
`zipalign`, and `apksigner` with the key in `~/.android/debug.keystore`.
Eight kilobytes.

```bash
adb install -r anrbait.apk
adb shell am start -n dev.echolot.anrbait/.MainActivity
adb shell input tap 540 1200
adb shell 'dumpsys dropbox --print data_app_anr' > record.txt
adb uninstall dev.echolot.anrbait
```

The ANR fired on the first tap: `Input dispatching timed out … Waited 10000ms
for MotionEvent`. The record is about seven thousand lines with two process
blocks: the app with a few dozen threads, and `system_server` with a few
hundred.

It corrected a parser written from memory of the ART format in more places
than one:

- three signature forms instead of two: `prio=… (not attached)`, and a state
  with an addition in parentheses. Without that last form two threads fell
  into the block above them and dropped out of the count;
- `- waiting on` and `- sleeping on` lines next to `- locked`;
- `(no managed stack frames)` and `DumpLatencyMs`;
- the drop box's own preamble, the CPU load table, the runtime's summary
  after the last thread, and the table of kernel waits after that.

Five of these forms would otherwise have been left unread.

The header rule comes from here too. `Intern table: …` and `Heap: …` have the
same `key: value` form as the header's fields. Without a rule against reading
them once the threads have started, the runtime's bookkeeping ends up among
the app's header fields.

## What an ANR report says and what it does not

It always says the app's version, each thread's state and stack, and, for a
`blocked` thread, the monitor's address with its owner. `BinderProxy.transact`
in a stack means a trip over IPC; frames of files and sockets mean waiting on
I/O. The cause (`Input dispatching timed out`, a broadcast, a service, a
provider), the component and the intent come from the drop box and Play
Console. A Crashlytics export has none of them; that was checked on all ten
reports.

It does not say the main thing: **it is a snapshot of one instant.** There is
no history in it. A stack taken five seconds into a hang shows where the
thread stood at that moment, which is not necessarily where the time went.

One case is worth naming out loud: the main thread in `nativePollOnce`. It
was idle, so the culprit is somewhere else entirely: a stuck pool of binder
threads, a hung broadcast, a shortage across the whole system. A person
reading such a report without a hint goes looking for the problem in the
first frame of the app they see, and loses a day.

The report's place in the loop follows from this: **the report is a
hypothesis, and the trace is the proof.**

A lock chain stands apart. A monitor held by a thread stuck on a blocking
wait, with five threads queued behind it and the main thread among them, is a
mechanism. A finding like that gets fixed without recording a single trace:
across ten reports it turned up four times, and the chains that were followed
unwound down to a line in a file. A trace is needed where the dump shows one
busy thread and says nothing about how long it worked.
