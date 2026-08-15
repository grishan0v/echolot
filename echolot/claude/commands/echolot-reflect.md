---
description: Reflect on how the last echolot session went and propose changes to the tool
---

Turn one agent session into a short list of concrete changes to echolot — to
the CLI, to the skill texts, to the config. This is for whoever maintains the
tool, not for the application being profiled.

## What the report is

`echolot reflect` reads the session's transcript (this Claude Code project's
`~/.claude/projects/…` files, subagents included) and the tool's own
`.echolot/log/runs.jsonl`, and compresses them into `.echolot/reflect/<id>.json`
and `.md`. It is the Marker Report over the agent instead of the trace: facts
and signals, no conclusions. You draw those.

## The run

```bash
echolot reflect --list            # which sessions are there
echolot reflect --last            # the newest one that used echolot (default)
echolot reflect --session <id>    # a specific one
echolot reflect --all             # every session, plus summary.md
```

Then read `.echolot/reflect/<id>.json` — the `signals` array first, then
`hunts`, `echolot_calls`, `questions`, `entry`. The markdown is the same data
for a human; do not re-read the transcript itself.

## What to return

Group the proposals by where the change lands, each with its evidence — a
signal id and the numbers from it — and a one-line diff-sized description:

```
CLI
  - <change>            evidence: <signal id>, <rows/numbers>
Skill / commands / perf-hunter.md
  - <change>            evidence: …
Config / calibrate
  - <change>            evidence: …
Not actionable (noise, one-off)
  - <signal id>: why it is noise this time
```

Rules:

- **A signal is a pointer, not a verdict.** `report_sliced_by_hand` eight
  times means "look at what the one-liners selected", not "add a flag". Say
  what the agent was after and only then what would have served it.
- **Separate the tool's faults from the environment's.** A hook redirect or a
  missing python module is friction the tool may absorb, not a bug in it; say
  which side each item is on.
- **Prefer the smallest change that removes the signal.** One line in SKILL.md
  beats a new subcommand when the agent merely did not know a flag existed.
- **Do not edit anything without asking.** Show the list; the human picks.
- **`warn` before `info`.** Protocol breaks (a config bypassed, an edit outside
  `instrumentation.allowed`, no cleanup grep) come first — they change what the
  conclusion of that session was worth.

If several sessions were reflected (`--all`), start from `summary.md`: a signal
that fires in most sessions is a design item; one that fired once is a note.
