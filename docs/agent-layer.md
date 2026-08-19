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
word: `init`, `init-force`, `doctor`, `setup`, `fix-config`, `hunt`. Setup
and hunt are the two commands beside it, invoked through the Skill tool, so
only the branch that applies enters the window; they remain callable
directly for whoever knows where they are going. An argument wins over the
state: `/echolot init` runs `echolot init` (not setup — a session once
confused the two), `/echolot hunt <words>` starts a hunt with the words as
the regression, free text about slowness means the same.

The template ships **inside the package** rather than living in the application
repository: knowledge of how to use the tool belongs to the tool. What lands in
the project is a copy — edit it for your modules and commit it. A repeated
`init` brings up to date what you did not touch and leaves what you edited
alone; `--force` overwrites those too.

The copy goes stale the moment the package moves on, and nothing in the
project would say so — a session ran with a `collect.md` that said "there is
no runner yet" while the binary had one, and the agent drove gradle by hand.
So `init` writes a small manifest of what it installed, and `doctor` (and
`echolot` with no arguments) compares the layer with the template: `current`,
`stale` (untouched since install, template moved on), `customised` (edited
here, template did not move), `conflict` (both), `missing`. Stale is a line
in the output and `echolot init`, not a failed check — a project may have
edited its copy on purpose, and the manifest is what lets the tool tell the
two apart. A layer installed before the manifest existed can only be
`differs`, and needs `--force`.

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
```

`echolot init` writes a few lines into whatever the project shows evidence of —
`AGENTS.md`, `.cursor/rules/echolot.mdc`, `.github/copilot-instructions.md` —
each naming that command. On a terminal it shows the detected set and lets you
change it; `--for claude,cursor` (or `--for all`) skips the question, and
`--no-input` takes the detection as-is.

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

**The knowledge is not copied per client, on purpose.** Four files would drift
apart within two releases, and a copy committed to somebody's repository goes
stale the moment the package moves — which is exactly why `init` has to be
re-run for `.claude/` and why `doctor` checks whether that layer is current.
Text printed by the installed package cannot be stale. The pointers are stubs,
and a self-check fails if one starts growing into a copy.

A file the project wrote itself is never rewritten. When `AGENTS.md` is
already there and has no echolot section, `init` prints the lines to add and
leaves the file alone.

### What still does not port

The subagent. A client without one runs the loop in the main context, where
raw output fills the window and the instability this whole design exists to
remove comes back. `guide` says so in as many words and tells the agent to
work in short passes and keep raw output out of the conversation. That is a
mitigation, not a fix — Claude Code remains the better experience, and now it
is the better one rather than the only one.

`reflect` also stays Claude Code-only for now: it reads that client's
transcripts. See [reflect.md](reflect.md) under "Other agents" for what
another reader would have to produce.

## Why the loop lives in a subagent

This is the most important of the four decisions.

Inside the loop, mess accumulates: raw SQL output, repository searches, diffs
of temporary instrumentation, five iterations in a row. In the main context
that fills the window within two rounds — and then the very instability this
whole thing exists to remove sets in.

The subagent works in its own window and returns only the conclusion:

```
Place:       <file:line or module>
Evidence:    <detector, numbers from the report>
Mechanism:   <why this costs that much time>
Suggestion:  <what to do>
Confidence:  high | medium | low — and why
Cleanup:     temporary instrumentation removed | none was added
```

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

### Filed under the investigation, without moving

`.echolot/traces/` and `.echolot/out/report.json` stay exactly where they are:
they are what every example, every CI job and the agent read, and moving them
would rewrite all three for tidiness. What changed is that each artefact is
*also* filed under the investigation it belongs to.

```
.echolot/
├── hunt.json                    the open investigation
├── traces/                      the working set — unchanged
│   └── coldStart-<stamp>/       a round, pushed aside by collect
├── out/report.json|md           the latest report — unchanged
└── hunts/
    └── 1/
        ├── hunt.json            the record, once it is closed
        └── reports/001.json…    a copy per analyze, oldest first
```

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
nor in the trace — of roughly 25 fields, two need a human decision.

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
