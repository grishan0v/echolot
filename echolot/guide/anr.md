# Hunting an ANR

The app stopped answering and the report came from a phone you do not have.
That is a different starting point from "it was 3 s, now it is 7 s", and the
first half of the work needs no device and no trace.

## Read the report

```bash
echolot anr report.txt --root .
echolot anr report.txt --json      # the same findings, for you
```

Reads and prints. It opens no investigation, and writes nothing but its own
line in `.echolot/log/runs.jsonl`, so a folder of exports goes through it in
one loop:

```bash
for f in ~/anr/*.txt; do echolot anr "$f" | head -6; done
```

It takes a Crashlytics export, a Play Console cluster, or the device's own
record from `adb shell dumpsys dropbox --print data_app_anr`. Only the last of
those carries the reason the system fired — checked across ten Crashlytics
exports, none of them has one.

A Crashlytics event from your own tool reads too — the YAML or JSON it
returned, saved to a file as it came, or just its `Thread:` text; one event
per file. It carries **one thread**, the one Crashlytics blamed, with no state,
no tid and no lock note, cut after eighteen or so frames: no lock chain can
come out of it, and the report says so first under what it does not say,
beside the revision, the process state and Crashlytics' own reading that the
event adds. When the question is a lock, ask the human for the console's
export of that event. Do not build an export from the event by hand: the
states and tids you would invent are what the report would then read.

Play Console strips who holds a monitor, so a chain from there names what
everyone is queued on and says the holder is not in the file — look for it
among the threads that were working. An export of the same freeze from
Crashlytics or off a device is worth more.

Read it from the checkout. The packages its sources declare are what makes a
frame the app's own; without them a library the tool has never heard of reads
as the app's.

A minified build's frames read `a.b.run(SourceFile:3)`, and nothing in them is
the app's until the build's mapping names them back:

```bash
echolot anr report.txt --root . --mapping app/build/outputs/mapping/release/mapping.txt
```

Without `--mapping`, `project.mapping` of the echolot.yml under `--root` is
used. The device's own record of such a build is always minified; an export is
when the console never got the mapping.

## What to do with what it says

| the report says | your next move |
|---|---|
| a lock chain with the main thread behind it | you have the mechanism. Open the frames of the thread at the bottom — this needs no trace |
| a queue with no holder named | the source withheld it. The holder is among the threads that were working |
| the main thread was **idle** (`nativePollOnce`) | it was not the culprit. Read the threads that were working |
| the main thread was **waiting for a garbage collection** (`--json`: `main.waiting_for: "gc"`) | the frame it allocated in is not the cause; how much the app allocates and keeps is. Read the threads it names as stopped too, then record the same scenario and read `gc_pressure` (needs `atrace:dalvik`), or take a heap dump. `gc.cause: "explicit"` is a `System.gc()` call instead: open that line |
| frames placed in the checkout | open those lines, once `anr` marks none of them as now inside another function |
| "probably not the build that froze" above the placed frames | most lines fall outside the function their frame names: check out the build the report names before opening any |
| frames landing nowhere | check out the build the report names. Line numbers are the first thing to go stale |
| "What N frames are: they read as R8 named them" under what it does not say | a minified build: run it again with that build's mapping |
| frames R8 wrote "have no place in the mapping", or are named after another mapping | the mapping is another build's. Find the one that froze before reading the names |
| "… had no place in it and keeps R8's method name", with no warning | a line the mapping has no range for, as the right one can leave in a synthetic class R8 merged. One such frame, or frames named after the mapping itself, are no sign of another build: read on |
| "every frame belongs to the platform or a library", then "the frames nearest to the app" | the platform is a dead end, a library the app drives is not: `SystemJobScheduler.cancel` points at the app's WorkManager setup. Read that setup |
| "every frame belongs to the platform or a library", then a platform frame such as `JobServiceEngine$JobHandler.ackStopMessage` with what it was doing | still a lead: the main thread was telling the system a JobScheduler job had stopped. Read the app's WorkManager or JobService setup |
| "Who was holding what" under what it does not say | this file carries no lock notes; an empty chain list is the file's limit, not the freeze's. Get the device's own record |
| "The other threads, their states and their locks" under what it does not say | the input was a Crashlytics event: one thread. Read it as the rows above say; for a lock — Crashlytics' subtitle says deadlock — or a main thread that is not in it, ask for the console's export of that event, or the device's own record |
| "This checkout is at the build's own revision", or "a line placed here may have moved since" | the event named its build's revision. In the second case `git worktree add --detach <dir> <revision>`, then `anr` again with `--root <dir>` |
| "What the main thread was doing" under what it does not say | the file has no main thread, often an export of the one thread a console blamed. Whether the main thread waited for it is not in the file: get the whole event, or the device's own record |
| a CPU table with the device busy | a machine under load is a different story from an app that blocked itself |

An idle main thread is the case that wastes a day if you miss it. The dump is a
snapshot taken five seconds in, and whatever caused the freeze had often let go
by then.

## Instrument what was on the stack

```bash
echolot mark --from-anr report.txt          # the plan
echolot mark --from-anr report.txt --apply
echolot mark --remove                       # always, when done
```

Targets come from the frames rather than from the manifest. Most proposals will
not be applicable and the reasons are the useful part — the line falls in a
different function than the frame names, a `return` in the body, a body on one
line. Show the reasons, do not work around them.

## Measure it, if you can record it

Two detectors work on the trace side, and both need a long enough recording:
the seconds until the freeze starts, plus the five an unanswered input event is
given before the system declares an ANR, plus a few for it to write the record.
A freeze eight seconds in needs about sixteen, past the default
`duration_ms: 12000`. That knob sets the recording in `launch` and `command`
modes; in `gradle` mode the macrobenchmark decides.

- **`anr_risk`** — a stretch where the main thread never got back to the message
  queue. Its bar is the platform's five seconds. Silent by construction on a
  cold-start window, which is a second or two. `detail` splits the stretch into
  time on a CPU, time waiting for one, and neither — the third means a lock or
  a disk, and `monitor_contention` is what to read next.
- **`anr`** — what the system recorded, if a freeze fired while the trace ran.
  The only detector not clipped to the scenario window. Its `detail` carries
  the platform's error id, the same string the device's drop box record has.

When the report says the main thread was waiting for a garbage collection,
`gc_pressure` is the third: how often the collector ran in the scenario and
which threads it stopped, by thread. It needs `atrace:dalvik`.

## What this does not do

It does not reproduce the freeze, and it does not pretend to. ANRs from the
field live on particular devices, particular data and races, and the report
rarely says which action led to the block. Finish the offline half — the places
and the build to check out — and hand the scenario back.
