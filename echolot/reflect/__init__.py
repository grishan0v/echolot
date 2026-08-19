"""`echolot reflect` — the Marker Report over an agent session.

The same idea as `analyze`, pointed at the agent instead of the trace. A
session transcript is hundreds of kilobytes to tens of megabytes; nobody reads
it, and a model fed with it drowns in exactly the way it drowns in a trace.
Between the transcript and the decision "what to change in the tool" sits a
deterministic layer that compresses the session into a table of facts and a
short list of signals.

Layout:

    model.py        the normalised session — what every reader must produce
    claude_code.py  the reader for Claude Code transcripts (~/.claude/projects)
    from_log.py     the reader that needs no transcript: .echolot/log/runs.jsonl
    signals.py      the detectors over a normalised session
    render.py       report.json / report.md
    cli.py          `echolot reflect`: which session, and the across-runs summary

Only the reader knows the agent's on-disk format. Signals and rendering work on
the model alone, so a second agent means a second reader and nothing else.

A reader also declares what its source can show, in `Session.carries`. That is
not bookkeeping: a check finding no evidence returns "clean", and on a source
that could never have held the evidence, "clean" is a green tick over a
question nobody asked. What a reader cannot show is reported as not checked.
"""
