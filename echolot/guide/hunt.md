# The hunt: from a report down to a place in the code

Run this when `echolot status --next` says `hunt`, or when a human describes a
regression.

## Before you start

**The environment.** `echolot doctor -q`. A non-zero exit means there is no
point going further — no report from that environment can be trusted. Show
what failed and stop.

**The three facts.** You need all three, in the human's own words:

1. what regressed and against what — "it was 3 s, now it is 7 s"
2. which traces show it
3. **after which change** — a commit, a dependency bump, a date, "since the
   tabs were redesigned"

Ask for the third one explicitly, even when the first two are clear.
"Unknown" is an acceptable answer and goes into the record as such; an omitted
one is not. The tool localises a **specific** regression well and searches for
the unknown badly — without the change you will hunt everything that looks
expensive and come back with a guess.

**The investigation.** Record what is being chased before you measure:

```bash
echolot hunt "cold start was 3s, now 7s" --since "the tab redesign"
```

That pushes the previous set of traces aside so this hunt cannot inherit them,
and reports whether the last one left temporary markers in the sources. Every
round and every report from here on is filed under it.

**Traces.** None? `echolot collect -c echolot.yml -n 5`. Repeats are not
belt-and-braces: a single run cannot tell a regression from a spike.

**No instrumentation at all?** `echolot domains --root .` says. If there is
none, the report will name system slices and threads, and your first move is
`echolot mark`, then `echolot mark --apply`, then one re-record.

## The loop

```
round = 1

1. echolot analyze <traces> -c echolot.yml
   read .echolot/out/report.json

2. check the config before concluding anything:
   window.start_anchor.matches == 0     → the anchor missed; the window is not the scenario
   window.process_alternatives present  → possibly the wrong process
   thresholds calibrated on these runs  → analyze --defaults before believing silence
   everything silent on a plausible window → exit: "clean"
   → in these cases fix the config, do not hunt a problem

3. hypotheses: firing detectors → domains → files
   localised to a place in the code → exit with the finding

4. otherwise pick a blind spot (usually uninstrumented_cpu):
   no instrumentation → echolot mark, then echolot mark --apply
   a named place → a few AGENTTMP_ markers around it, by hand
   re-record, round += 1
   round > loop.max_rounds (config, default 3) → exit with an interim conclusion

5. cleanup: remove every AGENTTMP_ marker — always
```

Stopping is a number from the config, not a feeling. Without a limit you will
spin and burn context.

## Commands you will reach for

```
echolot analyze <traces> -c echolot.yml        report → .echolot/out/
echolot analyze … --defaults                   every detector, built-in thresholds
echolot analyze … --set main_thread_block.min_slice_ms=4
                                               one threshold, this run only
echolot names <trace>                          slice names — --top 200 --min-ms 0 to see AGENTTMP_
echolot domains --root .                       slice name → file
echolot mark [--apply|--remove]                first markers, and taking them out
echolot hunt --show <n>                        this investigation: rounds, reports, evidence
```

Do not write a config of your own. `--defaults` and `--set` exist so you do not
have to, and both leave a mark in the report.

Do not re-record over the traces you analysed — they are the baseline.
`echolot collect` sets them aside for you and records where they went.

## What to report back

```
Place:       <file:line or module>
Evidence:    <detector, numbers from the report>
Mechanism:   <why this costs that much time>
Suggestion:  <what to do>
Confidence:  high | medium | low — and why
Cleanup:     temporary instrumentation removed | none was added
```

Close the investigation with what it came to:

```bash
echolot hunt --done "TextLayout:initLayout on the main thread, :feature:profile"
```

If the finding is about the device rather than the code — `runnable_starvation`
on an emulator or a loaded machine — say the run is worth repeating on real
hardware before anything is fixed.

If confidence is low, say so rather than smoothing it over. An interim
conclusion with an honest assessment beats a confident look at weak data.
