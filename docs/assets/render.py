#!/usr/bin/env python3
"""Draws the README's tables from their sources.

    python docs/assets/render.py              write the README's sample report, then
                                              every table next to this file
    python docs/assets/render.py --preview D  also write the README as pages into D:
                                              light and dark, desktop and phone

The README's tables are pictures. GitHub draws a Markdown table with a border
round every cell and a stripe on every other row, and a README cannot restyle
it; a picture can take the logo's colours and leave room between the rows.
The words in them come from where the tool keeps them: the detectors' table
from each detector's `@title`, the heading its section has in the report, in
the order DETECTOR_ORDER gives, and the questions from ASK below.
tests/test_readme_pictures.py draws everything again and fails when a
committed file differs.

The README's sample report is not written by hand either: it is what `analyze`
prints for the demo app (echolot/demo.py). tests/test_doc_samples.py renders
it again and fails when the README's copy differs.

The rules every picture follows are in docs/assets/README.md.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
README = ROOT / "README.md"
DETECTORS = ROOT / "echolot" / "sql" / "detectors"
# Where the README names every picture: PyPI resolves nothing relative.
RAW = "https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/"

SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
        "Helvetica, Arial, sans-serif")
MONO = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', monospace")

# The logo's colours: the slate of its plate, and its sphere from the top down.
# The sphere's three are the same in both themes. The rest are the greys of
# GitHub's page, with the slate as the ink on white.
SLATE = "#262C33"
SPHERE = ("#74E1C7", "#3DB2AE", "#3A6AB8")
THEMES = {
    "light": dict(ink=SLATE, sub="#646D78", line="#E6E9ED", accent="#1D6F7A",
                  page="#FFFFFF"),
    "dark": dict(ink="#E6EDF3", sub="#8B949E", line=SLATE, accent=SPHERE[0],
                 page="#0D1117"),
}

# What to type in the agent, by what a person arrives with: (when, what to
# type, what it needs). The first row is the first run, which sets the
# project up.
ASK = [
    ("First time in a project", "/echolot",
     "sets it up: echolot.yml from the repository and a probe trace"),
    ("Cold start got slower", "/echolot why is cold start slow", None),
    ("Scrolling stutters", "/echolot the feed janks on scroll",
     "a scroll scenario in echolot.yml, and Android 12+"),
    ("“App isn't responding”", "/echolot the app froze, see anr.txt",
     "the report from Crashlytics, Play Console or dumpsys dropbox"),
]

# The detectors' table, in the order a person reads it: the main thread,
# what it waits on, the frames, then what nobody instrumented. A detector
# added to echolot/sql/detectors/ stops the script until it has a place here.
DETECTOR_ORDER = [
    "main_thread_block", "main_thread_outlier", "app_init", "anr_risk", "anr",
    "monitor_contention", "binder_txn", "io_wait", "runnable_starvation",
    "main_thread_waits", "gc_pressure", "frame_jank", "composables",
    "uninstrumented_cpu", "repeated_work",
]

# Pictures the README no longer shows. The releases 0.9.0 to 0.15.0 show them
# on PyPI, which reads each one from `main`, so they stay here as they are;
# nothing draws them any more, and tests/test_readme_pictures.py checks that
# they are still in the tree.
RETIRED = (
    "ask-dark.svg", "ask-light.svg", "compare-dark.svg", "compare-light.svg",
    "detectors-dark.svg", "detectors-light.svg", "echolot-dark.png",
    "hero-dark.svg", "hero-light.svg", "hero-narrow.svg", "logo-dark.png",
    "logo-light.png", "loop-dark.svg", "loop-light.svg", "session.svg",
    "versus-dark.svg", "versus-light.svg",
)


# --- where the words come from ------------------------------------------------

def detector_rows() -> list[tuple[str, str, None]]:
    """Each shipped detector with its `@title`, in DETECTOR_ORDER."""
    sys.path.insert(0, str(ROOT))
    from echolot.tp import load_detectors

    titles = {d.id: d.title for d in load_detectors(DETECTORS)}
    unplaced = sorted(set(titles) - set(DETECTOR_ORDER))
    gone = sorted(set(DETECTOR_ORDER) - set(titles))
    if unplaced or gone:
        raise SystemExit(
            "docs/assets/render.py: DETECTOR_ORDER and echolot/sql/detectors/ "
            f"disagree — not placed: {unplaced or 'none'}, not shipped: {gone or 'none'}")
    return [(d, titles[d], None) for d in DETECTOR_ORDER]


# --- drawing ------------------------------------------------------------------

def text(x, y, s, *, size, fill, family=SANS, weight=400, spacing=0):
    track = f' letter-spacing="{spacing:g}"' if spacing else ""
    return (f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size:g}" '
            f'font-weight="{weight}" fill="{fill}"{track} xml:space="preserve" '
            f'style="white-space:pre">{html.escape(s)}</text>')


def svg(w, h, body, title, style=""):
    head = f"<style>{style}</style>" if style else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" '
            f'viewBox="0 0 {w:g} {h:g}" role="img" aria-label="{html.escape(title)}">'
            f'<title>{html.escape(title)}</title>{head}{body}</svg>\n')


def _rgb(colour: str) -> tuple[int, ...]:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def sphere_at(t: float) -> str:
    """The sphere's colour at t, from its top (0) to its bottom (1)."""
    a, b, u = (SPHERE[0], SPHERE[1], t * 2) if t <= 0.5 else (SPHERE[1], SPHERE[2], t * 2 - 1)
    return "#%02X%02X%02X" % tuple(round(p + (q - p) * u) for p, q in zip(_rgb(a), _rgb(b), strict=True))


