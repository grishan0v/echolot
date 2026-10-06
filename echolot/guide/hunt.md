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

If `echolot hunt` reported markers left by the previous investigation,
remove them before recording: `echolot mark --remove` takes out what
`mark --apply` wrote and every line in its shape, and it lists the rest,
which go by hand. Left in, they are compiled into the new traces and read as
part of code nobody touched this time.

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
temporary markers and the eight fields of the conclusion — is the subagent's
own guide, which it prints for itself. It is the text a Claude Code subagent
is given, from the same file.

**Hand it to a subagent when your host can start one.** The loop fills a
window with raw output within two rounds: reports, repository searches, marker
diffs. A subagent keeps that out of yours and returns the conclusion alone.
Start it with none of this conversation, and with this brief as its first
message, filled in. Two of its values are looked up: `#<n>` is the number
`echolot hunt` printed as `opened #<n>`, or the `→` row of
`echolot hunt --list` when you carry one on; for `Thresholds:`, the
`thresholds` column of `echolot report` says which detectors run on
calibrated numbers (`config`), and calibrated on the very runs that hold the
regression means `--defaults`.

```text
Run `echolot guide loop` first and follow it: it is your guide to the loop.
Traces: <each trace file by name, from the directory `echolot hunt` set aside or from .echolot/traces>
Regressed: <what, against what>, after <the change, or "unknown">
Investigation #<n> is open: every `analyze` you run is filed under it, and `echolot compare` reads from it.
Thresholds: <the config's can be trusted | start with `echolot analyze --defaults`>
Doctor passed at <time>: do not run it again.
Instrumentation: <what `echolot domains --root .` found | none: start with `echolot mark`, not with reading the app>
```

The brief is everything it needs, and anything more is window it starts
without. **Do not run `echolot guide loop` yourself**: it is the subagent's,
eighteen thousand characters it reads on its own, and here it only fills your
window. Then **wait for it**: its conclusion is the whole answer, and a turn
that ends on "I will come back with its output" may never get a next one.

**No subagents?** Then the loop is yours: run `echolot guide loop` and follow
it, in short passes. Quote the two or three rows that matter and keep the rest
out of the conversation.

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
  unclear, check from the checkout's root:
  `grep -rn --include='*.kt' --include='*.java' -e AGENTTMP_ -e 'echolot:mark' .` must come back empty.
- **Confidence.** If it is low, say so rather than smoothing it over.
- **The device.** A finding about the device rather than the code —
  `runnable_starvation` on an emulator or a loaded machine — is worth a run
  on real hardware before anything is fixed.
