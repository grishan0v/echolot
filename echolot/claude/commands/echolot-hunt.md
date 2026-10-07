---
description: Find the cause of a performance regression — opens an investigation, runs perf-hunter in its own context, closes the investigation with its answer. /echolot routes here once the config exists.
---

Find the cause of a performance regression.

## Before calling the agent

**Environment.** `echolot doctor -q`. A non-zero exit means there is no point
going further: no report from that environment can be trusted. Show what
exactly failed.

Then read its second line, the one about the `.claude/` layer, and look at
how it starts before anything else. `layer: NEWER` or
`layer: VERSION UNREADABLE` means the layer here was written by a newer
echolot than the one installed, or names a version the installed one cannot
read: the agent you are about to launch would read files written for a CLI
that is not here. Show that line to the human as it is — it names the
upgrade, `pipx upgrade echolot` (or `uv tool upgrade echolot`) — and stop.
Do not call the agent, and do not run `echolot init` or `echolot init --all`,
whatever else the line names: `init` refuses that layer, and the upgrade is
the human's to make.

Otherwise, if the line says the layer is stale and names plain
`echolot init`, run that before anything else — the agent you are about to
launch reads that layer. If it names `echolot init --all`, that overwrites
files edited here: ask the human first, the way the skill's `init-force`
section says. Note the time: the agent is told doctor passed and when, so it
does not run it again.

**Config.** No `echolot.yml` in the root? Go to `/echolot-setup` and come back.
A loop on an invented config burns rounds for nothing.

**The three facts.** Before anything is recorded or analysed, you must hold,
in the human's words:

1. what regressed and against what — "it was 3 s, now it is 7 s"
2. which traces show it — ones already on disk, or none yet
3. **after which change** — a commit, a dependency bump, a date, "since the
   redesign of the tabs"

Ask for the third one explicitly, with `AskUserQuestion`, even when the first
two are clear. "Unknown" is an acceptable answer and goes into the prompt as
such — an omitted one is not. The tool localises a **specific** regression
well and searches for the unknown in general badly; without the change the
agent hunts everything that looks expensive and comes back with a guess.

Carrying on an investigation that is already open, its record holds the first
and the third: `echolot hunt` prints them.

**The investigation.** First, are you carrying on the one already open? Yes
when the human chose "carry on" at the door (`echolot hunt --resume` has run),
or when `echolot hunt` shows an open investigation that the human's words
continue — "keep going", the same symptom in the same sitting. When you
cannot tell, ask with `AskUserQuestion`: carry on, or a new investigation.
Carrying on, open nothing. Otherwise open one now, before anything is
recorded or analysed for this question:

```bash
echolot hunt "<what regressed, in the human's words>" --since "<the change, or unknown>"
```

Everything from here on is filed under it: each `collect` notes the round it
set aside, each `analyze` keeps a copy of its report, and from the second
round on `echolot compare` reads those copies — with no investigation open it
has nothing to compare. Its last lines name the loop, which needs an agent:
that is the rest of this command, so do not start `/echolot` again.

Opening one moves the scenario's traces that are loose in `.echolot/traces/`
into a directory beside them, and says where on stderr:
`previous run set aside: N trace(s) → .echolot/traces/<scenario>-<stamp>/`.
Nothing is deleted. That is the point for traces of an earlier question and
for the probe capture setup made. Traces recorded for **this** question before
it had an investigation — the human captured them, then asked — move with
them and are still its evidence: fact 2 is now that directory. Hand it to the
agent, and do not record them again.

If `echolot hunt` exits 2 with `error: echolot.yml does not load: …`, it
opened nothing and moved no traces aside. Show that error to the human as it
is, ask them to fix `echolot.yml`, and stop — record nothing, and do not call
`perf-hunter`.

**Traces.** None that show it? Capture them now that the investigation is
open, so they are filed under it: `echolot collect -c echolot.yml -n 5`. With
a runner config that is new or was just changed, make the first run a cheap
one where the mode allows it: in `launch` and `command` mode that is `-n 1`.
In `gradle` mode the macrobenchmark sets its own iteration count and `-n`
never reaches it — the first `collect` records the whole set, so read what it
printed before running it again. A wrong variant fails after the whole build
either way. While it runs, `echolot` has a `collect` line saying how far it
got; a failure's sentence is on that line and in the run log.

**Leftover instrumentation.** `echolot hunt "<question>"` says on stderr when
the previous investigation left markers in the sources; otherwise, from the
checkout's root, `grep -rn --include='*.kt' --include='*.java' -e AGENTTMP_ -e 'echolot:mark' .`. Markers from an investigation that ran out
of context are still compiled in, still in the trace, and still in the
report — and they are attributed to code nobody touched this time.
`echolot mark --remove` takes out what `mark --apply` wrote; anything added by
hand goes by hand.

**Thresholds.** Read `config` and `detectors[].params_source` in the last
`report.json`, or the `Config:` line in `report.md`. If the thresholds were
calibrated on the very runs that hold the regression, the report is clean by
construction. Say so, and pass the agent `--defaults` for its first look.

## The run

Hand the work to the `perf-hunter` subagent, and **wait for it** —
`run_in_background: false`. Its conclusion is the whole output of this
command; there is nothing useful to do while it runs.

Say it here because the tool's own default is the other way. Backgrounded, the
turn ends with a promise to come back, and whether a next turn ever arrives is
not up to this command: under `claude -p` it does not, so the hunt runs, the
agent finishes, and its answer goes nowhere. That happened once in six
recorded runs of this very command — the agent did the work and the run
printed "I will come back with its output" and stopped. `--max-turns` does not
help; the turn was not cut short, it was finished.

Handing it over at all is not a formality either: the loop generates a lot of
mess — raw output, repository searches, instrumentation diffs, several
iterations. In the main context that fills the window within two rounds, and
then the very instability this whole thing exists to remove sets in.

Pass the agent:

- the path to the traces — the set-aside directory above, when they were
  recorded before the investigation opened
- what regressed and against what: "it was 3 s, now it is 7 s"
- after which change — or the word "unknown", said explicitly
- that the investigation is open, and its number (`opened #<n>` when it
  opened, `echolot hunt --list` otherwise): every `analyze` it runs is filed
  there, and `echolot compare` reads from it
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
removed. If it is unclear, check yourself from the checkout's root:
`grep -rn --include='*.kt' --include='*.java' -e AGENTTMP_ -e 'echolot:mark' .`.

**Confidence.** If it is low, say so to the human rather than smoothing it
over. An interim conclusion with an honest assessment is more useful than a
confident look on weak data.

If the finding is about the device rather than the code — for instance
`runnable_starvation` on an emulator or a loaded machine — warn that the run is
worth repeating on real hardware before fixing anything.

## Closing the investigation

When the agent has returned, close the investigation with what it came to,
in one line — the `Place`, a few words of the mechanism, and the confidence:

```bash
echolot hunt --done "<place> — <what happens there>; confidence <high|medium|low>"
```

Every time, whatever the answer: an interim conclusion, "clean", or "the
config needs fixing" is what this investigation came to, and
`echolot hunt --list` shows it beside the question from then on. Left open, a
finished hunt
stays the open one: every later `analyze`, about any question, is filed under
it, and the next `/echolot` asks whether to carry on with work that is over.
If the human wants more rounds, that is a new investigation, and the traces
this one left are where it starts.
