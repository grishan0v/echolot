---
description: Find the cause of a performance regression — runs perf-hunter in its own context
---

Find the cause of a performance regression.

## Before calling the agent

**Environment.** `echolot doctor`. A non-zero exit means there is no point
going further: no report from that environment can be trusted. Show what
exactly failed.

**Config.** No `echolot.yml` in the root? Go to `/echolot-setup` and come back.
A loop on an invented config burns rounds for nothing.

**Traces.** None? Capture them with `echolot collect -c echolot.yml -n 5`.

## The run

Hand the work to the `perf-hunter` subagent. This is not a formality: the loop
generates a lot of mess — raw output, repository searches, instrumentation
diffs, several iterations. In the main context that fills the window within two
rounds, and then the very instability this whole thing exists to remove sets
in.

Pass the agent:

- the path to the trace (or several)
- what regressed and against what: "it was 3 s, now it is 7 s"
- if known, after which change

That last one matters more than it seems. The tool localises a **specific**
regression well and searches for the unknown in general badly.

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
