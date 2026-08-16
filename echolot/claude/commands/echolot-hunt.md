---
description: Find the cause of a performance regression — runs perf-hunter in its own context. /echolot routes here once the config exists.
---

Find the cause of a performance regression.

## Before calling the agent

**Environment.** `echolot doctor -q`. A non-zero exit means there is no point
going further: no report from that environment can be trusted. Show what
exactly failed. If the second line says the `.claude/` layer is stale, run
the `echolot init` it names before anything else — the agent you are about
to launch reads that layer. Note the time: the agent is told doctor passed
and when, so it does not run it again.

**Config.** No `echolot.yml` in the root? Go to `/echolot-setup` and come back.
A loop on an invented config burns rounds for nothing.

**Traces.** None? Capture them with `echolot collect -c echolot.yml -n 5`.

**Thresholds.** Read `config` and `detectors[].params_source` in the last
`report.json`, or the `Config:` line in `report.md`. If the thresholds were
calibrated on the very runs that hold the regression, the report is clean by
construction. Say so, and pass the agent `--defaults` for its first look.

**The three facts.** Before calling the agent you must hold, in the human's
words:

1. what regressed and against what — "it was 3 s, now it is 7 s"
2. which traces show it
3. **after which change** — a commit, a dependency bump, a date, "since the
   redesign of the tabs"

Ask for the third one explicitly, with `AskUserQuestion`, even when the first
two are clear. "Unknown" is an acceptable answer and goes into the prompt as
such — an omitted one is not. The tool localises a **specific** regression
well and searches for the unknown in general badly; without the change the
agent hunts everything that looks expensive and comes back with a guess.

## The run

Hand the work to the `perf-hunter` subagent. This is not a formality: the loop
generates a lot of mess — raw output, repository searches, instrumentation
diffs, several iterations. In the main context that fills the window within two
rounds, and then the very instability this whole thing exists to remove sets
in.

Pass the agent:

- the path to the trace (or several)
- what regressed and against what: "it was 3 s, now it is 7 s"
- after which change — or the word "unknown", said explicitly
- whether the config's thresholds are trustworthy for this hunt (see above),
  and if not, that it should start with `analyze --defaults`
- that doctor passed, and at what time — so the agent skips its own run
- whether the project has any instrumentation (`echolot domains --root .`
  says). If it has none, say so and say what follows: the report will name
  system slices and threads, and the agent's first move is `echolot mark`
  (then `--apply`) and one re-record; reading the app to find where the
  time goes comes after the report has named a place.

The window is the budget. In two hunts out of two the agent spent forty to
sixty percent of it reading sources by hand; `echolot reflect` shows the
split (`window fed by:` in the Subagent section) and flags it. `echolot
mark` exists for exactly that step; if the share stays high with it in
place, the report says which reads it did not replace.

Do not re-record in the main context, and do not move the traces the agent
is about to compare against. If a re-record is needed, it happens inside the
loop, and the agent copies the current set into `.echolot/traces/<label>/`
first — a benchmark's output directory is cleaned by gradle on the next run,
and a rename inside it goes with the cleaning.

## What to show the human

The agent returns a short conclusion. Show it as it is; do not retell it in
your own words and do not pad it with guesses.

Check two things separately:

**Cleanup.** The answer must state whether the temporary instrumentation was
removed. If it is unclear, check yourself: `grep -rn AGENTTMP_ <source_root>`.

**Confidence.** If it is low, say so to the human rather than smoothing it
over. An interim conclusion with an honest assessment is more useful than a
confident look on weak data.

If the finding is about the device rather than the code — for instance
`runnable_starvation` on an emulator or a loaded machine — warn that the run is
worth repeating on real hardware before fixing anything.
