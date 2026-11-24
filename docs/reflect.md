# Reflect: the report over the agent

[← Docs index](README.md) · [README](../README.md) · **English** · [Русский](ru/reflect.md)

`echolot analyze` stands between the trace and the agent. `echolot reflect`
stands between the agent's session and the person maintaining the tool. Same
reason in both places: a session transcript is hundreds of kilobytes to tens
of megabytes, and reading it — by a human or by a model — is slow, expensive
and gives a different answer every time. So the transcript is compressed
deterministically into a table of facts and a short list of signals, and the
decision "what to change" is made over those.

```bash
cd ~/StudioProjects/my-app        # the project the agent worked in
echolot reflect --list            # candidate sessions
echolot reflect --last            # the newest one that used echolot
echolot reflect --session 3f2a    # one session, by the first characters of its id
echolot reflect --hunt 3          # the session that worked inside investigation 3
echolot reflect --since 2h        # every session in the last 2h / 30m / 3d
echolot reflect --all             # every one, plus a summary
```

`--hunt N` reads the session an investigation was worked in, the one the
page started an agent for above all. After such an investigation the newest
session that used echolot is most often the person's own — the one that
started `echolot ui`, or tried a command since — and `--last` read that one.
The run log ties the right one instead: every `collect` and `analyze` run
with `--hunt N` notes `inside: N` on its line, and the line carries the id
of the session whose command it was ([Two sources](#two-sources)). A
session whose transcript is here is read from it, and one whose transcript
is not, from its lines in the log. Lines that carry no id leave the sittings
of the log that hold them, and `reflect` says so. An investigation nothing
ran inside is nothing to reflect on (exit 1), and a number no investigation
has is an error (exit 2). The report's header names the investigation, and
the investigation's answer page names the command.

Output: `.echolot/reflect/<session>.md` for a human, `.json` for the agent —
`/echolot-reflect` reads the json and turns signals into a list of proposed
changes. When a run covers more than one session (`--all`, or `--since` that
finds several) it also writes `summary.md` and `summary.json` beside them: a
row per session and how often each signal fired, which is where a signal
that fires in most sessions shows up as a design item rather than a note.
One session's whole report is printed to a terminal only, the way `analyze`
prints its own ([What `analyze` prints](analysing.md#what-analyze-prints)):
anywhere else it is that session's row of the summary, 15–36 KB less in an
agent's context, and `--full` prints the whole report there too.

Where things are looked for, when they are not where the defaults say:

| flag | default | what it names |
|---|---|---|
| `--project ROOT` | the project this folder is in | the application project the agent worked in — its run log, its config, its source tree |
| `--transcripts DIR` | `~/.claude/projects/<slug>` | where Claude Code's transcripts are; naming it leaves Codex's sessions out |
| `-c`, `--local` | the copy the session's own commands read with `-c`, when they all read one; otherwise `echolot.yml` in the project; `local.yml` beside it | the config the protocol checks read; when it changed after the session ran, the header says so |
| `-o DIR` | `.echolot/reflect` in the project | where the reports go; a path given here is from the current directory, like `-c` |
| `--from-log` | off | ignore any transcript and read the run log alone — see below |

## Two sources

**The tool's own record.** Every command appends one line to
`.echolot/log/runs.jsonl` of its project — the one its `echolot.yml` is in,
found from a subfolder too: what was asked (`argv`), where, when, exit code,
duration, a hash of the config, a traceback if there was one, and a few facts
the command chose to note — how many detectors fired, whether the anchor
matched, what closed a window that is a launch, how many frames the app drew
in it, where the report went, which investigation it ran inside. And which agent session asked, when the host
exports one to its commands: Claude Code's `CLAUDE_CODE_SESSION_ID` (the name
of the session's transcript), Codex's `CODEX_THREAD_ID`, or `ECHOLOT_SESSION`
for any other host, a hook or CI to set. The id is written as given when it
is an opaque token — letters, digits, `.`, `_`, `-` — and not at all
otherwise, so a path never lands in the log; with none, the line has no
`session`, like every line written before there was one. This is the only source that
does not depend on which agent was typing: transcripts differ between agents
and lose the exit code the moment a call is wrapped in `2>&1 | tail`; this
file does not. `ECHOLOT_NO_RECORD=1` switches it off.

**The agent's transcript.** Claude Code keeps every session under
`~/.claude/projects/<slug>/`, the slug being the working directory with `/`
turned into `-`; subagents get their own file under
`<session>/subagents/`. It has what the tool cannot see: the prompts, the
questions to the human and the answers, every tool call with its input and
whether it failed, tokens, the prompt handed to perf-hunter and what came back.
A shell command run through an MCP tool counts like a `Bash` one, each
command of a batch tool as a call of its own; Python or another language run
through an execute tool is read by the checks that read code — a
`report.json` cut up by hand, a trace opened directly.
The format is undocumented; the reader treats every field as optional and
puts anything surprising into the report's "Reader notes" rather than into an
exception.

**Codex's sessions.** Codex keeps each thread under
`~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`, under
`CODEX_HOME` when that is set, with the working directory in its first line.
A subagent is a thread of its own, whose first line names its parent, so a
hunt's loop is found from the main thread and read with it. Commands come
with their exit codes and output, edits as the patches Codex applied, and
tokens per response. Two things are not there to read. A subagent's file
opens with a copy of its parent's history, and only what follows is the
subagent's own. And the message the main thread hands a subagent is
encrypted on disk, so the check on what the brief said (`agent_prompt_gaps`)
is listed as not checked, with that reason, instead of finding a brief with
nothing in it. A subagent is the loop when it ran `echolot guide loop`, which
the hunt skill tells it to read first (#193).

`reflect` runs from the application project because that is where all of
them live. Claude Code's sessions and Codex's are read side by side, newest
first, and `--list` says which agent each came from. Only sessions that used
echolot for real work are candidates — one that merely reflected does not
count, or the newest session would always be the one doing the reflecting. A
reflection is `reflect`, the `guide reflect` its skill runs first, and
`/echolot reflect` or `/echolot-reflect` under any prefix the host adds.

A subagent is a hunt when it is the loop: of type `perf-hunter`, or one that
ran `echolot guide loop`, which both readers can see. An Explore or
general-purpose helper is none, and gets none of the hunt's signals: reading
sources is its job.

## What is in the report

Signals first, then the facts they rest on. Each section exists because of a
decision it lets you make:

| section | what it holds | the decision it serves |
|---|---|---|
| Signals | protocol breaks (`warn`), workarounds and friction (`info`) — each with its rows and a hint | what to change, in what order |
| Protocol checks passed | the checks that came back `ok`, one line each | which rules held, so a short Signals section is read as clean and not as unchecked |
| Not checked | the checks that could not run (`skip`): the source does not carry what they read, the session was building the tool, or the check itself broke | which silences are no verdict |
| Entry | prompts, slash commands, skills loaded, time to real work | is the way into the tool obvious |
| Timeline | milestones: first call of each subcommand, questions, config written, agent launched, first temporary slice | where the time went |
| echolot calls | every invocation: subcommand, args, exit, duration, output size, config used, help lookups | which commands the agent fumbles |
| Questions to the human | each `AskUserQuestion`: options, recommended, chosen, time to answer | are the questions needed, are the defaults right |
| What the main context concluded | its last message, quoted — `conclusion` in the json | what the session came to; for one that read an ANR report or a report and ran no hunt, the whole result |
| Subagent | rounds against `loop.max_rounds`, re-records, tools, tokens, what fed its window, whether the prompt named the traces / the regression / the change, whether the conclusion has all eight fields, and what it returned | is the loop behaving |
| Temporary instrumentation | per file, prefix added vs removed; was there a grep after the last edit; what the whole source tree holds now, counted the way `hunt` and `mark --remove` count it (the prefix or a line tagged `echolot:mark`) | did cleanup happen |
| Cost | wall time, tokens for main and subagents separately, tools by type, largest tool outputs, longest silences | what eats the window |
| Reports the session built | each `analyze`: frames drawn, whether its report on disk is still that run's and its size, how many characters reached the agent, how often it was read back, and what the run's record says about its window | which rounds the session built on numbers that were not the scenario's |
| Recorder | the session's `runs.jsonl` lines — those that carry its id, and those that carry none inside its window — and how many lines of other sessions were left out | exit codes the transcript lost |

Not in the report: thinking text (counted, not quoted), full tool outputs
(sizes and error heads only), full prompts (truncated). It lives in
`.echolot/`, which is in `.gitignore`.

## Signals are detectors

The same idea as `echolot/sql/detectors/`: one signal is one small function
in `echolot/reflect/signals.py` over the normalised session; it returns a row
set or nothing. Add a function and append it to `SIGNALS` — and then decide
two things that list does not say. Whether it can run on echolot's own calls
alone: `FROM_CALLS_ALONE`, the checks a session without a transcript gets.
And whether it still means something in a session that was building the
tool: `MEANS_SOMETHING_WHILE_BUILDING`. Both are allowlists, so a new signal
is held back from both until somebody adds it — listed under **Not checked**
rather than reporting clean over evidence it never had. And a third thing:
what it says about the two sessions planted in `tests/test_reflect.py`. The
first carries one of everything; the second is made of the shapes behind
one day's false warnings — a subagent run in the background that hands its
answer back, an `Agent` call the host refused, the commands of a batch
tool, `gh … -R owner/echolot`, a passing doctor in the run log, a listing
of the traces — beside the one real warning among them, a script written
through a heredoc and run on a trace (#547). `VERDICTS` and
`SHAPES_VERDICTS` hold each signal's verdict on each, silence included, and
a signal without one fails the test.

A `hint` on a signal is what it points at for the tool — a pointer for
whoever reads the report, never a verdict — and it names the case where the
facts carry one: which `--help` the agent read, whether before the
command's first use, and the flags it then ran it with; which parts of
`report.json` its one-liners took, the most taken first; where the last
doctor was, what it failed on, and whether the session ran `echolot`, the
door that asks for one; which tables the queries that opened a trace read;
whether the loop that read the app ran `echolot mark`, and how much it read
after it. It used to be one sentence per signal, the same in every session.

The ones that ship, by what they watch:

- **entry** — `entry_fumbling`: several slash commands, or the human
  interrupting, before real work started; `agent_prompt_gaps`: the prompt
  handed to the subagent left out the traces, the regression or the change
- **protocol** — doctor before analyze (in the session, or a passing one in
  the run log within 24 hours before it, as `echolot` reports it); the trace never opened directly; the
  loop stayed in the subagent; the loop's own guides left to the subagent;
  rounds within the limit; every inserted tracing
  call carries the prefix; edits inside `instrumentation.allowed`; the
  temporary markers are gone (see below); the conclusion has its eight
  fields, each as a heading (`Place:`, `**Place**`, `## Place`) and not as a
  word in a sentence — the conclusion being what the subagent handed back,
  through the host's hand-back where it has one; analysis ran on the project's config and not one the agent wrote;
  thresholds edited only after `calibrate`; the traces analysed before a
  re-record were copied out first (a `mv` inside the build tree does not
  count — gradle cleans it); values a person confirmed still hold
- **workarounds** — `report.json` cut up by hand; `--help` / `explain` mid-work;
  gradle / adb / perfetto driven directly instead of `collect`;
  `agent_reported_bugs`: the agent saying in its own words, in English or
  Russian, that something in echolot was wrong — quoted rather than classified, since
  a sentence that names the defect is worth more than any rule
- **failures** — echolot calls that failed, tracebacks apart from clean exits,
  shell failures apart from the tool's own; `report_not_the_scenario`: an
  `analyze` that exited 0 on a window that was not the scenario's (see
  below); retries, with what moved between
  the two attempts; tool errors outside echolot, by kind (an exit 1 from
  `diff`, `cmp` or a grep is their answer, not an error, and is left out)
- **cost** — silences of two minutes or more, each with its likely cause:
  what the session was waiting on for most of it — a subagent at work, a
  command in the background before its notification, the turn handed back
  until a message from the human or another session — and failing that the
  call before it (the human answering a question, a build); tool
  outputs over 8k characters; the subagent's window fed by reading sources by
  hand rather than by the report (per activity: echolot, report reading,
  source reading, instrumentation edits, builds — calls and characters, with
  the reads made before the first marker was placed counted separately)

**Cleanup is read off the tree.** Whether a temporary marker is left is
answered by reading the checkout when the report is made — under
`instrumentation.allowed`, or all of it when the config names no roots — and
that outranks the transcript: what the agent typed,
edited or restored with git is intent, what the sources hold now is the fact.
A `git checkout` that restored a file leaves no edit to balance, and a grep
wrapped in `echo` labels once had the prefix read off its own label. The
transcript's reading — additions against removals, a grep after the last edit
— is what is left when the tree cannot be read.

**Which runs are the session's.** Each transcript call is matched to its
line in the run log by subcommand and time, and by session first: a line that
carries the session's id is its own, one that carries another session's is
never its own whatever its time, and one that carries none is taken by time,
as before. Three sessions in one checkout write to one log at the same
seconds — matched by time alone, a call took another session's exit code and
the session's runs were mostly the other two's (#543). For Codex a
subagent's commands carry its own thread's id, which counts as the session's.
The log read is the project's, and the log of every directory the session's
commands wrote to: one a `cd` took them into, one a `-c <dir>/echolot.yml`
named. The Recorder section says how many lines were told apart which way.

**What a report said about its window.** `analyze` exits 0 whatever the
header of its report says, so a round recorded on a phone that had lost its
network, or a window that was the whole trace, used to count as a success
(#545). `report_not_the_scenario` reads it off the run's own line in the log,
which keeps every round: an anchor that matched nothing, markers not in the
build on the phone (and whether `mark --apply` came before), runs that were
not cold, a round in which the app drew no frame, or a tenth of the frames
of the agent's round before. The report file adds what the line does not
carry, and only while it is still that run's — every `analyze` writes
`.echolot/out/report.json` over the one before, so a file written after the
run ended is a later run's and is not read for it. Each row says what the
agent took in (`printed`, `read back`) and what the round built on it cost:
the time until its next `analyze`.

**The harness is not echolot.** A call the agent's harness refused at its
permission screen, that the person declined, or that ran long enough to be
moved to the background did not fail — the command never ran, or is still
running. Those are left out of `echolot_failures` and counted as friction
under kinds of their own in `env_friction`.

**A call is what the shell runs.** A command line is split the way a shell
splits it, and `echolot` counts where it is the command — at the start, after
`;`, `&&`, `|`, inside `$(…)`, behind `FOO=1` or `time`, or as a path to the
binary such as `~/.local/bin/echolot` — and not where it is text handed to
another command: `echo "… echolot collect …"`, `sed -n '/echolot report/p'`
and `gh … -R owner/echolot` are no calls. The same goes for the trace: it is
opened directly when something runs on it — `trace_processor_shell`, python
whose code opens it (with `-c`, through a heredoc, or a script written in the
session and then run), a tool that reads its bytes such as `strings` — and
not when a path merely names `trace_processor`. And a config write is one to
the project's own `echolot.yml` or `local.yml`, not to a copy elsewhere.

## What it cannot see, honestly

`instrumentation.allowed` governs temporary instrumentation. A fix the human
asked for in the main context is a different matter, so only subagent edits
and prefixed edits are held against the list.

Rounds are counted from the transcript: an `analyze` that follows a re-record
opens a new round, and the commands of one shell line count in the order they
run, so `echolot collect … && echolot analyze …` is a round of its own. A
launch, `adb shell am start`, records nothing and is not a re-record. If a
project records traces some other way, the pattern in `RE_RE_RECORD` needs
that way, or the count is low.

The exit code in a transcript is the Bash tool's, not echolot's: a `cd` into
a path with spaces fails before the tool runs. Where the recorder has a line
for the call, it wins; where it has none, the failure is marked as the
shell's. Several `echolot` invocations in one Bash line, or the commands of
one batch tool, share the call's exit code, duration and output; the report
says so (`2 calls in one tool call`, `≤27 s`) rather than crediting each with
the whole. The later ones start when the ones before them end, so each is
matched to its run-log line anywhere in the time the call took: an `analyze`
behind a five-minute `collect` went without its line. When zsh reports a glob that
matched nothing, the invocation carrying that glob is marked as skipped by the
shell — it never ran, whatever the tool's exit code says.

Token counts come from the transcript's usage fields, taken as the maximum
per API response — the rows of one response are written as it streams and the
last row carries the final numbers.

## Without a transcript

Claude Code and Codex have a reader each. Every other client — and a run
from a plain shell, or from CI — gets the report built from
`.echolot/log/runs.jsonl` alone:

```bash
echolot reflect --last              # falls back on its own, and says it did
echolot reflect --last --from-log   # ignore any transcript and use the log
```

`--last` falls back when the project has no transcript, and also when it has
some and none of them used echolot: an old Claude Code session that edited
the README, while the hunt ran from Cursor.

The recorder is the floor because it does not depend on who was driving. Every
command writes a line from every caller, and it keeps the exit code a
transcript loses the moment a call is wrapped in `2>&1 | tail`. What it holds
is which echolot commands ran, when, for how long, with what exit code, and
the facts each attached.

So the checks that read echolot's own calls run unchanged — `doctor_first`,
`confirmed_changed`, `echolot_failures`, `report_not_the_scenario`,
`retries`, `help_lookups`, `long_gaps`, the `FROM_CALLS_ALONE` set. The rest
have nothing to read.

`confirmed_changed` is there by design rather than by luck. An investigation
records every value in `echolot.yml` a person confirmed when it opens, and
each `analyze` inside it logs the ones that no longer hold, with both values.
The first time it mattered was a Codex session, where there was no transcript
to read: the hunt rewrote the end of the scenario a person had confirmed, and
every report after that measured a window nobody had agreed to (#196).

**And that is the part worth getting right.** A check that finds no evidence
returns "clean": `trace_opened_directly` with nothing to look at reports "the
trace was never opened directly" — a green tick over the one rule the whole
design rests on, from a source that could never have seen it either way. So a
reader declares what its source can show, in `Session.carries`, and a check
needing more is listed under **Not checked** with its silence named as no
verdict rather than a clean one.

Lines that carry a session id are grouped by it: two sessions working at
the same minute are two reports, a session that came back after lunch is
one, and the report is named by the id, so `--session` takes the one from
the transcript. A sitting stands in for a session only where the lines carry
none — written by an older echolot, by a host that gives its commands no id,
by a person at a shell. Those are cut into sittings wherever the tool was
left alone for more than half an hour — the same notion `hunt` uses to
decide whether it is looking at the same sitting before asking a question.
The gap is measured from the end of the last run, so `collect -n 5` on a real
device does not split one sitting in two. Parallel sessions without ids still
read as one sitting; time cannot tell them apart.

## A session that was building the tool

Every protocol signal is a rule for a hunt: do not open the trace yourself,
analyse against the project's config, capture through the tool. A session
spent writing detectors breaks all three by definition, and the report then
leads with warnings about a hunt that never happened.

Two conditions decide it, both cheap. The project is a checkout of echolot
itself, which its own `pyproject.toml` says, and the session ran neither
`collect` nor `hunt` — those are what using it looks like, so somebody
dogfooding a real hunt from inside the source tree still gets the ordinary
report.

The protocol checks are then held back the same way a missing source holds
them back: listed under **Not checked**, with the reason, because silence
there is no verdict rather than a clean one. What still runs is everything
about friction — a failing call, a retry, a help lookup, a tool error, a long
silence, an output that ate the window: the `MEANS_SOMETHING_WHILE_BUILDING`
set. A tool that misbehaves under its own author is exactly as broken as one
that misbehaves under a user.

This was found by pointing `reflect` at the session that wrote it: three
findings came back, all true, none about anything anyone did wrong.

## Another agent's transcript

Everything above the reader — facts, signals, report — works on the normalised
session in `echolot/reflect/model.py`: turns, tool calls, questions, usage,
subagents. Another client means another reader producing that shape, declaring
what it carries, and nothing else changes. Codex's reader is the second one:
`codex.py`, beside `claude_code.py`. It reads questions to the human,
subagents and tokens as Claude Code's does. What degrades is what Codex does
not record in the clear: the brief the main thread hands a subagent is stored
encrypted, so `agent_prompt_gaps` is listed as not checked, and a question
carries no recommended option and no record of which one the person chose.
