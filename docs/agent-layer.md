# The `.claude/` layer

[← Docs index](README.md) · [README](../README.md)

```bash
cd ~/StudioProjects/my-app
echolot init
```

Installs into the project the knowledge of how to use the tool:

```
.claude/
├── skills/echolot/
│   ├── SKILL.md              /echolot — the door: reads the state, routes; how to read the report
│   └── references/           report, config, ART names, trace capture
├── agents/perf-hunter.md     subagent: the iterative loop
├── commands/echolot-setup.md /echolot-setup — build the config (reached from /echolot)
├── commands/echolot-hunt.md  /echolot-hunt — find the cause (reached from /echolot)
├── commands/echolot-reflect.md /echolot-reflect — how the session went, what to change in the tool
├── settings.json             permission to call echolot without asking
└── echolot-layer.json        what init installed, file by file — for doctor
```

`/echolot` is the one door. It runs `echolot` (the status command), shows
it, and acts on the `next` line — `echolot status --next` gives it as one
word: `upgrade`, `init`, `doctor`, `setup`, `fix-config`, `resume-or-new`,
`init-force`, `fix-settings`, `hunt`. Setup and hunt are the two commands
beside it, invoked through the Skill tool, so only the branch that applies
enters the window; they remain callable directly for whoever knows where they
are going. An argument wins over the state: `/echolot init` runs
`echolot init` (not setup — a session once confused the two),
`/echolot hunt <words>` starts a hunt with the words as the regression and
without the `resume-or-new` question, and free text about slowness means the
same.

They are listed in the order `next_kind` tries them, and the order is part of
the design. A layer a newer echolot wrote comes before everything
(`upgrade`): the agent is about to read files written for a newer CLI than
the one installed, every later step would be taken with the older one, and
`init` refuses to touch it — so the skill shows the line and stops, and a
person upgrades. Next comes what `init` does on its own — a layer that is
missing or stale: the
agent is about to read that layer, and `init` touches nothing the project
edited, so there is nothing to ask. Two things about the layer only a person
can settle come last, right before a hunt: files edited here that
`init --all` would overwrite (`init-force`), and a `settings.json` that does
not parse (`fix-settings`). The skill asks, and either answer leads on to the
hunt. Asked first, "keep my edits" had nowhere to lead but to `init-force`
again, and an unreadable `settings.json` — read as stale — sent the agent
round `init`, which cannot fix it, for good.

The template ships **inside the package** rather than living in the application
repository: knowledge of how to use the tool belongs to the tool. What lands in
the project is a copy — edit it for your modules and commit it. A repeated
`init` brings up to date what you did not touch and leaves what you edited
alone; `--all` overwrites those too — it is still `init`, and the flag says
"every file". The skill never runs it without asking: it shows the files
`--all` would overwrite and lets the human choose.

`init` also adds `/.echolot/` and `/local.yml` to the project's `.gitignore`
— a comment line and whichever of the two patterns is not already covered,
appended — but only at a git root. Anywhere else it writes nothing and says
so, with the two lines to put in a `.gitignore` in the project's directory:
the patterns are anchored there, and git reads a `.gitignore` at every level.
The traces are tens of megabytes each and `local.yml` holds a device serial,
and both have been documented as gitignored since before anything wrote them
there.

One file in that list is not echolot's copy. `settings.json` is Claude Code's
own configuration — the project keeps its hooks and its enabled plugins there
— and the template contributes a single permission to it. So it is **merged,
never overwritten**, `--all` included: the permission goes in, everything
else in the file stays, and lists gain what they are missing instead of being
replaced. A `settings.json` that does not parse as JSON is reported and left
untouched, with the line to add printed for a human — a file that could not be
read is not a file to rewrite.

The copy goes stale the moment the package moves on, and nothing in the
project would say so — a session ran with a `collect.md` that said "there is
no runner yet" while the binary had one, and the agent drove gradle by hand.
So `init` writes a small manifest of what it installed, and `doctor` (and
`echolot` with no arguments) compares the layer with the template: `current`,
`stale` (untouched since install, template moved on), `customised` (edited
here, template did not move), `conflict` (both), `missing`, and — for the
merged file above — `unreadable`. None of them fails a check: a project may
have edited its copy on purpose, and the manifest is what lets the tool tell
the two apart. A layer installed before the manifest existed can only be
`differs`.

