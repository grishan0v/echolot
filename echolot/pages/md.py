"""report.md as HTML: the Markdown `report.to_markdown` writes, and no more.

The technical report under the page's fold is report.md, so that the page says
what the file says and a line added to one is in the other. This reads the
few forms that text is written in:

    # heading, ## heading      a line of its own
    > a note                   a callout; one that opens with ⚠️ is a warning
    | a | table |              `table.render`'s, with `\\|` for a pipe in a cell
    _a whole line in italics_  the sentence under a heading
    <sub>a whole line</sub>    the small print under a table
    `code` and **bold**        inside a line

Every other line is a sentence of its own: the report's header is a list of
facts, one to a line. Everything is escaped. A cell of a table is a name out
of a trace, so it is shown as it is, with no `code` or **bold** read into it.
"""

from __future__ import annotations

import re

from .kit import e, icon

_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_PIPE = re.compile(r"(?<!\\)\|")
_NUMBER = re.compile(r"-?[\d.,]+%?")
# A cell this long is evidence: it gets room, and the table scrolls sideways.
_LONG = 80


def inline(text: str) -> str:
    """`code` and **bold** inside a line, everything else escaped."""
    parts = _CODE.split(text)
    return "".join(f"<code>{e(p)}</code>" if i % 2 else _BOLD.sub(r"<b>\1</b>", e(p))
                   for i, p in enumerate(parts))


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in _PIPE.split(inner)]


def _table(lines: list[str]) -> str:
    head = _cells(lines[0])
    body = [_cells(line) for line in lines[1:] if set(line.strip()) - set("|-: ")]
    width = len(head)
    # Figures line up at the right: a column that holds nothing but numbers.
    numeric = [bool(body) and all(i < len(r) and (_NUMBER.fullmatch(r[i]) or r[i] == "—") for r in body)
               for i in range(width)]

    def cls(i: int, text: str = "") -> str:
        names = (["r"] if i < width and numeric[i] else []) + (["long"] if len(text) > _LONG else [])
        return f' class="{" ".join(names)}"' if names else ""

    out = ['<div class="tblwrap"><table class="tbl"><thead><tr>',
           "".join(f"<th{cls(i)}>{e(c)}</th>" for i, c in enumerate(head)), "</tr></thead><tbody>"]
    for row in body:
        out.append("<tr>" + "".join(f"<td{cls(i, c)}>{e(c)}</td>" for i, c in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _note(text: str) -> str:
    warning = text.startswith("⚠")
    if warning:
        text = text.lstrip("⚠️ ")
        return (f'<div class="callout">{icon("yellow")}<p><span class="vh">Warning: </span>{inline(text)}</p></div>')
    return f'<div class="callout"><p>{inline(text)}</p></div>'


def to_html(text: str) -> str:
    """The blocks of a report.md, in order, one element each."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue
        if line.startswith("## "):
            out.append(f'<h3 class="md-h">{inline(line[3:])}</h3>')
        elif line.startswith("# "):
            out.append(f'<h2 class="h3">{inline(line[2:])}</h2>')
        elif line.startswith("> "):
            out.append(_note(line[2:]))
        elif line.startswith("<sub>") and line.endswith("</sub>"):
            out.append(f'<p class="micro sub">{inline(line[5:-6])}</p>')
        elif len(line) > 2 and line.startswith("_") and line.endswith("_"):
            out.append(f'<p class="sub">{inline(line[1:-1])}</p>')
        else:
            out.append(f"<p>{inline(line)}</p>")
        i += 1
    return "\n".join(out)
