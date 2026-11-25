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

**A reference.** A change is two builds: the one on the phone, and one from
before it to set it against. When the change is "unknown", or the words
describe how it is now rather than a change — "the list stutters", "it is
slow" — there is no such build yet, and without one the hunt returns where
this build spends its time. In five blind hunts with no earlier build, all
five named the same long-standing cost, and so did a control with no change
at all. So before anything is recorded, ask with `AskUserQuestion` for a
reference, the cheapest first:

1. traces of an earlier build already on disk: an earlier investigation's
   rounds (`echolot hunt --list`, then `--show <n>` for its trace sets), or a
   set the human recorded on a release before;
2. an earlier build to install and record: the store build, a release's APK,
   or a build of a commit before the change. Outside `gradle` mode it is
   recorded once the investigation is open: the earlier build installed (by
   the human, or `adb install -r -d <its file>`), `collect --hunt <n>`; then
   the build to hunt installed again before its own set. In `gradle` mode
   the macrobenchmark builds the checkout, so that build is the checkout at
   the earlier commit, which is the human's to set up;
3. none.

With a reference it is a regression hunt after all: `--since` names what lies
between the two builds ("since 4.1"), and the brief names the reference's
traces and which build they are. With none, say to the human what the answer
will be: what counts as slow for the scenario — the time limit
(`budget_ms`) for a launch, the frames the app made late for a scroll — how
this build stands against it, and where it spends its time; not what
changed. Recording the same build twice is not a reference: it says how far
the numbers wander by themselves, which each report's repeats already say.

**The scenario.** `echolot.yml` may name several journeys under
`scenarios:`, and `echolot status` lists them, a line each. With several,
the question is about one of them: match the human's words to a scenario by
its name and its anchors — "the feed stutters when scrolling" is a scroll,
"the app opens slowly" is the cold start — and when the words fit more than
one, ask with `AskUserQuestion`, the scenarios as the options.

With one, the words still have to fit it, by its name, its window and its
runner: `window: launch`, or a launch runner with a start anchor, is a start;
a `command` runner is whatever its script drives. A question about another
journey — a scroll when the one scenario is a cold start — is not hunted on
it: its traces would answer another question. When the words fit none of the
scenarios, propose adding one before anything is recorded, through
`/echolot-setup`'s "Adding a scenario", and come back. A journey a script
paces takes one of two shapes: a `command` runner that drives the screen with
`adb shell input swipe` (400 ms or more a swipe: a shorter one lands as a tap
on a loaded phone), `reset_policy: none`, no `start` or `end`, so the whole
recording is the scenario, and `duration_ms` longer than the script; or the
project's own frame-timing macrobenchmark (`FrameTimingMetric`) in `gradle`
mode, when `echolot scan` lists one. `echolot hunt --scenario <a name the
config lacks>` refuses with the same two shapes.

Carrying on the open investigation, its scenario is chosen already, and
`echolot hunt` names it. Hold the words to it all the same: a question about
another journey is a new investigation, on a scenario of its own.

**The investigation.** First, are you carrying on the one already open? Yes
when the human chose "carry on" at the door (`echolot hunt --resume` has run),
or when the door sent you here with no words of theirs: a bare `/echolot` in
the same sitting, where `next` said `hunt`. With their words and one open
that they have not chosen between, ask with `AskUserQuestion`: a new
investigation, with their words, first; carry on, second. Never decide it
from how much the words sound like the open question. Carrying on, open
nothing. Otherwise open one now, before anything is recorded or analysed for
this question:

```bash
echolot hunt "<what regressed, in the human's words>" --since "<the change, or unknown>"
```

That closes the one that was open, as `abandoned` — the word for one that came
to nothing. When the recap showed it had reports, ask the human first what it
came to, and if it came to something, record that before opening the new one:
`echolot hunt --done "<what it came to>"`.

From here on, every `collect` and `analyze` for this question carries
`--hunt <n>`, the number `opened #<n>` printed. That is what files it under
the investigation: each `collect --hunt <n>` notes the round it set aside,
each `analyze --hunt <n>` keeps a copy of its report, and from the second
round on `echolot compare` reads those copies. A run without the flag is
outside any investigation, files nothing, and says so on stderr. Its last
lines name the loop, which needs an agent: that is the rest of this command,
so do not start `/echolot` again.

With several scenarios, add `--scenario <name>` to the command: the
investigation is about that scenario, only its traces move aside, and every
`collect --hunt <n>`, `analyze --hunt <n>` and `report` inside it takes that
scenario without the flag.

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
open, so they are filed under it: `echolot collect -c echolot.yml -n 5 --hunt <n>`. With
a runner config that is new or was just changed, make the first run a cheap
one where the mode allows it: in `launch` and `command` mode that is `-n 1`.
In `gradle` mode the macrobenchmark sets its own iteration count and `-n`
never reaches it — the first `collect` records the whole set, so read what it
printed before running it again. A wrong variant fails after the whole build
either way. While it runs, `echolot` has a `collect` line saying how far it
got; a failure's sentence is on that line and in the run log.

**Leftover instrumentation.** `echolot hunt "<question>"` says on stderr when
the previous investigation left markers in the sources; otherwise, from the
checkout's root, `grep -rn --include='*.kt' --include='*.java' --include='*.c' --include='*.cc' --include='*.cpp' --include='*.cxx' --include='*.h' --include='*.hh' --include='*.hpp' --include='*.hxx' -e AGENTTMP_ -e 'echolot:mark' .`. Markers from an investigation that ran out
of context are still compiled in, still in the trace, and still in the
report — and they are attributed to code nobody touched this time.
`echolot mark --remove` takes out what `mark --apply` wrote and names, by
`file:line`, every marker added by hand — in Kotlin, Java, C or C++ — which
goes by hand. Outside gradle mode, build and install the app after that:
`collect` records the build on the phone, and the one there still has them.
A round recorded from it anyway gets a warning at the top of its report,
naming the markers the traces hold and the code no longer has.