def sphere_line(x, y, w, h, gid):
    """A line in the sphere's colours, left to right. A rect, since a gradient
    measured against a box draws nothing on a line that has no height."""
    stops = "".join(f'<stop offset="{i / 2:g}" stop-color="{colour}"/>'
                    for i, colour in enumerate(SPHERE))
    return (f'<defs><linearGradient id="{gid}" x1="0" x2="1" y1="0" y2="0">{stops}'
            f'</linearGradient></defs><rect x="{x:g}" y="{y:g}" width="{w:g}" '
            f'height="{h:g}" fill="url(#{gid})"/>')


def follows_the_theme(light: dict, dark: dict) -> str:
    """CSS that repaints a picture drawn in the light colours when the page it
    sits on is dark. Inside a picture, Chrome answers prefers-color-scheme with
    the page's color-scheme, which GitHub sets from the theme picked on GitHub;
    a browser that answers with the system's theme still shows the picture on
    its own card. Each light colour has one role only, so the swap goes by the
    colour itself."""
    rules = [f'[fill="{v}"]{{fill:{dark[k]}}}[stroke="{v}"]{{stroke:{dark[k]}}}'
             for k, v in light.items() if dark[k] != v]
    return "@media (prefers-color-scheme:dark){" + "".join(rules) + "}"


# A column's type: (family, size, colour role, weight).
Kind = tuple[str, float, str, int]
WIDE, NARROW = 880, 360


def card(w, h) -> str:
    """The card a table sits on. The table is one file for both themes, and
    the card keeps it readable when it is painted for the other theme than the
    page's: a browser that asks the system, a page whose theme it cannot see."""
    c = THEMES["light"]
    return (f'<rect x="0.5" y="0.5" width="{w - 1:g}" height="{h - 1:g}" rx="16" '
            f'fill="{c["page"]}" stroke="{c["line"]}"/>')


def wide_table(labels, rows, *, split, lead: Kind, rest: Kind, title, gid, row=58):
    """A table at the README's width, on its card. A row is a dot in the
    sphere's colour for its place in the table, its lead, and the rest at
    `split`, with a note under the rest when it has one. No borders between
    the cells: a line in the sphere's colours under the labels, and a hairline
    between the rows."""
    c = THEMES["light"]
    pad = 24
    left, split = pad + 30, pad + split
    out = [text(left, pad + 13, labels[0], size=11.5, weight=600, fill=c["sub"], spacing=1.6),
           text(split, pad + 13, labels[1], size=11.5, weight=600, fill=c["sub"], spacing=1.6),
           sphere_line(pad, pad + 28, WIDE - 2 * pad, 1.5, gid)]
    y = pad + 29.5
    for i, (first, second, note) in enumerate(rows):
        base = y + row / 2 + 7
        out.append(f'<circle cx="{pad + 6}" cy="{base - 5:g}" r="4" '
                   f'fill="{sphere_at(i / max(len(rows) - 1, 1))}"/>')
        out.append(text(left, base, first, family=lead[0], size=lead[1], fill=c[lead[2]],
                        weight=lead[3]))
        out.append(text(split, base, second, family=rest[0], size=rest[1], fill=c[rest[2]],
                        weight=rest[3]))
        if note:
            out.append(text(split, base + 23, note, size=13.5, fill=c["sub"]))
        y += row + (21 if note else 0)
        if i < len(rows) - 1:
            out.append(f'<rect x="{left}" y="{y:g}" width="{WIDE - pad - left}" height="1" '
                       f'fill="{c["line"]}"/>')
    height = y + pad - 8
    return svg(WIDE, height, card(WIDE, height) + "".join(out), title,
               style=follows_the_theme(THEMES["light"], THEMES["dark"]))


