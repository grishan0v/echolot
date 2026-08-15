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
    signals.py      the detectors over a normalised session
    render.py       report.json / report.md

Only the reader knows the agent's on-disk format. Signals and rendering work on
the model alone, so a second agent means a second reader and nothing else.
"""
