# echolot — how to work with it

You are an AI agent in a repository that uses echolot. This is what the tool
is for and the order to use it in. Printed by the package itself, so it can
never be out of step with the version installed.

## The one rule

**Never open the trace yourself.** No hand-rolled TraceProcessor, no ad-hoc
SQL, no reading `.perfetto-trace`. A trace is tens of megabytes and hundreds of
thousands of slices; everything you need arrives as a table of about twenty
rows.

Live proportions: an 81 MB trace with 475k slices becomes a 14 KB
`report.json` in about five seconds.

If the report seems to be missing something, that is not a reason to open the
trace. It is a reason to fix the config (anchors that did not match, the wrong
process), to add instrumentation and record again, or to add a detector.

## Ask the tool where you are

Do not guess the next step from the state of the repository. The tool knows:

```bash
echolot                 # where things stand, and one line saying what is next
echolot status --next   # the same as one word, for switching on
```

| `next` | what to do |
|---|---|
| `init` / `init-force` | run `echolot init` (add `--force` when it says so), then run `echolot` again |
| `doctor` | run `echolot doctor`, show what failed, stop — no report is trustworthy until it passes |
| `setup` | build `echolot.yml` — run `echolot guide setup` |
| `fix-config` | show the parse error, ask the human to fix `echolot.yml`, stop |
| `resume-or-new` | an investigation was left open. Show `echolot hunt` and ask the human: carry on, or start a new one — `echolot hunt --resume` or `echolot hunt "<their question>"` |
| `hunt` | find the regression — run `echolot guide hunt` |

## The order of work

```bash
echolot doctor -q                          # does this environment compute correctly?
echolot collect -c echolot.yml -n 5        # capture traces, when there are none
echolot analyze <trace...> -c echolot.yml  # the report
```

`analyze` writes `.echolot/out/report.json` for you and `report.md` for humans.
**Read the json** — it has a stable schema.

```bash
echolot compare                            # what moved since the previous round
echolot compare <before.json> <after.json> # or name the two reports
```

`compare` answers the other half of the question the report cannot: which rows
appeared, which grew, and whether the repeats support calling that a change.
Read `overlap` before acting on a row — `true` means the runs disagree among
themselves by more than the medians moved.

## Reading the report

Three things you will get wrong without being told.

**Silent detectors matter as much as firing ones.** They stay in the report
with empty `rows`. Silence means that ground was checked and is clean — do not
go there.

**`self_ms` versus `total_ms`.** Self time, with children subtracted, is where
the time actually went. `traversal` with `total_ms: 354` and `self_ms: 79` does
almost nothing itself; dig into its children. Never add `total_ms` across rows:
they nest inside one another.

**Warnings inside `window`.** If `start_anchor.matches == 0` the window
expanded to the whole trace and none of the numbers are about your scenario.
Same for `process_alternatives` — you may be analysing the wrong process. Fix
the config rather than hunting a problem.

**Silence is relative to the thresholds.** The report's `config` field and
`detectors[].params_source` say whether the numbers are the shipped defaults or
calibrated ones. Calibrated on the very runs that hold the regression means the
bar sits above it: look with `echolot analyze … --defaults` before calling a
run clean.

**`frame_jank` is about single frames, not totals.** The other six aggregate by
name and gate on sums, which is right for "cold start got slower" and blind to
a heavy tail: one 86 ms frame among thousands disappears into every sum there
is. This one reads SurfaceFlinger's per-frame record instead, so it needs no
instrumentation and answers a question the rest cannot.

Its `total_ms` and `max_ms` are time **past the deadline**; the frame's own
length is in `detail`, which is where a benchmark's percentiles can be matched.
`detail` also leads with the platform's verdict on whose deadline was missed —
`Self Jank` is the app, `Other Jank` is the compositor and is not something to
go into the code over.

Its silence is ambiguous: no bad frames, and no frame timeline in the trace at
all, look the same. Android 11 and below have none, and neither does a capture
that did not ask for it. Check before calling a scenario smooth.

## From a finding to the code

1. A firing detector gives a `location` — a slice or thread name.
2. The `domains` section of `echolot.yml` maps that name to a module and file.
3. Not there? Grep the repository for the slice name: it is a string literal
   inside `trace("...")`, survives minification, and is found exactly.
4. Nothing found? The slice is most likely a system one (`bindApplication`,
   `Choreographer#doFrame`, `binder transaction`).

When `uninstrumented_cpu` fires there is no code behind the finding by
definition: the thread burned CPU with no instrumentation. That is an address
for adding `trace{}`, not the location of a bug.

## Watch your context

The loop generates a lot of raw output — reports, repository searches,
instrumentation diffs, several rounds. If your host can run this in a separate
context or a subagent, do that and return only the conclusion. If it cannot,
work in short passes and keep raw output out of the conversation: read
`report.json`, quote the two or three rows that matter, and drop the rest. A
window filled with raw output is where the instability this tool exists to
remove comes back.

**`main_thread_outlier` is not `main_thread_block` again.** One gates on the
sum for a name and answers "where did the time go"; the other gates on a
single occurrence against the median for that same name and answers "which one
was out of line". A name in both is telling you two things.

They lead to different places in the code. Expensive every time means the fix
is in that work. Usually fine and once not means the cause is the state it hit
that once — a cold cache, a lock, a first-run path. `detail` carries the median
and how many occurrences it came from, which is what to hold a benchmark's
percentiles against.

## Reporting back on the tool itself

When the question is about echolot rather than the app — how a session went,
where the tool got in the way — that is `echolot reflect --last`, run from the
project you worked in.

Only Claude Code has a reader for its transcripts. From anywhere else the
report is built from the tool's own run log, `.echolot/log/runs.jsonl`: which
commands ran, when, for how long, with what exit code. It is smaller, and it
names under **Not checked** every check it could not make. Read that section —
a check listed there found nothing because it had nothing to look at, which is
not the same as finding nothing wrong.

## Boundaries

The tool **localises one specific regression**: you know "it was 3 s, now it is
7 s" and need to find where. It does not search for the unknown across a pile
of traces.

Thresholds are tied to a device and a scenario. When either changes, run
`echolot calibrate` on known-healthy runs instead of nudging numbers by hand.

## More

```bash
echolot reflect --last  # how this session went, and what to change in the tool
echolot compare --help  # the forms it takes and the floor it uses
echolot guide setup     # building echolot.yml for a project that has none
echolot guide hunt      # the loop: from a report down to a place in the code
echolot guide anr       # the app stopped answering: reading an ANR report, and measuring one
echolot explain         # the detectors and their parameters
echolot --help          # every command, grouped by who runs it
```