What each state asks for, and of whom, is decided in one place and said the
same way on the one line `doctor -q` and `status` print and in the full
`doctor` section:

| state | what it asks for |
|---|---|
| `newer` — the whole layer, see below | an upgrade: `pipx upgrade echolot` (or `uv tool upgrade echolot`); `init` refuses |
| `stale`, `missing` | `echolot init` — it touches nothing edited here |
| `conflict`, `differs` | `echolot init --all`, which overwrites every edited file, the customised ones included — so the skill shows the files and asks first |
| `customised` | nothing: the edit is the whole difference |
| `unreadable` | a person fixes the JSON; no flag of `init` touches a merged file |

They used to decide separately, and disagreed: one stale file beside one
customised read `echolot init` on the one line and `echolot init --all` in the
full section, and the second would have overwritten the customised file to
update the stale one.

### A layer from a newer echolot

The manifest also names the echolot that wrote it, and for a long time
nothing read that name back. In a team that commits the layer, that was a way
to lose an upgrade. One person upgraded echolot and ran `init`; a teammate
still on the older release ran `/echolot`. Every file the newer release had
changed still matched its hash in the manifest and no longer matched the
older template — which is what `stale` means — so `next` said `init`, the
skill ran it, the older files went back in, the manifest took the older
version, and `init` printed "Layer updated." The newer echolot then did the
same in the other direction, and the layer went back and forth with whoever
ran `init` last.

Now the name is read first, before any file is compared, and compared by the
numeric parts of the release: 0.10.0 is newer than 0.9.0, and 0.8 is 0.8.0.
Development releases, pre-releases and post-releases sort around their
release the way PEP 440 orders them, and a local label (`+mine`) orders
nothing. When a newer release wrote the layer, its state is `newer`,
whatever the files say:

- `echolot`, `doctor` and `doctor -q` name the release that wrote it and the
  one that is running, and the upgrade — `pipx upgrade echolot`, or
  `uv tool upgrade echolot`;
- `echolot status --next` says `upgrade`, and the skill shows that line to
  the human and stops;
- `init` refuses with exit 1 and writes none of what it installs: not
  `.claude/`, not the `.gitignore` lines, not the pointers for other agents,
  not the saved choice of agents. The release that wrote the layer may write
  every one of them differently, and this one cannot know how.

A version that does not read as one — a hand edit, a spelling a later
release may adopt — cannot be put in order, and gets the same answer. A
layer left alone costs a person one command; a layer rolled back undoes a
teammate's upgrade without a word. If an upgrade does not change it, the
manifest is what is wrong: delete it, and `init` adds what is missing and
keeps every file that differs until a person chooses `--all`. A manifest
with no name in it at all makes no claim, and its files are judged as
before.

There is no flag to go back. The layer is committed and the manifest with
it, so returning to an older layer is reverting the commit that brought the
newer one, which takes the manifest back too — and afterwards every echolot
agrees on what is installed. A flag would do the same thing less well, and it
would be one more command for an agent to try on a route whose whole point
is that it stops.

The same line is also the only news of a newer release echolot can give: the
tool makes no network calls, and a teammate's commit is what brings it. Two
limits. Releases up to 0.7.0 do not read the name, so they still put their
files back; the rule holds once the whole team is on a later one. And
between releases a checkout carries the number of the last one, so two
builds with the same number are the same version as far as this goes.

## Why a CLI and not an MCP server

- MCP tools sit in the context permanently, even when unused — a tax on every
  request
- a CLI is invoked through bash and costs nothing until it is needed
- a CLI runs in CI with no model at all
- it installs with one command, without editing a client config

MCP earns its place where interactive access to state is required. Here there
is no state: a trace goes in, a table comes out.

