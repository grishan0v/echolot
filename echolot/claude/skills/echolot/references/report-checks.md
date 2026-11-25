# Check these before drawing conclusions

**`window.end_anchor.before_start`** with `matches == 0` — the end anchor's
name is in the trace, but only before the start anchor, so nothing closed the
window and it ran to the end of the trace. The anchors are most likely
swapped.

**`window.start_anchor.matches == 0` or `window.end_anchor.matches == 0`** —
the anchor never matched and the window ran to that edge of the trace: from
the process's first slice, or to the end of the recording. None of the numbers
are about your scenario. Do not investigate, fix the config: look at the real
names via `echolot probe` and correct `scenario.start` or `scenario.end`.

**`window.end_anchor.unfinished`** — the end anchor opened and never closed:
the scenario had not reached its end when the recording stopped, and the
window runs to the end of the trace. What the scenario got stuck on is inside
the window; that it never finished is a finding of its own.

**`drift.drifts: true`** — the set's runs drift from the first to the last:
the rows found in every run follow the order of the runs more closely than a
random order would, `p` at 5% or less (one test for the whole set, against a
thousand orders drawn from a fixed seed). A median over them is no one state
of the device. `drift.most` names the three rows that follow the order
closest, with the median of their first and last third of runs: where only
the first runs stand apart, the device was settling and the set without them
is one state; where values climb or fall all the way, `runner.cooldown`. A
row on that list that holds in a comparison is the first to doubt. `drifts`
is `null` under 8 runs.

**`window.opened_inside` with `material: true`** — the scenario window opened
while the main thread was already blocked, and more of that block happened
before the anchor matched than inside the window. Thread states are clipped to
the window, so the waiting in the report is the tail of a stall whose cause is
outside it. The numbers below are a partial account rather than a wrong one,
which is why it is easy to miss. Move `scenario.start` earlier and analyse
again if the cause is what you are after.

Sleeping is only counted when a message was open at the time. A main thread
idle at the message queue looks identical in the state alone — on one real
scenario it sat there for 1615 ms waiting for a finger — and that is the app
working, not a stall.

**`window.defined_by`** — only for a scenario with `window: launch`: what
closed the window. `launch` is the launch's end, the first frame.
`launch+reportFullyDrawn` is the end of the app's `reportFullyDrawn()` call,
which came after that frame. `launch+handover` is the end of the launch the
app handed its start over to by itself after its first frame, a splash's
hand-over to the main activity: `window.handover` says `from` and `to`, the
first frame from the window's start (`first_frame_ms`), the time from it to
the app's call (`after_ms`) and, merged, `runs`. The work between the two
frames is the start's and is inside the window; `Startup` is still the first
launch alone. `window.alias` names the activity a launcher alias stands for
(`{"name": …, "of": …}`). `window.report_fully_drawn` says where the call
was read: `app` for the app's own slice on its main thread, `system_server`
for ActivityManager's record of it when the app's slice is missing, `null`
when the trace holds no call after the launch began. `launch` beside `app`
means the call came before the first frame and closed nothing. Such a window
has no anchors: `start_anchor` and `end_anchor` are `null`. A window cut by
anchors carries neither key. `whole` is the whole trace (`analyze
--whole-trace`, `window: whole`), with no `report_fully_drawn`: the one
window a report calls the whole trace, about everything the app did while the
recording ran, and refused by `compare` against any other.

The two definitions are two windows. A launch that ran on to
`reportFullyDrawn()` is longer and holds rows the other does not, so `compare`
refuses one against the other, and either against a window cut by anchors.
In a merged report, repeats that did not close the same way are named:
`defined_by` joins the two, `defined_by_runs` counts each
(`{"launch": "1/5", "launch+reportFullyDrawn": "4/5"}`), and the header
warns. Find the repeat without the call and leave it out, or look at why the
app did not make it. A set where `launch+handover` is one of the two handed
over in some repeats and not in others: a screen the app shows only now and
then, or a recording that stopped before the second launch ended.

**`window.process_alternatives` present** — the mask matched several processes
and the largest by slice count was taken. If you are analysing `:pushservice`
instead of the main process, narrow `project.process`. The list holds the
next few by slice count; `process_alternatives_total` is how many there were.

**`summary.absent_ids` non-empty** — the config turned these detectors off,
with `<detector>: false` under `detectors:`, so they did not run. They are not
silent; nobody asked them. `echolot analyze … --defaults` runs every shipped
detector without touching the config, which is the quickest way to see what
they would have said.

