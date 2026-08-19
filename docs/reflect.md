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
echolot reflect --all             # every one, plus summary.md
```

Output: `.echolot/reflect/<session>.md` for a human, `.json` for the agent —
`/echolot-reflect` reads the json and turns signals into a list of proposed
changes.

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

`reflect` runs from the application project because that is where both live.
Only sessions that used echolot for real work are candidates — one that merely
ran `reflect` does not count, or the newest session would always be the one
doing the reflecting.

## What is in the report

Signals first, then the facts they rest on. Each section exists because of a
decision it lets you make:

| section | what it holds | the decision it serves |
|---|---|---|
| Signals | protocol breaks (`warn`), workarounds and friction (`info`), checks that passed (`ok`) | what to change, in what order |
| Entry | prompts, slash commands, skills loaded, time to real work | is the way into the tool obvious |
| Timeline | milestones: first call of each subcommand, questions, config written, agent launched, first temporary slice | where the time went |
| echolot calls | every invocation: subcommand, args, exit, duration, output size, config used, help lookups | which commands the agent fumbles |
| Questions to the human | each `AskUserQuestion`: options, recommended, chosen, time to answer | are the questions needed, are the defaults right |
| Subagent | rounds against `loop.max_rounds`, re-records, tools, tokens, whether the prompt named the traces / the regression / the change, whether the conclusion has all six fields | is the loop behaving |
| Temporary instrumentation | per file, prefix added vs removed; was there a grep after the last edit | did cleanup happen |
| Cost | wall time, tokens for main and subagents separately, tools by type, largest tool outputs, longest silences | what eats the window |
| Recorder | the `runs.jsonl` lines inside the session's window | exit codes the transcript lost |

Not in the report: thinking text (counted, not quoted), full tool outputs
(sizes and error heads only), full prompts (truncated). It lives in
`.echolot/`, which is in `.gitignore`.

## Signals are detectors

The same idea as `echolot/sql/detectors/`: one signal is one small function
in `echolot/reflect/signals.py` over the normalised session; it returns a row
set or nothing. Add a function, append it to `SIGNALS`, done. A `hint` on a
signal is one line about what it usually means for the tool — a pointer for
whoever reads the report, never a verdict.

The ones that ship, by what they watch:

- **protocol** — doctor before analyze; the trace never opened directly; the
  loop stayed in the subagent; rounds within the limit; every inserted tracing
  call carries the prefix; edits inside `instrumentation.allowed`; additions
  and removals of the prefix balance, with a grep afterwards; the conclusion
  has its six fields; analysis ran on the project's config and not one the
  agent wrote; thresholds edited only after `calibrate`; the traces analysed
  before a re-record were copied out first (a `mv` inside the build tree does
  not count — gradle cleans it)
- **workarounds** — `report.json` cut up by hand; `--help` / `explain` mid-work;
  gradle / adb / perfetto driven directly instead of `collect`
- **failures** — echolot calls that failed, tracebacks apart from clean exits,
  shell failures apart from the tool's own; retries; tool errors outside
  echolot, by kind
- **cost** — silences of two minutes or more; tool outputs over 8k characters;
  the subagent's window fed by reading sources by hand rather than by the
  report (per activity: echolot, report reading, source reading,
  instrumentation edits, builds — calls and characters, with the reads made
  before the first marker was placed counted separately)

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

Only Claude Code has a reader for its transcripts. Every other client — and a
run from a plain shell, or from CI — gets the report built from
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
`echolot_failures`, `retries`, `help_lookups`, `long_gaps`. The rest have
nothing to read.

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

## Another agent's transcript

Everything above the reader — facts, signals, report — works on the normalised
session in `echolot/reflect/model.py`: turns, tool calls, questions, usage,
subagents. Another client means another reader producing that shape, declaring
what it carries, and nothing else changes. What degrades is what that client
does not record: questions to the human become a heuristic over text, subagents
may not exist, tokens may be missing.
