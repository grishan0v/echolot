# Reflect: the report over the agent

[← Docs index](README.md) · [README](../README.md)

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
echolot reflect --since 2h        # every session in the last 2h / 30m / 3d
echolot reflect --all             # every one, plus a summary
```

Output: `.echolot/reflect/<session>.md` for a human, `.json` for the agent —
`/echolot-reflect` reads the json and turns signals into a list of proposed
changes. When a run covers more than one session (`--all`, or `--since` that
finds several) it also writes `summary.md` and `summary.json` beside them: a
row per session and how often each signal fired, which is where a signal
that fires in most sessions shows up as a design item rather than a note.

Where things are looked for, when they are not where the defaults say:

| flag | default | what it names |
|---|---|---|
| `--project ROOT` | `.` | the application project the agent worked in — its run log, its config, its source tree |
| `--transcripts DIR` | `~/.claude/projects/<slug>` | where Claude Code's transcripts are; naming it leaves Codex's sessions out |
| `-c`, `--local` | `echolot.yml` in the project, `local.yml` beside it | the config the protocol checks read |
| `-o DIR` | `.echolot/reflect` | where the reports go; relative to the project |
| `--from-log` | off | ignore any transcript and read the run log alone — see below |

## Two sources

**The tool's own record.** Every command appends one line to
`.echolot/log/runs.jsonl`: what was asked (`argv`), where, when, exit code,
duration, a hash of the config, a traceback if there was one, and a few facts
the command chose to note — how many detectors fired, whether the anchor
matched. This is the only source that does not depend on which agent was
typing: transcripts differ between agents and lose the exit code the moment a
call is wrapped in `2>&1 | tail`; this file does not. `ECHOLOT_NO_RECORD=1`
switches it off.

**The agent's transcript.** Claude Code keeps every session under
`~/.claude/projects/<slug>/`, the slug being the working directory with `/`
turned into `-`; subagents get their own file under
`<session>/subagents/`. It has what the tool cannot see: the prompts, the
questions to the human and the answers, every tool call with its input and
whether it failed, tokens, the prompt handed to perf-hunter and what came back.
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
echolot for real work are candidates — one that merely ran `reflect` does not
count, or the newest session would always be the one doing the reflecting.

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
| Temporary instrumentation | per file, prefix added vs removed; was there a grep after the last edit; what the source tree holds now | did cleanup happen |
| Cost | wall time, tokens for main and subagents separately, tools by type, largest tool outputs, longest silences | what eats the window |
| Recorder | the `runs.jsonl` lines inside the session's window | exit codes the transcript lost |

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
rather than reporting clean over evidence it never had. A `hint` on a signal
is one line about what it usually means for the tool — a pointer for whoever
reads the report, never a verdict.

The ones that ship, by what they watch:

- **entry** — `entry_fumbling`: several slash commands, or the human
  interrupting, before real work started; `agent_prompt_gaps`: the prompt
  handed to the subagent left out the traces, the regression or the change
- **protocol** — doctor before analyze; the trace never opened directly; the
  loop stayed in the subagent; rounds within the limit; every inserted tracing
  call carries the prefix; edits inside `instrumentation.allowed`; the
  temporary markers are gone (see below); the conclusion has its eight
  fields; analysis ran on the project's config and not one the agent wrote;
  thresholds edited only after `calibrate`; the traces analysed before a
  re-record were copied out first (a `mv` inside the build tree does not
  count — gradle cleans it)
- **workarounds** — `report.json` cut up by hand; `--help` / `explain` mid-work;
  gradle / adb / perfetto driven directly instead of `collect`;
  `agent_reported_bugs`: the agent saying in its own words, in any language,
  that something in echolot was wrong — quoted rather than classified, since
  a sentence that names the defect is worth more than any rule
- **failures** — echolot calls that failed, tracebacks apart from clean exits,
  shell failures apart from the tool's own; retries, with what moved between
  the two attempts; tool errors outside echolot, by kind
- **cost** — silences of two minutes or more, each with what it was read off
  the call before it (a subagent running, the human answering, a build); tool
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

**The harness is not echolot.** A call the agent's harness refused at its
permission screen, that the person declined, or that ran long enough to be
moved to the background did not fail — the command never ran, or is still
running. Those are left out of `echolot_failures` and counted as friction
under kinds of their own in `env_friction`.

## What it cannot see, honestly

`instrumentation.allowed` governs temporary instrumentation. A fix the human
asked for in the main context is a different matter, so only subagent edits
and prefixed edits are held against the list.

Rounds are counted from the transcript: an `analyze` that follows a re-record
opens a new round. If a project records traces some other way, the pattern in
`RE_RE_RECORD` needs that way, or the count is low.

The exit code in a transcript is the Bash tool's, not echolot's: a `cd` into
a path with spaces fails before the tool runs. Where the recorder has a line
for the call, it wins; where it has none, the failure is marked as the
shell's. Several `echolot` invocations in one Bash line share the line's exit
code, duration and output; the report says so (`2 calls in one line`, `≤27 s`)
rather than crediting each with the whole. When zsh reports a glob that
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

The recorder is the floor because it does not depend on who was driving. Every
command writes a line from every caller, and it keeps the exit code a
transcript loses the moment a call is wrapped in `2>&1 | tail`. What it holds
is which echolot commands ran, when, for how long, with what exit code, and
the facts each attached.

So the checks that read echolot's own calls run unchanged — `doctor_first`,
`confirmed_changed`, `echolot_failures`, `retries`, `help_lookups`,
`long_gaps`, the `FROM_CALLS_ALONE` set. The rest have nothing to read.

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

A sitting stands in for a session. The log is a stream with no session id in
it, so runs are cut into sittings wherever the tool was left alone for more
than half an hour — the same notion `hunt` uses to decide whether it is looking
at the same sitting before asking a question. The gap is measured from the end
of the last run, so `collect -n 5` on a real device does not split one sitting
in two.

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
`codex.py`, beside `claude_code.py`. What degrades is what that client
does not record: questions to the human become a heuristic over text, subagents
may not exist, tokens may be missing.