The consequence is portability, and it took a user's report to finish
collecting on it. `.claude/` is a Claude Code mechanism: a skill found by its
`description`, slash commands, a subagent. In Cursor it is an invisible
directory. The CLI worked there the whole time — it is a program — but nothing
pointed an agent at it, so the instructions were followed only when the model
happened to read `SKILL.md` while looking around. From the outside that is a
tool that "sometimes follows the flow".

What ships now is one command and a set of pointers:

```bash
echolot guide          # how to work with this tool
echolot guide setup    # building echolot.yml
echolot guide hunt     # the loop
echolot guide anr      # an ANR report from the field, read and then measured
```

`echolot init` writes a few lines into whatever the project shows evidence of —
`AGENTS.md`, `GEMINI.md`, `.cursor/rules/echolot.mdc`,
`.github/copilot-instructions.md` — each naming that command.

Gemini CLI has its own entry rather than riding on `AGENTS.md`, and that is
worth recording because the obvious assumption is wrong: its context file is
`GEMINI.md`, and `AGENTS.md` reaches it only when somebody has set
`context.fileName` in `.gemini/settings.json`. The documentation shows that as
an example of overriding the default, not as a second default. One shared file
does not yet cover everyone. On a terminal `init` shows a set and lets you
change it — the choice saved last time, or on a first run the detected set,
with whatever the tree shows evidence of marked `(found)` either way.
`--for claude,cursor` (or `--for all`) skips the question and replaces the
choice, and `--no-input` keeps it as it is.

The question is asked only when the CLI parser turned it on **and** there is a
terminal at both ends, and never under `CI`. `init` is run by agents, and by
`doctor`'s own self-check five times over into temporary directories: a prompt
appearing there is a hang rather than a question, so both gates are pinned by
checks.

The answer is kept in `.echolot/hosts.json`, because declining Claude Code has
to be a state rather than a moment. `.claude/` missing normally means "run
init" — on a project that chose Cursor only, that same absence would have
`next` demand `echolot init` forever. With the choice recorded the layer reads
as `opted-out`, and the next step is whatever the config says.

A state has to be read back as well as written. `init` without `--for` starts
from the saved choice, and detects only when there is none. It used to detect
on every run, and detection names Claude Code whether or not it is there — so
`init --for cursor` followed by a plain `init` installed `.claude/` and wrote
the opt-out over. And where a project declined Claude Code, the lines that
name the next step — `status`, `echolot hunt "<q>"` — name the door it chose,
`echolot guide`, rather than a `/echolot` its human does not have.

**The knowledge is not copied per client, on purpose.** Four files would drift
apart within two releases, and a copy committed to somebody's repository goes
stale the moment the package moves — which is exactly why `init` has to be
re-run for `.claude/` and why `doctor` checks whether that layer is current.
Text printed by the installed package cannot be stale. The pointers are stubs,
and a self-check fails if one starts growing into a copy.

A file the project wrote itself is never rewritten. When `AGENTS.md` is
already there and has no echolot section, `init` leaves the file alone and
prints the whole section to paste into it, both marker lines included; pasted
as printed, the next `init` finds its markers and keeps what is between them
current. It used to print four lines and an ellipsis, and a section pasted
from that had no end marker — every later `init` found an echolot section it
could not bound, and left it alone as edited.

### What still does not port

The subagent. A client without one runs the loop in the main context, where
raw output fills the window and the instability this whole design exists to
remove comes back. `guide` says so in as many words and tells the agent to
work in short passes and keep raw output out of the conversation. That is a
mitigation, not a fix — Claude Code remains the better experience, and now it
is the better one rather than the only one.

`reflect` reads the full session only for Claude Code, because only that
client's transcripts have a reader. Everywhere else it falls back to the
recorder log, which every command writes from every caller — so the report
exists, it is smaller, and it says which checks it could not make. See
[reflect.md](reflect.md) under "Without a transcript".

## What calls what

Left is a file an agent reads; right is what it may run once it has. There is
one executable in the whole design — `echolot` — so every arrow lands on a verb
of the same CLI a human types.