**`detectors[].error != null`** — that detector failed while the rest ran. SQL
is version-fragile; report the error, but do not treat the absence of findings
as an answer. report.md lists it under `Failed`, never under `Silent`. In a
merged report, `failed_runs` says in how many repeats it failed; its rows come
from the others, and their `runs` counts those.

**`detectors[].checked: false`** — that detector could not look: the trace
does not hold what it reads (the platform state, a frame timeline, composable
names, an atrace category), and `reason` says which, in one sentence. Its empty
`rows` are not silence, and not good news. report.md names it in a line beside
the one for `absent_ids`, never under `Silent`; `echolot report` says `not
checked`; `compare` leaves it out of its table on both sides, and
`summary.not_compared` says why. To have it look, record again with what it
reads — `reason` says what that is — rather than reading the report harder. A
merged report is checked only when every repeat was, and `reason` says in how
many recordings it was not. A detector that failed is `checked: false` too, with
its `error`. A report with no `checked` at all is older than the key: unknown,
which is neither checked nor not.

**A merged report's window checks cover every repeat.** `start_anchor` and
`end_anchor` carry the fewest `matches` any repeat had, and `missed: "1/3"`
when the anchor found nothing in some of them: those repeats' windows ran from
the process's first slice to where its last one ends, and their rows are in
the medians. A scenario with no anchor in the config (`null`) has
`start_anchor` or `end_anchor` set to `null`, and the header says in one line
where its window was taken from. `opened_inside` comes from a
repeat where it is material, with `runs` saying in how many. `processes` lists
the process names when the repeats measured more than one.

**`environment.cpu.runs` or `environment.thermal.runs` short of the repeats**
— the clock or the temperature comes from those repeats alone, and so does
what `compare` says about the device. `throttled_runs` counts out of the
repeats that recorded thermal state.

**`environment.cpu` is `null`, or `environment.thermal.throttled` is true** —
in the first case nobody measured the machine, so a duration cannot be read as
a statement about the code alone; in the second the kernel was taking capacity
away while these numbers were made. Neither is a reason to discard the report,
and both are a reason to say so out loud before drawing a conclusion from a
number that moved.

**`environment.compile.filter` is `verify` or `run-from-apk`, or joins two
with `and`** — in the first case none of the app's code was compiled ahead
of time, and the numbers are slower than what most users see; say so before
calling anything slow, and record again on a build compiled from its
baseline profile. In the second the repeats ran two builds, or one build
installed two ways, and no median in the report is either of them.

**`recording`** — how the traces were recorded, which `compare` sets against
the other side's. `data_sources`, `atrace_categories` and `ftrace_events` are
what the trace's own config asked the device for, read from every trace, old
or new; `null` for a trace that carries no config. `runner`, `reset_policy`
and `app_at_stop` are what `collect` kept beside each trace it recorded, in
`<trace>.recording.json`: `app_at_stop` is `running` when the force-stop
before the repeat found the app still up — the start then followed a kill by
half a second — and `stopped` when it was already gone. They are `null` for
traces recorded by hand, by another tool, or by a `collect` from before it
kept them: unknown, not "the same". Each key is the value most repeats had,
and `mixed` lists the split for any key they did not all share. The first
repeat of a `collect` finds the app as the last session left it, so a split
in `app_at_stop` is normal; a split in the runner or the trace config is a set
recorded two ways.

`animation_scales` (window, transition and animator, as Developer options
set them) and `always_finish_activities` ("Don't keep activities") are what
`collect` read of the device once a round, in launch and command modes. An
animation lasts its length times its scale; 0 turns it off. With "Don't keep
activities" on, a return to a living process recreates the activity: a
scenario meant to measure a hot start measures a warm one. The option is
ActivityManager's own state, not the stored setting, which `settings put`
changes without effect until a reboot. Both are `null` for gradle mode and
for traces from a `collect` before it read them. The header names a scale
that is not 1 and says when the option was on.

**`toolchain.echolot`** and each detector's **`sha`** — which echolot wrote
the report, and what each detector read the trace with (its queries,
`@identity` and stdlib modules, comment lines aside). Two reports whose
detector has another `sha` read the trace differently, and `compare` gives no
verdict on that detector's rows. `status` and `echolot report` say when a
report was written by another minor version of echolot, or by one from before
the stamp: analyze its traces again before reading it against today's.

**`toolchain.trace_processor`** — the parser version. If the numbers diverge
between two reports, check this first: a version change alters trace semantics
while the SQL stays identical. `source` says where the binary came from —
`pinned`, `--tp-binary` on the command line, or `toolchain.tp_binary` from the
config, usually local.yml — and `binary` is its path when it was not the
pinned one, `null` when it was.
