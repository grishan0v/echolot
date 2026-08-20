"""Time, said the way a person reads it.

Two functions, and they were in two files. `state.ago` and `hunt._ago` were
the same eleven lines to the character — the same three thresholds, the same
four spellings — and `state.iso_epoch` and `hunt._epoch` the same parse. Both
pairs print into the same screen: `echolot` says when the last report was
made, `echolot hunt --show` says when an investigation was last worked on,
and the two are read one under the other. Edit one copy and they stop
agreeing, silently, because nothing compares them.

The obvious place for shared code is one of the two files, and it is not
available: `state` imports `hunt` to ask what is open, so `hunt` importing
`state` would be a cycle — and a cycle that happens to work depending on
which module is imported first, which is worse than one that does not. So
the shared thing goes below both, the way `table` sits below everything that
prints one.
"""

from __future__ import annotations

import time
from datetime import datetime


def ago(epoch: float | None) -> str:
    """How long ago, in the coarsest unit that still says something.

    Seconds up to a minute and a half, then minutes up to an hour and a half,
    then hours up to two days, then days. The thresholds overshoot each unit
    deliberately: "90m ago" reads better than "2h ago" for something that
    happened an hour and a half back.
    """
    if not epoch:
        return "never"
    delta = time.time() - epoch
    if delta < 90:
        return f"{int(delta)}s ago"
    if delta < 5400:
        return f"{int(delta // 60)}m ago"
    if delta < 172800:
        return f"{delta / 3600:.0f}h ago"
    return f"{delta / 86400:.0f}d ago"


def iso_epoch(ts: str | None) -> float | None:
    """An ISO-8601 stamp as epoch seconds. None when there is nothing to read.

    Every timestamp echolot writes is its own — `datetime.now(timezone.utc)`
    through `isoformat` — but they are read back out of files a person may
    have edited, so a stamp that does not parse is an absent one rather than
    a crash.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
