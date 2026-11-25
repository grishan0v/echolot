"""Small helpers for how a value reads: number checks, counts in words, and a
stored value of the wrong kind (#994).

Each of these was written again in the modules that needed it, and a copy
that got a fix left the others as they were. One copy lives here; a module
imports it, often under the private name its own lines already use.
`test_no_copied_helpers` fails when a top-level function is written out
twice again.
"""

from __future__ import annotations

import math
from typing import Any


def is_number(value: Any) -> bool:
    """An int or a float. True and false are no number here, though Python
    takes a bool for an int."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_ms(value: Any) -> bool:
    """A number that can be a duration: finite and not below zero."""
    return is_number(value) and math.isfinite(value) and value >= 0


def figure(value: float) -> str:
    """A number in its shortest form: `12` for 12.0, `0.5` for 0.5."""
    return f"{value:g}"


def counted(n: int, one: str, many: str | None = None) -> str:
    """`1 run`, `3 runs`. `many` defaults to `one` with an `s`."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def counted_in_words(n: int, one: str, many: str) -> str:
    """`one run`, `3 runs`: as `counted`, with one spelled out."""
    return f"one {one}" if n == 1 else f"{n} {many}"


def as_list(value: Any) -> list[Any]:
    """The list, or an empty one for a value of another kind."""
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    """The object, or an empty one for a value of another kind."""
    return value if isinstance(value, dict) else {}
