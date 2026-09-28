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

**The investigation.** Record what is being chased before you measure or
record anything for it:

```bash
echolot hunt "cold start was 3s, now 7s" --since "the tab redesign"
```

That pushes the previous set of traces aside so this hunt cannot inherit them,
and reports whether the last one left temporary markers in the sources. Every
round and every report from here on is filed under it — which is what
`echolot compare` reads from the second round on; with nothing open it has
nothing to compare.

Open nothing when you are carrying on the one already open: the human chose
to (`echolot hunt --resume` has run), or `echolot hunt` shows an open
investigation that the human's words continue. When you cannot tell, ask.

The traces it pushes aside go to a directory beside them, named on stderr:
`previous run set aside: N trace(s) → .echolot/traces/<scenario>-<stamp>/`.
Nothing is deleted. If the human recorded traces for **this** question before
asking, they moved too and are still its evidence: analyse that directory,
and do not record them again.

If `echolot hunt` exits 2 with `error: echolot.yml does not load: …`, it
opened nothing and moved no traces aside. Show that error to the human as it
is, ask them to fix `echolot.yml`, and stop — record nothing, and do not
start the loop.

**Traces.** None? `echolot collect -c echolot.yml -n 5`, once the
investigation is open. Repeats are not belt-and-braces: a single run cannot
tell a regression from a spike. With a runner config that is new or just
changed, make the first run a cheap one where the mode allows it: `-n 1` in
`launch` and `command` mode. In `gradle` mode the macrobenchmark sets its own
iteration count and `-n` never reaches it, so the first `collect` records the
whole set — read what it printed before running it again. A wrong variant
fails after the whole build either way. While it runs, `echolot` has a
`collect` line saying how far it got, and a failure's sentence is on it.

**No instrumentation at all?** `echolot domains --root .` says. If there is
none, the report will name system slices and threads, and your first move is
`echolot mark`, then `echolot mark --apply`, then one re-record.

## The loop

```
round = 1

1. echolot analyze <traces> -c echolot.yml
   echolot report                       what fired, one line per detector
   echolot report -d <id> --top 5       one detector's rows; --json for the rest
   (the views of .echolot/out/report.json — do not cut the json up by hand)

2. check the config before concluding anything:
   window.start_anchor.matches == 0     → the anchor missed; the window is not the scenario
   window.process_alternatives present  → possibly the wrong process
   thresholds calibrated on these runs  → analyze --defaults before believing silence
   everything silent on a plausible window → exit: "clean"
   → in these cases fix the config, do not hunt a problem

3. hypotheses: firing detectors → domains → files
   localised to a place in the code → exit with the finding

4. from round 2 on, before anything else:
   echolot compare
   the previous round against the one just recorded, sorted by what moved.
   New rows with the AGENTTMP_ prefix are your own markers breaking down a
   blind spot; the warning at the top names them. A row that grew with
   Ranges `apart` is a real move, `overlap` means the repeats disagree by
   more than the medians moved — record another round before concluding.

5. otherwise pick a blind spot (usually uninstrumented_cpu):
   a thread the JDK named — pool-N-thread-M, Thread-N → echolot mark --pools
     first: name the pool, re-record, and the row stops being anonymous
   no instrumentation → echolot mark, then echolot mark --apply
   a named place → a few AGENTTMP_ markers around it, by hand
   re-record, round += 1
   round > loop.max_rounds (config, default 3) → exit with an interim conclusion

6. cleanup: remove every AGENTTMP_ marker — always
```

Stopping is a number from the config, not a feeling. Without a limit you will
spin and burn context.

## Commands you will reach for

```
echolot analyze <traces> -c echolot.yml        report → .echolot/out/
echolot analyze … --defaults                   every detector, built-in thresholds
echolot analyze … --set main_thread_block.min_slice_ms=4
                                               one threshold, this run only
echolot report                                 what fired, one line per detector
echolot report -d <id> --top 5                 one detector's rows, evidence cut short
echolot report --markers                       every AGENTTMP_ name and domains name,
                                               measured across the runs
echolot compare                                previous round vs the latest — what moved
echolot compare --hunt <n>                     an investigation's first report vs its last
echolot compare <a.json> <b.json>              or name the two reports
echolot names <trace> --grep <regex> --json    one family of slice names, whole
echolot domains --root .                       slice name → file
echolot mark [--apply|--remove]                first markers, and taking them out
echolot mark --pools                           threads and pools the JDK will name
echolot hunt --show <n>                        this investigation: rounds, reports, evidence
```

Do not write a config of your own. `--defaults` and `--set` exist so you do not
have to, and both leave a mark in the report.

Do not re-record over the traces you analysed — they are the baseline.
`echolot collect` sets them aside for you and records where they went.

## Rules for temporary instrumentation

**Write only inside `instrumentation.allowed`** from `echolot.yml` — the
places the human said code may be written — and never into generated code,
`build/` output or a third-party module.

**Every temporary slice carries the prefix** — `AGENTTMP_` unless
`instrumentation.temp_prefix` says otherwise:

```kotlin
androidx.tracing.trace("AGENTTMP_collection_mapping") { … }
```

The prefix is what makes cleanup deterministic: a grep and a delete, rather
than remembering what you added.

**Name a marker after the work it wraps, never after where you put it.** Two
markers around the same work must end up with the same name —
`AGENTTMP_fill_presets`, not `AGENTTMP_fill_presets_v6`. `repeated_work` finds
the same named work entered from two callers; named by call site, the two get
two names and there is nothing to compare. When you need to say where a call
came from, put a second marker around the caller.

**One round, one blind spot**, five to seven slices around the boundaries of
the suspicious stretch — instrumentation costs time.

**Before you finish, grep for the prefix** — on success and on running out of
rounds alike. `grep -rn AGENTTMP_ <source_root>` must come back empty, and
your conclusion says so. `echolot mark --remove` takes out what
`mark --apply` put in; what you added by hand goes by hand.

## What to report back

```
Place:         <file:line or module>
Evidence:      <detector, numbers from the report — measured, nothing else>
Mechanism:     <why this costs that much time; mark a step you did not
               measure (inferred), and one you could not check (gap)>
Suggestion:    <what to do>
Confidence:    high | medium | low — and why
Ruled out:     <what you checked and did not carry to a cause, strongest
               evidence first — or `nothing else was checked`>
Also measured: <every marker you planted, one line and one number each>
Cleanup:       temporary instrumentation removed | none was added
```

`Also measured` is every number you took, whether or not it turned out to be
the answer: a measurement you hold and do not pass on is one nobody has.
`Ruled out` saves the next hunt a round spent where you already looked.

Close the investigation with what it came to — every time, an interim
conclusion or "clean" included:

```bash
echolot hunt --done "TextLayout:initLayout on the main thread, :feature:profile — confidence high"
```

Left open, a finished hunt stays the open one: every later `analyze` is filed
under it, and the next visit is asked whether to carry on with work that is
over.

If the finding is about the device rather than the code — `runnable_starvation`
on an emulator or a loaded machine — say the run is worth repeating on real
hardware before anything is fixed.

If confidence is low, say so rather than smoothing it over. An interim
conclusion with an honest assessment beats a confident look at weak data.
