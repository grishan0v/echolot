"""Pages for a person: the report, and what comes after it, as HTML.

A page is one file that opens from disk: no network, no font or script
fetched, system fonts, inline SVG, plain CSS and a little script. The same
input gives the same bytes.

What a page is made of is kept apart from the page, so that the next one is
arranged from the same pieces:

    kit.py          the pieces as Python: the document, the frame, a level's
                    icon and pill, a fold, a chart, the table with its filter
                    and panel, the tick box, the bar and the request
    base.css/.js    tokens, type, levels, folds, buttons, charts, the tooltip
    table.css/.js   the filter by level, the table as a grid, the panel
    picks.css/.js   the tick box, the selection bar, the request to copy
    report.css      what is the report's alone: its columns, report.md as HTML

    report_page.py  the report: `echolot analyze --html`, `echolot report --html`
    md.py           the Markdown that report.md is written in, as HTML

    answer.css/.js  what is the answer's alone: its columns, the rounds of an
                    investigation, the command to copy
    answer_page.py  an investigation and the agent's answer:
                    `echolot hunt --show [n] --html`, `echolot hunt --done --html`

A new page takes the `.css` and `.js` it needs by name in `kit.document`,
adds a stylesheet for its own columns, and builds its body from `kit`.
"""
