# The `.claude/` layer

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

The consequence is portability. For another client — Codex, Cursor, Copilot,
Junie — only the format of the knowledge file is rewritten; the core is
untouched.

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