```
/echolot                             what the file lets an agent run
 └─ skills/echolot/SKILL.md          echolot · status --next · init · doctor · hunt
    │                                analyze · report · compare · probe · anr · mark
    │                                domains · calibrate
    │
    ├─ references/report.md          report · analyze · compare · probe · collect
    ├─ references/config.md          domains · calibrate    → references/naming.md
    ├─ references/naming.md          names
    ├─ references/collect.md         collect · calibrate
    │
    ├─ commands/echolot-setup.md     scan · domains · mark · collect · probe · names
    │                                analyze · calibrate                → echolot.yml
    │
    ├─ commands/echolot-hunt.md      doctor · init · hunt · collect · mark · domains
    │  │                             hunt "<q>" opens it, hunt --done closes it
    │  │
    │  └─ agents/perf-hunter.md      doctor · analyze · report · compare · names
    │     its own window             probe · domains · mark · anr · explain · collect
    │                                hunt --show                 + Read Edit Grep
    │
    └─ commands/echolot-reflect.md   reflect          → .echolot/reflect/<id>.json

echolot guide                        the same map, for a client without `.claude/`
 └─ guide/overview.md                echolot · status --next · init · doctor · collect
    │                                analyze · report · compare · domains · hunt
    │                                calibrate · explain · reflect · guide
    ├─ guide/setup.md                scan · collect · probe · names · domains
    │                                analyze · echolot
    ├─ guide/hunt.md                 doctor · hunt · collect · analyze · report
    │                                compare · names · domains · mark
    └─ guide/anr.md                  anr · mark
```

`report` (views of a report already on disk) sits beside `analyze` wherever a
report is read row by row — the skill, its report reference, the hunter and
the two guides that hunt; `scan` (the facts setup starts from) is in the two
setup paths and nowhere else. Every invocation in these files, inline or
fenced, is run past the CLI's own parser by a test: the verb has to exist and
every flag has to be one that verb takes.

Four things the shape says.

**The first call is always the same.** Every path out of the door begins with
bare `echolot` — the status command — and the skill switches on the word on
its `next` line (`echolot status --next` prints the word alone) rather than on
the look of the project.

**The references are one level down.** They name verbs, and one of them points
further: `references/config.md` sends its reader to `references/naming.md` for
why thread masks are masks — `comm` is cut to fifteen characters. Nothing in
the layer sits more than three hops from `/echolot`. Two references are opened
from below as well: `perf-hunter` reads `references/report.md` instead of
working the schema out by hand, and setup reads `references/collect.md` to
capture the probe trace while there is still no config.

**One file puts markers in.** `perf-hunter` is the only place that adds
anything to the sources, which is what makes `AGENTTMP_` a prefix one agent
owns rather than a convention several of them have to keep. The main context
only takes out: `echolot mark --remove`, before a new hunt, for the markers an
earlier investigation's `mark --apply` left behind.

**One file points back at the others.** `echolot-reflect` proposes changes to
`SKILL.md`, `perf-hunter.md` and the CLI itself — the only arrow here that
returns to where it started.

## Why the loop lives in a subagent

This is the most important of the four decisions.

Inside the loop, mess accumulates: raw SQL output, repository searches, diffs
of temporary instrumentation, five iterations in a row. In the main context
that fills the window within two rounds — and then the very instability this
whole thing exists to remove sets in.

The subagent works in its own window and returns only the conclusion:

```
Place:         <file:line or module>
Evidence:      <detector, numbers from the report — measured, nothing else>
Mechanism:     <why this costs that much time; steps it did not measure
               marked (inferred), steps it could not check marked (gap)>
Suggestion:    <what to do>
Confidence:    high | medium | low — and why
Ruled out:     <what it checked and did not carry to a cause>
Also measured: <every marker it planted, one line and one number each>
Cleanup:       temporary instrumentation removed | none was added
```

Eight fields, and `reflect` checks for all eight. The last two were added
after hunts that lost something: an agent measured the redundant work it was
looking for at 252.7 ms and returned a conclusion about something else, with
no field to put the number in; and a suspect nobody measured to the end costs
the next hunt a round spent rediscovering it.

