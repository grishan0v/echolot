---
description: Build echolot.yml for this project — repository scan, a probe trace, four questions
---

Your job is to assemble `echolot.yml` in the project root.

The guiding principle: **the user does not open the config**. You obtain
everything obtainable and ask only about what exists neither in the repository
nor in the trace. Of roughly 25 fields, two need a human decision.

## The order: actions first, conversation after

Do not ask anything before you have data. By the time of the first question you
should be holding real options from a real trace, not guesses.

### 1. Scan the repository

- `applicationId` and `namespace` — from `build.gradle.kts`
- the process name — from the manifest, `android:process` if present
- `<profileable android:shell="true" />` in the manifest: **without it there
  will be no application slices in the trace** — say so immediately
- a module with `MacrobenchmarkRule` — if there is one, that is the future
  `runner`
- existing instrumentation: run `echolot domains --root .`
- paths for `instrumentation.allowed` — sources, not `build`, not `generated`

No instrumentation at all is normal and is an important fact. `echolot domains`
prints the coverage and the modules with the most code and none of it; show
that to the user and offer a skeleton later, after the first report.

### 2. A probe trace

Capture a cold start with `echolot collect -c echolot.yml -n 1`, or by the
recipe in `references/collect.md` if there is no config yet. Check that a
device is connected (`adb devices`).

### 3. Reconnaissance

```bash
echolot probe <trace> --process '<package>*'
echolot names <trace> --process '<package>*'
```

`probe` gives processes, threads (sorted by CPU, so blind spots show at once)
and anchor candidates. `names` shows whether the detector masks land on the
names this device produces.

### 4. Four questions

Each one a choice among options pulled from a real trace. Each with a default,
so it can be answered by pressing Enter.

```
Ran a cold start. Last slices before the first frame:
  1) Choreographer#doFrame*        @ 772 ms
  2) activityResume                @ 731 ms
  3) Compose:recompose             @ 690 ms
What counts as "the app is ready to use" for you?  [1]
```

1. **Which scenario** are we analysing (from the benchmarks found, or cold start)
2. **What counts as the end** of the scenario — the only genuinely semantic
   question, not derivable from the trace
3. **The budget** — propose `baseline * 1.1`
4. **May we write into the code** for temporary instrumentation, and where

You **do not decide** — you present candidates and ask for confirmation. That
makes it impossible to get wrong, and the decision is fixed in the config for
good.

## Provenance

Every field justified by a finding:

```yaml
scenario:
  end:
    name: "Choreographer#doFrame*"
    _source: confirmed_by_user
    _evidence: "probe: first frame after bindApplication, 772 ms"
```

`_source`: `derived` — you worked it out, `confirmed_by_user` — a human
confirmed it (untouchable), `default` — an engine default.

Nothing found? Write `null` and say so out loud. A plausible invented name is
worse than an honest gap: it will break the window silently.

## Verification instead of trust

After generating, do a dry run:

```bash
echolot analyze <trace> -c echolot.yml
```

Look not at the findings but at `window`:

- `start_anchor.matches == 0` — the anchor missed, the config is wrong
- the window is nearly the whole trace — the anchors did not work
- **every detector screaming at once** — almost certainly the process or the
  boundaries are off

In any of those cases show the problem and ask again. Entering the main loop
with a garbage config costs more than one extra question.

## Thresholds

Do not invent numbers. The detector defaults work, and once there are three to
five healthy runs of one scenario:

```bash
echolot calibrate run*.perfetto-trace -c echolot.yml
```

The command prints a `detectors:` section with the reasoning attached. Show it
to the human and explain what changed relative to the defaults.

**Healthy means known-good, not merely current.** If the human came because
something regressed, the traces you have are the regression: thresholds
derived from them sit above the problem, and the hunt that follows reports a
clean run. Ask before calibrating: *"Are these runs from a build you consider
healthy? If not, I keep the defaults and calibrate later on a good build."*
Leave the `detectors:` section out of the config until then. When the answer
is unclear, do not calibrate — a config with defaults is honest, a config
calibrated on the regression is a trap.

Whoever hunts later can still see what the shipped numbers say without
touching the config: `echolot analyze --defaults` (every detector, built-in
thresholds) and `--set detector.param=value` (one threshold, one run). Both
leave a mark in the report.
