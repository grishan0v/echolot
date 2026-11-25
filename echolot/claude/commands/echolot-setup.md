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
| `scenario.start` / `end` | the window. An anchor is a slice name as `probe` prints it, or a glob over names |
| `scenario.window` | `launch`, in place of the two anchors: how the app opens, and the trace says where it ends |
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
  `release` over anything debuggable, which skews everything. Where the app
  module applies `androidx.baselineprofile`, the build types that plugin
  adds are variants too, though no script names them: `benchmarkRelease`,
  release-like and profileable, is the one to measure on, and
  `nonMinifiedRelease`, built without R8 to generate profiles, is not. And whether
  that one is minified: its build type sets `minifyEnabled`, or starts with
  `initWith` from one that does. Then the skeleton names `project.mapping`,
  the R8 mapping AGP writes for that variant when it is built. `gradle` mode
  builds it with the first collect; in `launch` mode it is there once the
  human builds the variant. Until then `analyze` says so at the top of the
  report and leaves R8's names as they are. What the project's own build
  code decides is read too, `buildSrc` or an included `build-logic`: a
  suffix or an applicationId that is a constant or an enum entry's argument,
  and flavours a convention plugin registers for each entry of an enum; the
  lines under the table say where each came from. A part set any other way
  shows as `+ ?` in the table and in a note; when the variant to measure is
  one of those, the draft's package says `_source: default` — ask the human
  which package is on the phone (`adb shell pm list packages <base>`)
  rather than taking the base name. With several packages, the line under
  the table lists them all
- the module with `MacrobenchmarkRule`, its test classes and methods, the
  package it drives (where its code takes the package at run time, the one
  the app module its `targetProjectPath` names installs as), the
  `TraceSectionMetric` names it measures (anchor
  candidates, and `domains` entries), its runner arguments, and the gradle
  tasks by variant. Each class says which kind it is: it measures frames
  and not the launch (`FrameTimingMetric`), it measures the launch
  (`StartupTimingMetric`), it measures a section (`TraceSectionMetric`), it
  only sets `StartupMode`, or its own file names none of these
- the devices attached
- a config to start from — the skeleton the questions below refine. What
  it read off the tree says `_source: derived` with its `_evidence` (the
  package and process, a minified build's mapping, an end anchor taken from
  a `TraceSectionMetric`);
  what it could not read is an engine default and says `_source: default`
  (the start anchor, and an end it did not find, written `"?"`). For an app
  with a launcher Activity and no macrobenchmark there is nothing to find:
  the skeleton holds `window: launch` with `runner.mode: launch` and
  `runner.activity`, the app's launcher by its full class name (a debug
  build with a leak detector has a second one, and without the key the
  device answers with its chooser), the scenario that needs no end anchor,
  and its `_evidence` says the trace decides the end. A benchmark class that measures the launch and names no
  section gets the same window with `runner.mode: gradle`; one that only
  sets `StartupMode` keeps the `"?"`. A class that measures frames gets
  `window: whole`: each trace it writes is one repeat of its measure block,
  and the sections its test writes, `traced(…)` and the like, are in the
  benchmark's own process, which no window is read from. A module with
  several classes is a scenario for each under `scenarios:`, named after
  the class, its class filter in its own `runner.gradle_args`; the cold
  start is first, the one taken without `--scenario`. Where the benchmark
  module's own package begins with the app's, as
  `com.example.app.benchmark` does, `process` is the app's exact name, or
  stays the mask when a manifest declares another process of the app, and
  its `_evidence` says which.
  The
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

`probe` gives processes, threads (sorted by CPU, so blind spots show at once),
the longest slices as anchor candidates, and the start candidates: the
platform's own events a journey begins with — a tap, an intent, an activity,
a cold start — each with the time `analyze` would open the window at. `names`
shows whether the detector masks land on the names this device produces.

Pick the process deliberately: `com.example.app*` also catches `:pushservice`
and `:webview`, and a macrobenchmark's own `com.example.app.benchmark`. A candidate whose thread reads `(async)` is a
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
`max_ms` it prints; an `(async)` section is the usual end. That table gives
no start times, so the question names none.

1. **Which scenario** are we analysing (from the benchmarks found, or cold start)
2. **What counts as the end** of the scenario — the only genuinely semantic
   question, not derivable from the trace. For how the app opens it has a
   default, and you do not ask it: see below
3. **The budget** — `scenario.budget_ms`, the time the human expects the
   scenario to take. The report carries it and the plain reading says how the
   launch stands against it, but nothing fails over it, and one probe trace
   is no baseline to derive it from, so take the human's number or leave the
   key out
4. **May we write into the code** for temporary instrumentation, and where

And which process, when the probe shows several plausible ones: a fifth
question, asked only then.

You **do not decide** — you present candidates and ask for confirmation. That
makes it impossible to get wrong, and the decision is fixed in the config for
good.

### When the scenario is how the app opens

Then question 2 is answered by the trace, and you do not pick an end anchor.
Keep `scenario.window: launch` as `scan` wrote it, or write it yourself in
place of `start` and `end` when the human names a launch as the scenario:

```yaml
scenario:
  name: coldStart
  window: {name: launch, _source: derived, _evidence: "launcher Activity in app/src/main/AndroidManifest.xml"}
```

A startup macrobenchmark records this scenario as `launch` mode does: every
trace it writes holds the launch it drove. `scan` writes the window for a
class that measures the launch, with `StartupTimingMetric in <file>` as its
evidence, and the `gradle` runner stays.

The window starts at the intent and ends at the launch's end, the first
frame, or at the app's `reportFullyDrawn()` call when that comes later. After
the dry run below, read `window.defined_by` in the report and tell the human
in one sentence what closed the window: `launch` is the first frame,
`launch+reportFullyDrawn` the app's own call.

