# `window` — where the window went

**`window.main_thread`** — where the window went, in milliseconds, for the
main thread only. `summary` counts detectors; this counts time, and it is the
number that decides whether a quiet report means a clean run. Four buckets and
a remainder, summed from thread states clipped to the window, so in a report
of one trace the parts add up to the whole. In a merged report each bucket is
the median of its own runs and `window_ms` is a median of its own, so the
buckets need not add up to it, above or below, with nothing missing or
counted twice; `accounted_pct`, the median of each run's own share, is the
coverage to read.

Read it before the tables. A compound stall — 40% waiting for a CPU beside 35%
blocked in the kernel — is one line here and two unrelated rows in different
sections anywhere else. And the contrast between two runs is often the finding
itself: on the same phone a freshly installed app spent 10% of its cold start
blocked in the kernel against 1% for one whose pages were already cached.

`sleeping` is one bucket and stays ambiguous on purpose: an idle looper and a
blocking call inside a message are both `S`, and telling them apart needs the
slices rather than the states. A high `sleeping` share is a question, not a
finding.

`accounted_pct` short of 100 means the main thread was not there for the whole
window — the process started inside it, or the recording has a hole. The
shares are then of what was seen rather than of the scenario. The report
warns below 95%; a smaller shortfall passes without a word.

A launch window (`window.defined_by`) always starts before the process does:
it opens at the intent. That stretch is measured apart, as
`before_process_ms`, from the window's start to the main thread's first
state, and the header names it as a share: `13% before the process started`.
It is the launch's own time and no hole, so the warning counts it as
accounted for and fires only on what is still missing. It equals the
startup's `launch_delay`. Zero when the process was already running, as on a
warm or hot start.

**`in_rows_pct`** — how much of the main thread's window the findings cover,
each moment counted once however many rows describe it; `in_rows_ms` is the
same in milliseconds. Read it instead of adding up `self_ms`. A disk wait
inside a slice is in `io_wait` and in the slice's self time, a binder
transaction on the main thread is in `binder_txn` and in `main_thread_block`,
and the sum can pass 100% without a single row being wrong. The markers table
is not in it. `in_rows_uncounted` names a detector whose rows could not be
placed on the timeline, and the share is short by those rows.

Read it with the budget. A low share with the main thread mostly `sleeping`
means it waited — for work elsewhere, or for nothing at all — and the findings
on the main thread do not say which. Work it waited for, if the report has it,
is in the rows about other threads: `uninstrumented_cpu`, `io_wait`, a
background `binder_txn`. A low share with the main thread mostly on a CPU
means it ran code no row names: slices below the thresholds — `echolot names`
shows which. Its time outside every slice is `uninstrumented_cpu`'s row on
the main thread once it clears that detector's bar, and `mark` names where
markers go in it.

**`window.crowding`** — the app's threads against the device's cores, moment
by moment: `peak`, the most of them running or ready to run at once,
`above_cores_ms`, how long there were more than `cores`, and `threads`, how
many had a state in the window. Merged, the medians. The header prints it
under the budget when the threads outnumbered the cores: `App threads: up to
27 running or ready at once on 8 cores, more than 8 for 245 ms of the
window.` On a large app's launch that was the case behind the main thread's
wait for a CPU: half of it was spent behind the app's own work. Read it with
`waiting_for_cpu` and with `runnable_starvation`'s row for the main thread.

`crowding.outside` is the other side of the crowd: `pct`, the share of the
window's CPU time (cores × its length) that processes other than the app
took, and `top`, the three largest, processes of one name and uid together.
The processes that draw the app and record it are in neither:
SurfaceFlinger, the composer and allocator HALs, the display and GPU drivers'
threads, system_server, SystemUI, the home screen, zygote, and Perfetto's
daemons with adbd. They are busy whenever the app draws, so their share is
`platform_pct`, on its own: 47% of a 287 ms Wikipedia cold start on an
SM-F711B, which left 9–17% in `pct`. At 40% or more the header warns that the
app ran on a busy device and names the largest load, and the crowding line
puts the app's waits behind those processes too. Merged, the median, with
`pct_max` and the names from the repeat where they took most. The share of
the main thread's core others held (`runnable_starvation`) is no measure of
this: 35% at rest, 42% with eight busy loops taking 68% of the device.

**`window.frames`** — how many frames the app drew in the window, by the
frame timeline (`count`; merged, the median with each run's in `values`).
`null` without a frame timeline. `compare` reads it: in a scripted scroll or
drag the window is the gesture's length, and a build that janks draws fewer
frames in it, so work done once a frame shrinks with them. `late` is how many
of them the app made late — the frames `frame_jank`'s rows tag `Self Jank` —
with each run's in `late_values`; `compare --plain` judges a window that runs
to the end of the recording by their share, since its length cannot move.

**`window.drawn`** — how many frames the app's threads drew in the window, by
their `DrawFrame` and `Choreographer#doFrame` slices (`count`; merged, the
median, with `none`, the repeats that drew nothing, out of `runs`). `null`
when nothing in the trace drew a frame, which says the categories were not
recorded, and when the window closed before the app's first frame in a trace
the process was born in (`bindApplication` to `activityStart`): nothing was
drawn yet, so the count says nothing. A repeat that drew none never reached its screen: the markdown says
so first, and nothing else in that report is about the scenario. Look at the
phone, not at the code. `collect` reads the same on its first repeat and stops
the round there.

**`window.startup`** — the app's launch as Perfetto's standard library
measures it, when the trace holds one: `type` (cold, warm, hot), `dur_ms` from
the intent to the first frame, and `reasons`, every millisecond of it given to
one cause — `bind_application`, `activity_start`, `choreographer_do_frame`,
`binder`, `io`, `monitor_contention`, `launch_delay` (before the app's main
thread ran at all), the thread states for the rest. The header prints it under
the budget: `Startup: cold, 1359 ms from the launch to the first frame: 42%
bind_application · 13% monitor_contention · …`. In a merged report
`values` holds each repeat's `dur_ms` and `reason_values` each reason's, a
repeat without the reason as 0: `compare` tests the startup with them.

It is the platform's measure, not the window, and `from_window_start_ms` and
`from_window_end_ms` say where it sits against the window, negative for
before; the header says it in words when they differ. A window opened at
`bindApplication` misses the launch's first hundred milliseconds, and one that
runs to the end of the recording holds seconds after the first frame. For a
cold-start question, read the startup's reasons before the window's tables:
they divide the time the user waited, and nothing else. Absent when the trace
holds no launch of this app.

With several launches in one trace, the one described is the one that shares
the most time with the window. For a launch window it is the launch the
window was built from, whatever came after it: the startup then begins with
the window, and `from_window_end_ms` is how long before the window's end the
first frame came, zero when no `reportFullyDrawn()` call moved the end.
