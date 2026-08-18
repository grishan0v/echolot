---
name: echolot
description: The door to echolot — localise Android performance regressions from a Perfetto trace, from the metric down to a place in the code. /echolot with nothing after it reads the project's state and takes the next step (install the layer, build the config, or hunt); with an argument it does that. Use when cold start regressed, scrolling stutters, TTI grew, a benchmark dropped, or there is a .perfetto-trace to analyse. Also for questions like "why is startup slow", "where does main thread time go", "what is burning CPU".
---

# echolot

A deterministic layer between the Perfetto trace and you.

## The one rule

**Never open the trace yourself.** No hand-rolled TraceProcessor, no ad-hoc
SQL, no reading `.perfetto-trace`. A trace is tens of megabytes and hundreds of
thousands of slices; everything you need, `echolot` hands you as a table of
about twenty rows.

Live proportions: an 81 MB trace with 475k slices compresses into a 14 KB
`report.json`. Six thousand times smaller, and it takes five seconds.

If the report seems to be missing data, that is not a reason to open the trace.
It is a reason to fix the config (anchors that did not match, the wrong
process), to add instrumentation and re-record, or to add a detector.

## `/echolot` is the door

When you are invoked as `/echolot`, do not guess what the human wants from
the state of the project — ask the tool. First:

```bash
echolot            # where things stand: layer, config, traces, report, doctor — and `next`
```

Show that output to the human as it is, then act on the `next` line
(`echolot status --next` gives it as one word):

| `next` | what you do |
|---|---|
| `init` / `init-force` | run `echolot init` (with `--force` when it says so), show the result; then run `echolot` again and continue from its new `next` |
| `doctor` | run `echolot doctor`, show what failed, stop — no report is trustworthy until it passes |
| `setup` | invoke the `echolot-setup` skill (the Skill tool) — it builds `echolot.yml` |
| `fix-config` | show the parse error, ask the human to fix `echolot.yml`, stop |
| `resume-or-new` | **ask before attaching to anything** — see below |
| `hunt` | invoke the `echolot-hunt` skill — it asks what regressed and runs perf-hunter |

## `resume-or-new`: whose question are we answering

An investigation is open, it left traces and a report on disk, and nobody has
worked on it for a while. Attaching to that history without asking is how a
question about scrolling gets answered with cold-start traces.

`echolot` has already printed the recap under the table — the question, when it
was last worked on, how many traces it would reuse, what the last report said,
and anything that drifted since. **Show that recap as it is**, then ask with
`AskUserQuestion`:

| the human picks | what you do |
|---|---|
| carry on | `echolot status --hunt-resume`, then the `echolot-hunt` skill with the recorded question |
| something new | `echolot status --hunt-open "<their question>" --hunt-since "<the change, or unknown>"`, then the `echolot-hunt` skill |
| just show the report | print `.echolot/out/report.md` and stop |

Lean towards "something new" when the recap shows a `!` line — the scenario
changed, the config changed, or it has been untouched for over a week.

`--hunt-open` moves the previous set of traces aside before anything is
recorded, so the new investigation cannot inherit them, and it says on stderr
if the previous one left `AGENTTMP_` markers in the sources. **Deal with those
before hunting**: `echolot mark --remove` takes out what `mark --apply` wrote,
and anything added by hand has to go by hand. Instrumentation nobody meant to
leave behind is the starting condition of the next investigation otherwise.

When the human's own words already name a different question — `/echolot why
does the list stutter` while the open investigation is about cold start — still
ask, but offer "something new" first with their words filled in. A new
formulation is sometimes a sharper version of the same hunt.

Two things this is never asked about. Inside `perf-hunter`'s loop: it
re-records and re-instruments on purpose, works inside an investigation it was
handed, and never calls `status` at all. And within half an hour of the last
`collect` or `analyze` — that is the same sitting, and `next` says `hunt`.

With an argument, the argument wins over the state:

- `/echolot init` — run `echolot init`. Not setup: `init` installs or
  updates **this** layer; the config is setup's job.
- `/echolot setup` — the `echolot-setup` skill.
- `/echolot hunt <words>` — the `echolot-hunt` skill, with the words as what
  regressed. `/echolot <free text>` about slowness ("why is startup slow",
  "the list stutters since the redesign") means the same.
- `/echolot doctor`, `/echolot status`, `/echolot analyze …` — run that
  command, show the output.
- `/echolot new <words>` — start a fresh investigation with those words as the
  question, without asking. `/echolot resume` carries on with the open one.
- `/echolot reflect` — the `echolot-reflect` skill: how the last session
  went, what to change in the tool.

`/echolot-setup`, `/echolot-hunt` and `/echolot-reflect` still exist for
whoever knows where they are going; `/echolot` is the door for everyone else.

## The order of work

```bash
echolot doctor -q               # does the environment compute correctly?
echolot analyze <trace> -c echolot.yml
```

`analyze` writes `.echolot/out/report.json` (for you) and `report.md` (for
humans), next to the config. Read the **json** — it has a stable schema.

No config? That is `next: setup`. No idea what is in the trace?
`echolot probe <trace> --process '<package>*'`.

**Silence is relative to the thresholds.** The report's `Config:` line and
`detectors[].params_source` say whether the numbers are the shipped defaults
or calibrated ones. Calibrated on the runs that hold the regression means the
bar sits above it: look with `echolot analyze … --defaults` before calling a
run clean.

## Reading the report

Details live in `references/report.md`; three things here that you will get
wrong without them.

**Silent detectors matter as much as firing ones.** They stay in the report
with empty `rows`. Silence means that ground was checked and is clean — do not
go there.

**`self_ms` versus `total_ms`.** Self time, with children subtracted, is where
the time actually went. `traversal` with `total_ms: 354` and `self_ms: 79` does
almost nothing itself; dig into its children. Never add `total_ms` across rows:
they nest inside one another.

**Warnings inside `window`.** If `start_anchor.matches == 0`, the window
expanded to the whole trace and none of the numbers are about your scenario.
Fix the config rather than hunting a problem. Same for `process_alternatives` —
you may be analysing the wrong process.

## From a finding to the code

1. A firing detector gives you a `location` — a slice or thread name.
2. The `domains` section of `echolot.yml` maps that name to a module and file.
3. Not in `domains`? Grep the repository for the slice name: it is a string
   literal inside `trace("...")`, survives minification, and is found exactly.
4. Nothing found? The slice is most likely a system one (`bindApplication`,
   `Choreographer#doFrame`, `binder transaction`). See `references/naming.md`.

When `uninstrumented_cpu` fires there is no code behind the finding by
definition: the thread burned CPU with no instrumentation. That is an address
for adding `trace{}`, not the location of a bug.

## Boundaries

The tool **localises one specific regression**: you know "it was 3 s, now it is
7 s" and need to find where. It does not search for the unknown across a pile
of traces.

Networking in benchmarks is mocked on purpose — we hunt problems in code, not
network speed.

Thresholds are tied to a device and a scenario. When either changes, run
`echolot calibrate` on healthy runs instead of nudging numbers by hand.

When the question is about the tool rather than the app — "how did that
session go, what should change in echolot" — that is `/echolot reflect`
(the `echolot-reflect` skill), not a hunt.

## References

- `references/report.md` — the `report.json` schema, all detectors, what each column means
- `references/config.md` — the `echolot.yml` sections, what the code reads and what it does not
- `references/naming.md` — how ART names GC, locks and binder; facts from live Android 14
- `references/collect.md` — capturing a trace: perfetto and adb commands, verified in practice
