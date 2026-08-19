"""`check` — a named claim that fails the test it is in.

The four files this replaced each defined their own. They agreed on the shape
— a name, a condition, and the detail to print when it does not hold — and
disagreed on everything after: one collected into a module-level list, one
returned a list of strings, and each printed its own summary.

The name is kept rather than replaced by a bare `assert`. `assert row["count"]
== 5, row` tells you a number was wrong; `check("5 frames cleared the overrun
floor", ...)` tells you which claim about the tool stopped being true, which is
the thing worth reading in a failure two months from now.

One difference from what came before: a failing check ends its test, where the
old collectors ran every check and printed all the failures at once. That is
the trade pytest makes, and it pays for itself in granularity — a case is a
test, and `-k` runs one.
"""

from __future__ import annotations

from typing import Any


def check(name: str, ok: Any, why: Any = "") -> None:
    """Assert `ok`, and say which claim failed when it does not hold."""
    if ok:
        return
    detail = str(why).strip()
    raise AssertionError(f"{name}\n          {detail}" if detail else name)
