# The page in the browser

[← back to the documentation index](README.md)

`echolot` says where a project stands in a terminal, to someone who reads
terminals. `echolot ui` says the same on a page: whether everything is ready,
a button that checks the app, and the reports and the agent's answers there
are, each opened as a page.

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
| the project is not set up, has no `echolot.yml`, or the config does not load | that the step under **What to do next** comes first |
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
echolot compare .echolot/out/before.json -c echolot.yml
```

`analyze` writes the new report over the last one, so the last one is copied
to `before.json` beside it first. What `compare` writes stays where it writes
it, `comparison.md` and `comparison.json` beside the report, and the page says
in `compare`'s own words that a comparison was made. A page for the comparison
is not built yet.

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

**What to do next** is there when the next step is not the button's: `echolot
init` in a project not set up yet, `echolot doctor` after a self-check that
did not pass, and the agent's command where the step is an agent's (writing
`echolot.yml`, resuming an investigation). It is the step `echolot` names,
said in a plain sentence, with the command to copy.

**Reports** lists the last report of each scenario, with its level, its first
line, when it was made and from how many launches, and how many checks found
something. Then every investigation: what was asked, how many times the app
was recorded, how long it has been open or when it ended, what it came to,
and the reports it filed. A report opens as [the report page](analysing.md#the-report-as-a-page),
drawn from its `report.json` at that moment; a file that does not read is
said, not linked.

## The other pages

The top bar is a path through them: **Start**, **Report**, **Agent's answer**.
A step is there once it has something to show.

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
- the time limit, `budget_ms`;
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
  else is refused. Two such requests exist, both a form of the start page or
  of the answer's page: the press of the button, and Stop.
- Such a request may carry a form of at most 4096 bytes. One that carries
  more, or does not say how much, is refused unread. Nothing in a form is
  used as a path or as a command: a scenario has to be one the config names,
  a number of launches a whole number from 1 to 20, an investigation one the
  project has.
- It serves the pages it draws and no file. No address is turned into a path
  on disk.
- A page fetches nothing from anywhere else: no font, no script, no image.
  The browser is told to hold it to that. A page the server serves may do two
  things a report written to disk may not: send a form to the server it came
  from, and ask it how far a run is. A page on disk keeps the strict policy.

Two at once are fine: each has its own port and its own token.

## What it writes

Reading a page writes nothing. A press does, all of it under `.echolot/`:

| file | what it is |
|---|---|
| `.echolot/log/check.json` | the last check: its process, each command with its state and exit code, how it ended |
| `.echolot/log/check.collect.log`, `check.analyze.log`, `check.compare.log` | what each command printed, as it printed it |
| `.echolot/log/check.stop` | a request to stop, while one is pending |
| `before.json` beside the report | the report before a **Check again**, for `compare` |

The commands themselves write what they always write: the recordings, the
report, the comparison, their own lines in `.echolot/log/runs.jsonl`. The
server leaves its own line there when it stops, the line every command
leaves. `ECHOLOT_NO_RECORD=1` switches those lines off.
