---
description: Build echolot.yml for this project — repository scan, a probe trace, four questions. /echolot routes here when there is no config.
---

Your job is to assemble `echolot.yml` in the project root.

The guiding principle: **the user does not open the config**. You obtain
everything obtainable and ask only about what exists neither in the repository
nor in the trace. Of roughly 25 fields, four need a human decision.

| section | what it decides |
|---|---|
| `project.process` | which process is measured; an app usually has several |
| `scenario.start` / `end` | the window. Anchors are globs over slice names |
| `runner` | who drives the scenario: `launch`, `command` or `gradle` |
| `domains` | slice name → module and file, for turning a finding into a place |
| `instrumentation.allowed` | where the hunt may write its temporary markers, and nowhere else |

## The order: actions first, conversation after

Do not ask anything before you have data. By the time of the first question you
should be holding real options from a real trace, not guesses.

### 1. Scan the repository

```bash
echolot scan
```

One command, not a reading of the build scripts. It prints, each with where
it came from:

- the app module, its `applicationId` and `namespace`, `android:process` if
  the manifest sets one, and whether `<profileable android:shell="true" />`
  is there — **without it there will be no application slices in the
  trace**; the note says so, repeat it to the human immediately
- the variants — flavour × build type, what each installs as, and which one
  to measure on: a `benchmark` build type (release-like, profileable) over
  `release` over anything debuggable, which skews everything
- the module with `MacrobenchmarkRule`, its test classes and methods, the
  package it drives, the `TraceSectionMetric` names it measures (anchor
  candidates, and `domains` entries), its runner arguments, and the gradle
  tasks by variant
- the devices attached
- a config to start from — the skeleton the questions below refine. What
  it read off the tree says `_source: derived` with its `_evidence` (the
  package and process, an end anchor taken from a `TraceSectionMetric`);
  what it could not read is an engine default and says `_source: default`
  (the start anchor, and an end it did not find, written `"?"`). The
  `runner` section carries `_evidence` alone, and `instrumentation` neither
  — its `allowed` list is every module's `src/main` the scan found, test
  modules left out, for the human to confirm in question 4

Do not read `build.gradle` yourself for any of this; `scan --json` has the
facts if a value needs checking. Then existing instrumentation:
`echolot domains --root .`.

No instrumentation at all is normal and is an important fact. `echolot domains`
prints the coverage and the modules with the most code and none of it; show
that to the user. Before saying it, read the probe: an app whose markers are
`Trace.beginAsyncSection` spans shows them in `probe`'s `async` column and as
`(async)` rows in `names`, and those are instrumentation — anchors, in
particular. `domains` maps them when their names are `const val`s passed to
the app's own wrapper; a hint ending in `via X` is that case. Then run `echolot mark`: it lists the entry points the
first markers would go to — from the manifest and the SDK, with a source on
each row — and, when the app module uses Compose and its build script does
not name `androidx.compose.runtime:runtime-tracing`, adds a note saying so.
An app without Compose gets no such note. Show the list; do not apply
anything during setup. The hunt applies it when the first report has nothing
of the application's to name.

### 2. A probe trace

Capture a cold start with `echolot collect -c echolot.yml -n 1`, or by the
recipe `echolot guide collect` prints if there is no config yet. Check that a
device is connected (`adb devices`). `-n 1` holds in `launch` and `command`
mode; in `gradle` mode the macrobenchmark sets its own iteration count and
`-n` never reaches it, so the probe is the whole set it records.

### 3. Reconnaissance

```bash
echolot probe <trace> --process '<package>*'
echolot names <trace> --process '<package>*'
```

`probe` gives processes, threads (sorted by CPU, so blind spots show at once)
and anchor candidates. `names` shows whether the detector masks land on the
names this device produces.

Pick the process deliberately: `com.example.app*` also catches `:pushservice`
and `:webview`. A candidate whose thread reads `(async)` is a
`Trace.beginAsyncSection` span on no thread — usually the app's own marker,
and almost always the right end anchor. It is an anchor and nothing else: no
detector reads it.

### 4. Four questions

Each one a choice among options pulled from a real trace. Each with a default,
so it can be answered by pressing Enter.

```
Ran a cold start. The longest slices `echolot probe` lists:
  1) collection_load   (async)           n 1   max 867.65 ms
  2) traversal         com.example.app   n 7   max 116.5 ms
  3) activityStart     com.example.app   n 1   max 48.0 ms
What counts as "the app is ready to use" for you?  [1]
```

The options are rows of probe's "Longest slices" table, with the thread and
`max_ms` it prints; an `(async)` section is the usual end. Probe gives no
start times, so the question names none.

1. **Which scenario** are we analysing (from the benchmarks found, or cold start)
2. **What counts as the end** of the scenario — the only genuinely semantic
   question, not derivable from the trace
3. **The budget** — propose `baseline * 1.1`
4. **May we write into the code** for temporary instrumentation, and where

And which process, when the probe shows several plausible ones: a fifth
question, asked only then.

You **do not decide** — you present candidates and ask for confirmation. That
makes it impossible to get wrong, and the decision is fixed in the config for
good.

## Provenance

Every field justified by a finding:

```yaml
scenario:
  end:
    name: "collection_load"
    _source: confirmed_by_user
    _evidence: "probe: the longest slice, async, n 1, max 867.65 ms"
```

`_source`: `derived` — you worked it out, `confirmed_by_user` — a human
confirmed it (untouchable), `default` — an engine default.

Nothing found? Write `null` and say so out loud. A plausible invented name is
worse than an honest gap: it will break the window silently. For an anchor,
`end: null`, or `name: null` in the block above, means no anchor: the window
runs to the end of the trace.

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
echolot calibrate .echolot/traces/<scenario>_iter*.perfetto-trace -c echolot.yml
```

`collect` writes `<scenario>_iterNNN.perfetto-trace` into `.echolot/traces`; a
set already put aside is under `.echolot/traces/<scenario>-<stamp>/`.

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

## When you are done

```bash
echolot                 # should now say next: hunt
```

`echolot.yml` is committed — it describes the project. Device serials and the
path to a binary go in `local.yml`, which is not.
