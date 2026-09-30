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

The loop itself — the protocol, the commands you will reach for, the rules for
temporary markers and the eight fields of the conclusion — is
`echolot guide loop`. It is the text a Claude Code subagent is given, from the
same file.

**Hand it to a subagent when your host can start one.** The loop fills a
window with raw output within two rounds: reports, repository searches, marker
diffs. A subagent keeps that out of yours and returns the conclusion alone.
Start it with none of this conversation, since the brief below is everything
it needs and anything more is window it starts without. Then **wait for it**:
its conclusion is the whole answer, and a turn that ends on "I will come back
with its output" may never get a next one. The brief:

- its instructions: run `echolot guide loop` first, and follow it. That
  guide is for the subagent to read; reading it yourself only fills your
  window
- the traces — the directory `echolot hunt` set aside, when they were recorded
  before the investigation opened
- what regressed and against what, and after which change, or "unknown"
- that investigation #<n> is open: every `analyze` it runs is filed there,
  and `echolot compare` reads from it
- whether the config's thresholds can be trusted for this hunt, and if not,
  to start with `echolot analyze --defaults`
- that `doctor` passed, and when, so it does not run it again
- whether the project has instrumentation (`echolot domains --root .`); with
  none, its first move is `echolot mark`, not reading the app

**No subagents?** Run `echolot guide loop` and follow it yourself, in short
passes: quote the two or three rows that matter and keep the rest out of the
conversation.

## When the conclusion comes back

Close the investigation first — every time, an interim conclusion or "clean"
included — and only then answer the human. Left for after the answer, it is
the step that gets skipped:

```bash
echolot hunt --done "TextLayout:initLayout on the main thread, :feature:profile — confidence high"
```

Left open, a finished hunt stays the open one: every later `analyze` is filed
under it, and the next visit is asked whether to carry on with work that is
over.

Then show the conclusion as it is: do not retell it, and do not pad it with
guesses.

- **Cleanup.** It says whether the temporary markers were removed. If that is
  unclear, check: `grep -rn AGENTTMP_ <source_root>` must come back empty.
- **Confidence.** If it is low, say so rather than smoothing it over.
- **The device.** A finding about the device rather than the code —
  `runnable_starvation` on an emulator or a loaded machine — is worth a run
  on real hardware before anything is fixed.
