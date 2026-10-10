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
tool never checks for one, and its only download is the pinned
trace_processor, once. A teammate's commit is what brings the news. Two
limits. Releases up to 0.7.0 do not read the name, so they still put their
files back; the rule holds once the whole team is on a later one. And
between releases a checkout carries the number of the last one, so two
builds with the same number are the same version as far as this goes.

### A private install

`init` is built for a team that commits its layer. Sometimes the repository
is not yours to change: one the team shares, before anyone has agreed to a
new tool, or one a client owns. For that there is `echolot init --private`,
which installs the same files and keeps all of them out of git.

- **Where the paths go.** Every path `init` writes goes into the clone's own
  ignore file, `info/exclude`. git reads it like a `.gitignore` and never
  commits it. echolot asks git where the file is: in a worktree, `.git` is a
  file, and the exclude file is the one in the repository it points to. The
  paths sit in one block between two marker lines, written from the top of
  the repository, since the project may live in a subdirectory of it.
- **What the block names.** The layer's files and its manifest, the pointers
  `init` created for other agents, Codex's rule, and `/.echolot/`,
  `/local.yml` and `/echolot.yml`. A pattern works before its file exists, so
  the config setup writes later is covered too. Every private `init` writes
  the block anew, so a file a later release stops installing drops out of
  it. The rest of the exclude file is the person's, and `init` never touches
  it.
- **No file git tracks is written.** An exclude file hides untracked files
  only, so `init` asks git which of its targets are tracked.
  - The two `.gitignore` lines go into the block instead.
  - The permission goes into `.claude/settings.local.json`, Claude Code's
    per-machine settings, which it reads beside `settings.json`. `status`
    and `doctor` look for it there.
  - A tracked pointer file, such as an `AGENTS.md` with the team's rules,
    is left alone. `init` prints the section to paste, as it does for a
    file that is the project's own.
- **The choice is kept.** It goes into `.echolot/hosts.json` with the choice
  of agents, so a later plain `init` stays private: the one `/echolot` runs
  when the layer goes stale, or the plugin door's `init --for plugin`. The
  layer line in `status` and `doctor` says "private to this clone".
- **`echolot init --shared` hands it back to the team.** It takes the block
  out and does what a plain `init` does: the `.gitignore` lines, the
  permission in `settings.json`. The files stay where they are, and git
  sees them from then on.
- **Outside a git repository** there is nothing to keep from git: `init
  --private` says so and installs as usual.

The agents read their files whether or not git ignores them. With
everything `init` wrote kept from git:
- Claude Code loaded the skill, the three commands and the subagent;
- Codex's prompt carried the `AGENTS.md` section.