## The loop protocol

```
round = 1
while round <= loop.max_rounds:
    report = analyze(traces)
    if the config looks wrong  → fix the config, do not hunt a problem
    if everything is silent    → exit: "clean"
    hypotheses = firing detectors → domains → files
    if localised to a place in the code → exit
    otherwise: pick a blind spot (usually uninstrumented_cpu)
               add AGENTTMP_ trace{} inside instrumentation.allowed
               re-record
    round += 1

cleanup: remove every AGENTTMP_ marker
running out of rounds → an interim conclusion from the data at hand
```

**Stopping is hard-coded, not a heuristic.** The agent has no goal of its own
to economise; without a limit it will spin for days and eat context.
`max_rounds` is set by a human in the config.

**Instrumentation rules:** write only inside `instrumentation.allowed`, never
into `generated` or `build`; prefix every temporary slice with `AGENTTMP_`;
clean up on success and on running out of rounds alike. The prefix is what
makes cleanup deterministic — `grep` and delete, rather than "remember what you
added".

## Which investigation, and where that is asked

`.echolot/traces/` and `.echolot/out/report.json` mean "the latest set" and
nothing more. For a while the tool had no way of saying what question that set
was recorded for, so `/echolot` a week later found history, attached to it and
carried on — whether or not the human had come back for the same thing. Old
cold-start traces answered a question about scrolling; thresholds calibrated
for one scenario gated another; markers left behind by an investigation that
ran out of context became the starting conditions of the next one.

`.echolot/hunt.json` is the missing label: one open investigation at a time,
holding the question in the human's words, when it opened, what it has been
through, and the config it was opened against. It sits in `.gitignore` next to
the traces — an investigation is the state of a machine, while `echolot.yml`
describes the project and is committed.

`echolot hunt` is where that state is read and written — one noun with one
home. It began as four hidden flags on `status`, which made a reporting
command mutate state and left the concept without a name a person could find;
`status` reports, `hunt` is the investigation. The word means the same in a
shell and after `/echolot`: `echolot hunt "<q>"` does the half a shell can do
and names the half it cannot, `/echolot hunt <q>` does both.

`next_kind` reads it and gains one word, `resume-or-new`: there is an
investigation open, it left traces or a report behind, and nobody has worked on
it recently enough for this to be the same sitting. The CLI still asks nobody
anything — it prints the recap and the word, and the skill puts the question
with `AskUserQuestion`. That keeps `status` usable from CI, where there is no
one to answer.

**The loop never sees the question, by construction rather than by a flag.**
The question is *which investigation to work in*; `perf-hunter` is handed one
in its prompt, so for the loop the question cannot arise. It never calls
`status` at all. And the two set-aside boundaries do not meet: `collect` moves
traces aside between **rounds**, opening an investigation moves them aside
between **investigations**, and both use the same primitive at different
levels.

A second guard falls out of the same design: every `collect` and `analyze`
updates `touched_at`, so a running loop keeps its own investigation inside the
freshness window even if it started from a stale one. `touched_at` means work
rather than "when someone last typed `echolot`", which is what makes the
freshness rule honest.

Starting a new investigation is where the value beyond the question sits. The
previous one is archived rather than deleted — the question someone was chasing
three weeks ago costs a kilobyte and cannot be reconstructed from the traces.
The loose trace set moves aside, so the new investigation cannot inherit it,
and *where it moved to* is recorded against the investigation it belonged to.
That last part is what keeps the archive from being decoration: `set_aside`
already returned the directory it created, and dropping that return value left
the record remembering a question with nothing behind it. Investigations are
numbered so there is something short to name one by — `echolot hunt --show 2`.

Some of that loose set may have been recorded for the very question being
opened — the human captured the traces, then asked. They are moved, not lost,
and they are still that question's evidence: the command hands the agent the
directory `echolot hunt` names, rather than recording them again.

### Who opens it, and who closes it

