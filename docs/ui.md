# The page in the browser

[← back to the documentation index](README.md)

`echolot` says where a project stands in a terminal, to someone who reads
terminals. `echolot ui` says the same on a page: whether everything is ready,
what to do next, and the reports there are, each opened as a page.

```bash
echolot ui              # starts it and opens the browser
echolot ui --no-open    # prints the address and leaves the browser alone
echolot ui --port 8123  # on this port instead of a free one
```

It runs until Ctrl-C. Start it in the project's folder, the one `echolot.yml`
is in or will be in; `-c` names a config elsewhere.

The page is a face for a person over the same commands and files. It reads the
project anew every time it is opened, changes nothing in it and starts no
command: the next step is a command to copy. An agent's path stays the
command line.

## The start page

**Ready**, three sentences, each after an icon that says the same as its words:

| line | green | red | grey |
|---|---|---|---|
| This computer | the last self-check passed | the last self-check did not pass: run `echolot doctor` | no self-check has run here yet |
| The phone | the one `collect` would record on, by its model | none, several, or one that has not allowed debugging, in the sentence `collect` stops with | |
| The app | a recording will show it, and which build to measure | it has no `<profileable android:shell="true" />`, or only debuggable builds | the config names it and its code is not in this folder |

Red is something in the way, and the sentence says what to do. Grey is
something echolot cannot tell from here; it stops nothing. **Check again**
reads everything anew.

**What to do next** is the step `echolot` names, said in a plain sentence, with
the command to copy: `echolot init` in a project not set up yet, `echolot
collect` while there are no recordings, `echolot analyze` over them once there
are, and the agent's command where the step is an agent's (writing
`echolot.yml`, resuming an investigation).

**Reports** lists the last report of each scenario, with its level, its first
line, when it was made and from how many launches, and how many checks found
something. Then every investigation: what was asked, how many times the app
was recorded, how long it has been open or when it ended, what it came to,
and the reports it filed. A report opens as [the report page](analysing.md#the-report-as-a-page),
drawn from its `report.json` at that moment; a file that does not read is
said, not linked.

**All settings** is a fold, read-only. It holds what the page reads for you,
and under each row the words `echolot` and `echolot scan` say about it. The
page says things in sentences; the terminal's own lines are here:

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
  pages. Nothing on the pages sends such a request yet.
- It serves the pages it draws and no file. No address is turned into a path
  on disk.
- A page fetches nothing: no font, no script, no image. The browser is told
  to hold it to that, as with a report written to disk.

Two at once are fine: each has its own port and its own token.

It writes nothing but its own line in `.echolot/log/runs.jsonl`, the line
every command leaves, when it stops. `ECHOLOT_NO_RECORD=1` switches that off.
