# Hunting an ANR

The app stopped answering and the report came from a phone you do not have.
That is a different starting point from "it was 3 s, now it is 7 s", and the
first half of the work needs no device and no trace.

## Read the report

```bash
echolot anr report.txt --root .
echolot anr report.txt --json      # the same findings, for you
```

Reads and prints. It opens no investigation and writes nothing, so a folder of
exports goes through it in one loop:

```bash
for f in ~/anr/*.txt; do echolot anr "$f" | head -6; done
```

It takes a Crashlytics export, a Play Console cluster, or the device's own
record from `adb shell dumpsys dropbox --print data_app_anr`. Only the last of
those carries the reason the system fired — checked across ten Crashlytics
exports, none of them has one.

Play Console has not been checked against a real export. It shows ART's format,
so the reader should take it; if one is refused, say so and keep the file
rather than working around it.

## What to do with what it says

| the report says | your next move |
|---|---|
| a lock chain with the main thread behind it | you have the mechanism. Open the holder's frames — this needs no trace |
| the main thread was **idle** (`nativePollOnce`) | it was not the culprit. Read the threads that were working |
| frames placed in the checkout | open those lines |
| frames landing nowhere | check out the build the report names. Line numbers are the first thing to go stale |
| every frame in the platform or a library | say so, in those words. It is a finding, not an empty report |
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

Two detectors work on the trace side, and both need a long enough recording —
the default `duration_ms: 12000` does not hold a five-second freeze plus the
five the system waits before declaring anything.

- **`anr_risk`** — a stretch where the main thread never got back to the message
  queue. Its bar is the platform's five seconds. Silent by construction on a
  cold-start window, which is a second or two. `detail` splits the stretch into
  time on a CPU, time waiting for one, and neither — the third means a lock or
  a disk, and `monitor_contention` is what to read next.
- **`anr`** — what the system recorded, if a freeze fired while the trace ran.
  The only detector not clipped to the scenario window. Its `detail` carries
  the platform's error id, the same string the device's drop box record has.

## What this does not do

It does not reproduce the freeze, and it does not pretend to. ANRs from the
field live on particular devices, particular data and races, and the report
rarely says which action led to the block. Finish the offline half — the places
and the build to check out — and hand the scenario back.
