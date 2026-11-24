# The page in the browser

[← back to the documentation index](README.md) · **English** · [Русский](ru/ui.md)

`echolot` says where a project stands in a terminal, to someone who reads
terminals. `echolot ui` says the same on a page: whether everything is ready,
a button that checks the app, and the reports and the agent's answers there
are, each opened as a page. In a project that has no config yet it sets one
up first, for how the app opens. From a report it can start your own Claude
Code on the checks you tick, and show its work as it goes.

```bash
echolot ui              # starts it and opens the browser
echolot ui --no-open    # prints the address and leaves the browser alone
echolot ui --port 8123  # on this port instead of a free one
```

It runs until Ctrl-C. Start it in the project's folder, the one `echolot.yml`
is in or will be in; `-c` names a config elsewhere.

The page is a face for a person over the same commands and files. It reads the
project anew every time it is opened and keeps nothing in memory between two
pages. An agent's path stays the command line.

## Setting a project up

On a project with no `echolot.yml` the start page asks **What do you want to
check?** and has one answer: **How the app opens**, from the tap on its icon
until the first screen is there. It is the one scenario nobody has to
describe: its window is the launch as the phone records it
([`scenario.window: launch`](analysing.md#a-window-that-is-the-launch)), and
`echolot scan` drafts the whole config for it from the build files.

**Set it up** writes that draft as `echolot.yml`: the text `echolot scan`
prints under "A config to start from", with two lines of comment above it.
Where echolot was never set up in the project it also runs `echolot init`:
the files for an agent, the lines in `.gitignore`, and the self-check of this
computer, which takes about a minute. That part runs as a process beside the
server, so the page shows it under way and moves on to the button by itself.

Before the press the page says two things.

**Whose app it is**: the name the app installs under and the build that will
be measured, as `scan` read them. This is what the page asks you to confirm,
because it is what you can recognise, and the press is the answer. Nothing in
the file is marked as confirmed by a person (`_source: confirmed_by_user`):
every value keeps `scan`'s own mark and its evidence. You looked at two names,
not at the process pattern or the runner's numbers, and an agent that later
finds one of those wrong can still put it right.

**What it writes**, under a fold: every file, by what it is for, and the text
of the config. Every file is new, except a `.gitignore` or a
`.claude/settings.json` of your own, which get lines added. `echolot.yml` is
created or nothing is: one that exists, or that appears between the page and
the press, stops the press, and the page says that nothing was written.

**The time limit** is a sentence with a field in it, "It should take no
longer than … seconds", from 0.1 to 60. It is optional, and it is written as
`scenario.budget_ms`, in milliseconds. With a limit the report's first line
says whether the app opens fast enough. Without one it says how long the
launch takes and holds that against nothing: its level is "no time limit".
A report whose window is not the scenario's, because an anchor matched
nothing, is "not checked" with a limit too, and its card on the start page
says what was not found.

The limit is one line of the file, under the scenario's window, with a
comment of the page's own; with no limit the same line is there commented
out. Most people know the number only once the first report says "No time
limit is set", so the page can set or change it later, in that line or in
the limit's line of any `echolot.yml`: see
[The time limit, later](#the-time-limit-later).

**Something else**, one screen or one action, stays an agent's setup: it has
to learn from the code how to get there and where the wait begins and ends.
The page says so in one line under the button, with the command to copy.

**Where the page cannot set a project up** it says why in a sentence of its
own, with `echolot scan`'s words under it, and the step stays the one under
**What to do next**:

| what `scan` found | the page says |
|---|---|
| no module with a launcher Activity | that no Android app is in this folder |
| an `applicationId`, or a suffix of the build to measure, set in a way `scan` does not read (a constant or an enum entry of `buildSrc` or `build-logic` it does read) | that it could not read the app's name from the build files |

A macrobenchmark that measures the launch (`StartupTimingMetric`) is set up
like an app with none: the same window, run by the benchmark. One that
measures a section of its own, or names no metric `scan` reads, needs where
its window begins and ends picked from a trial recording, so the page sets up
how the app opens instead, as for an app with no benchmark: echolot opens the
app itself (`runner.mode: launch`). A line beside the app's name names the
benchmark and says an agent can set it up afterwards.

Some steps come first, as before: files written by a newer echolot, a
self-check that did not pass, and `echolot init` in a project that has an
older echolot's files or has already chosen its agents. There `init` brings
files up to date and says itself which, so it stays a command to copy; once
it ran, the page offers the config. A server started with `-c` naming another
file sets nothing up: the page creates `echolot.yml` and no file under a name
somebody typed.

**A config with no scenario** measures everything a recording holds. The page
shows the lines that make the launch its scenario, to copy and add by hand.
It writes into no file that is yours.

**After setup** the page is the button's. Until the scenario has been
recorded once it also says what stands between the two: the build you want
measured has to be on the phone, because in `launch` mode echolot opens what
is installed there and installs nothing. It names the gradle task that
installs it where the build files say one. And the phone has to be connected,
which its line under **Ready** says.

**When setup does not finish**, `echolot.yml` is written already. If `echolot
init` failed, the page shows the last lines it printed and the file with all
of them; if it was cut short (`echolot ui` stopped, the terminal closed), the
page says so. Either way the step under **What to do next** takes it from
there.

### The time limit, later

The same sentence with its field, "It should take no longer than … seconds",
stands beside the verdict of the report the page shows, and under **All
settings › Time limit**, with the limit in force in it and the line of
`echolot.yml` a press changes. **Set the limit** writes the number; an empty
field takes the limit off.

Which file the page may change is a matter of what it holds, not of who wrote
it: a config written by hand, by `echolot scan` or by an agent gets the field
as the page's own does, and so does each scenario of a config that names
several (the report of a scenario sets that scenario's limit). A press
changes exactly one line of `echolot.yml`, or adds one:

- a `budget_ms` line under the scenario gets the new number, and keeps
  everything else on it, your comment and your spacing included. An empty
  field turns it into a comment, `# budget_ms: 500`, which the next press with
  a number turns back. The line the page wrote itself is written as the page
  writes it, its seconds brought up to date;
- `budget_ms` commented out under the scenario is that line: a press takes
  the `#` off;
- a scenario with no `budget_ms` at all gets the page's own line, as the
  first of its keys and at their indentation;
- a scenario written on one line, `{name: coldStart, budget_ms: 500}`, gets
  the new number; the page cannot take that limit off, and says so.

Every other byte of the file stays as it is, your comments, blank lines and
the order of the keys included. The file is written beside the old one, read
back first (it has to say what it said before with that one limit changed,
for every scenario), loaded the way every command loads it, and only then put
in its place, so nothing ever reads half of it.

The page changes nothing, says why in a sentence, and names the line to edit
where:

- the scenario is written on one line with no limit in it, or its limit is
  not a number on its own line: on the line below the key, or taken from an
  anchor elsewhere;
- `local.yml` beside it sets the limit, which wins over `echolot.yml`;
- `echolot.yml` is a link to another file or has a second name, does not
  load, or `echolot ui` was started with `-c` and reads another file;
- the file changed between the page and the press. The page knows which file
  it showed you, and a press that finds another one writes nothing. The same
  press sent twice is one press.

`analyze` reads the limit when it reads the recordings, so after a change the
report is still the one made with the old limit. The page says so and offers
**Read the recordings again**: the button's own reading of the recordings
that are here, without the phone, and with no comparison after it, since the
recordings are the same. The verdict changes when that report is written;
the page works none out by itself.

A report written to disk by `analyze --html` has no field: a file cannot
change a config.

## Check the app

One button records the app, reads the recordings and shows the report. It runs
what you would run by hand, as the commands they are, so the report is the one
the terminal gives:

```bash
echolot collect -c echolot.yml -n 3
echolot analyze .echolot/traces/coldStart_iter*.perfetto-trace -c echolot.yml --html
```

The line beside the button says what a press does before you press it: how
many launches, about how long, and that nothing in your code changes. The
length comes from the config: the launches times `runner.duration_ms`, a few
seconds around each launch, and the reading. With a macrobenchmark
(`runner.mode: gradle`) the benchmark decides how many launches it takes, and
the line says so.

**When it cannot run** the button is disabled and the line says why:

| what is in the way | the line says |
|---|---|
| no phone, several, or one that has not allowed debugging | the phone's line under **Ready** as it is: the sentence `collect` stops with, and what to do |
| an app a recording would not show, or one with debuggable builds only | the same, from the app's line |
| the project is not set up, the config does not load, or there is no `echolot.yml` and the page cannot [set one up](#setting-a-project-up) | that the step under **What to do next** comes first |
| a setup is still under way | that echolot is checking this computer, and the button comes once it is done |
| the last self-check did not pass | that no report made here could be trusted |
| an investigation is open | that an agent works with the recordings, and its answer comes first |
| a recording runs already, started from a terminal or by an agent | what it is and how far, and to wait for it |

A press that reaches the server all the same is held against the project as
it stands at that moment, and refused with the same sentence.

**A run in progress** takes the button's place: **Stop**, and a list with a
row per launch, one for reading the recordings and, when a comparison follows,
one for that. Every row is there from the start, so what is left can be seen.
A row is waiting, in progress or done, by its icon and by its words. You can
watch the phone meanwhile: the app opens and closes by itself.

The page learns how far a run is from two files, and from nothing kept in the
server: the run's own (`.echolot/log/check.json`) and the one `collect` writes
after every launch (`.echolot/log/collect.json`, the file `echolot` reads its
`collect` line from). A page loaded again, a second tab and an `echolot ui`
started again all show the same run.

**Stop** ends the recording, keeps the recordings already finished and
analyses nothing. The command in progress is asked to end the way Ctrl-C asks,
so a recording puts back what it changed on the phone. The page then says
what was kept: "Stopped. 2 recordings are kept, and nothing was analysed."

**One check runs at a time.** A second press, from another tab too, starts
nothing and is told what is running.

**How a run ends**, each said in a sentence of the page's own:

| it | the page shows |
|---|---|
| finished | "Done. The short version:", how many checks stand at each level, **See the report** and **Check again** |
| was stopped | what was kept |
| failed | which step did not finish and what was kept; then the command as you would type it, the last lines it printed, as it printed them, and the file with all of them |
| was cut short from outside (`echolot ui` stopped, the terminal closed, the computer asleep) | that it was cut short, and what was kept. A run whose process is gone is never shown as still running |

Ctrl-C in the terminal of `echolot ui` ends a check that is running, the way
**Stop** does, and the page of the next start says that it was cut short.

A run's last word stays on the start page while it is the latest thing that
happened, for a day at most. Once a recording is made or a report written
some other way, or the day is over, the page shows the project as it stands.

**Check again** is the button once a report exists. It does the same and then
compares the new report with the one before it:

```bash
echolot compare .echolot/out/before.json -c echolot.yml --html
```

`analyze` writes the new report over the last one, so the last one is copied
to `before.json` beside it first. What `compare` writes stays where it writes
it, `comparison.md`, `comparison.json` and `comparison.html` beside the report.
The start page says what came of it in the words of the comparison's
[plain reading](compare.md#a-plain-reading), "Faster by 0.7 s. The launch went
from 2.1 s to 1.4 s, 33% less. Measured on 3 launches before and 3 after.",
and **See what changed** leads to the page of the comparison, **After the
fix**.

**Recordings that are already there** are read without the phone: when a
scenario has recordings newer than its report, or recordings and no report,
the button is **Read the recordings**, and it runs `analyze` alone. **Record
again** beside it leaves them and records anew. Recordings in a folder
elsewhere (a CI artefact, a macrobenchmark's output directory) are not read
from the page: name them to `echolot analyze` in a terminal, and the report
appears on the page.

**Several scenarios** in the config are cards to choose from, each with what it
measures and where it stands: not checked yet, recordings that wait, or when
it was last checked. The scenario goes to the commands as `--scenario`.

**The number of launches** for one press is under **All settings**, from 1
to 20. It starts at `runner.iterations` in the config, the number `echolot
collect` takes in a terminal and **Check again** on an investigation's page
records (5 where the config does not say; 3 where its value is not a whole
number). Three is quick and already shows how much the launches differ; to be
sure which changes hold, a comparison needs four a side, or three against
five. It goes to `collect` as `-n`. Nothing is written to `echolot.yml`.
**All settings** also lists the commands a press runs, with the number typed
in the field.

**With script switched off** in the browser everything above works: the
button and Stop are forms, and the page of a run loads itself again every few
seconds. With script on the list is redrawn in place once a second, and the
button and its line follow the choice of a scenario and the number of launches
as you change them.

## The start page

**Ready**, three sentences, each after an icon that says the same as its words:

| line | green | red | grey |
|---|---|---|---|
| This computer | the last self-check passed | the last self-check did not pass: run `echolot doctor` | no self-check has run here yet |
| The phone | the one `collect` would record on, by its model | none, several, or one that has not allowed debugging, in the sentence `collect` stops with | |
| The app | a recording will show it, and which build to measure | it has no `<profileable android:shell="true" />`, or only debuggable builds | the config names it and its code is not in this folder |

Red is something in the way, and the sentence says what to do. Grey is
something echolot cannot tell from here; it stops nothing. **Refresh**
reads everything anew.

**What to do next** is there when the next step is neither the button's nor
[a setup the page does itself](#setting-a-project-up): `echolot doctor` after
a self-check that did not pass, `echolot init` where it brings an older
install up to date, and the agent's command where the step is an agent's
(writing an `echolot.yml` the page cannot draft, resuming an investigation).
It is the step `echolot` names, said in a plain sentence, with the command to
copy.

**Reports** lists the last report of each scenario, with its level, its first
line, when it was made and from how many launches, and how many checks found
something. Then every investigation: what was asked, how many times the app
was recorded, how long it has been open or when it ended, what it came to,
and the reports it filed. A report opens as [the report page](analysing.md#the-report-as-a-page),
drawn from its `report.json` at that moment; a file that does not read is
said, not linked.

## The other pages

The top bar is a path through them: **Start**, **Report**, **Agent's answer**,
**After the fix**. A step is there once it has something to show.

- **Report** is the last report of the scenario the start page is about.
- **Agent's answer** is the newest investigation as
  [a page](agent-layer.md#the-answer-as-a-page), the one
  `echolot hunt --show --html` writes, drawn at that moment from the
  investigation's record and the reports it filed. What it shows follows the
  investigation: the answer with its causes once the agent closed it with one,
  the text it was closed with, or, while it is still open, the rounds so far,
  with the answer taking the page's place when it closes.
- Every earlier investigation has the same page behind the first link of its
  row on the start page. The link says what it leads to: "Agent's answer",
  "The rounds so far" for one still open, "Its rounds" for one that was left
  when the next question was asked.

- **After the fix** is [the comparison as a page](compare.md#the-comparison-as-a-page):
  the last report against the one before it. It is there once a report has
  been compared and the comparison is of the report the **Report** step shows:
  a comparison older than that report is of the one before, and is not shown.
  It is drawn from `comparison.json` at that moment, so it needs the plain
  reading in it, which `compare --html` and `compare --plain` write; a
  comparison made in a terminal with neither flag is a table for a terminal,
  and the step stays away. With several scenarios each has its own, behind
  the link of its own run.

On the answer's page **Check again** stands beside the line that page gives to
copy, and runs that line: `collect` with the config's own number of launches,
`analyze`, and `compare` against the first report the investigation filed,
which is the app as it was before anyone touched it. The run is shown on the
start page, and there the comparison's row and the sentence under the run
name that report: "Comparing with round 1 of investigation #5", "It was
compared with round 1 of investigation #5". A press on the start page
compares with the report before it, and says that.

**All settings** is a fold. It holds the number of launches for the button,
the commands a press runs, and, read-only, what the page reads for you, with
the words `echolot` and `echolot scan` say about each row. The page says
things in sentences; the terminal's own lines are here:

- the config, and the scenario the page is about (the open investigation's,
  else the first);
- the build to measure, and whether to measure on each of the others;
- how the scenario runs on the phone: `runner.mode` with its command or its
  gradle task;
- where the window opens and closes, or that it is the launch itself
  (`window: launch`);
- how many runs, and for how long each records; with a macrobenchmark, that
  the benchmark decides;
- the time limit, `budget_ms`, with the field that sets or changes it where
  the file holds it on a line of its own, whoever wrote it, and otherwise why
  not and the line to edit;
- the thresholds: how many checks run on the config's, and each one with
  where it comes from: built-in, set in the config, set for this scenario,
  or switched off;
- the versions of echolot and of trace_processor;
- what `echolot` prints behind the sentences above the fold: of the
  self-check, the devices, the next step, the recordings and the reports, and
  what `echolot hunt --list` prints of the investigations.

## Dig deeper from the page

A report says what is slow; an agent finds where in the code and what to
change. On a report, tick the checks to start from and press **Dig deeper**.
The button beside it ticks where to start: the checks that need attention, or
with none of those the two that found the largest thing. One investigation
answers one question, so the request
([as the report puts it together](analysing.md#the-report-as-a-page)) leads
with one, carries the report's warnings, and lists the ticked rows as its
evidence. With more than three ticked, the page says that one investigation
looks at one question and suggests ticking fewer.

The dialog has **Copy the request**, for an agent of your own, and, where
the page can start one, **Start it from here**. echolot has no model of its
own: what it starts is Claude Code or Codex, the one installed on this
computer, under your own account. The button is there when Claude Code is
installed, is 2.1.259 or newer, and says it is signed in (`claude auth
status`), or when Codex is installed, is 0.159 or newer, says it is signed in
(`codex login status`) and the project has what it needs
([below](#with-codex)); otherwise the dialog says what is missing, for each
one installed here, and the request is to copy. Where one can start and the
other is installed and cannot, the dialog and the screen before the start
say why that one cannot, such as the rules Codex needs and the
`echolot init --for …,codex` that writes them. Cursor is not started from the
page yet: its request is copied.

Every client needs a folder to put its markers in: with none in
`instrumentation.allowed`, the page starts no agent. The dialog has no
**Start it from here** then, and says so once, with where to name the
folders (`echolot.yml`, under `instrumentation:`) and how
(`allowed: ["app/src/main", "feature/*/src/main"]`); no screen offers one
client in place of the other. The `echolot.yml` that **Set it up** writes
names the folders `scan` found the app's code in.

**Before anything starts**, one screen says what a press of **Start** agrees
to, in plain words:

- **Who does it**: Claude Code, its version and the account it is signed in
  with. It counts against your plan or your API key like any session of
  yours, and stops by itself at about $10 of usage by its own estimate.
- **How long**: usually tens of minutes. `echolot ui` has to keep running, and
  the phone connected and unlocked: closing `echolot ui` stops the agent too.
- **What it does**: temporary markers into the folders `instrumentation.allowed`
  names, a rebuild, the app put on the phone and recorded again, for up to
  `loop.max_rounds` rounds; at the end the markers taken out and the answer
  written down.
- **What it may do without asking**, exactly: `echolot` (any command),
  `./gradlew` (any task), `adb` (any command on the phone), editing files
  under the allowed folders, and writing `.echolot/verdict.json`. It reads the
  project and runs commands that only read. Everything else is refused
  without asking anyone: git, downloads, any other program, any other file.
  A question it wants to ask is refused the same way, and it goes on with
  what it has. Allow rules in the project's own `.claude/settings.json` apply
  too, and the screen names any beyond echolot's.
- **What changes**: the code on disk while it works, with nothing committed
  (and how many files were already changed); the phone keeps a build with
  markers until the app is built again; Claude Code keeps its own record of
  the session, as for every session.
- **Stop**: ends Claude Code and everything it started.

The same screen has the two commands that start, and the request in full,
with **Copy the request** as the way out. A press of **Start** runs them one
after the other, in the project's folder:

```text
echolot hunt "<the question>" --since unknown
claude -p "<the request>" --output-format json --permission-mode dontAsk --permission-prompts none --setting-sources project --max-budget-usd 10 --allowedTools "Bash(echolot *)" "Bash(./gradlew *)" "Bash(adb *)" "Edit(/app/src/main/**)" "Edit(/.echolot/verdict.json)"
```

The server builds both from the config and the report on disk, never from
what the browser sent: a press names ticked checks, and they are matched
against the report as it is at that moment. There is one `Edit(…)` rule per
entry of `instrumentation.allowed`, and with none listed the page starts
nothing. No permission is bypassed: `dontAsk` refuses what no rule allows,
`--permission-prompts none` refuses a prompt nobody is there to answer, and
`--setting-sources project` leaves your own settings, hooks and plugins out
while the project's commands for Claude Code, which `echolot init` installs,
come in. The request says that nobody is at the keyboard, which investigation
is open for it (every `collect` and `analyze` goes into it with `--hunt <n>`)
and on which recordings, what it may do, and to close the investigation with
its verdict.

**While it works**, its investigation's page is where the browser goes. The
rounds come in as they always do (each report it files, round by round), and
above them a line says what the agent is doing, read from what runs under it
and never from what it prints: building the app, putting it on the phone,
recording, reading the recordings, putting markers in, looking at the
investigation, writing down the answer (only while it closes the
investigation), or, with none of those, reading the code and deciding. When nothing
has moved for about ten minutes (no round, no build, no recording), the page
says so beside **Stop**. A build can take that long, and the page names it
when one is still running.

**Stop** sends Ctrl-C to Claude Code and to everything under it, as a
terminal does to a whole job: a recording unwinds and puts the phone back. What
is still there after 30 seconds is ended, then killed. The page then counts
what `hunt` counts in the code: the temporary markers still there, how many
`echolot mark --remove` takes out, and the files with markers added by hand.
A line tagged `// echolot:mark` that holds more than a marker, such as an
agent's one-line `try { work() } finally { android.os.Trace.endSection() }`,
is one `--remove` leaves; the page names each by its file and line, to take
out by hand, and does not say the code is clean while one is there.
**Take the markers out** runs `echolot mark --remove`; the app on the phone
keeps the markers until it is built and installed again.

**When it ends**:

- closed with a verdict: the investigation's page is the answer, with one
  quiet line on how long it ran and what Claude Code says it spent;
- closed with a few words and no verdict: those words head the page;
- stopped: said so, with what it left in the code;
- stopped at the spending cap: "It reached the spending cap of $10", with
  what Claude Code reports it spent, which its own estimate can put past the
  cap;
- failed: "Claude Code stopped with an error", with what it said (a sign-in
  that ran out, a limit of your plan) and where all it printed is;
- cut short, when `echolot ui` stopped or the computer slept: said so;
- finished with no answer written down: said so, with its last words.

Unless the agent closed it, the investigation stays open, and **Check the
app** waits while it is open: **Close the investigation** closes it with what
came of it (`echolot hunt --done "Stopped from echolot ui, after 2 rounds, with
no answer. …"`). Those words head the page from then on, and the line on how
the agent ended says the page closed it after that, as in "It ended before it
had an answer, and the investigation was closed from this page after that". It
says the agent closed the investigation only when the agent did, while it ran;
one closed by hand after it ended is said to be closed after that. What it
tried and was refused is listed under the end, each command as its first line,
with whether it mattered: each refusal cost a turn, and a run with no answer
may have been refused a step it needed.

### With Codex

Codex is started the same way, from the same screen, and four things about it
differ: where its grant is, what it may edit, what it does when nobody
answers, and what it says at the end.

**Which one.** Where both can start, the screen before the start shows one,
and **Use Codex instead** (or **Use Claude Code instead**) leads to the
other's screen. Each says what its own client may do, and **Start** starts the
one on the screen. The first shown is the one the last run started from the
page used, and otherwise Claude Code, whose own rules keep its edits to the
allowed folders.

**What the project needs**: a git repository, which Codex asks for so that
what it changes can be told and undone; no mark of `untrusted` for it in your
Codex settings; and `.codex/rules/echolot.rules` as this echolot writes it,
with the rules that let `echolot`, `./gradlew` and `adb` out of Codex's
sandbox ([Codex's rule, from `init`](agent-layer.md#codexs-rule-from-init)).
`echolot init --for …,codex` writes that file. One an older echolot wrote,
with the rule for `echolot` alone, is said to be older, with the `init` that
brings it up to date.

**What its screen says besides Claude Code's**:

- Codex marks the project trusted in your own `~/.codex/config.toml`, as it
  does the first time it works in a project. That is a change outside the
  project, and it stays; where the file trusts the project already, the
  screen says so.
- Its edits cannot be kept to the allowed folders, so the page notes the
  project's files when it starts and lists afterwards the ones changed
  outside them.
- There is no spending cap to set: the page shows the tokens Codex reports
  at the end.
- If nothing moves for 30 minutes, the page stops it.
- Rules files of your own that Codex reads too, in `.codex/rules/` and
  `~/.codex/rules/`, are named: what they allow applies too.

```text
echolot hunt "<the question>" --since unknown
codex exec --json -s workspace-write -C /path/to/the/project "<the request>"
```

Under `exec` Codex asks nothing. A command runs in its sandbox, which has no
network and writes only inside the project, and a command line a rule lets
out runs outside it. No flag bypasses its approvals or its sandbox. The
request is the same, its facts word for word, `--hunt <n>` among them, with
the lines Codex needs in place of Claude Code's: that `/echolot-hunt` is Claude
Code's command and `echolot guide hunt` prints the same steps, and that a rule
lets a command out only when it is that command alone, with no pipe or `&&`.

**Nobody there.** Asked a question with nobody to answer, Codex takes it and
then waits, for hours. The request says that nobody can answer. After ten
minutes with nothing moving, the page says so, as for Claude Code, and that it
will stop Codex at half an hour. After 30 minutes with no event printed, no
round filed and nothing running under it, the page ends it as **Stop** does,
says why, and shows the question it was waiting on as its last words.
Programs your own Codex settings start with it, such as MCP servers, count as
its own too: they run the whole time and say nothing. A build or a recording
that runs long is something running, and is never ended this way; nor is
anything where the processes cannot be listed (Windows), since a long build
there could not be told from a wait.

**Stop** sends Ctrl-C to everything under Codex, then to Codex, as for Claude
Code. Codex starts each command in a process group of its own, which a signal
to Codex's own group would not reach; the page finds each one by the process
it was started from.

**When it ends**, the page reads Codex's events: its last message, the tokens
it used, and what its sandbox refused or Codex declined, which shows only as a
failed command, since its exit code is 0 even then. Whichever way it ended,
the page lists the files it changed outside the allowed folders, written, new
or gone, or says that it changed none. `.echolot/` and the folders a build or
the IDE writes (`build/`, `.gradle/`, `.idea/`) are not looked at. A file you
change yourself while it works is listed too; one changed after it ended is
not.

One thing runs at a time: a check and an agent do not run together, in either
order. While an agent works, the start page says so above the button, and the
button says why it waits; while a check runs, **Dig deeper** says that it
does. With script switched off every step is a form: the report lists the
checks as boxes to tick, and the agent's page loads itself again every five
seconds.

## What the server answers, and what it refuses

It is a server from Python's standard library, and it is built to be asked by
the page it opened and by nothing else:

- It listens on `127.0.0.1` only. A request that names the machine any other
  way, in its `Host`, is refused.
- A token is made at every start. It is the first part of the address
  echolot prints and opens, and of every link on its pages; a request without
  it is refused. The token is in the address and in no cookie: a browser sends
  a cookie along with any request to the address, whichever page asks, and
  shares it among all ports of `127.0.0.1`. An address has to be known. When
  the server stops, the token means nothing.
- Anything other than reading a page also has to come from the server's own
  pages: the browser says where a request comes from, and one from anywhere
  else is refused. Such requests are each a form of the start page, the
  report's page or the answer's page: the press of the button, Stop,
  **Set it up**, **Set the limit**, and those of **Dig deeper**: the screen
  before it starts, for either client, Start, its Stop, **Take the markers
  out** and **Close the investigation**.
- Such a request may carry a form of at most 4096 bytes. One that carries
  more, or does not say how much, is refused unread, and so is one that ends
  before the length it stated. Nothing in a form is
  used as a path or as a command: a scenario has to be one the config names,
  a number of launches a whole number from 1 to 20, an investigation one the
  project has, what to set up one of the names the page offered, and a time
  limit a number of seconds from 0.1 to 60, a ticked check one the report on
  disk has and that found something. The one file a setup creates is
  `echolot.yml` in the project's folder, whatever a form says, and the one
  file **Set the limit** changes is that same file, one line of it, and the
  scenario it names is one the config has. What the
  page saw of it travels as two fingerprints, which are compared and never
  opened. A client named in an address is one the page starts, Claude Code
  or Codex. An agent's command line is built from the config and the report
  alone.
- It serves the pages it draws and no file. No address is turned into a path
  on disk.
- An address a form is sent to is no page. Read, by a reload of what a press
  drew or by a step back to it in the history, it starts nothing and sends
  the browser where that press is made: the screen before an agent starts
  goes back to its report, where **Dig deeper** is pressed again; **Stop**,
  **Take the markers out** and **Close the investigation** to the agent's
  answer; the rest to the start page.
- A page fetches nothing from anywhere else: no font, no script, no image.
  The browser is told to hold it to that. A page the server serves may do two
  things a report written to disk may not: send a form to the server it came
  from, and ask it how far a run is. A page on disk keeps the strict policy.

Two at once are fine: each has its own port and its own token.

## What it writes

Reading a page writes nothing. A press of the button does, all of it under
`.echolot/`:

| file | what it is |
|---|---|
| `.echolot/log/check.json` | the last check: its process, each command with its state and exit code, how it ended |
| `.echolot/log/check.collect.log`, `check.analyze.log`, `check.compare.log` | what each command printed, as it printed it |
| `.echolot/log/check.stop` | a request to stop, while one is pending |
| `before.json` beside the report | the report before a **Check again**, for `compare` |
| `comparison.html` beside the report | the comparison as a page, the one **After the fix** shows; `compare --html` writes it with the comparison |

**Dig deeper** writes what its two commands write: `echolot hunt` opens the
investigation (`.echolot/hunt.json`, the recordings set aside under
`.echolot/traces/`), and the agent changes the code, Claude Code only under
the allowed folders, and writes `.echolot/verdict.json`. Codex also marks the
project trusted in your own `~/.codex/config.toml`, by itself. The page's own
files are its run:

| file | what it is |
|---|---|
| `.echolot/log/dig.json` | the last agent the page started: its process, its command line, the investigation it works in, how it ended |
| `.echolot/log/dig.agent.log` | what the agent printed: Claude Code's one JSON object at the end, or Codex's events, one JSON object a line, as they come |
| `.echolot/log/dig.files.json` | with Codex: the project's files when it started, a size and a time each, for the list of what it changed |
| `.echolot/log/dig.stop` | a request to stop, while one is pending |

**Set it up** writes into the project itself, once, and the page lists every
file before the press:

| file | what it is |
|---|---|
| `echolot.yml` | the config `echolot scan` drafts, created and never written over |
| what `echolot init` writes | the files for an agent, lines in `.gitignore`, `.echolot/hosts.json`: only where echolot was never set up here |
| `.echolot/log/setup.json`, `setup.init.log` | that run of `init`: its state, and what it printed |

**Set the limit** writes one file, `echolot.yml`, and one line of it: the
scenario's `budget_ms` line, or that line added where there was none. Nothing
else is written, not even a file beside it that stays.

The commands themselves write what they always write: the recordings, the
report, the comparison, their own lines in `.echolot/log/runs.jsonl`. The
server leaves its own line there when it stops, the line every command
leaves. `ECHOLOT_NO_RECORD=1` switches those lines off.