Ask about the end only if the human says the app is not ready at that
moment. That happens with an app that draws a placeholder first and never
calls `reportFullyDrawn()`: its window closes at the first frame, and what
loads after it is outside. The answer is then an end anchor from the probe,
with `start` and `end` in place of `window`, or the call added to the app:
their choice, not yours.

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
runs to where the process's last slice ends, which can be long after the
journey did, and the report says so in its header.

## Verification instead of trust

After generating, do a dry run:

```bash
echolot analyze <trace> -c echolot.yml
```

Look not at the findings but at `window`:

- `start_anchor.matches == 0` — the anchor missed, the config is wrong
- with `window: launch` there are no anchors to check: `defined_by` says what
  closed the window, and the window is about as long as the startup line
  says the launch was. `analyze` stopping at `no launch of <process>` means
  the app was not started inside the recording: use `runner.mode: launch`.
  When it says a permission dialog took the launch, run the `pm grant` it
  names (or tap Allow on the phone) and record again
- the window is nearly as long as the recording — the anchors did not work
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
to the human and explain what changed relative to the defaults. A threshold
whose healthy tail is lower than the default keeps the default; the line says
by how much. Below it, the section tries itself on the same runs and says how
many rows `analyze` would report with it, against now. Healthy runs should
come out quiet: if a detector goes from a few rows to many, the section is too
low for it, so leave that detector's number out.

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

## Adding a scenario

A project that has a config may want another journey measured, a feed scroll
beside the cold start. That is no new setup: the scenario that is there stays
as it was, and the new one gets a window and a runner of its own. A hunt
comes here too, when the question is about a journey no scenario measures:
a list that stutters, asked of a config that records only a cold start, is
not hunted on the cold start (`echolot hunt --scenario` with a name the
config lacks refuses with the two shapes below).

1. **Move the config to `scenarios:`.** The `scenario:` block goes under
   `scenarios:`, keyed by its `name`, with `name` dropped and everything else
   kept as it is, `_source` and `_evidence` included:

   ```yaml
   scenarios:
     coldStart:
       start: {name: "bindApplication", _source: default}
       end: {name: "Screen.firstFrame", _source: confirmed_by_user, _evidence: "probe: …"}
     feed_scroll:
       runner:
         mode: command
         command: "adb shell input swipe 500 1500 500 300 600"
         reset_policy: none
   ```

   The key is the name its traces and reports carry, so the scenario that was
   there keeps them. A scenario takes `start` and `end` or `window`,
   `budget_ms`, `runner` and `detectors`, and nothing else. The top-level `detectors:` stays where
   it is. Whatever was calibrated there was calibrated on the scenario that
   was there, and the new one runs on it until step 5.
2. **What drives the new journey.** Its `runner:` holds only the keys that
   differ from the top-level one. A journey a script paces takes one of two
   shapes:
   - `mode: command` with the command that drives the screen,
     `adb shell input swipe 500 1500 500 300 600` and the like — 400 ms or
     more a swipe, since a shorter one lands as a tap on a loaded phone —
     `reset_policy: none`, and `duration_ms` longer than the script;
   - the project's own frame-timing macrobenchmark (`FrameTimingMetric`),
     `mode: gradle` with its class in `gradle_args`, when `scan` lists one
     among the benchmarks. Its draft already holds it as a scenario of its
     own, `window: whole`, beside the cold start: keep that one.

   When the repository does not say, ask the human: a macrobenchmark test,
   a UI test, an `adb` command.
3. **A probe of it**, with no anchors yet, so its window is everything the
   process did in the recording:
   `echolot collect --scenario feed_scroll -n 1`, then
   `echolot probe <its trace> --process '<package>*'`.
4. **Where it ends, with the human**, as question 2 asks it: the options are
   the probe's longest slices, and the answer goes under the new scenario as
   `end`, with `_source: confirmed_by_user`. **Where it begins**, the same
   way, from probe's "Start candidates": the platform's own events, earliest
   first, written as `start` exactly as the first column shows them, a tap
   as `deliverInputEvent*`. Offer them with `first_at_ms`, the time the
   window would open at, beside the line under the table that says where it
   opens with no start: the process's first slice, often hundreds of
   milliseconds before the command acted. The earliest is the usual answer.
   "None" stays an option; when the list is empty, `echolot names <trace>
   --grep <word>` finds a section of the app's own to start at.
   A scroll a script drives may keep neither: the whole recording is then
   the scenario, and its frames, the late ones among them, are what it is
   held to.

   ```
   Where the journey begins — Android's own events in the probe, earliest first:
     1) deliverInputEvent*   a tap, a key                 at 650.0 ms   n 4
     2) activityPause        the activity in front paused at 730.0 ms   n 1
     3) activityStart        an activity was created      at 745.0 ms   n 1
     4) none — the window opens at the process's first slice, at 40.0 ms
   Where does it begin?  [1]
   ```
5. **Its thresholds**, once there are healthy runs of it, as "Thresholds"
   above says: `echolot calibrate --scenario feed_scroll <its traces>` prints
   `scenarios.feed_scroll.detectors`, which goes under the new scenario. A
   number the top-level section holds and these runs could not give comes
   back as the default, so the first scenario's numbers do not reach the new
   one. Until then the new scenario runs on the top-level section, calibrated
   on the other journey: say so to the human.

`echolot status` then lists both scenarios, a line each, and marks the ones
with thresholds of their own.

## When you are done

```bash
echolot                 # should now say next: hunt
```

`echolot.yml` is committed — it describes the project. Device serials and the
path to a binary go in `local.yml`, which is not.
