# The page in the browser

[← back to the documentation index](README.md) · **English** · [Русский](ru/ui.md)

`echolot` says where a project stands in a terminal, to someone who reads
terminals. `echolot ui` says the same on a page: whether everything is ready,
a button that checks the app, and the reports and the agent's answers there
are, each opened as a page. In a project that has no config yet it sets one
up first, for how the app opens.

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
launch takes and holds that against nothing: its level is "not checked".

The limit is one line of the file, under the scenario's window, with a
comment of the page's own; with no limit the same line is there commented
out. Most people know the number only once the first report says "No time
limit is set", so the page can set or change it later in that line, and
nowhere else: see [The time limit, later](#the-time-limit-later).

**Something else**, one screen or one action, stays an agent's setup: it has
to learn from the code how to get there and where the wait begins and ends.
The page says so in one line under the button, with the command to copy.

**Where the page cannot set a project up** it says why in a sentence of its
own, with `echolot scan`'s words under it, and the step stays the one under
**What to do next**:

| what `scan` found | the page says |
|---|---|
| no module with a launcher Activity | that no Android app is in this folder |
| an `applicationId` set through a constant or a convention plugin | that it could not read the app's name from the build files |
| a macrobenchmark that measures a section of its own, or names no metric `scan` reads | that where it begins and ends has to be picked from a trial recording |

A macrobenchmark that measures the launch (`StartupTimingMetric`) is set up
like an app with none: the same window, run by the benchmark.

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
settings › Time limit**, with the limit in force in it. **Set the limit**
writes the number; an empty field takes the limit off, and the line goes back
to its commented-out form.

A press changes exactly one line of `echolot.yml`: the `budget_ms` line the
page wrote when it set the project up. Every other byte of the file stays as
it is, your comments and blank lines included. The file is written beside
the old one, loaded the way every command loads it, and only then put in its
place, so nothing ever reads half of it.

The page changes that line only while it still reads as the page wrote it:
`budget_ms` two spaces in, under `scenario:`, a whole number of milliseconds,
then the page's own comment and nothing else on the line. The number may be
one you typed there yourself, and the first `#` of the commented-out line may
be gone: both comments ask for exactly that. Where anything else is the case
the page changes nothing, says why in a sentence, and shows the line to edit:

- the line was changed by hand in any other way (a word added, its spacing,
  a number that is not a whole one), moved out of `scenario:`, copied, or
  removed;
- another line of the file sets the limit too;
- the page did not write this `echolot.yml`: it does not begin with the two
  lines the page puts at its top, so a config written by hand or by an agent
  is never touched;
- the config names several scenarios;
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
| no phone, several, or one that has not allowed debugging | the sentence `collect` stops with; the phone's line under **Ready**, marked red, says what to do |
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

**The number of launches** for one press is under **All settings**: 3 unless
you change it, from 1 to 20. Three is quick and already shows how much the
launches differ; to be sure which changes hold, a comparison needs four a
side, or three against five. It goes to `collect` as `-n`. Nothing is written
to `echolot.yml`: `runner.iterations` stays what `echolot collect` takes in a
terminal. **All settings** also lists the commands a press runs.

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
start page.

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
  the page wrote the file, and otherwise why not and the line to edit;
- the thresholds: how many checks run on the config's, and each one with
  where it comes from: built-in, set in the config, set for this scenario,
  or switched off;
- the versions of echolot and of trace_processor;
- what `echolot` prints behind the sentences above the fold: of the
  self-check, the devices, the next step, the recordings and the reports, and
  what `echolot hunt --list` prints of the investigations.

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
  else is refused. Four such requests exist, each a form of the start page,
  the report's page or the answer's page: the press of the button, Stop,
  **Set it up** and **Set the limit**.
- Such a request may carry a form of at most 4096 bytes. One that carries
  more, or does not say how much, is refused unread, and so is one that ends
  before the length it stated. Nothing in a form is
  used as a path or as a command: a scenario has to be one the config names,
  a number of launches a whole number from 1 to 20, an investigation one the
  project has, what to set up one of the names the page offered, and a time
  limit a number of seconds from 0.1 to 60. The one file a setup creates is
  `echolot.yml` in the project's folder, whatever a form says, and the one
  file **Set the limit** changes is that same file, one line of it. What the
  page saw of it travels as two fingerprints, which are compared and never
  opened.
- It serves the pages it draws and no file. No address is turned into a path
  on disk.
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

**Set it up** writes into the project itself, once, and the page lists every
file before the press:

| file | what it is |
|---|---|
| `echolot.yml` | the config `echolot scan` drafts, created and never written over |
| what `echolot init` writes | the files for an agent, lines in `.gitignore`, `.echolot/hosts.json`: only where echolot was never set up here |
| `.echolot/log/setup.json`, `setup.init.log` | that run of `init`: its state, and what it printed |

**Set the limit** writes one file, `echolot.yml`, and one line of it: the
`budget_ms` line the page wrote. Nothing else is written, not even a file
beside it that stays.

The commands themselves write what they always write: the recordings, the
report, the comparison, their own lines in `.echolot/log/runs.jsonl`. The
server leaves its own line there when it stops, the line every command
leaves. `ECHOLOT_NO_RECORD=1` switches those lines off.