**Thresholds.** Read the `thresholds` column of `echolot report`, which is
`detectors[].params_source`, and `config` in `echolot report --json` — not
`report.json` whole. If the thresholds were
calibrated on the very runs that hold the regression, the report is clean by
construction. Say so, and pass the agent `--defaults` for its first look.

## The run

Hand the work to the `perf-hunter` subagent, and **wait for it** —
`run_in_background: false`, where the tool that starts it has that
parameter. Some hosts always run a subagent in the background and hand its
answer back into this conversation themselves; there, do nothing until the
answer arrives — no other work, and no closing message that promises to come
back. Its conclusion is the whole output of this command; there is nothing
useful to do while it runs.

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
- the reference: the earlier build's traces, each file by name, and which
  build they are — or "none", said explicitly, which sends the agent down its
  branch for one build
- that the investigation is open, and its number (`opened #<n>` when it
  opened, `echolot hunt --list` otherwise): every `collect` and `analyze` it
  runs carries `--hunt <n>` and is filed there, and `echolot compare` reads
  from it
- with several scenarios, which one the investigation is about: its commands
  take it without `--scenario`
- whether the config's thresholds are trustworthy for this hunt (see above),
  and if not, that it should start with `analyze --defaults`
- that doctor passed, and at what time — so the agent skips its own run
- whether the project has any instrumentation (`echolot domains --root .`
  says). If it has none, say so and say what follows: the report will name
  system slices and threads, and the agent's first move is `echolot mark`
  (then `--apply`, and a build and an install of the app outside gradle
  mode) and one re-record; reading the app to find where the time goes
  comes after the report has named a place.

The window is the budget. In two hunts out of two the agent spent forty to
sixty percent of it reading sources by hand; `echolot reflect` shows the
split (`window fed by:` in the Subagent section) and flags it. `echolot
mark` exists for exactly that step; if the share stays high with it in
place, the report says which reads it did not replace.

Do not re-record in the main context, and do not move the traces the agent
is about to compare against. If a re-record is needed, it happens inside the
loop, and `echolot collect --hunt <n>` sets the previous set aside itself, into
`.echolot/traces/<scenario>-<stamp>/`, and records it under the investigation.
Only traces analysed outside `.echolot/traces` need a copy first — a
benchmark's output directory is cleaned by gradle on the next run, and a
rename inside it goes with the cleaning.

## What to show the human

The agent returns a short conclusion. Show it as it is; do not retell it in
your own words and do not pad it with guesses.

Check two things separately:

**Cleanup.** The answer must state whether the temporary instrumentation was
removed. If it is unclear, check yourself from the checkout's root:
`grep -rn --include='*.kt' --include='*.java' --include='*.c' --include='*.cc' --include='*.cpp' --include='*.cxx' --include='*.h' --include='*.hh' --include='*.hpp' --include='*.hxx' -e AGENTTMP_ -e 'echolot:mark' .`.

**Confidence.** If it is low, say so to the human rather than smoothing it
over. An interim conclusion with an honest assessment is more useful than a
confident look on weak data.

**What it was set against.** The answer's `Against` line. With none, say
that what it names is where this build spends its time, measured against the
scenario's limit or its late frames, and not shown to be what changed — the
verdict reads that way in `echolot hunt --show` and on the page whatever its
words — and that a set of an earlier build would locate a change.

If the finding is about the device rather than the code — for instance
`runnable_starvation` on an emulator or a loaded machine — warn that the run is
worth repeating on real hardware before fixing anything. A main-thread row
saying the app held its core is not that: the app's own threads were in the
way.

## Closing the investigation

When the agent has returned, close the investigation with what it came to.
If its answer ends with a verdict file, `.echolot/verdict.json`, close with
that:

```bash
echolot hunt --done --verdict .echolot/verdict.json
```

echolot holds every row the verdict cites against the reports this
investigation filed, and keeps the verdict as it was written. The agent is
told to run that same check on its file before returning, with
`echolot hunt --verdict <file>` and no `--done`, which records nothing, so a
refusal here is the exception. If one comes — exit 2, each entry it could not
find named on stderr — nothing was recorded and the investigation is still
open: send the refusal back to the agent, which has the evidence, and run the
command again on the corrected file; without `--done` it only checks, which is
the way to try a correction first. It counts the temporary markers left in the
sources as well, and says on stderr when there are any.

With no file, close with one line — the `Place`, a few words of the mechanism,
and the confidence; with no reference, say so in it, since this line is all
the record keeps:

```bash
echolot hunt --done "<place> — <what happens there>; confidence <high|medium|low>"
echolot hunt --done "no reference: <place> — <what happens there>; confidence <high|medium|low> that it costs this"
```

Every time, whatever the answer: an interim conclusion, "clean", or "the
config needs fixing" is what this investigation came to, and
`echolot hunt --list` shows it beside the question from then on. Left open, a
finished hunt
stays the open one: the next `/echolot` asks whether to carry on with work
that is over, and the next hunt closes it as `abandoned`, as if it had come to
nothing.
If the human wants more rounds, that is a new investigation, and the traces
this one left are where it starts.
