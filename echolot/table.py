"""One markdown table, for everything that prints one.

There were five of these. The Marker Report had one, the reflect report had
another, `echolot reflect` built a third for its summary, `probe` and `names`
printed a fourth through a helper, and `_dump` printed a fifth inline. Each was
about eight lines, and each had drifted somewhere:

  * three different column orders — the first row's keys, the union of every
    row's keys, and a fixed list with extras appended;
  * two ways of rendering a missing value, `—` and the empty string;
  * and two of the five did not escape `|`.

That last one is not cosmetic. A slice name is an arbitrary string chosen by
whoever wrote the trace{} call, and one pipe in it shifts every column of the
row to the right — silently, in a table an agent is reading as data.

So: one renderer, escaping always, and the things that genuinely differ passed
in. What differs is the column order, the headings, and how a value is spelled;
those are three arguments, not three copies.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

Row = Mapping[str, Any]


def fmt(value: Any) -> str:
    """The default spelling: an absent value is a dash, not a blank."""
    return "—" if value is None else str(value)


def columns(rows: Iterable[Row], *, order: Iterable[str] | None = None,
            skip: Iterable[str] = ()) -> list[str]:
    """Which columns to show, in the order to show them.

    `order` names the ones with a preferred place; anything else a row carries
    follows in the order it was first seen. A detector that invents a column
    gets it rendered without being registered anywhere, which is the same
    promise the detector files make about themselves.
    """
    rows = list(rows)
    skip = set(skip)
    out = [c for c in (order or ()) if c not in skip and any(c in r for r in rows)]
    for row in rows:
        for key in row:
            if key not in out and key not in skip:
                out.append(key)
    return out


def render(rows: list[Row], *, order: Iterable[str] | None = None,
           headers: Mapping[str, str] | None = None,
           skip: Iterable[str] = (),
           cell: Callable[[Any], str] = fmt,
           empty: str = "_empty_") -> str:
    """Rows to a markdown table. `empty` is what an empty list renders as."""
    if not rows:
        return empty
    cols = columns(rows, order=order, skip=skip)
    headers = headers or {}
    head = "| " + " | ".join(_escape(headers.get(c, c)) for c in cols) + " |"
    rule = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(_escape(cell(r.get(c))) for c in cols) + " |"
            for r in rows]
    return "\n".join([head, rule, *body])


def show(rows: list[Row], **kw) -> None:
    """The same table, printed. For the commands that write to stdout."""
    print(render(rows, **kw))


def _escape(text: str) -> str:
    """A pipe inside a cell ends the cell. It has to stop being a pipe."""
    return str(text).replace("|", "\\|")