def _wrap(s: str, kind: Kind, width: float) -> list[str]:
    """Lines short enough for `width`, at a glyph width every face in the stack
    stays under: 0.6 of the size for a monospace, 0.55 for the rest."""
    per = kind[1] * (0.6 if kind[0] == MONO else 0.55)
    return textwrap.wrap(s, max(8, int(width // per))) or [""]


def narrow_table(rows, *, lead: Kind, rest: Kind, title, gid):
    """The same table for a phone, on its card: each row one under the other."""
    c = THEMES["light"]
    pad, left = 20, 40
    width = NARROW - left - pad
    out = [sphere_line(pad, pad, NARROW - 2 * pad, 1.5, gid)]
    y = pad + 1.5
    for i, (first, second, note) in enumerate(rows):
        y += 32
        out.append(f'<circle cx="{pad + 4}" cy="{y - 5:g}" r="4" '
                   f'fill="{sphere_at(i / max(len(rows) - 1, 1))}"/>')
        out.append(text(left - 2, y, first, family=lead[0], size=lead[1], fill=c[lead[2]],
                        weight=lead[3]))
        for line in _wrap(second, rest, width):
            y += 22
            out.append(text(left - 2, y, line, family=rest[0], size=rest[1], fill=c[rest[2]],
                            weight=rest[3]))
        for line in _wrap(note, (SANS, 12.5, "sub", 400), width) if note else []:
            y += 19
            out.append(text(left - 2, y, line, size=12.5, fill=c["sub"]))
        y += 18
        if i < len(rows) - 1:
            out.append(f'<rect x="{left - 2}" y="{y:g}" width="{NARROW - left - pad + 2}" '
                       f'height="1" fill="{c["line"]}"/>')
    height = y + pad - 6
    return svg(NARROW, height, card(NARROW, height) + "".join(out), title,
               style=follows_the_theme(THEMES["light"], THEMES["dark"]))


def tables() -> dict[str, dict]:
    """The README's tables, by the name their files start with: the words, and
    the type of each column at the README's width and at a phone's."""
    return {
        "ask": dict(
            title="What to type in the agent, by the problem you arrive with",
            labels=("WHEN", "WHAT TO TYPE"), rows=ASK, split=300, row=58,
            lead=(SANS, 16, "ink", 400), rest=(MONO, 14.5, "accent", 500),
            narrow=((SANS, 16, "ink", 400), (MONO, 13.5, "accent", 500))),
        "detectors": dict(
            title="The detectors, and what each one catches",
            labels=("DETECTOR", "WHAT IT CATCHES"), rows=detector_rows(), split=260, row=46,
            lead=(MONO, 14.5, "accent", 500), rest=(SANS, 15.5, "ink", 400),
            narrow=((MONO, 14, "accent", 500), (SANS, 14.5, "ink", 400))),
    }


def pictures() -> dict[str, str]:
    """Every picture this script draws, by file name."""
    out = {}
    for name, t in tables().items():
        out[f"{name}-wide.svg"] = wide_table(
            t["labels"], t["rows"], split=t["split"], lead=t["lead"], rest=t["rest"],
            title=t["title"], gid=name, row=t["row"])
        lead, rest = t["narrow"]
        out[f"{name}-narrow.svg"] = narrow_table(t["rows"], lead=lead, rest=rest,
                                                 title=t["title"], gid=name)
    return out


def alt(table: dict) -> str:
    """A table in words, for whoever does not see the picture: a screen reader,
    a search, a page with its images off."""
    rows = " ".join(f"{first}: {second}{f' ({note})' if note else ''}."
                    for first, second, note in table["rows"])
    return f"{table['title']}. {rows}"


_ALT = re.compile(r'(<img alt=")([^"]*)(" src="' + re.escape(RAW) + r'([a-z]+)-wide\.svg")')


def with_alts(readme: str) -> str:
    """The README with each table picture's alt text written from its rows."""
    known, found = tables(), set()

    def words(match: re.Match) -> str:
        name = match.group(4)
        if name not in known:
            return match.group(0)
        found.add(name)
        said = alt(known[name])
        for raw, escaped in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")):
            said = said.replace(raw, escaped)
        return match.group(1) + said + match.group(3)

    new = _ALT.sub(words, readme)
    if found != set(known):
        raise SystemExit(f"README.md: no <img> shows the table {sorted(set(known) - found)}")
    return new


# --- the README as a page, to look at -------------------------------------------

def preview_page(theme: str, phone: bool, readme: str | None = None) -> str:
    """The README as GitHub shows it, near enough to judge the pictures by: in
    one theme, at a desktop's width or a phone's. The <source> a reader would
    get is decided here, since the page is opened on a desktop either way; the
    theme is the page's color-scheme, as on GitHub, and the pictures, read
    from `assets/` beside the page, follow it."""
    from markdown_it import MarkdownIt

    readme = README.read_text(encoding="utf-8") if readme is None else readme
    body = MarkdownIt("commonmark", {"html": True}).render(readme).replace(RAW, "assets/")
    body = body.replace('media="(max-width: 767px)"', f'media="{"all" if phone else "not all"}"')
    c = THEMES[theme]
    edge, code = ("#D1D9E0", "#F6F8FA") if theme == "light" else ("#3D444D", "#151B23")
    width = 375 if phone else 928
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><meta name="color-scheme" content="{theme}">
<style>
:root{{color-scheme:{theme}}}
body{{margin:0;background:{c['page']};color:{c['ink']};font:16px/1.5 {SANS}}}
.md{{max-width:{width}px;box-sizing:border-box;margin:{0 if phone else 24}px auto;
padding:{16 if phone else 32}px;border:{0 if phone else 1}px solid {edge};border-radius:6px}}
img{{max-width:100%;height:auto}} p,ul,pre,details{{margin:0 0 16px}} a{{color:{c['accent']}}}
h2{{font-size:1.5em;font-weight:600;border-bottom:1px solid {edge};padding-bottom:.3em;margin:24px 0 16px}}
code{{font:85% {MONO};background:{code};padding:.2em .4em;border-radius:6px}}
pre{{background:{code};border-radius:6px;padding:16px;overflow:auto}}
pre code{{background:none;padding:0;font-size:85%}} summary{{cursor:pointer}}
</style></head><body><div class="md">
{body}</div></body></html>"""


def write_preview(target: Path) -> list[Path]:
    """The four pages, and the pictures they show."""
    shutil.copytree(HERE, target / "assets", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.py", "*.md", "__pycache__"))
    pages = []
    for theme in THEMES:
        for phone in (False, True):
            page = target / f"preview-{theme}{'-phone' if phone else ''}.html"
            page.write_text(preview_page(theme, phone), encoding="utf-8")
            pages.append(page)
    return pages


# --- the README's own words ---------------------------------------------------

SAMPLE = re.compile(r"^```markdown\n# Marker Report\n.*?^```", re.S | re.M)


def write_readme() -> bool:
    """The README's parts this script writes: the sample report, rendered from
    the demo app, and the alt text of each table. True when either changed."""
    sys.path.insert(0, str(ROOT))
    from echolot import demo, recorder
    from echolot import report as report_mod

    with recorder.isolated():
        rendered = report_mod.to_markdown(demo.report()).rstrip("\n")
    text_ = README.read_text(encoding="utf-8")
    if not SAMPLE.search(text_):
        raise SystemExit("README.md: the ```markdown block holding the sample report is gone")
    new = with_alts(SAMPLE.sub(lambda _: f"```markdown\n{rendered}\n```", text_, count=1))
    if new == text_:
        return False
    README.write_text(new, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="render.py", description="Draws the README's tables from their sources.")
    parser.add_argument("--preview", metavar="DIR", type=Path,
                        help="also write the README as pages into DIR: light and dark, "
                             "desktop and phone")
    args = parser.parse_args(argv)
    if write_readme():
        print(f"→ {README} (the sample report, the tables' alt text)")
    for name, body in pictures().items():
        (HERE / name).write_text(body, encoding="utf-8")
        print(f"→ {HERE / name}")
    if args.preview is not None:
        for page in write_preview(args.preview):
            print(f"→ {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