On the Claude Code path both ends belong to the `echolot-hunt` command. It
opens the investigation with the human's question and the change (`--since`)
before anything is recorded for it — unless the human is carrying on the one
already open — and closes it with `echolot hunt --done` and one line of the
conclusion when `perf-hunter` returns, whatever the answer was.

For a while the path opened one only when the door asked `resume-or-new` and
the human chose something new, and closed none. `collect` and `analyze` file
only into an investigation that is open, so on every other route `analyze`
kept no copy of its report and `perf-hunter`'s bare `echolot compare` said
there was nothing to compare; and a hunt that had finished stayed open, so the
next visit was asked `resume-or-new` about work that was over.

`perf-hunter` itself never opens or closes one. It is handed an open
investigation in its prompt and works inside it, which is the same boundary
as the question above: which investigation is settled before the loop starts.

### Filed under the investigation, without moving

`.echolot/traces/` and `.echolot/out/report.json` stay exactly where they are:
they are what every example, every CI job and the agent read, and moving them
would rewrite all three for tidiness. What changed is that each artefact is
*also* filed under the investigation it belongs to.

```
.echolot/
├── hunt.json                    the open investigation; a closed one stays
│                                here, readable, until the next one opens
├── traces/                      the working set — unchanged
│   └── coldStart-<stamp>/       a round, pushed aside by collect
├── out/report.json|md           the latest report — unchanged
└── hunts/
    └── 1/
        ├── hunt.json            the record, archived when the next one opens
        └── reports/001.json…    a copy per analyze, oldest first
```

`echolot hunt --done` marks the record concluded where it is;
`echolot hunt "<q>"` is what moves it here. Bare `echolot hunt` reads the one
in place and says whether it is open — a concluded one used to be headed
"Open investigation", which told an agent deciding whether to open one that
one was.

Reports are copied, trace directories are recorded by path. That asymmetry is
deliberate: a report is tens of kilobytes and there is no other way to see what
an investigation concluded at each step, while traces run to gigabytes and
copying them would be a way to fill a disk.

Two return values had to stop being dropped for this to work. `collect` called
`set_aside` and discarded the directory, so a hunt that ran four rounds
remembered only the last; it now hands it to the caller through `on_set_aside`.
And `analyze` overwrote one `report.json` for every question in the project.
And the sources are scanned for leftover `AGENTTMP_` markers, split by who can
remove them: lines `mark --apply` wrote carry its tag and `mark --remove` takes
them out, while ones an agent added by hand carry only the prefix and have to
go by hand. One number for both would send a human away believing the tree was
clean.

## Three things this layer closes

**The agent never looks at the trace.** The rule is the first item in the
skill, with the proportion attached: 81 MB and 475k slices against a 14 KB
report. Without it stated plainly, an agent will open the trace itself and
everything built here is wasted in one go.

**The loop is isolated.** See above.

**Knowledge about ART is written down, not rediscovered.**
`references/naming.md` holds facts from live Android 14 and 13: how GC cycles
are named and why their phases must not be counted separately, which locks are
application-level and which are runtime-internal, how a synchronous binder
transaction differs from an async one, that `comm` is truncated to 15
characters. Every one of those cost a trace and several iterations to
establish. Without them written down, an agent works it all out again on every
project.

## Setup as inverted configuration

`/echolot-setup` exists so the user never opens the config. The agent obtains
everything obtainable and asks only about what exists neither in the repository
nor in the trace — of roughly 25 fields, four need a human decision.

The order matters: scan, capture, reconnaissance, and only then conversation.
By the time of the first question the agent holds real options from a real
trace:

```
Ran a cold start. Last slices before the first frame:
  1) Choreographer#doFrame*        @ 772 ms
  2) activityResume                @ 731 ms
  3) Compose:recompose             @ 690 ms
What counts as "the app is ready to use" for you?  [1]
```

The agent does not decide; it presents candidates and asks for confirmation.
That makes it impossible to get wrong, and the answer is fixed in the config
for good.

Every field carries provenance — `_source` and `_evidence` — so a human sees
what to double-check, an agent knows that `confirmed_by_user` is untouchable,
and when something goes wrong you can see where the nonsense came from.
