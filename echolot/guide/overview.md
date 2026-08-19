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

## Boundaries

The tool **localises one specific regression**: you know "it was 3 s, now it is
7 s" and need to find where. It does not search for the unknown across a pile
of traces.

Thresholds are tied to a device and a scenario. When either changes, run
`echolot calibrate` on known-healthy runs instead of nudging numbers by hand.

## More

```bash
echolot guide setup     # building echolot.yml for a project that has none
echolot guide hunt      # the loop: from a report down to a place in the code
echolot explain         # the detectors and their parameters
echolot --help          # every command, grouped by who runs it
```