Cursor, Copilot and Gemini CLI were not checked.

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
`.github/copilot-instructions.md` — each naming that command. For Codex it
writes one more file, which is not a pointer: `.codex/rules/echolot.rules`,
the rule that lets `echolot` out of Codex's sandbox (see
[A sandbox with no network](#a-sandbox-with-no-network)).

Gemini CLI has its own entry rather than riding on `AGENTS.md`, and that is
worth recording because the obvious assumption is wrong: its context file is
`GEMINI.md`, and `AGENTS.md` reaches it only when somebody has set
`context.fileName` in `.gemini/settings.json`. The documentation shows that as
an example of overriding the default, not as a second default. One shared file
does not yet cover everyone. On a terminal `init` shows a set and lets you
change it — the choice saved last time, or on a first run the detected set,
with whatever the tree shows evidence of marked `(found)` either way.
`--for claude,cursor` (or `--for all`) skips the question and replaces the
choice, and `--no-input` keeps it as it is. `all` is every client but one of
a pair: the plugin, or Claude Code where the plugin is chosen already. The
`.claude/` layer and the plugin bring the same skills and Claude Code would
load both, so `--for` that names the two together is refused, and a layer
kept current beside a chosen plugin says so on its line.

The question is asked only when the CLI parser turned it on **and** there is a
terminal at both ends, and never under `CI`. `init` is run by agents, and by
`doctor`'s own self-check many times over into temporary directories: a prompt
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

`reflect` reads the full session for Claude Code and for Codex, whose
sessions have a reader each. Everywhere else it falls back to the recorder
log, which every command writes from every caller — so the report exists, it
is smaller, and it says which checks it could not make. See
[reflect.md](reflect.md) under "Without a transcript".

### The plugin: one set of skills for Claude Code and Codex

`plugins/echolot/` is a plugin both hosts load: four skills — the door,
setup, the hunt and reflect — and a manifest. It is not the layer packaged
again. Codex loads a plugin's skills and nothing else, turns a command into
a skill only when it fits in 4,000 bytes and drops the rest without a word,
and has no place for an agent file: today's layer, packaged as it is, arrived
in Codex as a door that routed to two skills that were not there (#188). And a
second set of texts written for Codex would be the drift #5 closed.

So each skill is a door to a topic, and says little besides what to run.
`echolot guide <topic>` prints the rest, from the files the layer is made of:

| topic | printed from |
|---|---|
| `overview`, `hunt`, `anr` | the guide's own pages |
| `setup` | `commands/echolot-setup.md` |
| `reflect` | `commands/echolot-reflect.md` |
| `loop` | `agents/perf-hunter.md` — the text a Claude Code subagent is given |
| `report`, `config`, `naming`, `collect` | the skill's references |

One file per text, and nothing printed can be older than the echolot that
prints it. The guide used to keep a `setup.md` of its own beside the command;
by then each of the two knew things the other did not, and they are one file
now.

The skills carry what a host needs and the guide cannot give: where to go
next, and how to hand the loop over. The door runs `echolot` and routes on
`next`; the hunt opens the investigation, then hands the loop to a subagent
that starts with none of the conversation — Codex copies the whole of it into
a subagent unless told otherwise — and waits for the conclusion. Nothing in
them names a Claude Code tool, and each stays under 4,000 bytes; `tests/test_plugin.py`
holds both, and that every topic a skill names exists.

The hunt skill is the main thread's part whole, and the loop's first message
is a brief to fill in, a block the skill and `echolot guide hunt` print alike
(a test holds the two to one text). The main thread never needs the loop's
own guide. It used to be told where to find the brief rather than given it,
and in two live Codex runs it read `echolot guide`, `guide hunt` and
`guide loop` before starting the subagent: about 35,000 characters in the
window the subagent is there to protect, `guide loop` alone half of it, and
the subagent read the loop's guide again (#202). With the brief handed over
as a block, a live Codex run read `echolot guide hunt` alone before the
hand-off: 5,878 characters of guides, and 13,504 in all against 47,354 in
the run before. `reflect` says when the loop's guide is read in the main
context (`guides_in_main`).

In Codex only the door is listed to the model at all. Each of the other
three turns implicit use off in its `agents/openai.yaml`, and Codex leaves
such a skill out of the list a session starts with; a person can still call
it by name (`$echolot-hunt`). So the door reaches them through the guide:
`echolot guide hunt`, `setup` and `reflect` print their text, and that is
what the live runs read. All four stay out of ChatGPT's chat, where there is
no shell to run echolot in.

A project whose skills come with the plugin does not get `.claude/` —
Claude Code would load the skills twice. The door answers `next: init` with
`echolot init --for plugin`, which writes the `.gitignore` lines, saves the
choice, and installs nothing else; `echolot` then reads the layer as provided
by the plugin, and stops asking for `init`. In Codex the door runs
`echolot init --for plugin,codex`, which writes Codex's rule as well.

### Installing the plugin

`.claude-plugin/marketplace.json` at the root of this repository is a
marketplace with one plugin in it, and both hosts read it:

```bash
pipx install echolot                          # the CLI the skills drive
```

- **Claude Code:** `/plugin marketplace add grishan0v/echolot`, then
  `/plugin install echolot@echolot`, or from a shell `claude plugin
  marketplace add grishan0v/echolot` and `claude plugin install
  echolot@echolot`. The door is `/echolot:echolot`.
- **Codex:** `codex plugin marketplace add grishan0v/echolot`, then
  `codex plugin add echolot@echolot`, or install it from `/plugins` in the
  CLI or the Plugins tab of the ChatGPT desktop app. The door is the
  `echolot` skill, `$echolot` in a prompt and `echolot:echolot` in Codex's
  list, and it also answers a plain question about slow startup.

Claude Code needs git 2.36 or newer on `PATH` for this. It fetches
`plugins/echolot` with a sparse checkout of a partial clone, and tries the
checkout offline first: git 2.35 and older reach for the network anyway, are
refused, and crash with the index still locked, so the retry stops at
`index.lock: File exists`. Ubuntu 22.04 ships git 2.34.1 and the git-scm.com
installer for macOS 2.33.0; git from Xcode's command line tools or Homebrew is
new enough. Codex installs the plugin with old git as well (#205).

An upgrade is `pipx upgrade echolot`, and then the plugin: `claude plugin
marketplace update echolot` and `claude plugin update echolot@echolot`, which
Claude Code applies after a restart; `codex plugin marketplace upgrade
echolot` and `codex plugin add echolot@echolot` again in Codex.

The entry fetches `plugins/echolot` at the release tag of the version in the
code. So what a person installs is the plugin the CLI
on PyPI was released with: served from `main`, it would name topics and flags
that no released echolot has yet. A new plugin reaches people with the next
release, when the tag and `version` move together — Claude Code copies a
plugin again only when its `version` changes. `tests/test_plugin.py` fails
when the tag, `plugin.json` and `echolot/__init__.py` disagree.

On a project that ran `init` before, the `.claude/` layer is still there, and
Claude Code loads its skills beside the plugin's. Once the plugin's door has
run `echolot init --for plugin`, `echolot` says so on its layer line and stops
asking for `init`; whether the files go is the human's call, since teammates
without the plugin may work from them.

### A sandbox with no network

Codex runs every command in a sandbox, and by default the sandbox has no
network. On macOS that takes localhost with it. echolot needs localhost twice:
perfetto binds a free port before it starts trace_processor, and `collect`
reaches the phone through the adb server on port 5037. So in Codex, as it
comes, `doctor`, `analyze` and `collect` all fail.

They used to fail in ways that looked like a broken install: `doctor` said
"could not run — [Errno 1] Operation not permitted", `analyze` ended in a
traceback, and `collect` passed on twenty lines of adb's startup log. Now each
says that a sandbox refused the port, whose sandbox it was when the
environment names it (Codex marks the commands it sandboxes with
`CODEX_SANDBOX_NETWORK_DISABLED`), and the way out. `doctor` logs the reason,
so `echolot` and its `next` line say it too.

The way out is to let the one command through:

- **Codex:** approve running `echolot` outside the sandbox when Codex asks,
  or let it out for good with a rule, which `echolot init` writes when
  `codex` is among its agents (below). `~/.codex/rules/` holds the same rule
  for every project.
- **Claude Code**, when its own sandbox is turned on: `"echolot *"` in
  `sandbox.excludedCommands`.

Turning the network on for every command is wider than it needs to be, and
still leaves the first download of trace_processor nowhere to write: perfetto
keeps it outside the workspace. The rule was tried in a live Codex session
(#188): with it, all three commands passed, in the main thread and in a
subagent.

### Codex's rule, from `init`

`codex` is an agent of its own in `init`'s list, apart from `AGENTS.md`:
Codex reads `AGENTS.md` like many clients do, and the rule is Codex's alone.
A project with a `.codex/` folder gets it by default on its first `init`;
after that the saved choice decides, and `--for …,codex` adds it, as the
`codex` line of `echolot` says. `init` writes `.codex/rules/echolot.rules`:
`prefix_rule(pattern = ["echolot"], decision = "allow")`, with a few
`echolot` commands as examples that Codex checks when it loads the file, so
a rule that stopped matching says so as a session starts. The file is
echolot's alone, so it is written whole; an edit made here is kept, and
`--all` puts echolot's back, as with the files of the layer.

Three things decide whether the rule works, and `doctor` and `status` say
each of them on a `codex` line, shown wherever Codex is chosen, has a
`.codex/` folder, or is what runs the command:

- **The file**: there, as echolot wrote it, edited, or gone. A rule counts
  when it is a `prefix_rule` with the pattern `["echolot"]` and
  `decision = "allow"`, not commented out, in either quote and laid out any
  way Starlark allows. It is looked for from the directory the command ran
  in up to the project's root, the first directory with a `.git` in it, as
  Codex bounds a project, and under `~/.codex/rules/`. A worktree under the
  main checkout is a project of its own, and the main checkout's rule is not
  its rule.
- **Trust.** Codex reads a project's `.codex/` only once the person has said
  they trust the project, and records that in `~/.codex/config.toml`
  (`[projects."<path>"]`, `trust_level = "trusted"`). echolot reads that the
  way Codex does: the folder, then the repository it is in, then the main
  checkout of a linked worktree. An untrusted project is told both ways out:
  trust it when Codex asks, or put the same file in `~/.codex/rules/`. A rule
  there is read in every project, so with one in place an untrusted
  project's own rule hides nothing: the line says `in every project`.
- **The moment.** Codex reads rules when a session starts, a rule under
  `~/.codex/rules/` as much as a project's. A session open when the rule was
  written keeps echolot in the sandbox until it restarts.

The rule lets out a command line that starts with the word `echolot`.
`python -m echolot` is not one, and a refusal under it says so.

One thing `init` cannot do from inside: Codex keeps `.codex/` read-only for
the commands it sandboxes (`.agents/` and `.git/` too), so that no command
can let itself out. Tried on `codex sandbox`, `init --for codex` there got
`Operation not permitted` making `.codex/`. So it says that, and names the
command to run outside the sandbox — with the project's other agents in
`--for`, since `--for` replaces the saved choice and `--for codex` alone
would stop keeping `.claude/` current.

The `AGENTS.md` pointer carries one sentence about this, so that an agent
which read only the pointer knows why its first `analyze` failed.

### The copies `/import` leaves

Codex's `/import` turns a Claude Code project's `.claude/` into
`.agents/skills/echolot/`, `.agents/skills/source-command-echolot-*/` and
`.codex/agents/perf-hunter.toml`, and rewrites `.claude/` to `.Codex/` in
them on the way. The manifest knows only `.claude/`, so the layer line went
on saying `current` while an agent in Codex followed copies that described a
`.Codex/settings.json` nobody has. `doctor` now lists them, says the plugin
replaces them, and deletes nothing: someone may have edited them on purpose.

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
    │                                hunt --show            + Read Edit Grep Glob
    │
    └─ commands/echolot-reflect.md   reflect          → .echolot/reflect/<id>.json

echolot guide                        the same map, for a client without `.claude/`
 └─ guide/overview.md                echolot · status --next · init · doctor · collect
    │                                analyze · report · compare · domains · hunt
    │                                calibrate · explain · reflect · guide
    ├─ guide setup                   commands/echolot-setup.md, printed
    ├─ guide/hunt.md                 doctor · hunt · collect · domains · mark
    │  └─ guide loop                 agents/perf-hunter.md, printed: the brief a
    │                                subagent is given, or the loop run by hand
    ├─ guide reflect                 commands/echolot-reflect.md, printed
    ├─ guide report | config         references/*.md, printed
    │  | naming | collect
    └─ guide/anr.md                  anr · mark

plugins/echolot/skills/              the plugin: four skills, each a door to a topic
 ├─ echolot/SKILL.md                 --version · echolot · status --next
 │                                   init --for plugin[,codex] · doctor · hunt --resume
 │                                   guide
 ├─ echolot-setup/SKILL.md           scan · probe · names · domains · analyze
 │                                   guide setup
 ├─ echolot-hunt/SKILL.md            doctor -q · hunt · collect · mark --remove
 │                                   report · hunt --done · guide hunt · guide loop
 └─ echolot-reflect/SKILL.md         guide reflect
```

`report` (views of a report already on disk) sits beside `analyze` wherever a
report is read row by row — the skill, its report reference, the hunter and
the guide's overview; `scan` (the facts setup starts from) is in the setup
command, which `guide setup` prints, and in the plugin's setup skill, and
nowhere else. Every invocation in
these files and in the plugin's skills, inline or fenced, is run past the
CLI's own parser by a test: the verb has to exist and every flag has to be one
that verb takes.

Four things the shape says.

**The first call is always the same.** Every path out of the door begins with
bare `echolot` — the status command — and the skill switches on the word on
its `next` line (`echolot status --next` prints the word alone) rather than on
the look of the project.

**The references are one level down.** They name verbs, and one of them points
further: the config reference sends its reader to the naming one for why
thread masks are masks — `comm` is cut to fifteen characters. Nothing in the
layer sits more than three hops from `/echolot`. Two references are opened
from below as well, by the topic `guide` prints them under, so the same line
works with the layer and without it: `perf-hunter` reads `echolot guide report`
instead of working the schema out by hand, and setup reads
`echolot guide collect` to capture the probe trace while there is still no
config.

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
    if round == loop.max_rounds → exit, before recording what nobody
                                  would analyse
    otherwise: pick a blind spot (usually uninstrumented_cpu)
               add AGENTTMP_ markers inside instrumentation.allowed
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

### From a shell

`/echolot` runs all of this for you. By hand, or to look back:

```bash
echolot hunt "cold start was 3s, now 7s" --since "the tab redesign"
```

That opens one, moves the previous set of traces aside without deleting it,
and says whether the last investigation left temporary markers in your
sources. `echolot hunt` on its own says what is open.

```
echolot hunt --list          every investigation, newest first
echolot hunt --show 2        one of them in full — including where its traces went
echolot hunt --resume        carry on with the open one
echolot hunt --done "..."    record what it came to
```

Each one is numbered, and everything it produces is filed under it: every
round of traces by path, every report as a copy in
`.echolot/hunts/<n>/reports/`. So a question asked three weeks ago still knows
what was measured to answer it, and what each round concluded on the way.

You rarely type any of it. `/echolot` reads the state and, when an
investigation has been sitting untouched with traces behind it, asks whether
to carry on or start something new — and never asks inside the hunting loop,
which re-records and re-instruments on purpose.

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
