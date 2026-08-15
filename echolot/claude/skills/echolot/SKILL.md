---
name: echolot
description: Localise Android performance regressions from a Perfetto trace — from the metric down to a place in the code. Use when cold start regressed, scrolling stutters, TTI grew, a benchmark dropped, or there is a .perfetto-trace to analyse. Also for questions like "why is startup slow", "where does main thread time go", "what is burning CPU".
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

## The order of work

```bash
echolot doctor                  # does the environment compute correctly?
echolot analyze <trace> -c echolot.yml
```

`analyze` writes `.echolot/out/report.json` (for you) and `report.md` (for
humans), next to the config. Read the **json** — it has a stable schema.

No config? Go to `/echolot-setup`. No idea what is in the trace?
`echolot probe <trace> --process '<package>*'`.

Which door is which — three things with similar names:

- `echolot init` (the CLI) installs or updates **this** layer in the project;
  `doctor` says when the copy here is behind the package. `/echolot init`
  means run that command, not build a config.
- `/echolot-setup` builds `echolot.yml`.
- `/echolot-hunt` finds the cause of one regression, in a subagent.

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
session go, what should change in echolot" — that is `/echolot-reflect`, not
this skill.

## References

- `references/report.md` — the `report.json` schema, all detectors, what each column means
- `references/config.md` — the `echolot.yml` sections, what the code reads and what it does not
- `references/naming.md` — how ART names GC, locks and binder; facts from live Android 14
- `references/collect.md` — capturing a trace: perfetto and adb commands, verified in practice
